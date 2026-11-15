"""Device registry API (T-0043, diff §4.3): enrol, list, revoke, wipe confirmation.

* ``POST /api/v1/devices/enrol`` (bearer): the signed-in field client registers its
  ``device_id``; the login session is bound to it. ``201`` new, with the device's
  ``wipe_secret`` (issued once, T-0080); ``200`` known (``wipe_secret: null``). Another
  account's ``device_id`` (or a session already bound to another device) is ``409``; a
  revoked device ``401 DEVICE_REVOKED``. ``pin`` sets the device's field PIN once (T-0038,
  diff §4.2); a device that has one answers ``409 {code: PIN_ALREADY_SET}``. Binding a new
  session to the device (a sign-in there) clears its PIN failures.
* ``PUT /api/v1/devices/{device_id}/pin`` (bearer, from a session bound to the device): a new
  PIN (``204``); clears the PIN failures; audited as ``pin_changed`` (never the PIN). Not the
  caller's active device: ``404``; another session of the caller: ``403``.
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
  are dead): the revoked client reports its wipe with its ``wipe_secret`` (T-0080). ``204``
  for a revoked device with the right secret (``wipe_confirmed_at`` set once). Everything
  else (unknown id, active device, wrong or missing secret, a device enrolled without a
  secret) gets one uniform ``404 {"detail": "Not Found"}`` after comparable work, so the
  answer reveals no device status (TD-286, TD-291). Confirmations and refusals are audited
  on ``device:<device_id>``, never with the secret.

Authorisation lives here; the registry itself is ``devices.py``. The caller's
``system_admin`` flag is the one bound at authentication (never from the request).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
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
from app.core.auth.deps import CurrentPrincipal, get_auth_service
from app.core.auth.models import DEVICE_ACTIVE, DEVICE_REVOKED, Device
from app.core.auth.passwords import hash_password
from app.core.auth.runtime import AuthRuntime, get_auth_runtime
from app.core.auth.schemas import (
    DeviceEnrolled,
    DeviceEnrolRequest,
    DevicePinRequest,
    DeviceRead,
    DeviceRevoked,
    DeviceRevokeRequest,
    WipeConfirmRequest,
)
from app.core.db import Database, DbSession
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_tenancy
from app.core.tenancy.scope import OutOfScopeError

PREFIX: Final = "/api/v1/devices"
CROSS_PROJECT_CODE: Final = "cross_project_wipe_confirmation_required"
PIN_ALREADY_SET_CODE: Final = "PIN_ALREADY_SET"
# The one answer to every refused wipe confirmation (T-0080): same status and body as any
# out-of-scope 404, whatever the reason.
WIPE_REFUSED_STATUS: Final = status.HTTP_404_NOT_FOUND
WIPE_REFUSED_BODY: Final = {"detail": "Not Found"}

router = APIRouter(tags=["devices"])

RuntimeDep = Annotated[AuthRuntime, Depends(get_auth_runtime)]
AuthServiceDep = Annotated[users.AuthService, Depends(get_auth_service)]
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
    response_model=DeviceEnrolled,
    responses={
        status.HTTP_201_CREATED: {
            "description": "Enrolled: the only response that carries the `wipe_secret`",
            "model": DeviceEnrolled,
        },
        status.HTTP_409_CONFLICT: {
            "description": (
                "Another account's device, or session; `code: PIN_ALREADY_SET` when `pin` is "
                "sent for a device that has a PIN"
            )
        },
    },
)
def enrol(
    body: DeviceEnrolRequest,
    principal: CurrentPrincipal,
    session: DbSession,
    runtime: RuntimeDep,
    service: AuthServiceDep,
    response: Response,
) -> DeviceEnrolled:
    """Register this field device for the signed-in user and bind the session to it.

    A new device gets its wipe secret in this response, once (store it per account). `pin`
    sets the device's field PIN, once: a sign-in on the device later keeps it."""
    session_id = principal.claims.session_id
    # A session bound at sign-in carries ``did``: it is never "newly bound", even when the
    # store's binding was evicted, so re-enrolling cannot clear the PIN counter (TD-142 class).
    newly_bound = (
        principal.claims.device_id is None and runtime.store.session_device(session_id) is None
    )
    pin_hash = hash_password(body.pin) if body.pin is not None else None
    try:
        device, wipe_secret = devices.enrol(
            session,
            runtime.store,
            user_id=principal.user_id,
            session_id=session_id,
            session_ttl=runtime.tokens.ttl["refresh"],
            device_id=body.device_id,
            platform=body.platform,
            os_version=body.os_version,
            app_version=body.app_version,
            pin_hash=pin_hash,
        )
    except devices.DeviceConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except devices.DevicePinAlreadySetError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": PIN_ALREADY_SET_CODE,
                "message": "The device already has a PIN: change it with PUT .../pin",
            },
        ) from exc
    if newly_bound:
        # A new session on the device is a sign-in there: it ends a "10th wrong PIN" stop.
        service.clear_pin_failures(principal.tenant_id, device.device_id)
    if wipe_secret is not None:  # created now; the secret never enters the ledger
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
    if pin_hash is not None:
        _audit_pin(session, device, "pin_set", principal.user_id)
    return DeviceEnrolled.model_validate({**_read(device).model_dump(), "wipe_secret": wipe_secret})


def _audit_pin(session: Session, device: Device, action: str, actor_id: uuid.UUID) -> None:
    """A PIN set or change on the device's chain: who and when, never the PIN or its hash."""
    audit.append(
        session,
        f"device:{device.device_id}",
        {"action": action, "device_id": str(device.device_id), "user_id": str(device.user_id)},
        actor_id=actor_id,
        source="api",
        device_id=device.device_id,
    )


@router.put(
    f"{PREFIX}/{{device_id}}/pin",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_403_FORBIDDEN: {"description": "Not a session of this device"},
        status.HTTP_404_NOT_FOUND: {"description": "Not an active device of the caller"},
    },
)
def change_pin(
    device_id: uuid.UUID,
    body: DevicePinRequest,
    principal: CurrentPrincipal,
    session: DbSession,
    runtime: RuntimeDep,
    service: AuthServiceDep,
) -> Response:
    """Set a new field PIN for this device, from a signed-in session of the device (diff
    §4.2: the PIN is changed after an online sign-in; the old PIN is not needed). Clears the
    device's PIN failures."""
    device = devices.find_device(session, device_id)
    if device is None or device.user_id != principal.user_id or device.status != DEVICE_ACTIVE:
        raise OutOfScopeError("device not found")
    claims = principal.claims
    if device_id not in (claims.device_id, runtime.store.session_device(claims.session_id)):
        raise _forbidden()
    devices.set_pin(session, device, hash_password(body.pin), actor_id=principal.user_id)
    service.clear_pin_failures(principal.tenant_id, device_id)
    _audit_pin(session, device, "pin_changed", principal.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
        WIPE_REFUSED_STATUS: {
            "description": (
                "Refused: the one answer for an unknown device, a device that is not revoked, "
                "and a wrong or missing wipe secret"
            )
        },
    },
)
def wipe_confirmed(
    device_id: uuid.UUID,
    session: DbSession,
    runtime: RuntimeDep,
    body: Annotated[WipeConfirmRequest | None, Body()] = None,
) -> Response:
    """The revoked device confirms it wiped its local store (records, outbox, media, keys),
    with the wipe secret its enrolment returned."""
    tenant = runtime.settings.default_tenant_id
    if tenant is None:
        return JSONResponse(WIPE_REFUSED_BODY, status_code=WIPE_REFUSED_STATUS)
    bind_tenancy(session, TenancyContext(tenant_id=tenant))
    outcome = devices.confirm_wipe(session, device_id, body.wipe_secret if body else None)
    chain = f"device:{device_id}"
    if outcome.refused is not None:
        # Audited (never the secret), then the uniform answer. The refusal is a normal
        # response, not an exception, so the request's unit of work keeps the entry.
        payload: dict[str, object] = {
            "action": "wipe_refused",
            "device_id": str(device_id),
            "reason": outcome.refused,
        }
        if outcome.device is not None:
            payload["user_id"] = str(outcome.device.user_id)
        audit.append(session, chain, payload, source="api")
        return JSONResponse(WIPE_REFUSED_BODY, status_code=WIPE_REFUSED_STATUS)
    if outcome.recorded and outcome.device is not None:
        audit.append(
            session,
            chain,
            {
                "action": "wipe_confirmed",
                "device_id": str(device_id),
                "user_id": str(outcome.device.user_id),
            },
            source="api",
            device_id=device_id,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
