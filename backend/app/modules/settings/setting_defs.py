"""The settings module's own registry entries: the seven T-0044 settings (diff §4.4, §4.7).

Defaults are ``SHIPPED_DEFAULTS`` and the bounds are the ones ``schemas.py`` checks on
``PATCH /deployment``. Other modules declare their entries in their own ``setting_defs.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from pydantic import TypeAdapter, ValidationError

from app.core.locale import Currency, Locale, TimeZone
from app.modules.settings.models import (
    CLOCK_SKEW_S,
    GRACE_HOURS_MIN,
    HOURS_PER_DAY,
    OFFLINE_WINDOW_DAYS,
    SHIPPED_DEFAULTS,
    STATUSES,
)
from app.modules.settings.registry import SettingDef, SettingRule, add_rule, register
from app.modules.settings.schemas import grace_error

OFFLINE: Final = "offline"
PROJECT_DEFAULTS: Final = "project_defaults"


def _checked(annotated: object) -> Callable[[str], str]:
    """The validator of an ``app.core.locale`` type as a plain ``str -> str`` check."""
    adapter: TypeAdapter[str] = TypeAdapter(annotated)

    def check(value: str) -> str:
        try:
            return adapter.validate_python(value)
        except ValidationError as exc:
            raise ValueError(exc.errors()[0]["msg"]) from exc

    return check


def _grace_within_window(values: Mapping[str, Any]) -> str | None:
    window: int = values["offline_window_days"]
    return grace_error(window) if values["offline_grace_hours"] > HOURS_PER_DAY * window else None


DEPLOYMENT_ENTRIES: Final = (
    SettingDef(
        key="offline_window_days",
        category=OFFLINE,
        type="int",
        default=SHIPPED_DEFAULTS.offline_window_days,
        minimum=OFFLINE_WINDOW_DAYS[0],
        maximum=OFFLINE_WINDOW_DAYS[1],
        client_visible=True,
        description="Days an offline device may work",
    ),
    SettingDef(
        key="offline_grace_hours",
        category=OFFLINE,
        type="int",
        default=SHIPPED_DEFAULTS.offline_grace_hours,
        minimum=GRACE_HOURS_MIN,
        maximum=HOURS_PER_DAY * OFFLINE_WINDOW_DAYS[1],
        client_visible=True,
        description="Hours after the server session ended that an offline device may keep "
        "unlocking with the PIN and queueing changes (at most 24 x offline_window_days)",
    ),
    SettingDef(
        key="max_checkout_clock_skew_s",
        category=OFFLINE,
        type="int",
        default=SHIPPED_DEFAULTS.max_checkout_clock_skew_s,
        minimum=CLOCK_SKEW_S[0],
        maximum=CLOCK_SKEW_S[1],
        client_visible=True,
        description="Largest device clock skew in seconds allowed when taking work offline",
    ),
    SettingDef(
        key="default_status",
        category=PROJECT_DEFAULTS,
        type="enum",
        default=SHIPPED_DEFAULTS.default_status,
        choices=STATUSES,
        description="Status of a new project",
    ),
    SettingDef(
        key="default_site_timezone",
        category=PROJECT_DEFAULTS,
        type="string",
        default=SHIPPED_DEFAULTS.default_site_timezone,
        max_length=64,
        check=_checked(TimeZone),
        description="IANA site timezone of a new project",
    ),
    SettingDef(
        key="default_currency_code",
        category=PROJECT_DEFAULTS,
        type="string",
        default=SHIPPED_DEFAULTS.default_currency_code,
        max_length=3,
        check=_checked(Currency),
        description="ISO 4217 currency of a new project",
    ),
    SettingDef(
        key="default_locale",
        category=PROJECT_DEFAULTS,
        type="string",
        default=SHIPPED_DEFAULTS.default_locale,
        max_length=35,
        check=_checked(Locale),
        description="BCP 47 locale of a new project",
    ),
)

register(*DEPLOYMENT_ENTRIES)
add_rule(SettingRule(("offline_window_days", "offline_grace_hours"), _grace_within_window))
