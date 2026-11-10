"""Auth endpoints: login, refresh, logout, me, and the JWKS document (T-0008)."""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.auth.bearer import AUTH_UNAVAILABLE
from app.core.auth.deps import CurrentPrincipal, get_auth_service, unauthorized
from app.core.auth.providers import PasswordCredentials
from app.core.auth.revocation import RevocationUnavailableError
from app.core.auth.runtime import AuthRuntime, get_auth_runtime
from app.core.auth.schemas import (
    JsonWebKeySet,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    TokenResponse,
)
from app.core.auth.service import AuthenticationError, AuthService, TokenPair
from app.core.db import DbSession

PREFIX: Final = "/api/v1/auth"
JWKS_PATH: Final = "/.well-known/jwks.json"
JWKS_MAX_AGE: Final = 300

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


@router.post(f"{PREFIX}/login", response_model=TokenResponse)
def login(
    body: LoginRequest, session: DbSession, service: AuthServiceDep, response: Response
) -> TokenResponse:
    """Exchange email + password for an access token (15 min) and a refresh token."""
    credentials = PasswordCredentials(email=body.email, password=body.password)
    try:
        pair = service.login(session, tenant_id=body.tenant_id, credentials=credentials)
    except AuthenticationError as exc:
        raise unauthorized() from exc
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
