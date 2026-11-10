"""The ``audit_ledger`` table and its ORM-level append-only guard (T-0006)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, NoReturn

from sqlalchemy import BigInteger, CheckConstraint, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, Session, UOWTransaction, mapped_column

from app.core.db import Base, BaseRecordMixin, JSONType

TABLE: str = "audit_ledger"


class AppendOnlyError(RuntimeError):
    """An attempt to change, delete or soft-delete an audit ledger entry."""


class AuditEntry(Base, BaseRecordMixin):
    """One entry of a hash chain. Written once by ``service.append``, never changed.

    ``(tenant_id, chain_key, seq)`` is unique: two writers can never both add entry ``n``
    to a chain, so a race fails instead of forking the chain.
    """

    __tablename__ = TABLE
    __table_args__ = (
        UniqueConstraint("tenant_id", "chain_key", "seq", name="uq_audit_ledger_chain_seq"),
        CheckConstraint("seq >= 1", name="seq_positive"),
    )

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
        if isinstance(obj, AuditEntry) and obj.deleted_at is not None:
            raise AppendOnlyError("audit ledger entries cannot be created deleted")
    for obj in session.dirty:
        if isinstance(obj, AuditEntry) and session.is_modified(obj, include_collections=False):
            raise AppendOnlyError("audit ledger entries cannot be changed")
    for obj in session.deleted:
        if isinstance(obj, AuditEntry):
            raise AppendOnlyError("audit ledger entries cannot be deleted")
