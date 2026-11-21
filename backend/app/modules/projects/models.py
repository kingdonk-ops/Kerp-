"""The project record ``oe_projects_project`` (T-0012, master spec §1.2, §4.1).

A project is a tenant-level row (``project_id IS NULL``); its own id is the project id that
every project-scoped row of the other modules carries. It is under the membership-based RLS
policy (migration ``0015``, T-0014, TD-053): visible to sessions bound to it, allowed to read
it or holding a membership in it, and to ``system_admin`` sessions.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, BaseRecordMixin
from app.core.geo import GeoJSONGeometry
from app.core.tenancy.rls import RlsPolicy, rls_table_info

TABLE: Final = "oe_projects_project"
STATUSES: Final = ("ACTIVE", "ON_HOLD", "CLOSED")
# Fallback literals only: ``create_project`` always passes the four values explicitly, taken
# from the deployment settings (T-0044). They equal the settings' shipped defaults (tested).
# Status and site timezone are also the DB-side defaults of spec §4.1 (migration 0033,
# T-0063); currency and locale have none (TD-185).
DEFAULT_STATUS: Final = "ACTIVE"
DEFAULT_TIMEZONE: Final = "Australia/Perth"  # master spec §4.1
DEFAULT_CURRENCY: Final = "AUD"
DEFAULT_LOCALE: Final = "en-AU"
MAX_LEGAL_HOLD_REASON: Final = 500
# While on, a hold has its reason and the time it was set (migration 0038, T-0223). The
# actor may be ``None`` (provisioning), like ``created_by``.
LEGAL_HOLD_CHECK: Final = (
    "NOT legal_hold OR (legal_hold_reason IS NOT NULL AND legal_hold_set_at IS NOT NULL)"
)


class Project(Base, BaseRecordMixin):
    """One project of a tenant: identity, site timezone, site bounds, display settings.

    * ``site_timezone``: IANA zone; clients show every UTC timestamp in it (spec §1.2).
    * ``spatial_bounds``: the site polygon, PostGIS ``geometry(Polygon, 4326)`` on
      PostgreSQL (``app.core.geo``); a GeoJSON dict in Python.
    * ``currency_code`` (ISO 4217) / ``locale`` (BCP 47): presentation of the read-only
      money mirror and of numbers and dates. KERP never authors money.
    * ``visible_in_lobby``: listed in the lobby for internal users (T-0040, diff §4.1); set
      by the project's PER-09 holders or a System admin only. Default off.
    * ``legal_hold`` (T-0223, diff §4.7): set and cleared by a System admin only, with a
      reason; while on, the project cannot be soft-deleted and its archives cannot be deleted
      (``service.is_on_legal_hold``). ``legal_hold_reason`` / ``_set_by`` / ``_set_at``
      describe the active hold and are cleared with it; the history is in the audit ledger.
    """

    __tablename__ = TABLE
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_oe_projects_project_tenant_code"),
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        CheckConstraint("status IN (" + ", ".join(f"'{s}'" for s in STATUSES) + ")", name="status"),
        CheckConstraint(LEGAL_HOLD_CHECK, name="legal_hold_reason"),  # migration 0038
        rls_table_info(RlsPolicy("membership", project_column="id")),  # T-0014, migration 0015
    )

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DEFAULT_STATUS, server_default=DEFAULT_STATUS
    )  # server default: migration 0033
    site_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DEFAULT_TIMEZONE, server_default=DEFAULT_TIMEZONE
    )  # server default: migration 0033
    spatial_bounds: Mapped[dict[str, Any] | None] = mapped_column(
        GeoJSONGeometry("Polygon"), nullable=True
    )
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default=DEFAULT_CURRENCY)
    locale: Mapped[str] = mapped_column(String(35), nullable=False, default=DEFAULT_LOCALE)
    visible_in_lobby: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )  # T-0040, migration 0021
    legal_hold: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )  # T-0223, migration 0038
    legal_hold_reason: Mapped[str | None] = mapped_column(
        String(MAX_LEGAL_HOLD_REASON), nullable=True
    )
    legal_hold_set_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    legal_hold_set_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
