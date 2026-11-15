"""Tenant-level admin grants ``oe_admin_grant`` (T-0014, diff §4.1).

One row per user and grant: ``system_admin`` (runs the deployment) or ``create_projects``
(Project Admin). A grant is neither a persona nor a project membership and confers no
capability (no ``A``/``S``, TD-151). The row is tenant-level (no project) and has its own RLS
policy (migration ``0015``): a user reads their own rows (``app.current_user_id``); an
``app.is_system_admin`` session reads and writes all; nobody else writes. Revoking a grant
soft-deletes it; at most one live row per user and grant.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.auth.access import ADMIN_GRANTS
from app.core.db import GUID, Base, BaseRecordMixin
from app.core.tenancy.rls import RlsPolicy, rls_table_info

TABLE: Final = "oe_admin_grant"
LIVE_INDEX: Final = "uq_oe_admin_grant_live"
_LIVE: Final = "deleted_at IS NULL"


class AdminGrant(Base, BaseRecordMixin):
    __tablename__ = TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        CheckConstraint("kind IN (" + ", ".join(f"'{g}'" for g in ADMIN_GRANTS) + ")", name="kind"),
        Index(
            LIVE_INDEX,
            "tenant_id",
            "user_id",
            "kind",
            unique=True,
            postgresql_where=text(_LIVE),
            sqlite_where=text(_LIVE),
        ),
        rls_table_info(RlsPolicy("admin_grant", owner_column="user_id")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("auth_user.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
