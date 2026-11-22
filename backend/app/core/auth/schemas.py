"""Request and response bodies of the auth endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from app.core.auth.field import MAX_OFFLINE_FAILURES, PIN_LENGTH, PIN_PATTERN, is_weak_pin
from app.core.timestamps import aware_utc

INVITE_TOKEN_MAX_LENGTH: Final = 512

# An invite to accept right after this sign-in (T-0052, diff §4.1 Invites). Body only: never a
# URL path or query. Kept out of ``repr`` so a logged model never shows it.
InviteToken = Annotated[
    str | None,
    Field(
        min_length=1,
        max_length=INVITE_TOKEN_MAX_LENGTH,
        repr=False,
        description=(
            "Optional project invite token (from the `/invite#<token>` link): accepted right "
            "after a successful sign-in. The token response then carries `joined_project_id`, "
            "or `invite_error`; a bad token never fails the sign-in"
        ),
    ),
]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    # Bounded so a huge body cannot make scrypt hash megabytes.
    password: str = Field(min_length=1, max_length=1024)
    # No tenant field (diff §4.1): the deployment's hidden tenant; ``tenant_id`` is a 422.
    invite_token: InviteToken = None


class MagicLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    invite_token: InviteToken = None


class PasswordResetRequest(BaseModel):
    """Ask for a password-reset link (T-0077). No tenant field: the deployment's tenant."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)


class PasswordResetConfirm(BaseModel):
    """Spend a reset link: the token from the link fragment and the new password."""

    model_config = ConfigDict(extra="forbid")

    # Loose bounds on purpose: any wrong token gets the neutral 410, not a 422.
    token: str = Field(min_length=1, max_length=512)
    # Bounded so a huge body cannot make scrypt hash megabytes; the policy (minimum length,
    # at most 256 characters, blocklist) answers with a ``code``.
    new_password: str = Field(min_length=1, max_length=1024)


class PasswordResetDone(BaseModel):
    """The password was changed. Nobody is signed in: sign in with the new password."""

    status: Literal["password_changed"] = "password_changed"


class PasswordResetRefusal(BaseModel):
    """``410``: the link is unknown, used, expired or superseded (`RESET_LINK_INVALID`).
    ``422``: the password policy refused the new password (`PASSWORD_TOO_SHORT`,
    `PASSWORD_TOO_LONG`, `PASSWORD_TOO_COMMON`); the link is still usable."""

    detail: str
    code: Literal[
        "RESET_LINK_INVALID", "PASSWORD_TOO_SHORT", "PASSWORD_TOO_LONG", "PASSWORD_TOO_COMMON"
    ]
    min_length: int | None = Field(
        default=None, description="Policy refusals: the deployment's minimum password length"
    )
    max_length: int | None = Field(
        default=None, description="Policy refusals: the longest accepted password"
    )


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
    joined_project_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The project joined by the `invite_token` this sign-in carried (T-0052): open it. "
            "Absent without an invite"
        ),
    )
    invite_error: str | None = Field(
        default=None,
        description=(
            "Why the sign-in's `invite_token` was not accepted (the sign-in itself succeeded): "
            "`INVITE_WRONG_USER`, `INVITE_GONE` (expired, revoked or used), `INVITE_CONFLICT` "
            "or `INVITE_UNAVAILABLE`"
        ),
    )


ImpersonationRefusalCode = Literal[
    "NOT_IMPERSONATING",
    "IMPERSONATION_ACTION_FORBIDDEN",
    "CANNOT_IMPERSONATE_SYSTEM_ADMIN",
    "CANNOT_IMPERSONATE_SELF",
    "USER_DEACTIVATED",
]


class ImpersonationRefusal(BaseModel):
    """A refused impersonation request (T-0277, ADR 0015): ``403``
    `IMPERSONATION_ACTION_FORBIDDEN` (the caller is itself impersonating),
    `CANNOT_IMPERSONATE_SYSTEM_ADMIN`, `NOT_IMPERSONATING` (exit without an impersonation
    token); ``422`` `CANNOT_IMPERSONATE_SELF`, `USER_DEACTIVATED`."""

    detail: str
    code: ImpersonationRefusalCode


class ImpersonationStartRequest(BaseModel):
    """Why the System admin impersonates the user: kept in the audit ledger, never e-mailed."""

    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str, Field(min_length=1, max_length=500)]

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value.strip()


class ImpersonationStarted(BaseModel):
    """The impersonation token: send it as `Authorization: Bearer` while acting as the user.
    Keep it in memory only (never storage, never offline). There is no refresh token: at
    `expires_at`, or after `POST /api/v1/auth/impersonation/exit`, resume the own session."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    impersonation_session_id: uuid.UUID
    expires_in: int = Field(description="Seconds until the impersonation ends by itself")
    expires_at: datetime
    target_user_id: uuid.UUID
    target_display_name: str


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
        return None if value is None else aware_utc(value)


class DeviceEnrolled(DeviceRead):
    wipe_secret: str | None = Field(
        default=None,
        description=(
            "The device's wipe secret: only in the response of the enrolment that creates the "
            "device (`201`), never again (`null` on re-enrolment). Keep it in the keystore-backed "
            "store of this account and present it with `wipe-confirmed`"
        ),
    )


# An issued secret is 43 characters; anything longer is refused before any work (T-0212).
WIPE_SECRET_MAX_LENGTH: Final = 128


class WipeConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wipe_secret: str | None = Field(
        default=None,
        max_length=WIPE_SECRET_MAX_LENGTH,
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


# --- Entra ID SSO (T-0054, diff §4.5) -----------------------------------------------------

# RFC 7636: a verifier is 43-128 unreserved characters; its S256 challenge is 43 base64url.
PKCE_VERIFIER_PATTERN: Final = r"^[A-Za-z0-9._~-]{43,128}$"
PKCE_CHALLENGE_PATTERN: Final = r"^[A-Za-z0-9_-]{43}$"


class PasswordOption(BaseModel):
    available: Literal[True] = True


class SsoOption(BaseModel):
    """Entra ID SSO is offered: the promoted, default sign-in for company staff (diff §4.5
    *SSO promoted, password kept*). Password sign-in stays available for everyone."""

    provider: Literal["entra"] = "entra"
    preferred: bool = True
    start_url: str = Field(
        description=(
            "Open by top-level navigation (web) or in the system browser (Android, with "
            "`device_id` and `app_challenge`)"
        )
    )


class AuthConfigResponse(BaseModel):
    """What the sign-in screen offers. No secret, no tenant id."""

    password: PasswordOption = Field(default_factory=PasswordOption)
    sso: SsoOption | None = Field(
        default=None, description="Present when the deployment offers Entra ID SSO"
    )


class SsoExchangeRequest(BaseModel):
    """The native app redeems the single-use exchange code of its App Link (diff §4.5)."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=16, max_length=128)
    verifier: str = Field(
        pattern=PKCE_VERIFIER_PATTERN,
        description="The app-generated verifier whose S256 hash `/start` received",
    )
    device_id: uuid.UUID
