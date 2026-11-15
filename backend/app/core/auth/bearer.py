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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth.device_signal import DeviceRevokedError
from app.core.auth.revocation import RevocationUnavailableError
from app.core.auth.runtime import AuthRuntime, app_auth_runtime
from app.core.auth.tokens import InvalidTokenError, TokenClaims

bearer_scheme: Final = HTTPBearer(auto_error=False, description="KERP access token (JWT)")

AUTH_UNAVAILABLE: Final = "Authentication temporarily unavailable"


@dataclass(frozen=True, slots=True)
class BearerResult:
    """``claims`` of a valid, unrevoked access token; ``presented`` if any bearer was sent;
    ``device_revoked`` if the token's session died because its device was revoked."""

    claims: TokenClaims | None
    presented: bool
    device_revoked: bool = False


ANONYMOUS: Final = BearerResult(claims=None, presented=False)


def verify_access_token(runtime: AuthRuntime, token: str) -> TokenClaims:
    """Verify an access token including revocation. Raises ``InvalidTokenError``, or
    ``DeviceRevokedError`` when the session was killed by a device revoke (T-0043)."""
    claims = runtime.tokens.verify(token, "access")
    if runtime.store.is_revoked(claims.jti, claims.session_id):
        if runtime.store.revoked_by_device(claims.session_id):
            raise DeviceRevokedError("device revoked")
        raise InvalidTokenError("token revoked")
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
    except InvalidTokenError:
        return BearerResult(claims=None, presented=True)
    except DeviceRevokedError:
        return BearerResult(claims=None, presented=True, device_revoked=True)
    except RevocationUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, AUTH_UNAVAILABLE) from exc
