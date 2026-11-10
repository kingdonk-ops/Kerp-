"""Asymmetric signing keys and the JWKS document (T-0008, diff §4).

Replaces Inspection-ERP's shared HS256 secret (ADR 0001): tokens are signed with a private
key (Ed25519 for ``EdDSA``, RSA >= 2048 bits for ``RS256``) and anyone can verify them with
the public half published at ``/.well-known/jwks.json``. Each key's ``kid`` is its RFC 7638
JWK thumbprint, so it is stable for the key and needs no registry.

Rotation: configure the new private key and list the old public key in
``KERP_JWT_RETIRED_PUBLIC_KEYS``. Tokens signed by the old key keep verifying (and the old
key stays in the JWKS) until they have expired; then drop it.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Final, Literal, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from jwt.algorithms import OKPAlgorithm, RSAAlgorithm

from app.core.config import Settings

Algorithm = Literal["EdDSA", "RS256"]
PrivateKey = ed25519.Ed25519PrivateKey | rsa.RSAPrivateKey
PublicKey = ed25519.Ed25519PublicKey | rsa.RSAPublicKey

MIN_RSA_BITS: Final = 2048
UNSUPPORTED_KEY: Final = "unsupported key type: use an Ed25519 (EdDSA) or RSA (RS256) key"
_THUMBPRINT_MEMBERS: Final[dict[str, tuple[str, ...]]] = {
    "OKP": ("crv", "kty", "x"),
    "RSA": ("e", "kty", "n"),
}


class AuthConfigError(RuntimeError):
    """The auth key or revocation configuration is unusable."""


@dataclass(frozen=True, slots=True)
class VerificationKey:
    """A public key that verifies tokens, with its JWKS entry."""

    algorithm: Algorithm
    public_key: PublicKey
    kid: str = field(init=False)
    _jwk: dict[str, str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        jwk = _public_jwk(self.public_key)
        object.__setattr__(self, "kid", jwk_thumbprint(jwk))
        object.__setattr__(
            self, "_jwk", {**jwk, "kid": self.kid, "use": "sig", "alg": self.algorithm}
        )

    def jwk(self) -> dict[str, str]:
        return dict(self._jwk)


@dataclass(frozen=True, slots=True)
class SigningKey:
    """The private key that signs new tokens."""

    private_key: PrivateKey
    verification: VerificationKey

    @property
    def kid(self) -> str:
        return self.verification.kid

    @property
    def algorithm(self) -> Algorithm:
        return self.verification.algorithm


class KeySet:
    """The active signing key plus every key a token may still be verified with."""

    def __init__(self, signing: SigningKey, retired: tuple[VerificationKey, ...] = ()) -> None:
        self.signing = signing
        self._by_kid: dict[str, VerificationKey] = {signing.kid: signing.verification}
        for key in retired:
            self._by_kid.setdefault(key.kid, key)

    def get(self, kid: str) -> VerificationKey | None:
        return self._by_kid.get(kid)

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        """The public JWK Set (RFC 7517). Never contains private material."""
        return {"keys": [key.jwk() for key in self._by_kid.values()]}


def jwk_thumbprint(jwk: dict[str, str]) -> str:
    """RFC 7638 SHA-256 thumbprint, base64url without padding."""
    members = _THUMBPRINT_MEMBERS[jwk["kty"]]
    canonical = json.dumps({name: jwk[name] for name in members}, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _public_jwk(public_key: PublicKey) -> dict[str, str]:
    if isinstance(public_key, ed25519.Ed25519PublicKey):
        raw = OKPAlgorithm.to_jwk(public_key, as_dict=True)
    else:
        raw = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk = cast(dict[str, str], raw)
    members = _THUMBPRINT_MEMBERS[jwk["kty"]]
    return {name: jwk[name] for name in members}


def _algorithm_of(key: PrivateKey | PublicKey) -> Algorithm:
    if isinstance(key, ed25519.Ed25519PrivateKey | ed25519.Ed25519PublicKey):
        return "EdDSA"
    if key.key_size < MIN_RSA_BITS:
        raise AuthConfigError(f"RSA keys must be at least {MIN_RSA_BITS} bits")
    return "RS256"


def signing_key_from_private(private_key: object) -> SigningKey:
    if not isinstance(private_key, ed25519.Ed25519PrivateKey | rsa.RSAPrivateKey):
        raise AuthConfigError(UNSUPPORTED_KEY)
    algorithm = _algorithm_of(private_key)
    return SigningKey(private_key, VerificationKey(algorithm, private_key.public_key()))


def generate_signing_key(algorithm: Algorithm) -> SigningKey:
    """A fresh key pair (development/test, or bootstrapping a deployment's key)."""
    if algorithm == "EdDSA":
        return signing_key_from_private(ed25519.Ed25519PrivateKey.generate())
    return signing_key_from_private(rsa.generate_private_key(public_exponent=65537, key_size=3072))


def load_signing_key(pem: str | bytes) -> SigningKey:
    """Load an unencrypted PKCS#8 / traditional PEM private key."""
    data = pem.encode() if isinstance(pem, str) else pem
    try:
        private_key = serialization.load_pem_private_key(data, password=None)
    except (ValueError, TypeError) as exc:
        raise AuthConfigError("the JWT private key is not a readable PEM private key") from exc
    return signing_key_from_private(private_key)


def load_verification_key(pem: str | bytes) -> VerificationKey:
    """Load a PEM public key (a retired signing key)."""
    data = pem.encode() if isinstance(pem, str) else pem
    try:
        public_key = serialization.load_pem_public_key(data)
    except (ValueError, TypeError) as exc:
        raise AuthConfigError("a retired JWT public key is not a readable PEM public key") from exc
    if not isinstance(public_key, ed25519.Ed25519PublicKey | rsa.RSAPublicKey):
        raise AuthConfigError(UNSUPPORTED_KEY)
    return VerificationKey(_algorithm_of(public_key), public_key)


def private_key_pem(key: SigningKey) -> str:
    """PKCS#8 PEM of a signing key (for provisioning a generated key into a secret store)."""
    return key.private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def public_key_pem(key: VerificationKey) -> str:
    return key.public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


def keyset_from_settings(settings: Settings) -> tuple[KeySet, bool]:
    """Build the key set. Returns ``(keyset, ephemeral)``.

    Without a configured key a fresh one is generated (``ephemeral=True``), except in
    production, where that is a configuration error.
    """
    pem: str | None = None
    if settings.jwt_private_key is not None:
        pem = settings.jwt_private_key.get_secret_value()
    elif settings.jwt_private_key_file is not None:
        try:
            pem = settings.jwt_private_key_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise AuthConfigError("cannot read KERP_JWT_PRIVATE_KEY_FILE") from exc
    if pem is None:
        if settings.environment == "production":
            raise AuthConfigError(
                "KERP_JWT_PRIVATE_KEY or KERP_JWT_PRIVATE_KEY_FILE must be set in production"
            )
        signing = generate_signing_key(settings.jwt_algorithm)
        ephemeral = True
    else:
        signing = load_signing_key(pem)
        ephemeral = False
    if signing.algorithm != settings.jwt_algorithm:
        raise AuthConfigError(
            f"KERP_JWT_ALGORITHM is {settings.jwt_algorithm} but the key is for {signing.algorithm}"
        )
    retired = tuple(load_verification_key(p) for p in settings.jwt_retired_public_keys)
    return KeySet(signing, retired), ephemeral
