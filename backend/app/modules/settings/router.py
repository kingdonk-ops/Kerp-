"""Settings API, mounted at ``/api/v1/settings`` (T-0044, diff §4.4).

* ``GET /deployment``: any signed-in user (field clients need the offline window, the grace
  and the clock-skew limit; sync reads them through the service).
* ``PATCH /deployment``: ``system_admin`` only (else 403); the flag is the one the request
  session was bound with at authentication, never from the request. Invalid values 422.

No endpoint takes or returns a tenant id.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.core.auth.access import CallerIsSystemAdmin
from app.core.auth.deps import CurrentPrincipal
from app.core.db import DbSession
from app.modules.settings import service
from app.modules.settings.schemas import DeploymentSettingsRead, DeploymentSettingsUpdate

router = APIRouter(tags=["settings"])


@router.get("/deployment", response_model=DeploymentSettingsRead)
def read_deployment_settings(
    _principal: CurrentPrincipal, session: DbSession
) -> DeploymentSettingsRead:
    return DeploymentSettingsRead.model_validate(service.get_settings(session))


@router.patch("/deployment", response_model=DeploymentSettingsRead)
def update_deployment_settings(
    body: DeploymentSettingsUpdate,
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> DeploymentSettingsRead:
    if not is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    try:
        values = service.update_settings(session, body, actor_id=principal.user_id)
    except service.InvalidSettingsError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except service.ConcurrentUpdateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return DeploymentSettingsRead.model_validate(values)
