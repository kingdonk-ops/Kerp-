"""Auth dependencies: credential -> tenant -> RLS bind -> current user.

Adapted from Inspection-ERP ``core/auth/deps.py`` (ADR 0001). The shape is kept: the
tenant comes exclusively from the verified credential, never from request parameters,
and the request session is bound to it before any query runs. Here that happens in
``get_db`` (via ``get_tenancy_context`` and ``bearer.get_bearer``), so by the time
``get_current_user`` loads the user, RLS and the ORM scope already confine the session to
the token's tenant. Personas, capability guards and the multi-project scope: ``access.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.core.auth.bearer import BearerResult, get_bearer
from app.core.auth.models import User
from app.core.auth.runtime import AuthRuntime, get_auth_runtime
from app.core.auth.service import AuthenticationError, AuthService, load_active_user
from app.core.auth.tokens import TokenClaims
from app.core.db import DbSession
from app.core.tenancy.context import current_tenant_id, current_user_id


def unauthorized(*, invalid_token: bool = False) -> HTTPException:
    """401 with the RFC 6750 challenge. The detail never says why."""
    challenge = 'Bearer error="invalid_token"' if invalid_token else "Bearer"
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Not authenticated",
        headers={"WWW-Authenticate": challenge},
    )


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller of a request."""

    user: User
    claims: TokenClaims

    @property
    def user_id(self) -> uuid.UUID:
        return self.claims.user_id

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.claims.tenant_id


def require_claims(bearer: Annotated[BearerResult, Depends(get_bearer)]) -> TokenClaims:
    """The verified, unrevoked access token's claims, or 401 (before any database work)."""
    if bearer.claims is None:
        raise unauthorized(invalid_token=bearer.presented)
    return bearer.claims


def get_current_user(
    claims: Annotated[TokenClaims, Depends(require_claims)],
    session: DbSession,
) -> Principal:
    """The token's active user, or 401. ``session`` is already bound to the token's tenant."""
    try:
        user = load_active_user(session, claims)
    except AuthenticationError as exc:
        raise unauthorized(invalid_token=True) from exc
    return Principal(user=user, claims=claims)


async def get_principal(
    principal: Annotated[Principal, Depends(get_current_user)],
) -> Principal:
    """Require authentication; also publish the tenant/user in the request's context vars.

    Async on purpose: it runs in the request's own context, so the values are visible to
    the endpoint (sync dependencies run in a copied context and their writes are lost).
    """
    current_tenant_id.set(principal.tenant_id)
    current_user_id.set(principal.user_id)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def get_auth_service(
    runtime: Annotated[AuthRuntime, Depends(get_auth_runtime)],
) -> AuthService:
    return AuthService(runtime)
