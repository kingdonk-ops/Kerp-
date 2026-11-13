"""The in-app notification record ``oe_notifications_notification`` (T-0019).

One row per recipient, always project-scoped, under the standard project RLS (migration
``0010``). The row *is* the in-app inbox entry; other channels (FCM push T-0144, e-mail
TD-084) deliver the same row elsewhere.

Text is never stored: ``kind`` is a message key and ``params`` its values, ``label`` a label
key, so the client renders them with ``t()`` in the reader's language. A ``pinned`` row
stays at the top of the list until the action named by ``pin_key`` clears it
(``service.clear_pins``); reading it does not.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Boolean, CheckConstraint, DateTime, String
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
