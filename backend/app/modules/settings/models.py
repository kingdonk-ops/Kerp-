"""Settings tables: ``oe_settings_value`` (T-0230) and the legacy ``oe_settings_deployment``.

``oe_settings_value`` (diff §4.7): one live row per tenant and registry key, the value as
JSON; no row means the entry's code default. Tenant-level, standard RLS policy.

``oe_settings_deployment`` (T-0044, diff §4.4) is **read-only since T-0230**: migration
``0037`` copied its rows into ``oe_settings_value`` and nothing reads or writes it any more;
the table stays until an ADR drops it. What follows describes it as it was used.

At most one live row per tenant (the deployment's hidden tenant, diff §4.1); tenant-level
(``project_id IS NULL``), standard RLS policy (T-0007): every session of the tenant reads it,
only a tenant-level session writes it. Who may change it (a ``system_admin``) is the router's
check. Until a System admin first changes a value there is no row and the service answers
``SHIPPED_DEFAULTS``.

Region is **not** here: it is deployment configuration (environment), diff §4.4.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Final

from sqlalchemy import CheckConstraint, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, BaseRecordMixin, JSONType

TABLE: Final = "oe_settings_deployment"
LIVE_INDEX: Final = "uq_oe_settings_deployment_live"
VALUE_TABLE: Final = "oe_settings_value"
VALUE_LIVE_INDEX: Final = "uq_oe_settings_value_live"
KEY_LENGTH: Final = 64
_LIVE: Final = "deleted_at IS NULL"

# Project status values (master spec §4.1 amendment); equal to the projects module's (tested).
STATUSES: Final = ("ACTIVE", "ON_HOLD", "CLOSED")

# Bounds (diff §4.4; clock-skew and grace bounds are planner defaults, T-0044).
OFFLINE_WINDOW_DAYS: Final = (1, 14)
CLOCK_SKEW_S: Final = (1, 300)
GRACE_HOURS_MIN: Final = 1
HOURS_PER_DAY: Final = 24


@dataclass(frozen=True, slots=True)
class DeploymentSettingsValues:
    """The effective settings of a deployment (stored row or shipped defaults)."""

    offline_window_days: int
    offline_grace_hours: int
    max_checkout_clock_skew_s: int
    default_status: str
    default_site_timezone: str
    default_currency_code: str
    default_locale: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Diff §4.4: offline window 7 days, grace 24 h, clock skew 5 s; new projects ACTIVE,
# Australia/Perth, AUD, en-AU.
SHIPPED_DEFAULTS: Final = DeploymentSettingsValues(
    offline_window_days=7,
    offline_grace_hours=24,
    max_checkout_clock_skew_s=5,
    default_status="ACTIVE",
    default_site_timezone="Australia/Perth",
    default_currency_code="AUD",
    default_locale="en-AU",
)
FIELDS: Final = tuple(SHIPPED_DEFAULTS.as_dict())


def _between(column: str, low: int, high: int) -> str:
    return f"{column} BETWEEN {low} AND {high}"


class DeploymentSettings(Base, BaseRecordMixin):
    __tablename__ = TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        CheckConstraint(_between("offline_window_days", *OFFLINE_WINDOW_DAYS), name="window"),
        CheckConstraint(_between("max_checkout_clock_skew_s", *CLOCK_SKEW_S), name="clock_skew"),
        CheckConstraint(
            f"offline_grace_hours >= {GRACE_HOURS_MIN}"
            f" AND offline_grace_hours <= {HOURS_PER_DAY} * offline_window_days",
            name="grace",
        ),
        CheckConstraint(
            "default_status IN (" + ", ".join(f"'{s}'" for s in STATUSES) + ")",
            name="default_status",
        ),
        Index(
            LIVE_INDEX,
            "tenant_id",
            unique=True,
            postgresql_where=text(_LIVE),
            sqlite_where=text(_LIVE),
        ),
    )

    offline_window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    offline_grace_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    max_checkout_clock_skew_s: Mapped[int] = mapped_column(Integer, nullable=False)
    default_status: Mapped[str] = mapped_column(String(32), nullable=False)
    default_site_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    default_currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    default_locale: Mapped[str] = mapped_column(String(35), nullable=False)

    def values(self) -> DeploymentSettingsValues:
        return DeploymentSettingsValues(**{name: getattr(self, name) for name in FIELDS})


class SettingValue(Base, BaseRecordMixin):
    """A tenant's stored value of one registry entry (``app.modules.settings.registry``)."""

    __tablename__ = VALUE_TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        Index(
            VALUE_LIVE_INDEX,
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=text(_LIVE),
            sqlite_where=text(_LIVE),
        ),
    )

    key: Mapped[str] = mapped_column(String(KEY_LENGTH), nullable=False)
    value: Mapped[Any] = mapped_column(JSONType(), nullable=False)
