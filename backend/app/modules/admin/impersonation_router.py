"""Start a System admin impersonation (T-0277, ADR 0015 option (a)), under ``/api/v1/admin``.

``POST /users/{user_id}/impersonate`` ``{reason}``: ``system_admin`` only. Refused, in this
order: a caller who is itself impersonating (``403 IMPERSONATION_ACTION_FORBIDDEN``, no
nesting), anyone else without ``system_admin`` (``403``), oneself (``422
CANNOT_IMPERSONATE_SELF``), an unknown or deleted user (``404``), a deactivated one (``422
USER_DEACTIVATED``) and a ``system_admin`` (``403 CANNOT_IMPERSONATE_SYSTEM_ADMIN``). A blank
or longer than 500 characters ``reason`` is ``422``.

Targets holding ``create_projects`` (ADR 0015 §8) and PER-15 Client Representatives (§9, the
session is then read-only) may be impersonated. The session, its token and its ledger entry
are core auth's (``app.core.auth.impersonation``); its maximum length is the registry entry
``impersonation_max_minutes`` (``setting_defs.py``), capped by the admin session's end.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from app.core.auth import impersonation
from app.core.auth import service as auth_service
from app.core.auth.access import CallerIsSystemAdmin, system_admin_holders
from app.core.auth.deps import CurrentPrincipal, unauthorized
from app.core.auth.runtime import AuthRuntime, get_auth_runtime
from app.core.auth.schemas import (
    ImpersonationRefusal,
    ImpersonationRefusalCode,
    ImpersonationStarted,
    ImpersonationStartRequest,
)
from app.core.db import DbSession
from app.modules.admin.setting_defs import IMPERSONATION_MAX_MINUTES
from app.modules.settings import service as runtime_settings

router = APIRouter(tags=["admin"])

_REFUSALS: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_403_FORBIDDEN: {
        "model": ImpersonationRefusal,
        "description": (
            "Not a System admin (plain `detail`, no `code`); `IMPERSONATION_ACTION_FORBIDDEN`: "
            "the caller is impersonating; `CANNOT_IMPERSONATE_SYSTEM_ADMIN`: the target holds "
            "`system_admin`"
        ),
    },
    status.HTTP_404_NOT_FOUND: {"description": "No such user"},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "model": ImpersonationRefusal,
        "description": (
            "`CANNOT_IMPERSONATE_SELF`, `USER_DEACTIVATED`; or a validation error (blank or "
            "too long `reason`)"
        ),
    },
}


def _refused(status_code: int, code: ImpersonationRefusalCode, detail: str) -> JSONResponse:
    refusal = ImpersonationRefusal(detail=detail, code=code)
    return JSONResponse(refusal.model_dump(), status_code=status_code)


@router.post(
    "/users/{user_id}/impersonate",
    response_model=ImpersonationStarted,
    responses=_REFUSALS,
)
def impersonate(
    user_id: uuid.UUID,
    body: ImpersonationStartRequest,
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
    runtime: Annotated[AuthRuntime, Depends(get_auth_runtime)],
    request: Request,
    response: Response,
) -> ImpersonationStarted | JSONResponse:
    """Act as `user_id` for a short, audited session (ADR 0015). The answer carries the
    impersonation token; the caller's own access token and refresh cookie stay as they are.
    The session ends at `expires_at`, on `POST /api/v1/auth/impersonation/exit`, when the
    caller's own session ends or the caller loses `system_admin`; the user is then told by
    e-mail."""
    if principal.is_impersonation:
        return _refused(
            status.HTTP_403_FORBIDDEN,
            "IMPERSONATION_ACTION_FORBIDDEN",
            "Not allowed while impersonating",
        )
    if not is_admin:
        return JSONResponse({"detail": "Forbidden"}, status_code=status.HTTP_403_FORBIDDEN)
    if user_id == principal.user_id:
        return _refused(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "CANNOT_IMPERSONATE_SELF",
            "You cannot impersonate yourself",
        )
    target = auth_service.get_user(session, user_id)  # 404 for an unknown or deleted user
    if not target.is_active:
        return _refused(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "USER_DEACTIVATED", "The user is deactivated"
        )
    if system_admin_holders(session, principal.tenant_id, [user_id]):
        return _refused(
            status.HTTP_403_FORBIDDEN,
            "CANNOT_IMPERSONATE_SYSTEM_ADMIN",
            "A System admin cannot be impersonated",
        )
    max_minutes = runtime_settings.get_int(
        session, IMPERSONATION_MAX_MINUTES.key, config=runtime.settings
    )
    try:
        started = impersonation.start(
            session,
            runtime.tokens,
            admin=principal.claims,
            admin_device_id=principal.device_id,
            target_id=target.id,
            reason=body.reason,
            max_minutes=max_minutes,
            # Behind a proxy, uvicorn sets this from X-Forwarded-For only for trusted peers.
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except auth_service.AuthenticationError as exc:
        # The caller's password changed (or the account was deactivated) since the request
        # was authenticated: the caller's own token is dead too.
        raise unauthorized(invalid_token=True) from exc
    # A token response: never cached (RFC 6749 §5.1).
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    claims = started.token.claims
    return ImpersonationStarted(
        access_token=started.token.token,
        impersonation_session_id=started.session_id,
        expires_in=round((claims.expires_at - claims.issued_at).total_seconds()),
        expires_at=started.expires_at,
        target_user_id=target.id,
        target_display_name=target.display_name,
    )
