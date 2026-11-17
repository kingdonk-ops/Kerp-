"""Entra ID SSO, identity only: the ``EntraOidcProvider`` (T-0054, diff §4.5, TD-043, TD-003).

The second ``AuthProvider``. It proves **who** a member of the company Entra tenant is and
nothing else: no groups, no app roles, no personas (diff §4.5 *No group-to-role mapping*).

* **Pinned tenant.** The authority and issuer are
  ``https://login.microsoftonline.com/<KERP_ENTRA_TENANT_ID>/v2.0``; the settings accept only
  a GUID there, so ``common`` / ``organizations`` can never be configured.
* **ID token.** RS256 signature against the tenant's JWKS (``EntraJwks``: cached; on an
  unknown ``kid`` refetched at most once per minute *deployment-wide*, through the shared
  store, so forged ``kid`` values cannot hammer Microsoft), ``iss`` = the pinned issuer,
  ``tid`` = the configured tenant, ``aud`` = the client id (a single string),
  ``exp`` / ``nbf`` / ``iat`` with at most 2 minutes of leeway, ``nonce`` = the stored one.
* **Members only.** ``acct`` must be ``0`` (missing = not proven: the distinct reason
  ``acct_claim_missing``); an ``idp`` claim other than the pinned issuer is a guest.
* **Matching.** By ``oid`` once linked; the first sign-in matches an existing active account
  by the **verified** ``email`` claim (``xms_edov`` true, or the domain is on
  ``KERP_INTERNAL_EMAIL_DOMAINS``), case-insensitively, and links it (``entra_oid`` /
  ``entra_tid``). No ``preferred_username`` fallback, no account creation.

Every failure raises ``SsoRefusedError(reason)``. The reason is for the logs and the audit
ledger only; the client gets one of a few translated ``code`` values (``client_code``). No
token content is ever logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final, Protocol

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.models import User, normalize_email
from app.core.auth.revocation import RevocationStore
from app.core.config import Settings

AUTHORITY_HOST: Final = "https://login.microsoftonline.com"
SCOPES: Final = "openid profile email"
# The optional ID-token claims the app registration must emit (diff §4.5 App registration).
REQUIRED_OPTIONAL_CLAIMS: Final = ("email", "acct", "xms_edov")
ID_TOKEN_ALGORITHMS: Final = ("RS256",)
CLOCK_LEEWAY: Final = timedelta(minutes=2)
JWKS_CACHE_TTL: Final = timedelta(hours=24)
JWKS_REFETCH_INTERVAL: Final = timedelta(minutes=1)
JWKS_REFETCH_KEY: Final = "sso:entra:jwks-refetch"
HTTP_TIMEOUT_SECONDS: Final = 10.0
MAX_RESPONSE_BYTES: Final = 512 * 1024

_logger = logging.getLogger("kerp.auth.sso")

# --- refusal reasons (logs + audit) and client codes (translated) ------------------------

# Before an identity is proven (security log only: unauthenticated noise).
STATE_INVALID: Final = "state_invalid"
PREAUTH_COOKIE_MISMATCH: Final = "preauth_cookie_mismatch"
PROVIDER_ERROR: Final = "provider_error"
TOKEN_EXCHANGE_FAILED: Final = "token_exchange_failed"
JWKS_UNAVAILABLE: Final = "jwks_unavailable"
ID_TOKEN_MALFORMED: Final = "id_token_malformed"
UNKNOWN_KEY: Final = "id_token_unknown_key"
BAD_SIGNATURE: Final = "id_token_bad_signature"
EXPIRED: Final = "id_token_expired"
NOT_YET_VALID: Final = "id_token_not_yet_valid"
WRONG_ISSUER: Final = "id_token_wrong_issuer"
WRONG_AUDIENCE: Final = "id_token_wrong_audience"
WRONG_TENANT: Final = "id_token_wrong_tenant"
CLAIM_MISSING: Final = "id_token_claim_missing"
ID_TOKEN_INVALID: Final = "id_token_invalid"
NONCE_MISMATCH: Final = "nonce_mismatch"
# After the ID token verified (audit ledger + security log).
ACCT_CLAIM_MISSING: Final = "acct_claim_missing"
GUEST: Final = "guest"
FOREIGN_IDP: Final = "foreign_idp"
EMAIL_MISSING: Final = "email_missing"
EMAIL_UNVERIFIED: Final = "email_unverified"
NO_ACCOUNT: Final = "no_account"
ACCOUNT_DEACTIVATED: Final = "account_deactivated"
OID_MISMATCH: Final = "oid_mismatch"

# The ``code`` the client translates (frontend ``auth.sso.errors.<code>``, T-0055 / T-0143).
SSO_FAILED: Final = "SSO_FAILED"
SSO_GUEST: Final = "SSO_GUEST"
SSO_EMAIL_UNVERIFIED: Final = "SSO_EMAIL_UNVERIFIED"
SSO_NO_ACCOUNT: Final = "SSO_NO_ACCOUNT"
SSO_ACCOUNT_DEACTIVATED: Final = "SSO_ACCOUNT_DEACTIVATED"
SSO_ACCOUNT_LINKED_ELSEWHERE: Final = "SSO_ACCOUNT_LINKED_ELSEWHERE"
CLIENT_CODES: Final[Mapping[str, str]] = {
    ACCT_CLAIM_MISSING: SSO_GUEST,
    GUEST: SSO_GUEST,
    FOREIGN_IDP: SSO_GUEST,
    EMAIL_MISSING: SSO_EMAIL_UNVERIFIED,
    EMAIL_UNVERIFIED: SSO_EMAIL_UNVERIFIED,
    NO_ACCOUNT: SSO_NO_ACCOUNT,
    ACCOUNT_DEACTIVATED: SSO_ACCOUNT_DEACTIVATED,
    OID_MISMATCH: SSO_ACCOUNT_LINKED_ELSEWHERE,
}
# Reasons that come with a verified Entra identity: written to the audit ledger.
IDENTIFIED_REASONS: Final = frozenset(CLIENT_CODES)


def client_code(reason: str) -> str:
    """The translated message key for a refusal reason (``SSO_FAILED`` for the rest)."""
    return CLIENT_CODES.get(reason, SSO_FAILED)


class SsoRefusedError(Exception):
    """An SSO sign-in is refused. ``reason`` is never shown to the client (``client_code``)."""

    def __init__(self, reason: str, *, user_id: uuid.UUID | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.user_id = user_id

    @property
    def code(self) -> str:
        return client_code(self.reason)


class EntraTransportError(Exception):
    """Microsoft's token or keys endpoint could not be used (network, status, body)."""


# --- configuration ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EntraConfig:
    tenant_id: str
    client_id: str
    client_secret: str | None

    @property
    def authority(self) -> str:
        return f"{AUTHORITY_HOST}/{self.tenant_id}/v2.0"

    @property
    def issuer(self) -> str:
        return self.authority

    @property
    def authorize_url(self) -> str:
        return f"{AUTHORITY_HOST}/{self.tenant_id}/oauth2/v2.0/authorize"

    @property
    def token_url(self) -> str:
        return f"{AUTHORITY_HOST}/{self.tenant_id}/oauth2/v2.0/token"

    @property
    def jwks_url(self) -> str:
        return f"{AUTHORITY_HOST}/{self.tenant_id}/discovery/v2.0/keys"


def entra_config(settings: Settings) -> EntraConfig | None:
    """The deployment's Entra configuration, or ``None`` when SSO is disabled."""
    if settings.entra_tenant_id is None or settings.entra_client_id is None:
        return None
    secret = settings.entra_client_secret
    return EntraConfig(
        tenant_id=settings.entra_tenant_id,
        client_id=settings.entra_client_id,
        client_secret=secret.get_secret_value() if secret is not None else None,
    )


def pkce_challenge(verifier: str) -> str:
    """RFC 7636 S256: ``BASE64URL(SHA256(verifier))`` without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# --- talking to Microsoft (a seam: tests use a fake provider, no network) ----------------


class EntraTransport(Protocol):
    def redeem_code(
        self, config: EntraConfig, *, code: str, verifier: str, redirect_uri: str
    ) -> str:
        """Exchange an authorization code (+ PKCE verifier) for the ID token."""
        ...

    def fetch_jwks(self, config: EntraConfig) -> Mapping[str, Any]:
        """The tenant's signing keys (a JWKS document)."""
        ...


def _read_json(request: urllib.request.Request) -> Mapping[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        # The status only: an error body may echo request parameters.
        raise EntraTransportError(f"Entra answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise EntraTransportError(f"Entra unreachable: {type(exc).__name__}") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise EntraTransportError("Entra response too large")
    try:
        data = json.loads(body)
    except ValueError:
        raise EntraTransportError("Entra response is not JSON") from None
    if not isinstance(data, dict):
        raise EntraTransportError("Entra response is not a JSON object")
    return data


class HttpEntraTransport:
    """The production transport: HTTPS to ``login.microsoftonline.com`` (stdlib only)."""

    def redeem_code(
        self, config: EntraConfig, *, code: str, verifier: str, redirect_uri: str
    ) -> str:
        if config.client_secret is None:
            raise EntraTransportError("KERP_ENTRA_CLIENT_SECRET is not set")
        form = urllib.parse.urlencode(
            {
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
                "scope": SCOPES,
            }
        ).encode()
        request = urllib.request.Request(
            config.token_url,
            data=form,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        data = _read_json(request)
        id_token = data.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise EntraTransportError("Entra token response has no id_token")
        return id_token

    def fetch_jwks(self, config: EntraConfig) -> Mapping[str, Any]:
        request = urllib.request.Request(config.jwks_url, headers={"Accept": "application/json"})
        return _read_json(request)


# --- the tenant's signing keys ----------------------------------------------------------


class EntraJwks:
    """The tenant's JWKS, cached in the process.

    Loaded on first use and again once ``JWKS_CACHE_TTL`` has passed. A token naming a
    ``kid`` the cache does not know triggers a refetch only when the deployment-wide limit
    allows it (``store.claim``: once per ``JWKS_REFETCH_INTERVAL`` across every worker), so a
    stream of forged ``kid`` values costs Microsoft at most one request a minute. A failed
    reload keeps the keys already known.
    """

    def __init__(
        self,
        fetch: Callable[[], Mapping[str, Any]],
        store: RevocationStore,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._store = store
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._loaded_at: float | None = None
        self._lock = threading.Lock()

    def key_for(self, kid: str) -> jwt.PyJWK | None:
        with self._lock:
            fresh = False
            stale = (
                self._loaded_at is None
                or self._clock() - self._loaded_at >= JWKS_CACHE_TTL.total_seconds()
            )
            if stale:
                try:
                    self._load()
                    fresh = True
                except EntraTransportError:
                    if not self._keys:
                        raise
            key = self._keys.get(kid)
            if (
                key is None
                and not fresh
                and self._store.claim(JWKS_REFETCH_KEY, JWKS_REFETCH_INTERVAL)
            ):
                try:
                    self._load()
                except EntraTransportError:
                    return None
                key = self._keys.get(kid)
            return key

    def _load(self) -> None:
        document = self._fetch()
        raw_keys = document.get("keys")
        if not isinstance(raw_keys, list):
            raise EntraTransportError("JWKS document has no keys")
        keys: dict[str, jwt.PyJWK] = {}
        for raw in raw_keys:
            if not isinstance(raw, dict):
                continue
            kid = raw.get("kid")
            if not isinstance(kid, str) or raw.get("kty") != "RSA":
                continue
            if raw.get("use", "sig") != "sig":
                continue
            try:
                keys[kid] = jwt.PyJWK(raw, algorithm="RS256")
            except jwt.PyJWTError:
                continue
        self._keys = keys
        self._loaded_at = self._clock()


# --- the verified identity --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EntraIdentity:
    """What a verified member ID token proves: the object id, the tenant, maybe an e-mail."""

    oid: str
    tid: str
    email: str | None
    email_verified: bool


@dataclass(frozen=True, slots=True)
class EntraCredentials:
    """The provider's credentials: the ID token from the code exchange and the nonce kept
    with the sign-in's ``state``."""

    id_token: str
    nonce: str


@dataclass(frozen=True, slots=True)
class EntraMatch:
    user: User
    linked: bool  # this sign-in linked the account (first SSO sign-in)


_DECODE_ERRORS: Final[tuple[tuple[type[jwt.PyJWTError], str], ...]] = (
    (jwt.InvalidSignatureError, BAD_SIGNATURE),
    (jwt.ExpiredSignatureError, EXPIRED),
    (jwt.ImmatureSignatureError, NOT_YET_VALID),
    (jwt.InvalidIssuerError, WRONG_ISSUER),
    (jwt.InvalidAudienceError, WRONG_AUDIENCE),
    (jwt.MissingRequiredClaimError, CLAIM_MISSING),
)


def _is_true(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _is_member_acct(value: object) -> bool:
    # ``acct`` is an integer (0 member, 1 guest); a boolean is never a valid value.
    if isinstance(value, bool):
        return False
    return value == 0 or value == "0"


def _email_domain(email: str) -> str:
    return email.rpartition("@")[2]


class EntraOidcProvider:
    """Entra ID SSO behind the ``AuthProvider`` seam (``authenticate``)."""

    name = "entra"

    def __init__(
        self,
        config: EntraConfig,
        jwks: EntraJwks,
        *,
        internal_domains: tuple[str, ...] = (),
    ) -> None:
        self.config = config
        self.jwks = jwks
        self.internal_domains = internal_domains

    # The seam (TD-003): credentials → the matching active user, or SsoRefusedError.
    def authenticate(
        self, session: Session, tenant_id: uuid.UUID, credentials: EntraCredentials
    ) -> User:
        identity = self.verify(credentials.id_token, nonce=credentials.nonce)
        return self.match(session, tenant_id, identity).user

    def verify(self, id_token: str, *, nonce: str) -> EntraIdentity:
        """Verify the ID token and the member rule; ``SsoRefusedError`` on any failure."""
        claims = self._decode(id_token)
        if claims.get("tid") != self.config.tenant_id:
            raise SsoRefusedError(WRONG_TENANT)
        stored = nonce.encode()
        presented = claims.get("nonce")
        if not isinstance(presented, str) or not _equal(presented.encode(), stored):
            raise SsoRefusedError(NONCE_MISMATCH)
        if "acct" not in claims:
            raise SsoRefusedError(ACCT_CLAIM_MISSING)
        if not _is_member_acct(claims["acct"]):
            raise SsoRefusedError(GUEST)
        idp = claims.get("idp")
        if idp is not None and idp != self.config.issuer:
            raise SsoRefusedError(FOREIGN_IDP)
        oid = claims.get("oid")
        if not isinstance(oid, str) or not oid:
            raise SsoRefusedError(CLAIM_MISSING)
        raw_email = claims.get("email")
        email = normalize_email(raw_email) if isinstance(raw_email, str) else None
        if email is not None and ("@" not in email or not _email_domain(email)):
            email = None
        verified = email is not None and (
            _is_true(claims.get("xms_edov")) or _email_domain(email) in self.internal_domains
        )
        return EntraIdentity(
            oid=oid.lower(), tid=self.config.tenant_id, email=email, email_verified=verified
        )

    def match(self, session: Session, tenant_id: uuid.UUID, identity: EntraIdentity) -> EntraMatch:
        """The KERP account of a verified member: by ``oid`` once linked, else by the verified
        e-mail (and then linked). Never creates an account; group and role claims play no
        part. ``session`` is bound to ``tenant_id``."""
        linked_user = session.scalars(
            select(User).where(User.tenant_id == tenant_id, User.entra_oid == identity.oid)
        ).one_or_none()
        if linked_user is not None:
            if not linked_user.is_active:
                raise SsoRefusedError(ACCOUNT_DEACTIVATED, user_id=linked_user.id)
            return EntraMatch(user=linked_user, linked=False)
        if identity.email is None:
            raise SsoRefusedError(EMAIL_MISSING)
        if not identity.email_verified:
            raise SsoRefusedError(EMAIL_UNVERIFIED)
        user = session.scalars(
            select(User).where(User.tenant_id == tenant_id, User.email == identity.email)
        ).one_or_none()
        if user is None:
            raise SsoRefusedError(NO_ACCOUNT)
        if not user.is_active:
            raise SsoRefusedError(ACCOUNT_DEACTIVATED, user_id=user.id)
        if user.entra_oid is not None and user.entra_oid != identity.oid:
            raise SsoRefusedError(OID_MISMATCH, user_id=user.id)
        user.entra_oid = identity.oid
        user.entra_tid = identity.tid
        session.flush()
        return EntraMatch(user=user, linked=True)

    def _decode(self, id_token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError:
            raise SsoRefusedError(ID_TOKEN_MALFORMED) from None
        kid = header.get("kid")
        if header.get("alg") not in ID_TOKEN_ALGORITHMS or not isinstance(kid, str):
            raise SsoRefusedError(ID_TOKEN_MALFORMED)
        try:
            key = self.jwks.key_for(kid)
        except EntraTransportError:
            raise SsoRefusedError(JWKS_UNAVAILABLE) from None
        if key is None:
            raise SsoRefusedError(UNKNOWN_KEY)
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key=key,
                algorithms=list(ID_TOKEN_ALGORITHMS),
                audience=self.config.client_id,
                issuer=self.config.issuer,
                leeway=CLOCK_LEEWAY,
                options={"require": ["exp", "nbf", "iat", "iss", "aud", "tid", "nonce"]},
            )
        except jwt.PyJWTError as exc:
            for error_type, reason in _DECODE_ERRORS:
                if isinstance(exc, error_type):
                    raise SsoRefusedError(reason) from None
            raise SsoRefusedError(ID_TOKEN_INVALID) from None
        # ``aud`` must be exactly the client id: PyJWT also accepts a list containing it.
        if claims.get("aud") != self.config.client_id:
            raise SsoRefusedError(WRONG_AUDIENCE)
        return claims


def _equal(left: bytes, right: bytes) -> bool:
    return hmac.compare_digest(left, right)


def log_refusal(reason: str, *, flow: str) -> None:
    """The security log line of a refused SSO sign-in: the reason only, never token content."""
    _logger.warning(
        "entra sso sign-in refused: %s", reason, extra={"sso_reason": reason, "sso_flow": flow}
    )


def startup_warning(settings: Settings) -> None:
    """With SSO enabled, name the optional claims the app registration must emit: without
    ``acct`` every SSO sign-in is refused (diff §4.5 *Deployment prerequisite*)."""
    if settings.entra_enabled:
        _logger.warning(
            "Entra ID SSO is enabled: the app registration must emit the optional ID-token "
            "claims %s; without 'acct' every SSO sign-in is refused (acct_claim_missing)",
            ", ".join(REQUIRED_OPTIONAL_CLAIMS),
            extra={"sso_required_claims": list(REQUIRED_OPTIONAL_CLAIMS)},
        )
