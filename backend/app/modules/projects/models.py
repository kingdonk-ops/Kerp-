"""The project record ``oe_projects_project`` (T-0012, master spec §1.2, §4.1).

A project is a tenant-level row (``project_id IS NULL``); its own id is the project id that
every project-scoped row of the other modules carries. It is under the standard RLS policy
(migration ``0008``); T-0014 replaces that with the membership-based policy (TD-053).
"""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy import CheckConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, BaseRecordMixin
from app.core.geo import GeoJSONGeometry

TABLE: Final = "oe_projects_project"
STATUSES: Final = ("ACTIVE", "ON_HOLD", "CLOSED")
DEFAULT_TIMEZONE: Final = "Australia/Perth"  # master spec §4.1
DEFAULT_CURRENCY: Final = "AUD"
DEFAULT_LOCALE: Final = "en-AU"


class Project(Base, BaseRecordMixin):
    """One project of a tenant: identity, site timezone, site bounds, display settings.

    * ``site_timezone``: IANA zone; clients show every UTC timestamp in it (spec §1.2).
    * ``spatial_bounds``: the site polygon, PostGIS ``geometry(Polygon, 4326)`` on
      PostgreSQL (``app.core.geo``); a GeoJSON dict in Python.
    * ``currency_code`` (ISO 4217) / ``locale`` (BCP 47): presentation of the read-only
      money mirror and of numbers and dates. KERP never authors money.
    """

    __tablename__ = TABLE
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_oe_projects_project_tenant_code"),
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        CheckConstraint("status IN (" + ", ".join(f"'{s}'" for s in STATUSES) + ")", name="status"),
    )

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    site_timezone: Mapped[str] = mapped_column(String(64), nullable=False, default=DEFAULT_TIMEZONE)
    spatial_bounds: Mapped[dict[str, Any] | None] = mapped_column(
        GeoJSONGeometry("Polygon"), nullable=True
    )
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default=DEFAULT_CURRENCY)
    locale: Mapped[str] = mapped_column(String(35), nullable=False, default=DEFAULT_LOCALE)
