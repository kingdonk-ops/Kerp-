"""The ``audit_ledger`` table and its ORM-level append-only guard (T-0006)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final, NoReturn

from sqlalchemy import BigInteger, CheckConstraint, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, Session, UOWTransaction, mapped_column

from app.core.db import GUID, Base, BaseRecordMixin, JSONType

TABLE: Final = "audit_ledger"
# ``scope_id`` of a tenant-level chain (``project_id IS NULL``): the nil UUID.
TENANT_SCOPE: Final = uuid.UUID(int=0)


def scope_of(project_id: uuid.UUID | None) -> uuid.UUID:
    """The chain scope of a row: its project, or ``TENANT_SCOPE`` for tenant-level rows."""
    return TENANT_SCOPE if project_id is None else project_id


class AppendOnlyError(RuntimeError):
    """An attempt to change, delete or soft-delete an audit ledger entry."""


class AuditEntry(Base, BaseRecordMixin):
    """One entry of a hash chain. Written once by ``service.append``, never changed.

    A chain is identified by ``(tenant_id, scope_id, chain_key)``: each project, and the
    tenant level, has its own chains, so one scope can never claim or block another's key.
    ``scope_id`` is ``project_id`` made non-null (a plain unique constraint treats NULLs as
    distinct). ``(tenant_id, scope_id, chain_key, seq)`` is unique, so two writers can never
    both add entry ``n`` to a chain: a race fails instead of forking. The other CHECKs keep
    the mixin columns at their insert values, since rows never change.
    """

    __tablename__ = TABLE
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "scope_id", "chain_key", "seq", name="uq_audit_ledger_chain_seq"
        ),
        CheckConstraint("seq >= 1", name="seq_positive"),
        CheckConstraint(f"scope_id = COALESCE(project_id, '{TENANT_SCOPE}')", name="scope_id"),
        CheckConstraint("deleted_at IS NULL", name="not_deleted"),
        CheckConstraint("revision = 1", name="first_revision"),
        CheckConstraint("custom_fields = '{}'", name="no_custom_fields"),
    )

    scope_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    chain_key: Mapped[str] = mapped_column(String(200), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType(), nullable=False)

    def soft_delete(self, *, at: datetime | None = None) -> NoReturn:
        raise AppendOnlyError("audit ledger entries cannot be deleted")


@event.listens_for(Session, "before_flush")
def _refuse_changes(session: Session, flush_context: UOWTransaction, instances: object) -> None:
    """Mirror of the database guard, so SQLite and the ORM fail the same way."""
    for obj in session.new:
        if isinstance(obj, AuditEntry):
            if obj.deleted_at is not None:
                raise AppendOnlyError("audit ledger entries cannot be created deleted")
            if obj.scope_id is None:
                obj.scope_id = scope_of(obj.project_id)
    for obj in session.dirty:
        if isinstance(obj, AuditEntry) and session.is_modified(obj, include_collections=False):
            raise AppendOnlyError("audit ledger entries cannot be changed")
    for obj in session.deleted:
        if isinstance(obj, AuditEntry):
            raise AppendOnlyError("audit ledger entries cannot be deleted")
