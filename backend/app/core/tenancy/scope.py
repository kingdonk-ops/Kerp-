"""ORM-level read filters and write guard (T-0007, TD-033).

Ported from Inspection-ERP ``core/tenancy/scope.py`` (ADR 0001). It is the app-layer
complement of the RLS policy in ``rls.py`` and mirrors it exactly, so behaviour is the
same on SQLite (where it is the only filter) and on PostgreSQL (where RLS backs it up).

* **Soft delete** (TD-033): every ORM ``SELECT`` hides rows with ``deleted_at`` set, unless
  the statement carries ``execution_options(include_deleted=True)``.
* **Tenancy scope**: for a session bound with ``bind_tenancy``, every ORM ``SELECT`` sees
  only rows of the bound tenant whose ``project_id`` is ``NULL`` or the bound project.
  Out-of-scope rows do not exist for the caller: lists omit them, lookups by id 404.
* **Write guard**: a bound session refuses to flush (insert, update or delete) a row
  outside its exact scope with ``OutOfScopeError``: another tenant, another project, or a
  tenant-level row from a project session. The API answers 404 (never 403, so the
  existence of another project's data is not revealed).

An unbound session is not filtered here. On PostgreSQL RLS then shows it nothing.
ORM bulk ``update()``/``delete()`` statements skip both the loader criteria and the flush
guard; on PostgreSQL RLS still scopes them, on SQLite nothing does. Use them only with an
explicit tenant/project ``WHERE``.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy import and_, event, or_
from sqlalchemy.orm import ORMExecuteState, Session, UOWTransaction, with_loader_criteria

from app.core.db import BaseRecordMixin
from app.core.tenancy.rls import session_tenancy

INCLUDE_DELETED: Final = "include_deleted"


class OutOfScopeError(LookupError):
    """A record outside the caller's tenant/project. The API maps it to 404."""


def is_visible(session: Session, record: BaseRecordMixin, *, include_deleted: bool = False) -> bool:
    """Whether ``record`` is readable by ``session``: same rule as the read filters."""
    if record.deleted_at is not None and not include_deleted:
        return False
    context = session_tenancy(session)
    if context is None:
        return True
    return record.tenant_id == context.tenant_id and record.project_id in (
        None,
        context.project_id,
    )


def get_or_404[RecordT: BaseRecordMixin](
    session: Session,
    model: type[RecordT],
    record_id: uuid.UUID,
    *,
    include_deleted: bool = False,
) -> RecordT:
    """Load a record visible to the session, or raise ``OutOfScopeError``.

    ``session.get`` may answer from the identity map without SQL, so the result is
    re-checked against the session's scope and soft-delete state.
    """
    record = session.get(model, record_id, execution_options={INCLUDE_DELETED: include_deleted})
    if record is None or not is_visible(session, record, include_deleted=include_deleted):
        raise OutOfScopeError(f"{model.__name__} not found")
    return record


@event.listens_for(Session, "do_orm_execute")
def _filter_reads(state: ORMExecuteState) -> None:
    if not state.is_select or state.is_column_load or state.is_relationship_load:
        return
    options: list[Any] = []
    if not state.execution_options.get(INCLUDE_DELETED, False):
        options.append(
            with_loader_criteria(
                BaseRecordMixin, lambda cls: cls.deleted_at.is_(None), include_aliases=True
            )
        )
    context = session_tenancy(state.session)
    if context is not None:
        tenant_id, project_id = context.tenant_id, context.project_id
        if project_id is None:
            options.append(
                with_loader_criteria(
                    BaseRecordMixin,
                    lambda cls: and_(cls.tenant_id == tenant_id, cls.project_id.is_(None)),
                    include_aliases=True,
                )
            )
        else:
            options.append(
                with_loader_criteria(
                    BaseRecordMixin,
                    lambda cls: and_(
                        cls.tenant_id == tenant_id,
                        or_(cls.project_id.is_(None), cls.project_id == project_id),
                    ),
                    include_aliases=True,
                )
            )
    if options:
        state.statement = state.statement.options(*options)


@event.listens_for(Session, "before_flush")
def _guard_writes(session: Session, flush_context: UOWTransaction, instances: object) -> None:
    context = session_tenancy(session)
    if context is None:
        return
    # Writes need the exact scope (mirrors project_isolation_policy): a project session
    # writes only its project, a tenant-level session only tenant-level rows. NULL is not
    # a wildcard here, so a row cannot be created as, or demoted to, a tenant-level row.
    for obj in (*session.new, *session.dirty, *session.deleted):
        if not isinstance(obj, BaseRecordMixin):
            continue
        if obj.tenant_id is not None and obj.tenant_id != context.tenant_id:
            raise OutOfScopeError(f"{type(obj).__name__} not found")
        if obj.project_id != context.project_id:
            raise OutOfScopeError(f"{type(obj).__name__} not found")
