"""ORM-level read filters and write guard (T-0007, TD-033).

Ported from Inspection-ERP ``core/tenancy/scope.py`` (ADR 0001). It is the app-layer
complement of the RLS policy in ``rls.py`` and mirrors it exactly, so behaviour is the
same on SQLite (where it is the only filter) and on PostgreSQL (where RLS backs it up).

* **Soft delete** (TD-033): every ORM ``SELECT`` hides rows with ``deleted_at`` set, unless
  the statement carries ``execution_options(include_deleted=True)``.
* **Tenancy scope**: for a session bound with ``bind_tenancy``, every ORM ``SELECT`` sees
  only rows of the bound tenant whose ``project_id`` is ``NULL``, the bound project or one
  of the allowed projects (T-0010, TD-053).
  Out-of-scope rows do not exist for the caller: lists omit them, lookups by id 404.
* **Write guard**: a bound session refuses to flush (insert, update or delete) a row
  outside its exact scope with ``OutOfScopeError``: another tenant, another project, or a
  tenant-level row from a project session. The API answers 404 (never 403, so the
  existence of another project's data is not revealed). Allowed projects are read-only.
* **Asset-Site scope** (T-0010, diff §4, PER-07): for a session whose context carries
  ``site_node_ids``, rows of ``SiteScopedMixin`` models are visible and writable only when
  their ``site_node_id`` is one of those sites. This filter is ORM-only (no RLS policy).
* **Membership-based tables** (T-0014, TD-053): models whose table declares an ``RlsPolicy``
  (``rls.rls_table_info``) get the same rule as their PostgreSQL policy: a ``membership``
  row (project row, membership) is visible when its described project is in
  ``TenancyContext.described_project_ids``, it is the caller's own row, or the session is
  ``is_system_admin``; an ``admin_grant`` row when it is the caller's own or the session is
  ``is_system_admin``. Updates and deletes need that visibility too; admin grants are
  written by ``is_system_admin`` sessions only.

An unbound session is not filtered here. On PostgreSQL RLS then shows it nothing.
ORM bulk ``update()``/``delete()`` statements skip both the loader criteria and the flush
guard; on PostgreSQL RLS still scopes them, on SQLite nothing does. Use them only with an
explicit tenant/project ``WHERE``.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy import ColumnElement, and_, event, false, or_
from sqlalchemy.orm import (
    Mapped,
    ORMExecuteState,
    Session,
    UOWTransaction,
    mapped_column,
    with_loader_criteria,
)

from app.core.db import GUID, Base, BaseRecordMixin
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import RlsPolicy, session_tenancy, table_policy

INCLUDE_DELETED: Final = "include_deleted"


class OutOfScopeError(LookupError):
    """A record outside the caller's tenant/project. The API maps it to 404."""


class SiteScopedMixin:
    """Opt-in Asset-Site scoping: the record belongs to one physical site (asset node).

    A model that a Site Inspector (PER-07) must only see for their own site mixes this in
    next to ``BaseRecordMixin``. ``NULL`` means "no site yet" and is hidden from a
    site-scoped session (fail closed).
    """

    site_node_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)


def _site_allows(context: TenancyContext, record: object) -> bool:
    if context.site_node_ids is None or not isinstance(record, SiteScopedMixin):
        return True
    return record.site_node_id in context.site_node_ids


def _policy_of(cls: type[Any]) -> RlsPolicy | None:
    table = getattr(cls, "__table__", None)
    return table_policy(table) if table is not None else None


def _policy_classes() -> list[tuple[type[Any], RlsPolicy]]:
    """Mapped classes whose table declares a non-standard RLS policy (T-0014)."""
    found = []
    for mapper in Base.registry.mappers:
        policy = _policy_of(mapper.class_)
        if policy is not None:
            found.append((mapper.class_, policy))
    return found


def _policy_allows(context: TenancyContext, record: object) -> bool:
    """The membership / admin-grant visibility rule for one loaded record (mirrors RLS)."""
    policy = _policy_of(type(record))
    if policy is None or context.is_system_admin:
        return True
    owner = policy.owner_column
    if (
        owner is not None
        and context.user_id is not None
        and getattr(record, owner) == context.user_id
    ):
        return True
    if policy.kind == "admin_grant" or policy.project_column is None:
        return False
    return getattr(record, policy.project_column) in context.described_project_ids


def _policy_criteria(
    cls: type[Any], policy: RlsPolicy, context: TenancyContext
) -> ColumnElement[bool]:
    clauses: list[ColumnElement[bool]] = []
    if policy.owner_column is not None and context.user_id is not None:
        clauses.append(getattr(cls, policy.owner_column) == context.user_id)
    if policy.kind == "membership" and policy.project_column is not None:
        described = sorted(context.described_project_ids)
        if described:
            clauses.append(getattr(cls, policy.project_column).in_(described))
    return or_(*clauses) if clauses else false()


def is_visible(session: Session, record: BaseRecordMixin, *, include_deleted: bool = False) -> bool:
    """Whether ``record`` is readable by ``session``: same rule as the read filters."""
    if record.deleted_at is not None and not include_deleted:
        return False
    context = session_tenancy(session)
    if context is None:
        return True
    return (
        record.tenant_id == context.tenant_id
        and (record.project_id is None or record.project_id in context.readable_project_ids)
        and _site_allows(context, record)
        and _policy_allows(context, record)
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
        tenant_id, readable = context.tenant_id, sorted(context.readable_project_ids)
        if not readable:
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
                        or_(cls.project_id.is_(None), cls.project_id.in_(readable)),
                    ),
                    include_aliases=True,
                )
            )
        if context.site_node_ids is not None:
            sites = sorted(context.site_node_ids)
            options.append(
                with_loader_criteria(
                    SiteScopedMixin,
                    lambda cls: cls.site_node_id.in_(sites),
                    include_aliases=True,
                )
            )
        if not context.is_system_admin:
            options.extend(
                with_loader_criteria(
                    cls, _policy_criteria(cls, policy, context), include_aliases=True
                )
                for cls, policy in _policy_classes()
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
    # Allowed projects are read-only; a site-scoped session writes only its sites' rows.
    for obj in (*session.new, *session.dirty, *session.deleted):
        if not isinstance(obj, BaseRecordMixin):
            continue
        if obj.tenant_id is not None and obj.tenant_id != context.tenant_id:
            raise OutOfScopeError(f"{type(obj).__name__} not found")
        if obj.project_id != context.project_id or not _site_allows(context, obj):
            raise OutOfScopeError(f"{type(obj).__name__} not found")
        # Membership-based tables (T-0014): admin grants are written by system admins only;
        # other rows are updated or deleted only while the session can see them.
        policy = _policy_of(type(obj))
        if policy is None:
            continue
        if policy.kind == "admin_grant" and not context.is_system_admin:
            raise OutOfScopeError(f"{type(obj).__name__} not found")
        if obj not in session.new and not _policy_allows(context, obj):
            raise OutOfScopeError(f"{type(obj).__name__} not found")
