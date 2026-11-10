"""The ``auth_user`` table (T-0008).

A user is a tenant-level row (``project_id IS NULL``) of one tenant, under the same
project-scoped RLS as every table: a session bound to the tenant (no project) reads and
writes it; another tenant's users do not exist for it. Project access (membership,
personas) is T-0010's.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, BaseRecordMixin

TABLE: Final = "auth_user"


def normalize_email(email: str) -> str:
    """Emails are matched case-insensitively: stored and looked up lower-cased and trimmed."""
    return email.strip().lower()


class User(Base, BaseRecordMixin):
    """A person who signs in to one tenant.

    ``password_hash`` is ``NULL`` for users without a local password (e.g. SSO-only users
    later, TD-043). ``password_changed_at`` invalidates every token issued before it.
    """

    __tablename__ = TABLE
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_auth_user_tenant_email"),
        CheckConstraint("project_id IS NULL", name="tenant_level"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
