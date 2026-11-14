"""The ``auth_user`` (T-0008) and ``auth_project_membership`` (T-0010) tables.

A user is a tenant-level row (``project_id IS NULL``) of one tenant, under the same
project-scoped RLS as every table: a session bound to the tenant (no project) reads and
writes it; another tenant's users do not exist for it. Project access (membership,
personas) is ``ProjectMembership`` below.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, BaseRecordMixin

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


MEMBERSHIP_TABLE: Final = "auth_project_membership"
# PER-01 ... PER-16 (edge spec §5.2; PER-16 Project Viewer since migration 0012, T-0076).
# The capability matrix lives in ``access.py``.
PERSONA_CODES: Final = tuple(f"PER-{n:02d}" for n in range(1, 17))
SITE_INSPECTOR: Final = "PER-07"


class ProjectMembership(Base, BaseRecordMixin):
    """One persona of one user on one project (T-0010): the membership store.

    A tenant-level row (``project_id IS NULL``): the project it grants is
    ``member_project_id``, so the tenant-level session that resolves a request's scope can
    read the user's memberships across projects (T-0014 tightens who else may, TD-053).
    A Site Inspector (PER-07) membership names its site (Asset-Site scoping). Revoking a
    membership soft-deletes it.
    """

    __tablename__ = MEMBERSHIP_TABLE
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "member_project_id",
            "persona",
            "site_node_id",
            name="uq_auth_project_membership_grant",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        CheckConstraint(
            "persona IN (" + ", ".join(f"'{code}'" for code in PERSONA_CODES) + ")",
            name="persona",
        ),
        CheckConstraint(
            f"persona <> '{SITE_INSPECTOR}' OR site_node_id IS NOT NULL", name="inspector_site"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey(f"{TABLE}.id"), nullable=False, index=True
    )
    member_project_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    persona: Mapped[str] = mapped_column(String(6), nullable=False)
    site_node_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
