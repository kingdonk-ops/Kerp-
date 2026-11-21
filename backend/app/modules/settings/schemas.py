"""Request and response shapes of ``/api/v1/settings`` (T-0044, T-0230; diff §4.4, §4.7).

Integers are strict (a JSON integer, never a boolean, float or numeric string). The project
defaults use the same validation as a project's own settings (``app.core.locale``).
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from app.core.locale import Currency, Locale, TimeZone
from app.modules.settings.models import (
    CLOCK_SKEW_S,
    GRACE_HOURS_MIN,
    HOURS_PER_DAY,
    OFFLINE_WINDOW_DAYS,
)

Status = Literal["ACTIVE", "ON_HOLD", "CLOSED"]
WindowDays = Annotated[
    int, Field(strict=True, ge=OFFLINE_WINDOW_DAYS[0], le=OFFLINE_WINDOW_DAYS[1])
]
ClockSkew = Annotated[int, Field(strict=True, ge=CLOCK_SKEW_S[0], le=CLOCK_SKEW_S[1])]
# The upper bound depends on the window (24 x offline_window_days); this is its widest value.
GraceHours = Annotated[
    int, Field(strict=True, ge=GRACE_HOURS_MIN, le=HOURS_PER_DAY * OFFLINE_WINDOW_DAYS[1])
]


class DeploymentSettingsRead(BaseModel):
    """The effective deployment settings (the shipped defaults until a System admin edits)."""

    model_config = ConfigDict(from_attributes=True)

    offline_window_days: int = Field(description="Days an offline device may work, 1-14")
    offline_grace_hours: int = Field(
        description="Hours after the server session ended that an offline device may keep "
        "unlocking with the PIN and queueing changes (1 to 24 x offline_window_days)"
    )
    max_checkout_clock_skew_s: int = Field(
        description="Largest device clock skew in seconds allowed when taking work offline"
    )
    default_status: Status = Field(description="Status of a new project")
    default_site_timezone: str = Field(description="IANA site timezone of a new project")
    default_currency_code: str = Field(description="ISO 4217 currency of a new project")
    default_locale: str = Field(description="BCP 47 locale of a new project")


class DeploymentSettingsUpdate(BaseModel):
    """``PATCH``: only the given fields change; none may be ``null``; no unknown fields."""

    model_config = ConfigDict(extra="forbid")

    offline_window_days: WindowDays | None = None
    offline_grace_hours: GraceHours | None = None
    max_checkout_clock_skew_s: ClockSkew | None = None
    default_status: Status | None = None
    default_site_timezone: TimeZone | None = None
    default_currency_code: Currency | None = None
    default_locale: Locale | None = None

    @model_validator(mode="after")
    def _given(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("give at least one field to change")
        for name in sorted(self.model_fields_set):
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        window, grace = self.offline_window_days, self.offline_grace_hours
        if window is not None and grace is not None and grace > HOURS_PER_DAY * window:
            raise ValueError(grace_error(window))
        return self


def grace_error(window: int) -> str:
    return (
        f"offline_grace_hours may be at most 24 x offline_window_days"
        f" ({HOURS_PER_DAY * window} for a {window}-day window)"
    )


# --- runtime settings registry (T-0230, diff §4.7) ------------------------------------------

# A registry value as JSON. Strict, so ``true`` stays a boolean and ``"7"`` a string; the
# entry's own type and bounds are checked by the registry.
SettingJson = StrictBool | StrictInt | StrictFloat | StrictStr | list[StrictInt]
SettingKind = Literal["int", "float", "bool", "string", "enum", "int_list"]


class SettingEntryRead(BaseModel):
    """One registry entry with the deployment's effective value."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    category: str
    type: SettingKind
    description: str
    value: SettingJson = Field(description="The effective value (the default while not set)")
    default: SettingJson
    is_default: bool = Field(description="No value is stored: the code default applies")
    minimum: int | float | None = Field(description="Lowest number (each item of an int_list)")
    maximum: int | float | None = Field(description="Highest number (each item of an int_list)")
    choices: list[str] = Field(description="The values of an enum entry")
    max_length: int | None = Field(description="Longest string, or most items of an int_list")
    client_visible: bool = Field(description="Returned to signed-in clients at bootstrap")
    cap: int | float | None = Field(
        description="The deployment-configuration limit the value can never exceed"
    )
    cap_source: str | None = Field(description="The environment variable that sets the cap")


class SettingValueUpdate(BaseModel):
    """``PATCH /registry/{key}``: the new value. Out of bounds is 422, never clamped."""

    model_config = ConfigDict(extra="forbid")

    value: SettingJson


class LockoutScheduleRead(BaseModel):
    free_attempts: int = Field(description="Wrong PINs before the first delay")
    base_delay_seconds: int
    max_delay_seconds: int
    max_attempts: int | None = Field(description="Wrong PINs before the device must sign in")


class DeploymentConfigRead(BaseModel):
    """Read-only deployment configuration: set by the environment, never a secret."""

    refresh_token_hours: int
    pin_idle_minutes: int
    pin_lockout: LockoutScheduleRead
    region: str = Field(description="AWS region of the deployment (SES, SNS)")
    storage_region: str = Field(description="AWS region of the object storage")
    mail_transport: Literal["ses", "log"]
    mail_from_address: str | None
    upload_scanner: Literal["stub", "clamd"]
    upload_max_bytes: int
    upload_url_seconds: int
    ai_provider: Literal["none"]
