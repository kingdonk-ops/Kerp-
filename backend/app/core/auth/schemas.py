"""Request and response bodies of the auth endpoints."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    # Bounded so a huge body cannot make scrypt hash megabytes.
    password: str = Field(min_length=1, max_length=1024)
    # Omitted: the deployment's default tenant (KERP_DEFAULT_TENANT_ID).
    tenant_id: uuid.UUID | None = None


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1, max_length=4096)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")
    refresh_expires_in: int = Field(description="Refresh token lifetime in seconds")


class MeResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    display_name: str


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
