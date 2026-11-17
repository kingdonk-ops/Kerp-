"""A fake Entra ID (OIDC) provider for the SSO tests (T-0054): local RSA keys, a local JWKS,
signed ID tokens, authorization codes bound to the PKCE challenge. No network.

All identities are synthetic.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.auth.entra import EntraConfig, EntraTransportError, pkce_challenge

ENTRA_TENANT = "0b4c6a52-6c1e-4e4b-9a55-5e0f0d7d0001"  # synthetic
OTHER_TENANT = "0b4c6a52-6c1e-4e4b-9a55-5e0f0d7d0002"  # synthetic
ENTRA_CLIENT = "6f1d5f0e-3c2b-4a19-8b7e-3a1c0e9d0001"  # synthetic
ISSUER = f"https://login.microsoftonline.com/{ENTRA_TENANT}/v2.0"


def new_rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    jwk: dict[str, Any] = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update(kid=kid, use="sig", alg="RS256")
    return jwk


@dataclass
class PendingCode:
    claims: dict[str, Any]
    challenge: str
    redirect_uri: str


@dataclass
class FakeEntra:
    """Plays login.microsoftonline.com for one tenant: ``authorize`` (the browser's trip),
    ``redeem_code`` (the token endpoint) and ``fetch_jwks`` (the keys endpoint)."""

    keys: dict[str, rsa.RSAPrivateKey] = field(default_factory=dict)
    published: list[str] = field(default_factory=list)
    signing_kid: str = "kid-1"
    codes: dict[str, PendingCode] = field(default_factory=dict)
    jwks_fetches: int = 0
    redeemed: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.keys:
            self.keys[self.signing_kid] = new_rsa_key()
            self.published.append(self.signing_kid)

    # --- the transport seam -----------------------------------------------------------

    def redeem_code(
        self, config: EntraConfig, *, code: str, verifier: str, redirect_uri: str
    ) -> str:
        self.redeemed.append({"code": code, "redirect_uri": redirect_uri})
        pending = self.codes.pop(code, None)
        if pending is None:
            raise EntraTransportError("Entra answered HTTP 400")  # invalid_grant
        if pkce_challenge(verifier) != pending.challenge:
            raise EntraTransportError("Entra answered HTTP 400")  # PKCE mismatch
        if redirect_uri != pending.redirect_uri:
            raise EntraTransportError("Entra answered HTTP 400")
        return self.sign(pending.claims)

    def fetch_jwks(self, config: EntraConfig) -> Mapping[str, Any]:
        self.jwks_fetches += 1
        return {"keys": [public_jwk(self.keys[kid], kid) for kid in self.published]}

    # --- the browser's trip to the authorize endpoint ------------------------------------

    def authorize(self, location: str, **overrides: Any) -> tuple[str, str]:
        """Sign the user in at the authorize URL ``location``: a code bound to its PKCE
        challenge and nonce. ``overrides`` change claims (``None`` removes one)."""
        parts = urlsplit(location)
        params = {k: v[0] for k, v in parse_qs(parts.query).items()}
        assert params["code_challenge_method"] == "S256"
        claims = self.claims(**{"nonce": params["nonce"], **overrides})
        code = f"code-{uuid.uuid4()}"
        self.codes[code] = PendingCode(claims, params["code_challenge"], params["redirect_uri"])
        return code, params["state"]

    def claims(self, **overrides: Any) -> dict[str, Any]:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": ENTRA_CLIENT,
            "tid": ENTRA_TENANT,
            "oid": "a7e3c1d0-0000-4000-8000-000000000001",
            "sub": "synthetic-subject",
            "email": "staff@company.example",
            "xms_edov": True,
            "acct": 0,
            "name": "Synthetic Staff",
            "preferred_username": "staff@company.example",
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
        }
        for name, value in overrides.items():
            if value is None:
                claims.pop(name, None)
            else:
                claims[name] = value
        return claims

    # --- keys -------------------------------------------------------------------------

    def sign(
        self,
        claims: Mapping[str, Any],
        *,
        kid: str | None = None,
        key: rsa.RSAPrivateKey | None = None,
    ) -> str:
        kid = kid or self.signing_kid
        signer = key or self.keys[kid]
        return jwt.encode(dict(claims), signer, algorithm="RS256", headers={"kid": kid})

    def rotate(self, kid: str, *, drop_old: bool = False) -> None:
        """Microsoft rolls its signing key: a new key is published and used."""
        self.keys[kid] = new_rsa_key()
        self.published = [kid] if drop_old else [*self.published, kid]
        self.signing_kid = kid
