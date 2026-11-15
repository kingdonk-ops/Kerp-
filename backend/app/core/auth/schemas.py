"""Request and response bodies of the auth endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from app.core.auth.field import MAX_OFFLINE_FAILURES, PIN_LENGTH, PIN_PATTERN, is_weak_pin


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    # Bounded so a huge body cannot make scrypt hash megabytes.
    password: str = Field(min_length=1, max_length=1024)
    # No tenant field (diff §4.1): the deployment's hidden tenant; ``tenant_id`` is a 422.


class MagicLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)


def _strong_pin(pin: str) -> str:
    if is_weak_pin(pin):
        raise ValueError("PIN is too easy to guess")
    return pin


Pin = Annotated[
    str,
    Field(pattern=PIN_PATTERN, description=f"{PIN_LENGTH}-digit PIN of this device"),
]
# A PIN being set (enrolment, change): also refused when too easy to guess.
NewPin = Annotated[Pin, AfterValidator(_strong_pin)]


class MagicLinkRedeemRequest(BaseModel):
    """Spend a magic link: a new session (on an enrolled device: bound to it, PIN kept)."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=256)


REFRESH_TOKEN_MAX_LENGTH = 4096

BodyRefreshToken = Annotated[
    str | None,
    Field(
        min_length=1,
        max_length=REFRESH_TOKEN_MAX_LENGTH,
        description=(
            "The refresh token, for the native app only (its `Origin` is on "
            "`KERP_NATIVE_APP_ORIGINS`); ignored on every other request, which uses the cookie"
        ),
    ),
]


class PinUnlockRequest(BaseModel):
    """The PIN. The device's refresh token comes from the refresh cookie (the native app:
    ``refresh_token`` here): both are required, a PIN alone is never a credential."""

    model_config = ConfigDict(extra="forbid")

    pin: Pin
    refresh_token: BodyRefreshToken = None
    failed_offline: int = Field(
        default=0,
        ge=0,
        le=MAX_OFFLINE_FAILURES,
        description=(
            "Wrong PINs entered on the device while offline since its last successful unlock, "
            "not yet reported: counted before this attempt with the same escalation. Applied "
            "only when the attempt runs (not on 429): keep the count until another answer"
        ),
    )


class RefreshRequest(BaseModel):
    """Optional body of ``/auth/refresh``: only the native app sends its refresh token here."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: BodyRefreshToken = None


class TokenResponse(BaseModel):
    """The access token. The refresh token is the ``httpOnly`` refresh cookie (diff §4.2,
    TD-203); only a request from an allow-listed native-app ``Origin`` gets it in the body
    (``refresh_token``, T-0043) instead."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")
    refresh_expires_in: int = Field(
        description="Seconds until the login session ends (sign-in + 12 h); then sign in again"
    )
    session_expires_at: datetime = Field(
        description=(
            "When the login session ends (sign-in + 12 h, the refresh token's end): the same "
            "on every rotation and PIN unlock. A field client starts its offline grace here"
        )
    )
    refresh_token: str | None = Field(
        default=None,
        description=(
            "Native app only (`Origin` on `KERP_NATIVE_APP_ORIGINS`): the refresh token, kept "
            "in the device keystore. Absent for every other caller (cookie transport)"
        ),
    )


class MeResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    display_name: str
    is_internal: bool = Field(
        description=(
            "Internal staff (read-only, derived at sign-in from the company Entra link or "
            "the deployment's internal e-mail domains); grants no access by itself"
        )
    )


class JsonWebKey(BaseModel):
    """A public JWK (RFC 7517): ``OKP``/Ed25519 has ``crv`` + ``x``, ``RSA`` has ``n`` + ``e``."""

    kty: Literal["OKP", "RSA"]
    kid: str
    use: Literal["sig"]
    alg: Literal["EdDSA", "RS256"]
    crv: str | None = None
    x: str | None = None
    n: str | None = None
    e: str | None = None


class JsonWebKeySet(BaseModel):
    keys: list[JsonWebKey]


# --- device registry (T-0043, diff §4.3) ---------------------------------------------------

Platform = Literal["android", "ios", "web"]
VersionText = Annotated[str, Field(max_length=64, pattern=r"^[\x20-\x7e]*$")]


class DeviceEnrolRequest(BaseModel):
    """A field client registers itself after sign-in (every sign-in; idempotent)."""

    model_config = ConfigDict(extra="forbid")

    device_id: uuid.UUID = Field(description="Generated by the client once per installation")
    platform: Platform = Field(description="Shown to admins; never decides the token transport")
    os_version: VersionText = ""
    app_version: VersionText = ""
    pin: NewPin | None = Field(
        default=None,
        description=(
            "The device's field PIN, set once (the device keeps it across sign-ins). `409` "
            "when the device already has one: change it with `PUT /devices/{device_id}/pin`"
        ),
    )


class DevicePinRequest(BaseModel):
    """A new PIN for the device (from a signed-in session of that device)."""

    model_config = ConfigDict(extra="forbid")

    pin: NewPin


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: uuid.UUID
    user_id: uuid.UUID
    platform: Platform
    os_version: str
    app_version: str
    status: Literal["active", "revoked"]
    enrolled_at: datetime
    last_seen_at: datetime
    last_sync_at: datetime | None = Field(
        description="Last successful sync: work changed on the device after it is lost on revoke"
    )
    revoked_at: datetime | None
    revoked_by: uuid.UUID | None
    wipe_confirmed_at: datetime | None
    pin_set_at: datetime | None = Field(
        default=None, description="When the device's field PIN was last set (null: none)"
    )

    @field_validator(
        "enrolled_at",
        "last_seen_at",
        "last_sync_at",
        "revoked_at",
        "wipe_confirmed_at",
        "pin_set_at",
    )
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        # SQLite hands TIMESTAMPTZ back naive; every value was stored in UTC.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class DeviceEnrolled(DeviceRead):
    wipe_secret: str | None = Field(
        default=None,
        description=(
            "The device's wipe secret: only in the response of the enrolment that creates the "
            "device (`201`), never again (`null` on re-enrolment). Keep it in the keystore-backed "
            "store of this account and present it with `wipe-confirmed`"
        ),
    )


class WipeConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wipe_secret: str | None = Field(
        default=None,
        description=(
            "The secret enrolment returned. Required: without it, or with a wrong one, the "
            "answer is the uniform refusal"
        ),
    )


class DeviceRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_cross_project_wipe: bool = Field(
        default=False,
        description=(
            "Required (else 409) when a PER-09 revokes a device whose user also belongs to "
            "other projects: the wipe also deletes their unsynced work of those projects"
        ),
    )


class DeviceRevoked(DeviceRead):
    role: Literal["self", "system_admin", "per_09"] = Field(
        description="The authority the revoke used (audited)"
    )
    cross_project_warning: bool = Field(
        description="The device's user belongs to projects beyond the revoking PER-09's"
    )
