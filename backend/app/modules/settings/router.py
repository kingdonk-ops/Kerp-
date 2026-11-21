"""Settings API, mounted at ``/api/v1/settings`` (T-0044, diff §4.4).

* ``GET /deployment``: any signed-in user (field clients need the offline window, the grace
  and the clock-skew limit; sync reads them through the service).
* ``PATCH /deployment``: ``system_admin`` only (else 403); the flag is the one the request
  session was bound with at authentication, never from the request. Invalid values 422.

* ``GET /registry`` and ``PATCH /registry/{key}`` (T-0230, diff §4.7): ``system_admin`` only.
  Every declared entry with its value, default, bounds and category; a change out of bounds
  or over the deployment cap is 422 (never clamped), an undeclared key 404.
* ``GET /deployment-config``: ``system_admin`` only; the read-only deployment configuration
  (environment), never a secret.
* ``GET /client`` (T-0231, diff §4.7 client bootstrap): any signed-in user; the effective
  values of the ``client_visible`` entries only. ``Cache-Control: no-store`` and a strong
  ``ETag`` over the values; a matching ``If-None-Match`` is 304 with no body.

No endpoint takes or returns a tenant id.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from app.core.auth.access import CallerIsSystemAdmin
from app.core.auth.deps import CurrentPrincipal
from app.core.config import Settings
from app.core.db import DbSession
from app.modules.settings import deployment_config, service
from app.modules.settings.schemas import (
    ClientSettingsRead,
    DeploymentConfigRead,
    DeploymentSettingsRead,
    DeploymentSettingsUpdate,
    SettingEntryRead,
    SettingValueUpdate,
)

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


def _admin_config(request: Request, is_admin: bool) -> Settings:
    """The deployment configuration, for a System admin only (else 403)."""
    if not is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    config: Settings = request.app.state.settings
    return config


@router.get("/registry", response_model=list[SettingEntryRead])
def read_registry(
    request: Request,
    _principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> list[SettingEntryRead]:
    config = _admin_config(request, is_admin)
    entries = service.registry_entries(session, config=config)
    return [SettingEntryRead.model_validate(entry) for entry in entries]


@router.patch("/registry/{key}", response_model=SettingEntryRead)
def update_registry_entry(
    key: str,
    body: SettingValueUpdate,
    request: Request,
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> SettingEntryRead:
    config = _admin_config(request, is_admin)
    try:
        service.set_values(session, {key: body.value}, actor_id=principal.user_id, config=config)
    except service.UnknownSettingError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found") from exc
    except service.InvalidSettingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except service.ConcurrentUpdateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return SettingEntryRead.model_validate(service.registry_entry(session, key, config=config))


NO_STORE = "no-store"


def _etag(body: ClientSettingsRead) -> str:
    """A strong validator over the values (canonical JSON, so key order never matters)."""
    canonical = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return '"' + hashlib.sha256(canonical.encode()).hexdigest()[:32] + '"'


def _matches(if_none_match: str | None, etag: str) -> bool:
    """``If-None-Match`` (RFC 9110 §13.1.2): ``*`` or any listed tag, compared weakly."""
    if not if_none_match:
        return False
    tags = {tag.strip().removeprefix("W/") for tag in if_none_match.split(",")}
    return "*" in tags or etag in tags


@router.get(
    "/client",
    response_model=ClientSettingsRead,
    responses={304: {"description": "Not modified: the If-None-Match tag is still current"}},
)
def read_client_settings(
    request: Request,
    response: Response,
    _principal: CurrentPrincipal,
    session: DbSession,
    if_none_match: Annotated[str | None, Header()] = None,
) -> ClientSettingsRead | Response:
    config: Settings = request.app.state.settings
    body = ClientSettingsRead(values=service.client_values(session, config=config))
    etag = _etag(body)
    headers = {"ETag": etag, "Cache-Control": NO_STORE}
    if _matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return body


@router.get("/deployment-config", response_model=DeploymentConfigRead)
def read_deployment_config(
    request: Request, _principal: CurrentPrincipal, is_admin: CallerIsSystemAdmin
) -> DeploymentConfigRead:
    return deployment_config.read(_admin_config(request, is_admin))
