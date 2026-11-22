"""The in-app notification record ``oe_notifications_notification`` (T-0019) and the broadcast
e-mail records (T-0228).

One notification row per recipient, always project-scoped, under the standard project RLS
(migration ``0010``). The row *is* the in-app inbox entry; other channels (FCM push T-0144,
e-mail TD-084) deliver the same row elsewhere.

Text is never stored: ``kind`` is a message key and ``params`` its values, ``label`` a label
key, so the client renders them with ``t()`` in the reader's language. A ``pinned`` row
stays at the top of the list until the action named by ``pin_key`` clears it
(``service.clear_pins``); reading it does not.

Broadcast e-mail (T-0228, diff §4.7, migration ``0043``): ``oe_notifications_broadcast``,
``oe_notifications_broadcast_recipient`` and ``oe_notifications_broadcast_optout``, all
tenant-level under the standard tenant RLS. No address is ever stored, only user ids.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, BaseRecordMixin, JSONType

TABLE: Final = "oe_notifications_notification"
# Lowest first; the list orders by the index (highest first).
PRIORITIES: Final = ("low", "normal", "high", "critical")
PRIORITY_RANK: Final = {name: rank for rank, name in enumerate(PRIORITIES)}


class Notification(Base, BaseRecordMixin):
    """An in-app notification for one user in one project."""

    __tablename__ = TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NOT NULL", name="project_level"),
        CheckConstraint(
            "priority IN (" + ", ".join(f"'{p}'" for p in PRIORITIES) + ")", name="priority"
        ),
        CheckConstraint("NOT pinned OR pin_key IS NOT NULL", name="pin_key"),
        CheckConstraint("(subject_type IS NULL) = (subject_id IS NULL)", name="subject"),
    )

    recipient_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONType(), nullable=False, default=dict)
    label: Mapped[str | None] = mapped_column(String(40), nullable=True)
    priority: Mapped[str] = mapped_column(String(8), nullable=False, default="normal")
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pin_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    unpinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # What the notification is about (another module's record): a reference, never an FK.
    subject_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)


# --- broadcast e-mail (T-0228, diff §4.7 Broadcast e-mail) -----------------------------------

BROADCAST_TABLE: Final = "oe_notifications_broadcast"
RECIPIENT_TABLE: Final = "oe_notifications_broadcast_recipient"
OPTOUT_TABLE: Final = "oe_notifications_broadcast_optout"
BROADCAST_SCOPES: Final = ("tenant", "projects")
BROADCAST_STATUSES: Final = ("draft", "queued", "sending", "sent", "failed")
RECIPIENT_STATUSES: Final = ("pending", "queued", "skipped")
SKIP_REASONS: Final = ("opted_out", "inactive", "suppressed", "no_address")
TOKEN_HASH_LENGTH: Final = 64


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Broadcast(Base, BaseRecordMixin):
    """One broadcast e-mail: who sent it, to whom (scope + audience filter, never addresses),
    the plain-text subject and body, its dispatch status and counts.

    Tenant-level under the standard tenant RLS. Reads go through ``broadcasts``: a System
    admin reads every broadcast of the tenant, a PER-09 only those whose projects are all its
    own PER-09 projects. ``recipient_count``: the audience resolved when it was queued
    (opted-out, deactivated and suppressed users excluded); ``queued_count`` /
    ``skipped_count``: what the dispatch job did. Delivery counts come from the send log
    (``broadcast_id``, T-0226)."""

    __tablename__ = BROADCAST_TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        CheckConstraint(_in("scope", BROADCAST_SCOPES), name="scope"),
        CheckConstraint(_in("status", BROADCAST_STATUSES), name="status"),
        CheckConstraint(
            "recipient_count >= 0 AND queued_count >= 0 AND skipped_count >= 0", name="counts"
        ),
    )

    sender_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    # The targeted projects as UUID strings (empty for ``tenant``): references, never FKs.
    project_ids: Mapped[list[str]] = mapped_column(JSONType(), nullable=False, default=list)
    # ``{"category": "internal" | "external" | "client" | null, "personas": ["PER-01", ...]}``
    audience: Mapped[dict[str, Any]] = mapped_column(JSONType(), nullable=False, default=dict)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queued_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    job_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BroadcastRecipient(Base, BaseRecordMixin):
    """One user of a broadcast's resolved audience (a user id, never an address).

    ``pending`` until the dispatch job queues the e-mail (``queued``, ``send_id`` = the send-log
    row) or skips it (``skipped`` with ``skip_reason``). ``unsubscribe_hash``: the SHA-256 of
    the opaque one-click opt-out token mailed to this recipient (the token itself is never
    stored); ``unsubscribed_at``: when it was used."""

    __tablename__ = RECIPIENT_TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        CheckConstraint(_in("status", RECIPIENT_STATUSES), name="status"),
        CheckConstraint(
            f"skip_reason IS NULL OR {_in('skip_reason', SKIP_REASONS)}", name="skip_reason"
        ),
        Index(f"uq_{RECIPIENT_TABLE}_user", "broadcast_id", "user_id", unique=True),
        Index(f"uq_{RECIPIENT_TABLE}_token", "unsubscribe_hash", unique=True),
    )

    broadcast_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    skip_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    send_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    unsubscribe_hash: Mapped[str | None] = mapped_column(String(TOKEN_HASH_LENGTH), nullable=True)
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BroadcastOptOut(Base, BaseRecordMixin):
    """A user who opted out of broadcast e-mails (tenant-level, at most one live row per user).

    Applies to broadcasts only: transactional mail (sign-in, invites, resets, call-outs) never
    reads it. ``broadcast_id``: the broadcast whose link was used (a reference)."""

    __tablename__ = OPTOUT_TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        Index(
            f"uq_{OPTOUT_TABLE}_user",
            "tenant_id",
            "user_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    broadcast_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
