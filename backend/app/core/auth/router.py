"""Auth endpoints: login, refresh, logout, me, JWKS (T-0008); magic link + PIN (T-0009).

The refresh token travels only in the ``httpOnly`` refresh cookie (T-0037, diff §4.2,
TD-203): set by every endpoint that issues tokens, read by ``/refresh`` and
``/pin/unlock`` behind an Origin check, cleared by ``/logout``. No body carries it.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status

from app.core.auth.bearer import AUTH_UNAVAILABLE
from app.core.auth.deps import CurrentPrincipal, get_auth_service, unauthorized
from app.core.auth.lockout import LockedOutError
from app.core.auth.providers import PasswordCredentials
from app.core.auth.revocation import RevocationUnavailableError
from app.core.auth.runtime import AuthRuntime, get_auth_runtime
from app.core.auth.schemas import (
    JsonWebKeySet,
    LoginRequest,
    MagicLinkRedeemRequest,
    MagicLinkRequest,
    MeResponse,
    PinUnlockRequest,
    TokenResponse,
)
from app.core.auth.service import AuthenticationError, AuthService, TokenPair
from app.core.db import DbSession

PREFIX: Final = "/api/v1/auth"
JWKS_PATH: Final = "/.well-known/jwks.json"
JWKS_MAX_AGE: Final = 300
TOO_MANY_ATTEMPTS: Final = "Too many attempts"
REFRESH_COOKIE: Final = "kerp_refresh"
# Scoped to the auth endpoints: the cookie never rides along on any other API request.
REFRESH_COOKIE_PATH: Final = PREFIX
REFRESH_COOKIE_MAX_LENGTH: Final = 4096
CROSS_ORIGIN: Final = "Cross-origin request refused"

router = APIRouter(tags=["auth"])

AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def _token_response(pair: TokenPair, response: Response) -> TokenResponse:
    # RFC 6749 §5.1: token responses must not be cached.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    access, refresh = pair.access.claims, pair.refresh.claims
    refresh_seconds = int((refresh.expires_at - refresh.issued_at).total_seconds())
    response.set_cookie(
        REFRESH_COOKIE,
        pair.refresh.token,
        max_age=refresh_seconds,
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return TokenResponse(
        access_token=pair.access.token,
        expires_in=int((access.expires_at - access.issued_at).total_seconds()),
        refresh_expires_in=refresh_seconds,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, secure=True, httponly=True, samesite="strict"
    )


def refresh_cookie(
    request: Request,
    runtime: Annotated[AuthRuntime, Depends(get_auth_runtime)],
    kerp_refresh: Annotated[
        str | None, Cookie(description="The refresh token (httpOnly cookie, never JS)")
    ] = None,
) -> str:
    """The refresh cookie of a same-origin request (CSRF: ``SameSite=Strict`` + Origin).

    The ``Origin`` header must be the web SPA's origin (``KERP_WEB_ORIGIN``, else the API's
    own origin); a missing or foreign one is ``403`` before the cookie is looked at.
    """
    own = f"{request.url.scheme}://{request.url.netloc}"
    allowed = (runtime.settings.web_origin or own).rstrip("/")
    if request.headers.get("origin") != allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, CROSS_ORIGIN)
    if not kerp_refresh or len(kerp_refresh) > REFRESH_COOKIE_MAX_LENGTH:
        raise unauthorized(invalid_token=True)
    return kerp_refresh


RefreshCookie = Annotated[str, Depends(refresh_cookie)]

_COOKIE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_403_FORBIDDEN: {"description": "`Origin` is not the web app's origin"}
}


def _unavailable() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, AUTH_UNAVAILABLE)


@router.get(JWKS_PATH, response_model=JsonWebKeySet, response_model_exclude_none=True)
def jwks(
    runtime: Annotated[AuthRuntime, Depends(get_auth_runtime)], response: Response
) -> JsonWebKeySet:
    """Public keys that verify KERP tokens (RFC 7517). Contains no private material."""
    response.headers["Cache-Control"] = f"public, max-age={JWKS_MAX_AGE}"
    return JsonWebKeySet.model_validate(runtime.keys.jwks())


def _locked(exc: LockedOutError) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        TOO_MANY_ATTEMPTS,
        headers={"Retry-After": str(exc.retry_after)},
    )


_LOCKED_RESPONSE: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_429_TOO_MANY_REQUESTS: {
        "description": "Locked after repeated failures; retry after `Retry-After` seconds"
    }
}


@router.post(f"{PREFIX}/login", response_model=TokenResponse, responses=_LOCKED_RESPONSE)
def login(
    body: LoginRequest,
    session: DbSession,
    service: AuthServiceDep,
    request: Request,
    response: Response,
) -> TokenResponse:
    """Exchange email + password for an access token (15 min) and a refresh token."""
    credentials = PasswordCredentials(email=body.email, password=body.password)
    # Behind a proxy, uvicorn sets this from X-Forwarded-For only for peers listed in
    # FORWARDED_ALLOW_IPS; otherwise it is the direct peer (the proxy itself).
    client = request.client.host if request.client else None
    try:
        pair = service.login(session, credentials=credentials, client=client)
    except AuthenticationError as exc:
        raise unauthorized() from exc
    except LockedOutError as exc:
        raise _locked(exc) from exc
    except RevocationUnavailableError as exc:
        raise _unavailable() from exc
    return _token_response(pair, response)


@router.post(
    f"{PREFIX}/magic-link",
    status_code=status.HTTP_202_ACCEPTED,
    responses=_LOCKED_RESPONSE,
)
def request_magic_link(body: MagicLinkRequest, session: DbSession, service: AuthServiceDep) -> None:
    """Send a one-time sign-in link (15 min) to a field worker's e-mail.

    Always ``202`` (unknown, inactive and other-tenant addresses included), so the answer
    reveals no accounts.
    """
    try:
        service.request_magic_link(session, email=body.email)
    except LockedOutError as exc:
        raise _locked(exc) from exc
    except RevocationUnavailableError as exc:
        raise _unavailable() from exc


@router.post(f"{PREFIX}/magic-link/redeem", response_model=TokenResponse)
def redeem_magic_link(
    body: MagicLinkRedeemRequest, session: DbSession, service: AuthServiceDep, response: Response
) -> TokenResponse:
    """Spend a magic link: start a session on this device, gated by the PIN set here.

    The session's refresh token then works only through `/auth/pin/unlock`.
    """
    try:
        pair = service.redeem_magic_link(session, token=body.token, pin=body.pin)
    except AuthenticationError as exc:
        raise unauthorized(invalid_token=True) from exc
    except RevocationUnavailableError as exc:
        raise _unavailable() from exc
    return _token_response(pair, response)


@router.post(
    f"{PREFIX}/pin/unlock",
    response_model=TokenResponse,
    responses={**_LOCKED_RESPONSE, **_COOKIE_RESPONSES},
)
def pin_unlock(
    body: PinUnlockRequest,
    refresh_token: RefreshCookie,
    session: DbSession,
    service: AuthServiceDep,
    response: Response,
) -> TokenResponse:
    """Unlock a PIN-gated session: the device's refresh cookie + the PIN rotate the tokens.

    Wrong PINs lock the session progressively; the 10th wrong PIN revokes it.
    """
    try:
        pair = service.unlock(session, refresh_token=refresh_token, pin=body.pin)
    except AuthenticationError as exc:
        raise unauthorized(invalid_token=True) from exc
    except LockedOutError as exc:
        raise _locked(exc) from exc
    except RevocationUnavailableError as exc:
        raise _unavailable() from exc
    return _token_response(pair, response)


@router.post(f"{PREFIX}/refresh", response_model=TokenResponse, responses=_COOKIE_RESPONSES)
def refresh(
    refresh_token: RefreshCookie, session: DbSession, service: AuthServiceDep, response: Response
) -> TokenResponse:
    """Rotate: the refresh cookie is spent and a new pair is issued. Reuse revokes the session.

    The new refresh token keeps the session's end (sign-in + 12 h); after it, sign in again.
    """
    try:
        pair = service.refresh(session, refresh_token)
    except AuthenticationError as exc:
        raise unauthorized(invalid_token=True) from exc
    except RevocationUnavailableError as exc:
        raise _unavailable() from exc
    return _token_response(pair, response)


@router.post(f"{PREFIX}/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(principal: CurrentPrincipal, service: AuthServiceDep, response: Response) -> None:
    """Revoke this access token and every token of its login session; clear the cookie."""
    try:
        service.logout(principal.claims)
    except RevocationUnavailableError as exc:
        raise _unavailable() from exc
    _clear_refresh_cookie(response)


@router.get(f"{PREFIX}/me", response_model=MeResponse)
def me(principal: CurrentPrincipal) -> MeResponse:
    user = principal.user
    return MeResponse(
        id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        display_name=user.display_name,
        is_internal=user.is_internal,
    )
