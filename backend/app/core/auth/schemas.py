"""Request and response bodies of the auth endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.core.auth.field import PIN_LENGTH, PIN_PATTERN, is_weak_pin


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
    Field(pattern=PIN_PATTERN, description=f"{PIN_LENGTH}-digit PIN of this device's session"),
]


class MagicLinkRedeemRequest(BaseModel):
    """Spend a magic link and set the PIN that will unlock the new session on this device."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=256)
    pin: Annotated[Pin, AfterValidator(_strong_pin)]


class PinUnlockRequest(BaseModel):
    """The PIN. The device's refresh token comes from the refresh cookie: both are
    required, a PIN alone is never a credential."""

    model_config = ConfigDict(extra="forbid")

    pin: Pin


class TokenResponse(BaseModel):
    """The access token. The refresh token is never in a body: it is the ``httpOnly``
    refresh cookie (diff §4.2, TD-203)."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")
    refresh_expires_in: int = Field(
        description="Seconds until the login session ends (sign-in + 12 h); then sign in again"
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
