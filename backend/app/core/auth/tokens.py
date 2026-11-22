"""Access and refresh JWTs signed with the asymmetric key set (T-0008, diff §4).

Rewrite of Inspection-ERP's HS256 token layer (ADR 0001). Every token carries:

* ``iss`` / ``aud``: this deployment; checked on every verify.
* ``sub``: the user id. ``tid``: the tenant id. The tenant of a request comes only from
  here (CLAUDE.md §6).
* ``sid``: the login session. A refresh rotates the tokens but keeps the ``sid``, so a
  whole session (every access and refresh token of one login) can be revoked at once.
* ``jti``: unique per token, the key of the revocation list.
* ``type``: ``access`` (15 minutes), ``refresh`` (rotating, one use each) or
  ``impersonation`` (T-0277, ADR 0015: a ``system_admin`` acting as another user; single
  session, never refreshed, own ``typ`` header ``imp+jwt``).
* ``sxp`` (access tokens): the end of the login session (``not_after``), so a session started
  from it (impersonation) never outlives the session it came from.
* ``imp`` / ``imp_sid`` (impersonation tokens only, required there, refused elsewhere): the
  impersonating admin's user id and login session id.
* ``did`` (optional): the enrolled device the session is bound to (T-0038), so the field-PIN
  gate of ``/auth/refresh`` does not depend on the revocation store's binding (TD-142).
* ``auth_time`` / ``amr`` (optional, T-0345): when and how the session's sign-in happened
  (the OIDC Core / RFC 8176 claim names). ``auth_time`` is the sign-in moment (fractional,
  like ``iat``); ``amr`` names the sign-in method (``pwd``, ``magic_link``, ``sso``) and gains
  ``pin`` once the session has been renewed by PIN unlock. Every rotation copies both
  unchanged, so they describe the session, not the token. A token issued before T-0345 has
  neither: it verifies as before and never counts as a fresh sign-in.
* ``iat`` / ``nbf`` / ``exp``. ``iat`` and ``exp`` are fractional NumericDates (RFC 7519
  §2) with microsecond precision, so a token is ordered exactly against a password change
  (T-0061) and a rotated token keeps its session's exact end (``not_after``, diff §4.2).

The header carries the ``kid`` that selects the verification key; only the algorithm of
that key is accepted, so ``alg: none`` and algorithm-confusion tokens fail.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import jwt

from app.core.auth.keys import KeySet

TokenType = Literal["access", "refresh", "impersonation"]
LEEWAY: Final = timedelta(seconds=10)
_REQUIRED: Final = ["exp", "iat", "nbf", "iss", "aud", "sub", "jti"]
IMPERSONATION_TYP: Final = "imp+jwt"
_HEADER_TYP: Final[dict[TokenType, str]] = {
    "access": "at+jwt",
    "refresh": "JWT",
    "impersonation": IMPERSONATION_TYP,
}


class InvalidTokenError(Exception):
    """The token is malformed, forged, expired, of the wrong type or for someone else."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    type: TokenType
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    session_id: uuid.UUID
    jti: str
    issued_at: datetime
    expires_at: datetime
    device_id: uuid.UUID | None = None
    # When and how the session was signed in (T-0345); ``None`` / ``()`` on older tokens.
    auth_time: datetime | None = None
    amr: tuple[str, ...] = ()
    # Impersonation tokens (T-0277): the admin acting as ``user_id`` and the admin's session.
    impersonator_id: uuid.UUID | None = None
    impersonator_session_id: uuid.UUID | None = None
    # Access tokens: the login session's end (``sxp``), when the token carries it.
    session_expires_at: datetime | None = None

    @property
    def is_impersonation(self) -> bool:
        return self.type == "impersonation"

    def remaining(self, now: datetime | None = None) -> timedelta:
        """Time until expiry (at least zero)."""
        return max(self.expires_at - (now or datetime.now(UTC)), timedelta(0))


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    claims: TokenClaims


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TokenService:
    """Issues and verifies tokens with one key set."""

    def __init__(
        self,
        keys: KeySet,
        *,
        issuer: str,
        audience: str,
        access_ttl: timedelta,
        refresh_ttl: timedelta,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.keys = keys
        self.issuer = issuer
        self.audience = audience
        self.ttl: dict[TokenType, timedelta] = {"access": access_ttl, "refresh": refresh_ttl}
        self._clock = clock

    def issue(
        self,
        token_type: TokenType,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        not_after: datetime | None = None,
        device_id: uuid.UUID | None = None,
        auth_time: datetime | None = None,
        amr: Sequence[str] = (),
    ) -> IssuedToken:
        """``not_after`` caps the expiry (the session's end: rotation never extends it).
        ``device_id`` is the session's bound device (the ``did`` claim), if known.
        ``auth_time`` / ``amr``: the session's sign-in moment and methods (T-0345)."""
        if token_type == "impersonation":
            raise ValueError("impersonation tokens are issued by issue_impersonation")
        now = self.now()
        expires_at = now + self.ttl[token_type]
        if not_after is not None:
            expires_at = min(expires_at, not_after)
        claims = TokenClaims(
            type=token_type,
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            jti=uuid.uuid4().hex,
            issued_at=now,
            expires_at=expires_at,
            device_id=device_id,
            auth_time=auth_time,
            amr=tuple(amr),
            session_expires_at=not_after if token_type == "access" else None,
        )
        return self._sign(claims)

    def issue_impersonation(
        self,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        impersonator_id: uuid.UUID,
        impersonator_session_id: uuid.UUID,
        expires_at: datetime,
    ) -> IssuedToken:
        """An impersonation token (T-0277, ADR 0015): ``user_id`` is the target; the admin
        and the admin's session ride in ``imp`` / ``imp_sid``. The caller computes the expiry
        (the registry maximum capped by the admin session's end). Never bound to a device."""
        now = self.now()
        if expires_at <= now:
            raise ValueError("an impersonation token must expire in the future")
        if impersonator_id == user_id:
            raise ValueError("an admin never impersonates themselves")
        claims = TokenClaims(
            type="impersonation",
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            jti=uuid.uuid4().hex,
            issued_at=now,
            expires_at=expires_at,
            impersonator_id=impersonator_id,
            impersonator_session_id=impersonator_session_id,
        )
        return self._sign(claims)

    def _sign(self, claims: TokenClaims) -> IssuedToken:
        now, token_type = claims.issued_at, claims.type
        payload: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": str(claims.user_id),
            "tid": str(claims.tenant_id),
            "sid": str(claims.session_id),
            "jti": claims.jti,
            "type": token_type,
            # Fractional: a float round-trips the microseconds exactly (JSON repr is exact
            # and a double resolves well below 1 µs at today's epoch).
            "iat": now.timestamp(),
            "nbf": int(now.timestamp()),
            "exp": claims.expires_at.timestamp(),
        }
        if claims.device_id is not None:
            payload["did"] = str(claims.device_id)
        if claims.auth_time is not None:
            payload["auth_time"] = claims.auth_time.timestamp()
        if claims.amr:
            payload["amr"] = list(claims.amr)
        if claims.session_expires_at is not None:
            payload["sxp"] = claims.session_expires_at.timestamp()
        if claims.impersonator_id is not None and claims.impersonator_session_id is not None:
            payload["imp"] = str(claims.impersonator_id)
            payload["imp_sid"] = str(claims.impersonator_session_id)
        signing = self.keys.signing
        token = jwt.encode(
            payload,
            signing.private_key,
            algorithm=signing.algorithm,
            headers={"kid": signing.kid, "typ": _HEADER_TYP[token_type]},
        )
        return IssuedToken(token, claims)

    def now(self) -> datetime:
        return self._clock()

    def verify(
        self, token: str, expected_type: TokenType, *, allow_expired: bool = False
    ) -> TokenClaims:
        """Verify signature, issuer, audience, lifetime and type. Revocation is separate.

        An impersonation token needs its own header ``typ`` (``imp+jwt``) and that header is
        refused for every other type. ``imp`` / ``imp_sid`` are required on an impersonation
        token and refused on any other; ``did`` is refused on an impersonation token.
        ``allow_expired`` skips only the ``exp`` check, to recognise an expired impersonation
        session and end it (T-0277); it never authenticates anything.
        """
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            key = self.keys.get(kid) if isinstance(kid, str) else None
            if key is None:
                raise InvalidTokenError("unknown signing key")
            if (header.get("typ") == IMPERSONATION_TYP) != (expected_type == "impersonation"):
                raise InvalidTokenError("wrong token type")
            payload = jwt.decode(
                token,
                key.public_key,
                algorithms=[key.algorithm],
                audience=self.audience,
                issuer=self.issuer,
                leeway=LEEWAY,
                options={"require": _REQUIRED, "verify_exp": not allow_expired},
            )
            if payload.get("type") != expected_type:
                raise InvalidTokenError("wrong token type")
            impersonator, impersonator_session = _impersonation(payload, expected_type)
            return TokenClaims(
                type=expected_type,
                user_id=uuid.UUID(payload["sub"]),
                tenant_id=uuid.UUID(payload["tid"]),
                session_id=uuid.UUID(payload["sid"]),
                jti=_text(payload["jti"]),
                issued_at=datetime.fromtimestamp(_number(payload["iat"]), UTC),
                expires_at=datetime.fromtimestamp(_number(payload["exp"]), UTC),
                device_id=_optional_uuid(payload.get("did")),
                auth_time=_optional_moment(payload.get("auth_time")),
                amr=_methods(payload.get("amr")),
                impersonator_id=impersonator,
                impersonator_session_id=impersonator_session,
                session_expires_at=_optional_moment(payload.get("sxp")),
            )
        except InvalidTokenError:
            raise
        except (jwt.PyJWTError, AttributeError, KeyError, TypeError, ValueError) as exc:
            raise InvalidTokenError(str(exc) or type(exc).__name__) from exc


def bearer_token_type(token: str) -> TokenType:
    """The type a bearer token claims by its header (unverified): ``impersonation`` for an
    ``imp+jwt`` header, else ``access``. ``verify`` still checks everything."""
    try:
        typ = jwt.get_unverified_header(token).get("typ")
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc) or type(exc).__name__) from exc
    return "impersonation" if typ == IMPERSONATION_TYP else "access"


def _impersonation(
    payload: dict[str, Any], expected_type: TokenType
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """``(imp, imp_sid)``: both required on an impersonation token, absent on any other."""
    imp, imp_sid = payload.get("imp"), payload.get("imp_sid")
    if expected_type != "impersonation":
        if imp is not None or imp_sid is not None:
            raise InvalidTokenError("impersonation claims on a non-impersonation token")
        return None, None
    if payload.get("did") is not None:
        raise InvalidTokenError("an impersonation token is never bound to a device")
    impersonator, impersonator_session = _optional_uuid(imp), _optional_uuid(imp_sid)
    if impersonator is None or impersonator_session is None:
        raise InvalidTokenError("an impersonation token needs imp and imp_sid")
    if impersonator == uuid.UUID(payload["sub"]):
        raise InvalidTokenError("an admin never impersonates themselves")
    return impersonator, impersonator_session


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidTokenError("jti must be a non-empty string")
    return value


def _optional_uuid(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidTokenError("did, imp and imp_sid must be strings")
    return uuid.UUID(value)


def _optional_moment(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(_number(value), UTC)


def _methods(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(m, str) and m for m in value):
        raise InvalidTokenError("amr must be a list of non-empty strings")
    return tuple(value)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidTokenError("iat, exp, auth_time and sxp must be numbers")
    return value
