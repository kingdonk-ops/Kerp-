"""Auth endpoints: login, refresh, logout, me, JWKS (T-0008); magic link + PIN (T-0009).

The refresh token travels in the ``httpOnly`` refresh cookie (T-0037, diff §4.2, TD-203):
set by every endpoint that issues tokens, read by ``/refresh`` and ``/pin/unlock`` behind an
Origin check, cleared by ``/logout``.

**Native-app body transport (T-0043, TD-203).** A request whose ``Origin`` is on the
deployment's explicit allow-list ``KERP_NATIVE_APP_ORIGINS`` (the Capacitor Android app) gets
the refresh token in the JSON body instead (``refresh_token``; no cookie) and sends it back
in the body of ``/refresh`` and ``/pin/unlock``. The choice depends on the ``Origin`` header
only: never on the client-reported device platform or a request flag. Every other request,
including one without ``Origin`` or from the web origin, gets the cookie transport; a body
token from such a request is ignored. The web origin can never be on the list (startup
check), and a request from the API's own origin never gets the body transport either.

Every token endpoint also reads ``X-KERP-Device-Id``: a revoked device is answered ``401``
with ``code: DEVICE_REVOKED`` (diff §4.3). Sign-in (login, magic-link redeem) checks the
credentials first, so with wrong credentials every device state gets the same ``401``
(T-0080, TD-291).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Final

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Cookie,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth import impersonation
from app.core.auth.deps import CurrentPrincipal, get_auth_service, unauthorized
from app.core.auth.device_signal import DEVICE_HEADER, request_device_id
from app.core.auth.lockout import LockedOutError
from app.core.auth.post_auth import invite_reference
from app.core.auth.providers import PasswordCredentials
from app.core.auth.runtime import AuthRuntime, get_auth_runtime
from app.core.auth.schemas import (
    ImpersonationRefusal,
    JsonWebKeySet,
    LoginRequest,
    MagicLinkRedeemRequest,
    MagicLinkRequest,
    MeResponse,
    PinUnlockRequest,
    RefreshRequest,
    TokenResponse,
)
from app.core.auth.service import AuthenticationError, AuthService, TokenPair
from app.core.config import Settings
from app.core.db import Database, DbSession

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
RuntimeDep = Annotated[AuthRuntime, Depends(get_auth_runtime)]


def own_origin(request: Request) -> str:
    """The API's own origin as this request sees it."""
    return f"{request.url.scheme}://{request.url.netloc}".lower()


def native_app_origin(request: Request, settings: Settings) -> bool:
    """Whether the request comes from an allow-listed native app (``Origin`` header only).

    Never the web origin or the API's own origin, even if misconfigured onto the list.
    """
    origin = request.headers.get("origin")
    if not origin or origin not in settings.native_app_origins:
        return False
    return origin not in (settings.web_origin, own_origin(request))


def set_refresh_cookie(response: Response, pair: TokenPair) -> None:
    """The web SPA's refresh cookie (``httpOnly``, ``Secure``, ``SameSite=Strict``, auth
    paths only), ``Max-Age`` = what is left of the session."""
    refresh = pair.refresh.claims
    response.set_cookie(
        REFRESH_COOKIE,
        pair.refresh.token,
        max_age=int((refresh.expires_at - refresh.issued_at).total_seconds()),
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )


def token_response(pair: TokenPair, response: Response, *, native: bool) -> TokenResponse:
    """The token response: refresh token in the cookie, or in the body for a native app."""
    # RFC 6749 §5.1: token responses must not be cached.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    access, refresh = pair.access.claims, pair.refresh.claims
    refresh_seconds = int((refresh.expires_at - refresh.issued_at).total_seconds())
    if not native:
        set_refresh_cookie(response, pair)
    return TokenResponse(
        access_token=pair.access.token,
        expires_in=int((access.expires_at - access.issued_at).total_seconds()),
        refresh_expires_in=refresh_seconds,
        session_expires_at=refresh.expires_at,
        refresh_token=pair.refresh.token if native else None,
        joined_project_id=pair.invite.joined_project_id,
        invite_error=pair.invite.invite_error,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, secure=True, httponly=True, samesite="strict"
    )


@dataclass(frozen=True, slots=True)
class RefreshCredential:
    """The presented refresh token and whether it came by the native body transport."""

    token: str
    native: bool


def _refresh_credential(
    request: Request, runtime: AuthRuntime, body_token: str | None, cookie: str | None
) -> RefreshCredential:
    """The refresh token of a request: the body of an allow-listed native app, else the
    cookie of a same-origin request (CSRF: ``SameSite=Strict`` + Origin).

    For the cookie, the ``Origin`` header must be the web SPA's origin (``KERP_WEB_ORIGIN``,
    else the API's own origin); a missing or foreign one is ``403`` before the cookie is
    looked at. A body token from anywhere but a native app is ignored.
    """
    if native_app_origin(request, runtime.settings):
        if not body_token:
            raise unauthorized(invalid_token=True)
        return RefreshCredential(body_token, native=True)
    allowed = runtime.settings.web_origin or own_origin(request)
    if request.headers.get("origin") != allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, CROSS_ORIGIN)
    if not cookie or len(cookie) > REFRESH_COOKIE_MAX_LENGTH:
        raise unauthorized(invalid_token=True)
    return RefreshCredential(cookie, native=False)


CookieToken = Annotated[
    str | None,
    Cookie(alias=REFRESH_COOKIE, description="The refresh token (httpOnly cookie, never JS)"),
]

_COOKIE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_403_FORBIDDEN: {
        "description": "`Origin` is neither the web app's origin nor an allow-listed native app"
    }
}
_DEVICE_REVOKED_RESPONSE: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_401_UNAUTHORIZED: {
        "description": (
            f"Not authenticated; with `code: DEVICE_REVOKED` when `{DEVICE_HEADER}` (or the "
            "session's device) is revoked: wipe the device's local data. Sign-in answers "
            "`DEVICE_REVOKED` only after the credentials are right"
        )
    }
}


@router.get(JWKS_PATH, response_model=JsonWebKeySet, response_model_exclude_none=True)
def jwks(runtime: RuntimeDep, response: Response) -> JsonWebKeySet:
    """Public keys that verify KERP tokens (RFC 7517). Contains no private material."""
    response.headers["Cache-Control"] = f"public, max-age={JWKS_MAX_AGE}"
    return JsonWebKeySet.model_validate(runtime.keys.jwks())


def _locked(exc: LockedOutError) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        TOO_MANY_ATTEMPTS,
        headers={"Retry-After": str(exc.retry_after)},
    )


_PIN_RESPONSE: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_401_UNAUTHORIZED: {
        "description": (
            "Not authenticated. With `code`: `PIN_REQUIRED` (refresh: idle too long, unlock "
            "with the PIN), `PIN_INVALID` (unlock: wrong PIN), `PIN_EXHAUSTED` (unlock: the "
            "10th wrong PIN, sign in again on this device), `DEVICE_REVOKED` (wipe the device)"
        )
    }
}

_LOCKED_RESPONSE: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_429_TOO_MANY_REQUESTS: {
        "description": "Locked after repeated failures; retry after `Retry-After` seconds"
    }
}


_TOKEN_ROUTE: Final[dict[str, Any]] = {
    "response_model": TokenResponse,
    "response_model_exclude_none": True,
}


@router.post(
    f"{PREFIX}/login", **_TOKEN_ROUTE, responses={**_LOCKED_RESPONSE, **_DEVICE_REVOKED_RESPONSE}
)
def login(
    body: LoginRequest,
    session: DbSession,
    service: AuthServiceDep,
    runtime: RuntimeDep,
    request: Request,
    response: Response,
) -> TokenResponse:
    """Exchange email + password for an access token (15 min) and a refresh token."""
    credentials = PasswordCredentials(email=body.email, password=body.password)
    # Behind a proxy, uvicorn sets this from X-Forwarded-For only for peers listed in
    # FORWARDED_ALLOW_IPS; otherwise it is the direct peer (the proxy itself).
    client = request.client.host if request.client else None
    try:
        pair = service.login(
            session,
            credentials=credentials,
            client=client,
            device_id=request_device_id(request),
            invite_ref=invite_reference(body.invite_token),
        )
    except AuthenticationError as exc:
        raise unauthorized() from exc
    except LockedOutError as exc:
        raise _locked(exc) from exc
    return token_response(pair, response, native=native_app_origin(request, runtime.settings))


@router.post(
    f"{PREFIX}/magic-link",
    status_code=status.HTTP_202_ACCEPTED,
    responses=_LOCKED_RESPONSE,
)
def request_magic_link(
    body: MagicLinkRequest, request: Request, tasks: BackgroundTasks, service: AuthServiceDep
) -> None:
    """Send a one-time sign-in link (15 min) to a Field Worker's e-mail (PER-14, diff §4.2).

    Always ``202`` (unknown, inactive, non-Field-Worker and other-tenant addresses included),
    so the answer reveals no accounts or personas. The request only counts the attempt; the
    lookup, the eligibility check, the link and its e-mail run after the response (T-0057).
    With `invite_token` (a pending invite naming PER-14 also makes its invitee eligible) the
    invite is accepted when the link is redeemed; the link itself never carries it (T-0052).
    Requests are limited per client address (any e-mail), then per address: `429` with
    `Retry-After` (T-0345).
    """
    database = _database(request)
    client = request.client.host if request.client else None
    try:
        job = service.request_magic_link(
            email=body.email, invite_ref=invite_reference(body.invite_token), client=client
        )
    except LockedOutError as exc:
        raise _locked(exc) from exc
    if job is not None:
        tasks.add_task(service.issue_magic_link, database, job)


def _database(request: Request) -> Database:
    database: Database | None = getattr(request.app.state, "db", None)
    if database is None:
        raise RuntimeError("database not configured (set KERP_DATABASE_URL)")
    return database


@router.post(f"{PREFIX}/magic-link/redeem", **_TOKEN_ROUTE, responses=_DEVICE_REVOKED_RESPONSE)
def redeem_magic_link(
    body: MagicLinkRedeemRequest,
    session: DbSession,
    service: AuthServiceDep,
    runtime: RuntimeDep,
    request: Request,
    response: Response,
) -> TokenResponse:
    """Spend a magic link: start a session. On an enrolled device (`X-KERP-Device-Id`) it is
    bound to that device at once; the device's PIN stays as it is.
    """
    try:
        pair = service.redeem_magic_link(
            session, token=body.token, device_id=request_device_id(request)
        )
    except AuthenticationError as exc:
        raise unauthorized(invalid_token=True) from exc
    return token_response(pair, response, native=native_app_origin(request, runtime.settings))


@router.post(
    f"{PREFIX}/pin/unlock",
    **_TOKEN_ROUTE,
    responses={**_LOCKED_RESPONSE, **_COOKIE_RESPONSES, **_PIN_RESPONSE},
)
def pin_unlock(
    body: PinUnlockRequest,
    session: DbSession,
    service: AuthServiceDep,
    runtime: RuntimeDep,
    request: Request,
    response: Response,
    kerp_refresh: CookieToken = None,
) -> TokenResponse:
    """Renew a session of a device with a PIN: the device's refresh token + the PIN.

    Wrong PINs lock the device's PIN unlock progressively (offline failures reported in
    `failed_offline` count too); the 10th wrong PIN ends PIN unlock on the device until the
    next sign-in there. Nothing is revoked and nothing is wiped.
    """
    credential = _refresh_credential(request, runtime, body.refresh_token, kerp_refresh)
    if body.refresh_token is not None and not credential.native:
        # Only the native app's body carries a refresh token (TD-203); refused as before.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "refresh_token is accepted from the native app only",
        )
    try:
        pair = service.unlock(
            session,
            refresh_token=credential.token,
            pin=body.pin,
            device_id=request_device_id(request),
            failed_offline=body.failed_offline,
        )
    except AuthenticationError as exc:
        raise unauthorized(invalid_token=True) from exc
    except LockedOutError as exc:
        raise _locked(exc) from exc
    return token_response(pair, response, native=credential.native)


@router.post(
    f"{PREFIX}/refresh",
    **_TOKEN_ROUTE,
    responses={**_COOKIE_RESPONSES, **_PIN_RESPONSE},
)
def refresh(
    session: DbSession,
    service: AuthServiceDep,
    runtime: RuntimeDep,
    request: Request,
    response: Response,
    body: Annotated[RefreshRequest | None, Body()] = None,
    kerp_refresh: CookieToken = None,
) -> TokenResponse:
    """Rotate: the refresh token is spent and a new pair is issued. Reuse revokes the session,
    except a retry within 30 seconds of the token being spent (a lost answer): a plain `401`
    that leaves the session alive (T-0345).

    The new refresh token keeps the session's end (sign-in + 12 h); after it, sign in again.
    The web SPA sends the cookie; an allow-listed native app sends ``refresh_token``. A
    session of a device with a PIN that was idle too long gets `401 {code: PIN_REQUIRED}`
    (the token is not spent: unlock with it and the PIN).
    """
    body_token = body.refresh_token if body is not None else None
    credential = _refresh_credential(request, runtime, body_token, kerp_refresh)
    try:
        pair = service.refresh(session, credential.token, device_id=request_device_id(request))
    except AuthenticationError as exc:
        raise unauthorized(invalid_token=True) from exc
    return token_response(pair, response, native=credential.native)


@router.post(f"{PREFIX}/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    principal: CurrentPrincipal,
    service: AuthServiceDep,
    runtime: RuntimeDep,
    session: DbSession,
    response: Response,
) -> None:
    """Revoke this access token and every token of its login session; clear the cookie.

    With an impersonation token it ends the impersonation only, like
    `/impersonation/exit`: the refresh cookie is the admin's own session and stays."""
    if principal.is_impersonation:
        _exit_impersonation(principal, session, runtime)
        return
    service.logout(principal.claims)
    _clear_refresh_cookie(response)


def _exit_impersonation(
    principal: CurrentPrincipal, session: Session, runtime: AuthRuntime
) -> None:
    claims = principal.claims
    impersonation.end(
        session, claims.session_id, impersonation.EXIT, actor_id=claims.impersonator_id
    )
    runtime.store.revoke_token(claims.jti, claims.remaining())
    runtime.store.revoke_session(claims.session_id, claims.remaining())


@router.post(
    f"{PREFIX}/impersonation/exit",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_403_FORBIDDEN: {
            "model": ImpersonationRefusal,
            "description": "`code: NOT_IMPERSONATING`: the bearer is not an impersonation token",
        }
    },
)
def exit_impersonation(
    principal: CurrentPrincipal, runtime: RuntimeDep, session: DbSession
) -> Response:
    """End the caller's impersonation (T-0277, ADR 0015 §3): its token and session are revoked
    at once and the target is told by e-mail. The admin's own access token and refresh cookie
    were never touched, so the SPA resumes the admin's session. Expiry needs no call."""
    if not principal.is_impersonation:
        refusal = ImpersonationRefusal(detail="Not impersonating", code="NOT_IMPERSONATING")
        return JSONResponse(refusal.model_dump(), status_code=status.HTTP_403_FORBIDDEN)
    _exit_impersonation(principal, session, runtime)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
