"""Bearer-token verification for requests (no database access).

``get_bearer`` verifies the ``Authorization: Bearer`` access token once per request
(FastAPI caches the dependency): signature, issuer, audience, lifetime, type and the
Redis revocation list. ``app.core.tenancy.context.get_tenancy_context`` turns the result
into the request's tenancy, which ``get_db`` binds for RLS. ``deps.get_principal`` turns it
into the authenticated user or a 401.

A missing or invalid token yields an anonymous result here (an unbound session that RLS
shows nothing), not an error: endpoints such as ``/auth/refresh`` must still work when the
client sends its expired access token along. Requiring authentication is ``deps``' job.
An unreachable revocation store is a 503: never treat "cannot check" as "not revoked".

**Impersonation tokens** (T-0277, ADR 0015) are accepted here too (header ``typ`` ``imp+jwt``):
refused when their own ``jti`` or ``sid`` is revoked, when the admin's session (``imp_sid``)
is revoked, or once expired. A refused impersonation token whose signature is valid ends its
session at once (``impersonation.end_refused``: ledger ``impersonation.ended`` with
``expired`` or ``revoked`` and the target's notice). Whether the admin still holds
``system_admin`` and is active is checked with the grants (``access.resolve_request_scope``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth.device_signal import DeviceRevokedError
from app.core.auth.runtime import AuthRuntime, app_auth_runtime
from app.core.auth.tokens import InvalidTokenError, TokenClaims, bearer_token_type

bearer_scheme: Final = HTTPBearer(auto_error=False, description="KERP access token (JWT)")


@dataclass(frozen=True, slots=True)
class BearerResult:
    """``claims`` of a valid, unrevoked access token; ``presented`` if any bearer was sent;
    ``device_revoked`` if the token's session died because its device was revoked."""

    claims: TokenClaims | None
    presented: bool
    device_revoked: bool = False


ANONYMOUS: Final = BearerResult(claims=None, presented=False)


class ImpersonationEndedError(InvalidTokenError):
    """A signature-valid impersonation token that is refused: its session is over
    (``reason``: ``expired`` or ``revoked``)."""

    def __init__(self, claims: TokenClaims, reason: str) -> None:
        super().__init__(f"impersonation {reason}")
        self.claims = claims
        self.reason = reason


def verify_access_token(runtime: AuthRuntime, token: str) -> TokenClaims:
    """Verify an access or impersonation token including revocation. Raises
    ``InvalidTokenError`` (``ImpersonationEndedError`` for a genuine impersonation token whose
    session is over), or ``DeviceRevokedError`` when the session was killed by a device revoke
    (T-0043)."""
    if bearer_token_type(token) == "impersonation":
        return _verify_impersonation(runtime, token)
    claims = runtime.tokens.verify(token, "access")
    if runtime.store.is_revoked(claims.jti, claims.session_id):
        if runtime.store.revoked_by_device(claims.session_id):
            raise DeviceRevokedError("device revoked")
        raise InvalidTokenError("token revoked")
    return claims


def _verify_impersonation(runtime: AuthRuntime, token: str) -> TokenClaims:
    try:
        claims = runtime.tokens.verify(token, "impersonation")
    except InvalidTokenError:
        # Only the expiry failed when it verifies without the ``exp`` check: an expired session.
        expired = runtime.tokens.verify(token, "impersonation", allow_expired=True)
        raise ImpersonationEndedError(expired, "expired") from None
    admin_session = claims.impersonator_session_id
    if runtime.store.is_revoked(claims.jti, claims.session_id) or (
        admin_session is not None and runtime.store.is_session_revoked(admin_session)
    ):
        raise ImpersonationEndedError(claims, "revoked")
    return claims


def get_bearer(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> BearerResult:
    if credentials is None:
        return ANONYMOUS  # no key or store needed for anonymous requests
    runtime = app_auth_runtime(request.app)
    try:
        return BearerResult(verify_access_token(runtime, credentials.credentials), presented=True)
    except ImpersonationEndedError as exc:
        database = getattr(request.app.state, "db", None)
        if database is not None:
            # Imported here: it needs the DB layer, which imports this module.
            from app.core.auth.impersonation import end_refused

            end_refused(database, exc.claims, exc.reason)
        return BearerResult(claims=None, presented=True)
    except InvalidTokenError:
        return BearerResult(claims=None, presented=True)
    except DeviceRevokedError:
        return BearerResult(claims=None, presented=True, device_revoked=True)
