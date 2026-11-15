"""Device registry API (T-0043, diff §4.3): enrol, list, revoke, wipe confirmation.

* ``POST /api/v1/devices/enrol`` (bearer): the signed-in field client registers its
  ``device_id``; the login session is bound to it. ``201`` new, ``200`` known. Another
  account's ``device_id`` (or a session already bound to another device) is ``409``; a
  revoked device ``401 DEVICE_REVOKED``.
* ``GET /api/v1/devices`` (bearer): the caller's own devices.
* ``GET /api/v1/users/{user_id}/devices`` (bearer): a user's devices, for the user, a
  ``system_admin``, or a PER-09 of a project the user belongs to.
* ``POST /api/v1/devices/{device_id}/revoke`` (bearer): the same three may revoke (user
  decision Q4a); a member of a shared project without PER-09 gets ``403``, anyone else
  ``404``. Every session of the device dies at once. A PER-09 revoking the device of a user
  who also belongs to other projects must confirm with ``confirm_cross_project_wipe``
  (else ``409``; planner mitigation for Q4a). Audited on ``device:<device_id>`` with the
  actor, the role used (``self`` / ``system_admin`` / ``per_09`` + projects) and whether
  the warning was confirmed.
* ``POST /api/v1/devices/{device_id}/wipe-confirmed`` (no bearer: the device's sessions
  are dead): the revoked client reports its wipe. ``204``; an unknown device ``404``, an
  active one ``409``. The device is named only by its ``device_id``, a random id the
  client generated; the call changes nothing but the wipe time of a revoked device.

Authorisation lives here; the registry itself is ``devices.py``. The caller's
``system_admin`` flag is the one bound at authentication (never from the request).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth import devices
from app.core.auth import service as users
from app.core.auth.access import (
    CallerGrants,
    CallerIsSystemAdmin,
    Grant,
    Persona,
    load_grants,
    user_has_projects_beyond,
)
from app.core.auth.deps import CurrentPrincipal
from app.core.auth.models import DEVICE_REVOKED, Device
from app.core.auth.runtime import AuthRuntime, get_auth_runtime
from app.core.auth.schemas import (
    DeviceEnrolRequest,
    DeviceRead,
    DeviceRevoked,
    DeviceRevokeRequest,
)
from app.core.db import Database, DbSession
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_tenancy
from app.core.tenancy.scope import OutOfScopeError

PREFIX: Final = "/api/v1/devices"
CROSS_PROJECT_CODE: Final = "cross_project_wipe_confirmation_required"

router = APIRouter(tags=["devices"])

RuntimeDep = Annotated[AuthRuntime, Depends(get_auth_runtime)]
Role = Literal["self", "system_admin", "per_09"]


@dataclass(frozen=True, slots=True)
class Authority:
    """Why the caller may act on a user's devices: the role audited with a revoke."""

    role: Role
    project_ids: frozenset[uuid.UUID] = frozenset()  # PER-09: the shared projects used


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


def authority_over(
    session: Session,
    *,
    caller_id: uuid.UUID,
    tenant_id: uuid.UUID,
    grants: list[Grant],
    is_system_admin: bool,
    owner_id: uuid.UUID,
) -> Authority:
    """The caller's authority over ``owner_id``'s devices (diff §4.3, Q4a).

    The user themselves, a ``system_admin``, or a PER-09 of a project the owner belongs to
    (visible to the caller: memberships of the caller's own projects). A member of a shared
    project without PER-09 is ``403``; anyone else ``OutOfScopeError`` (404).
    """
    if owner_id == caller_id:
        return Authority("self")
    if is_system_admin:
        return Authority("system_admin")
    owner_projects = {g.project_id for g in load_grants(session, tenant_id, owner_id)}
    administered = {g.project_id for g in grants if g.persona is Persona.PROJECT_ADMINISTRATOR}
    shared_admin = owner_projects & administered
    if shared_admin:
        return Authority("per_09", frozenset(shared_admin))
    if owner_projects & {g.project_id for g in grants}:
        raise _forbidden()
    raise OutOfScopeError("not found")


def _device_or_404(session: Session, device_id: uuid.UUID) -> Device:
    device = devices.find_device(session, device_id)
    if device is None:
        raise OutOfScopeError("device not found")
    return device


def _read(device: Device) -> DeviceRead:
    return DeviceRead.model_validate(device)


@router.post(
    f"{PREFIX}/enrol",
    response_model=DeviceRead,
    responses={
        status.HTTP_201_CREATED: {"description": "Enrolled", "model": DeviceRead},
        status.HTTP_409_CONFLICT: {"description": "Another account's device, or session"},
    },
)
def enrol(
    body: DeviceEnrolRequest,
    principal: CurrentPrincipal,
    session: DbSession,
    runtime: RuntimeDep,
    response: Response,
) -> DeviceRead:
    """Register this field device for the signed-in user and bind the session to it."""
    try:
        device, created = devices.enrol(
            session,
            runtime.store,
            user_id=principal.user_id,
            session_id=principal.claims.session_id,
            session_ttl=runtime.tokens.ttl["refresh"],
            device_id=body.device_id,
            platform=body.platform,
            os_version=body.os_version,
            app_version=body.app_version,
        )
    except devices.DeviceConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if created:
        payload = {
            "action": "enrol",
            "device_id": str(device.device_id),
            "user_id": str(device.user_id),
            "platform": device.platform,
            "os_version": device.os_version,
            "app_version": device.app_version,
        }
        audit.append(
            session,
            f"device:{device.device_id}",
            payload,
            actor_id=principal.user_id,
            source="api",
            device_id=device.device_id,
        )
        response.status_code = status.HTTP_201_CREATED
    return _read(device)


@router.get(PREFIX, response_model=list[DeviceRead])
def my_devices(principal: CurrentPrincipal, session: DbSession) -> list[DeviceRead]:
    """The caller's own devices with their last-seen and last-sync times."""
    return [_read(d) for d in devices.devices_of_user(session, principal.user_id)]


@router.get("/api/v1/users/{user_id}/devices", response_model=list[DeviceRead])
def user_devices(
    user_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> list[DeviceRead]:
    """A user's devices (the user, a ``system_admin``, or a PER-09 of a shared project)."""
    authority_over(
        session,
        caller_id=principal.user_id,
        tenant_id=principal.tenant_id,
        grants=grants,
        is_system_admin=is_admin,
        owner_id=user_id,
    )
    users.get_user(session, user_id)  # 404 for an unknown account (System admins too)
    return [_read(d) for d in devices.devices_of_user(session, user_id)]


@router.post(
    f"{PREFIX}/{{device_id}}/revoke",
    response_model=DeviceRevoked,
    responses={
        status.HTTP_403_FORBIDDEN: {"description": "A shared project, but no PER-09 on it"},
        status.HTTP_404_NOT_FOUND: {"description": "Unknown device, or not the caller's to see"},
        status.HTTP_409_CONFLICT: {
            "description": "PER-09 revoke that also wipes other projects' unsynced work: "
            "repeat with `confirm_cross_project_wipe: true`"
        },
    },
)
def revoke(
    device_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
    runtime: RuntimeDep,
    body: Annotated[DeviceRevokeRequest | None, Body()] = None,
) -> DeviceRevoked:
    """Revoke a device: its sessions die now; on its next call it is told to wipe itself."""
    confirm = body.confirm_cross_project_wipe if body is not None else False
    device = _device_or_404(session, device_id)
    authority = authority_over(
        session,
        caller_id=principal.user_id,
        tenant_id=principal.tenant_id,
        grants=grants,
        is_system_admin=is_admin,
        owner_id=device.user_id,
    )
    already = device.status == DEVICE_REVOKED
    warn = False
    if authority.role == "per_09" and not already:
        database: Database = request.app.state.db
        warn = user_has_projects_beyond(
            database, principal.tenant_id, device.user_id, authority.project_ids
        )
        if warn and not confirm:
            last_sync = device.last_sync_at.isoformat() if device.last_sync_at else None
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": CROSS_PROJECT_CODE,
                    "cross_project_warning": True,
                    "last_sync_at": last_sync,
                    "message": (
                        "The device's user also works on other projects: revoking wipes the "
                        "device, including unsynced work of those projects. Confirm with "
                        "confirm_cross_project_wipe."
                    ),
                },
            )
    ttl = runtime.tokens.ttl["refresh"]
    if devices.mark_revoked(session, device, actor_id=principal.user_id):
        payload = {
            "action": "revoke",
            "device_id": str(device.device_id),
            "user_id": str(device.user_id),
            "role": authority.role,
            "project_ids": sorted(str(p) for p in authority.project_ids),
            "cross_project_warning": warn,
            "warning_confirmed": warn and confirm,
            "sessions_revoked": runtime.store.device_session_count(device.device_id),
        }
        audit.append(
            session,
            f"device:{device.device_id}",
            payload,
            actor_id=principal.user_id,
            source="api",
        )
    # Commit the revoke before killing the sessions: a failed commit must not tell a device
    # that is still active to wipe itself. Should the kill fail after the commit, every session
    # bound to the device is refused anyway (``resolve_request_device`` re-checks the row).
    # Revoking again changes nothing in the row; any session still bound is killed again.
    session.commit()
    runtime.store.revoke_device(device.device_id, ttl)
    return DeviceRevoked.model_validate(
        {**_read(device).model_dump(), "role": authority.role, "cross_project_warning": warn}
    )


@router.post(
    f"{PREFIX}/{{device_id}}/wipe-confirmed",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Unknown device"},
        status.HTTP_409_CONFLICT: {"description": "The device is not revoked"},
    },
)
def wipe_confirmed(device_id: uuid.UUID, session: DbSession, runtime: RuntimeDep) -> Response:
    """The revoked device confirms it wiped its local store (records, outbox, media, keys)."""
    tenant = runtime.settings.default_tenant_id
    if tenant is None:
        raise OutOfScopeError("no tenant")
    bind_tenancy(session, TenancyContext(tenant_id=tenant))
    device = _device_or_404(session, device_id)
    try:
        recorded = devices.confirm_wipe(session, device)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Device is not revoked") from exc
    if recorded:
        audit.append(
            session,
            f"device:{device.device_id}",
            {
                "action": "wipe_confirmed",
                "device_id": str(device.device_id),
                "user_id": str(device.user_id),
            },
            source="api",
            device_id=device.device_id,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
