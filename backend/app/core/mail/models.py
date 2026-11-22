"""The send log and the suppression list (T-0058, diff §4.6; migration ``0024``), the send
log extended into the delivery ledger (T-0226, diff §4.7, ADR 0020; migration ``0039``).

Both use ``BaseRecordMixin`` and the standard tenant RLS policies (``project_id`` is the
sending session's project, ``NULL`` for tenant-level sends: the auth e-mails). Neither ever
holds an address in clear, a link, a token or a message body: recipients are kept as the
SHA-256 of the normalised address plus a masked display form (``addresses.py``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, BaseRecordMixin

SEND_TABLE: Final = "mail_send_log"
SUPPRESSION_TABLE: Final = "mail_suppression"
SEND_STATUSES: Final = (
    "queued",
    "sent",
    "delivered",
    "bounced",
    "complained",
    "failed",
    "suppressed",
)
SUPPRESSION_REASONS: Final = ("bounce", "complaint")
HASH_LENGTH: Final = 64
MASKED_LENGTH: Final = 260


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class MailSend(Base, BaseRecordMixin):
    """One e-mail: what was sent to whom (hashed + masked), and what became of it."""

    __tablename__ = SEND_TABLE
    __table_args__ = (
        CheckConstraint(_in("status", SEND_STATUSES), name="status"),
        Index(f"ix_{SEND_TABLE}_tenant_created", "tenant_id", "created_at"),
    )

    template_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    recipient_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False, index=True)
    recipient_masked: Mapped[str] = mapped_column(String(MASKED_LENGTH), nullable=False)
    locale: Mapped[str | None] = mapped_column(String(35), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # A short provider code (``ses:MessageRejected``), never message content.
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Last delivery / bounce / complaint / suppression / failure.
    status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Delivery ledger (T-0226): the recipient's user id when the sender knows one (never the
    # address), and the broadcast the e-mail belongs to (T-0228). Plain ids, no foreign keys.
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    broadcast_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)


class MailSuppression(Base, BaseRecordMixin):
    """An address SES reported as a hard bounce or a complaint: nothing is sent to it.

    At most one live row per tenant and address; a System admin removes it by soft-deleting
    it (audited)."""

    __tablename__ = SUPPRESSION_TABLE
    __table_args__ = (
        CheckConstraint(_in("reason", SUPPRESSION_REASONS), name="reason"),
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        Index(
            "uq_mail_suppression_live",
            "tenant_id",
            "recipient_hash",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    recipient_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    recipient_masked: Mapped[str] = mapped_column(String(MASKED_LENGTH), nullable=False)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    # SES detail: bounce type / sub-type or complaint feedback type (``Permanent/General``).
    detail: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
