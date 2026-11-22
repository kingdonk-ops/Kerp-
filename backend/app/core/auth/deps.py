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
from typing import Annotated, Final

from fastapi import Depends, HTTPException, Request, status

from app.core.auth.bearer import BearerResult, get_bearer
from app.core.auth.device_signal import DeviceRevokedError, request_device_id
from app.core.auth.devices import DeviceConflictError, resolve_request_device
from app.core.auth.models import User
from app.core.auth.runtime import AuthRuntime, get_auth_runtime
from app.core.auth.service import AuthenticationError, AuthService, load_active_user
from app.core.auth.tokens import TokenClaims
from app.core.db import Database, DbSession
from app.core.tenancy.context import current_device_id, current_impersonator_id


def unauthorized(*, invalid_token: bool = False) -> HTTPException:
    """401 with the RFC 6750 challenge. The detail never says why."""
    challenge = 'Bearer error="invalid_token"' if invalid_token else "Bearer"
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Not authenticated",
        headers={"WWW-Authenticate": challenge},
    )


# ``request.state`` attribute holding the ``ImpersonationScope`` of an impersonation request,
# set by ``access.resolve_request_scope`` once the admin was re-checked and the target's
# grants loaded (T-0277). Without it an impersonation token authenticates nothing.
IMPERSONATION_STATE: Final = "kerp_impersonation"


@dataclass(frozen=True, slots=True)
class ImpersonationScope:
    """What ``resolve_request_scope`` established for an impersonation request (T-0277)."""

    tenant_id: uuid.UUID
    target_user_id: uuid.UUID
    session_id: uuid.UUID
    impersonator_user_id: uuid.UUID
    # The target holds a PER-15 (Client Representative) membership on some project: the
    # session is read-only (ADR 0015 §9; enforced by the guardrail, T-0278).
    read_only: bool


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller of a request.

    While a ``system_admin`` impersonates (T-0277, ADR 0015), ``user`` and ``user_id`` are the
    *target* (records are attributed to the target) and ``impersonator_user_id`` is the admin.
    """

    user: User
    claims: TokenClaims
    # The enrolled field device the request comes through (T-0043), else ``None``. Always
    # ``None`` while impersonating: an impersonation is never bound to a device.
    device_id: uuid.UUID | None = None
    # Computed per request from the target's memberships (ADR 0015 §9): ``True`` when the
    # target holds PER-15 on any project. Always ``False`` outside impersonation.
    impersonation_read_only: bool = False

    @property
    def user_id(self) -> uuid.UUID:
        return self.claims.user_id

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.claims.tenant_id

    @property
    def impersonator_user_id(self) -> uuid.UUID | None:
        """The ``system_admin`` acting as ``user`` (impersonation token), else ``None``."""
        return self.claims.impersonator_id

    @property
    def is_impersonation(self) -> bool:
        return self.claims.is_impersonation


def require_claims(bearer: Annotated[BearerResult, Depends(get_bearer)]) -> TokenClaims:
    """The verified, unrevoked access token's claims, or 401 (before any database work)."""
    if bearer.device_revoked:
        raise DeviceRevokedError("device revoked")
    if bearer.claims is None:
        raise unauthorized(invalid_token=bearer.presented)
    return bearer.claims


def get_current_user(
    request: Request,
    claims: Annotated[TokenClaims, Depends(require_claims)],
    session: DbSession,
    runtime: Annotated[AuthRuntime, Depends(get_auth_runtime)],
) -> Principal:
    """The token's active user, or 401. ``session`` is already bound to the token's tenant.

    Also resolves the enrolled device the request comes through (T-0043): the session's
    bound device, or the ``X-KERP-Device-Id`` header naming one of the user's devices. A
    revoked device is ``401`` with ``code: DEVICE_REVOKED``.

    The device is resolved first, in its own short unit of work, *before* the request session
    runs its first query (T-0210): once that session has queried, it holds a pooled connection
    until the request ends, and a second unit of work then waiting on the same pool deadlocks
    the pool under load (every request holds one connection and waits for another).

    An impersonation token (T-0277) authenticates only when ``resolve_request_scope`` has
    re-checked its admin and loaded the target's grants for this request
    (``IMPERSONATION_STATE``); else ``401``. It never resolves or touches a device.
    """
    if claims.is_impersonation:
        return _impersonation_principal(request, claims, session)
    device_id: uuid.UUID | None = None
    database: Database | None = getattr(request.app.state, "db", None)
    if database is not None:  # None: a test app whose session dependency is overridden
        try:
            device_id = resolve_request_device(
                database,
                runtime.store,
                tenant_id=claims.tenant_id,
                user_id=claims.user_id,
                session_id=claims.session_id,
                header_device_id=request_device_id(request),
                session_ttl=runtime.tokens.ttl["refresh"],
            )
        except DeviceConflictError as exc:
            raise unauthorized(invalid_token=True) from exc
    try:
        user = load_active_user(session, claims)
    except AuthenticationError as exc:
        raise unauthorized(invalid_token=True) from exc
    return Principal(user=user, claims=claims, device_id=device_id)


def _impersonation_principal(
    request: Request, claims: TokenClaims, session: DbSession
) -> Principal:
    scope = getattr(request.state, IMPERSONATION_STATE, None)
    if not isinstance(scope, ImpersonationScope) or (
        scope.tenant_id,
        scope.target_user_id,
        scope.session_id,
        scope.impersonator_user_id,
    ) != (claims.tenant_id, claims.user_id, claims.session_id, claims.impersonator_id):
        raise unauthorized(invalid_token=True)
    try:
        user = load_active_user(session, claims)
    except AuthenticationError as exc:
        raise unauthorized(invalid_token=True) from exc
    return Principal(user=user, claims=claims, impersonation_read_only=scope.read_only)


async def get_principal(
    principal: Annotated[Principal, Depends(get_current_user)],
) -> Principal:
    """Require authentication; also publish the device and, while impersonating, the
    impersonating admin in the request's context vars (the audit ledger records both).

    Async on purpose: it runs in the request's own context, so the value is visible to the
    endpoint (sync dependencies run in a copied context and their writes are lost).
    """
    current_device_id.set(principal.device_id)
    current_impersonator_id.set(principal.impersonator_user_id)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def get_auth_service(
    runtime: Annotated[AuthRuntime, Depends(get_auth_runtime)],
) -> AuthService:
    return AuthService(runtime)
