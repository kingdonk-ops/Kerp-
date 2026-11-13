"""Auth endpoints: login, refresh, logout, me, JWKS (T-0008); magic link + PIN (T-0009)."""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Response, status

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
    RefreshRequest,
    TokenResponse,
)
from app.core.auth.service import AuthenticationError, AuthService, TokenPair
from app.core.db import DbSession

PREFIX: Final = "/api/v1/auth"
JWKS_PATH: Final = "/.well-known/jwks.json"
JWKS_MAX_AGE: Final = 300
TOO_MANY_ATTEMPTS: Final = "Too many attempts"

router = APIRouter(tags=["auth"])

AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def _token_response(pair: TokenPair, response: Response) -> TokenResponse:
    # RFC 6749 §5.1: token responses must not be cached.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    access, refresh = pair.access.claims, pair.refresh.claims
    return TokenResponse(
        access_token=pair.access.token,
        refresh_token=pair.refresh.token,
        expires_in=int((access.expires_at - access.issued_at).total_seconds()),
        refresh_expires_in=int((refresh.expires_at - refresh.issued_at).total_seconds()),
    )


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
    body: LoginRequest, session: DbSession, service: AuthServiceDep, response: Response
) -> TokenResponse:
    """Exchange email + password for an access token (15 min) and a refresh token."""
    credentials = PasswordCredentials(email=body.email, password=body.password)
    try:
        pair = service.login(session, tenant_id=body.tenant_id, credentials=credentials)
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
        service.request_magic_link(session, tenant_id=body.tenant_id, email=body.email)
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


@router.post(f"{PREFIX}/pin/unlock", response_model=TokenResponse, responses=_LOCKED_RESPONSE)
def pin_unlock(
    body: PinUnlockRequest, session: DbSession, service: AuthServiceDep, response: Response
) -> TokenResponse:
    """Unlock a PIN-gated session: the device's refresh token + the PIN rotate the tokens.

    Wrong PINs lock the session progressively; the 10th wrong PIN revokes it.
    """
    try:
        pair = service.unlock(session, refresh_token=body.refresh_token, pin=body.pin)
    except AuthenticationError as exc:
        raise unauthorized(invalid_token=True) from exc
    except LockedOutError as exc:
        raise _locked(exc) from exc
    except RevocationUnavailableError as exc:
        raise _unavailable() from exc
    return _token_response(pair, response)


@router.post(f"{PREFIX}/refresh", response_model=TokenResponse)
def refresh(
    body: RefreshRequest, session: DbSession, service: AuthServiceDep, response: Response
) -> TokenResponse:
    """Rotate: the refresh token is spent and a new pair is issued. Reuse revokes the session."""
    try:
        pair = service.refresh(session, body.refresh_token)
    except AuthenticationError as exc:
        raise unauthorized(invalid_token=True) from exc
    except RevocationUnavailableError as exc:
        raise _unavailable() from exc
    return _token_response(pair, response)


@router.post(f"{PREFIX}/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(principal: CurrentPrincipal, service: AuthServiceDep) -> None:
    """Revoke this access token and every token of its login session."""
    try:
        service.logout(principal.claims)
    except RevocationUnavailableError as exc:
        raise _unavailable() from exc


@router.get(f"{PREFIX}/me", response_model=MeResponse)
def me(principal: CurrentPrincipal) -> MeResponse:
    user = principal.user
    return MeResponse(
        id=user.id, tenant_id=user.tenant_id, email=user.email, display_name=user.display_name
    )
