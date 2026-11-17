"""Access-request API, mounted at ``/api/v1/access-requests`` (T-0050, diff §4.1).

The requester:

* ``POST /``: file a request (``project_id``, optional ``message``, ``lookup_ticket`` where the
  first request needs one). 201; unknown, deleted or not legitimately found project: 404;
  effective access already or a pending request: 409; cooldown after a rejection: 409
  ``{"code": "ACCESS_REQUEST_COOLDOWN", "retry_after": ...}``.
* ``GET /mine``: the lobby's "Active requests" panel (the authoritative outcome channel).
* ``POST /{id}/withdraw``: own pending request only (someone else's: 404; not pending: 409).

The project's PER-09 holders and System admins:

* ``GET /pending[?project_id=]``: pending requests of the projects they administer. With
  ``project_id``: a non-member (or a deleted project) gets 404, a member without PER-09 403;
  without it, a caller who administers no project and is no System admin gets 403.
* ``POST /{id}/approve`` (persona(s), site scope, ``confirm_sod``) and ``POST /{id}/reject``
  (optional ``reason``): only ``pending`` requests (409 otherwise); a request the caller cannot
  see is 404, one they see but may not decide (their own) 403.

Who sees a request is the table's RLS policy (edge spec §6 amendments); the tenant, the
requester and the decider come from the token, never the body.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.core.auth.access import (
    CallerGrants,
    CallerIsSystemAdmin,
    Capability,
    Persona,
    project_access,
)
from app.core.auth.deps import CurrentPrincipal
from app.core.auth.runtime import AuthRuntime, get_auth_runtime
from app.core.db import DbSession, utcnow
from app.modules.access_requests import service
from app.modules.access_requests.models import AccessRequest
from app.modules.access_requests.schemas import (
    AccessRequestApprove,
    AccessRequestCreate,
    AccessRequestDecided,
    AccessRequestError,
    AccessRequestRead,
    AccessRequestReject,
    PendingAccessRequest,
    Requester,
)
from app.modules.admin import service as admin
from app.modules.admin.schemas import GrantRead
from app.modules.projects import service as projects

router = APIRouter(tags=["access-requests"])

COOLDOWN: Final = "ACCESS_REQUEST_COOLDOWN"
_NOT_FOUND: Final = "Not Found"
_FILE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    404: {"description": "No such project, or not found through the lobby or a code lookup"},
    409: {
        "model": AccessRequestError,
        "description": f"Already a member, already pending, or the cooldown (`code: {COOLDOWN}`)",
    },
}
_DECIDE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    403: {"description": "Not an administrator of the project (or the caller's own request)"},
    404: {"description": "No such request for the caller"},
    409: {"description": "Not pending, or PER-09 + hold authority without `confirm_sod`"},
}


def _conflict(detail: str, code: str) -> JSONResponse:
    body = AccessRequestError(detail=detail, code=code)
    return JSONResponse(body.model_dump(mode="json"), status_code=status.HTTP_409_CONFLICT)


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)


def _cooldown(runtime: AuthRuntime) -> timedelta:
    return timedelta(hours=runtime.settings.access_request_cooldown_hours)


def _read(entry: service.MineEntry) -> AccessRequestRead:
    request, project = entry.request, entry.project
    return AccessRequestRead(
        id=request.id,
        project_id=project.id,
        project_code=project.code,
        project_name=project.name,
        status=request.status,  # type: ignore[arg-type]  # CHECK constraint: one of Status
        message=request.message,
        created_at=request.created_at,
        decided_at=request.decided_at,
        withdrawn_at=request.withdrawn_at,
        rejection_reason=request.rejection_reason if request.status == "rejected" else None,
        request_again_at=entry.request_again_at,
    )


def _decided(request: AccessRequest, sod_warning: bool = False) -> AccessRequestDecided:
    granted = None
    if request.granted is not None:
        granted = [GrantRead.model_validate(g) for g in request.granted]
    return AccessRequestDecided(
        id=request.id,
        project_id=request.requested_project_id,
        requester_id=request.requester_id,
        status=request.status,  # type: ignore[arg-type]  # CHECK constraint: one of Status
        decided_by=request.decided_by,
        decided_at=request.decided_at,
        rejection_reason=request.rejection_reason,
        granted=granted,
        sod_warning=sod_warning,
    )


# --- the requester --------------------------------------------------------------------------


@router.post(
    "",
    response_model=AccessRequestRead,
    status_code=status.HTTP_201_CREATED,
    responses=_FILE_RESPONSES,
)
def file_request(
    body: AccessRequestCreate,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
    runtime: Annotated[AuthRuntime, Depends(get_auth_runtime)],
) -> AccessRequestRead | JSONResponse:
    now = utcnow()
    try:
        request, project = service.file_request(
            session,
            body.project_id,
            message=body.message,
            lookup_ticket=body.lookup_ticket,
            caller_grants=grants,
            is_internal=principal.user.is_internal,
            tokens=runtime.tokens,
            cooldown=_cooldown(runtime),
            now=now,
        )
    except service.AccessRequestNotFoundError as exc:
        raise _not_found() from exc
    except service.AlreadyMemberError as exc:
        return _conflict(str(exc), "ACCESS_REQUEST_ALREADY_MEMBER")
    except service.AlreadyPendingError as exc:
        session.rollback()  # a concurrent insert may have broken the unit of work
        return _conflict(str(exc), "ACCESS_REQUEST_PENDING")
    except service.CooldownError as exc:
        body_ = AccessRequestError(detail=str(exc), code=COOLDOWN, retry_after=exc.retry_after)
        return JSONResponse(body_.model_dump(mode="json"), status_code=status.HTTP_409_CONFLICT)
    return _read(service.MineEntry(request, project, None))


@router.get("/mine", response_model=list[AccessRequestRead])
def my_requests(
    principal: CurrentPrincipal,
    session: DbSession,
    runtime: Annotated[AuthRuntime, Depends(get_auth_runtime)],
) -> list[AccessRequestRead]:
    entries = service.list_mine(session, cooldown=_cooldown(runtime), now=utcnow())
    return [_read(e) for e in entries]


@router.post(
    "/{request_id}/withdraw",
    response_model=AccessRequestRead,
    responses={
        404: {"description": "Not the caller's request"},
        409: {"description": "Not pending"},
    },
)
def withdraw(
    request_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
) -> AccessRequestRead:
    try:
        entry = service.withdraw(session, request_id, now=utcnow())
    except service.AccessRequestNotFoundError as exc:
        raise _not_found() from exc
    except service.NotPendingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _read(entry)


# --- the project's administrators -----------------------------------------------------------


@router.get(
    "/pending",
    response_model=list[PendingAccessRequest],
    responses={403: {"description": "Administers no project"}, 404: {"description": "Not found"}},
)
def pending_requests(
    principal: CurrentPrincipal,
    grants: CallerGrants,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
    project_id: uuid.UUID | None = None,
) -> list[PendingAccessRequest]:
    if not is_admin:
        if project_id is not None:
            # A non-member gets 404 (project_access), a member without PER-09 403.
            if not project_access(grants, project_id).has(Capability.ADMINISTER):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
        elif not any(g.persona is Persona.PROJECT_ADMINISTRATOR for g in grants):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    entries = service.list_pending(session, project_id=project_id, is_system_admin=is_admin)
    if project_id is not None and is_admin and not entries:
        # A System admin naming a deleted or unknown project: 404, like everyone else.
        projects.get_project(session, project_id)
    return [
        PendingAccessRequest(
            id=e.request.id,
            project_id=e.project.id,
            project_code=e.project.code,
            project_name=e.project.name,
            requester=Requester(
                id=e.requester.id, email=e.requester.email, display_name=e.requester.display_name
            ),
            message=e.request.message,
            created_at=e.request.created_at,
        )
        for e in entries
    ]


@router.post(
    "/{request_id}/approve", response_model=AccessRequestDecided, responses=_DECIDE_RESPONSES
)
def approve(
    request_id: uuid.UUID,
    body: AccessRequestApprove,
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> AccessRequestDecided:
    wanted = [(g.persona, g.site_node_id) for g in body.grants]
    try:
        request, warn = service.approve(
            session,
            request_id,
            wanted,
            confirm_sod=body.confirm_sod,
            is_system_admin=is_admin,
            now=utcnow(),
        )
    except service.AccessRequestNotFoundError as exc:
        raise _not_found() from exc
    except service.NotAllowedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden") from exc
    except service.NotPendingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except admin.SodConfirmationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "sod_confirmation_required", "sod_warning": True, "message": str(exc)},
        ) from exc
    return _decided(request, warn)


@router.post(
    "/{request_id}/reject", response_model=AccessRequestDecided, responses=_DECIDE_RESPONSES
)
def reject(
    request_id: uuid.UUID,
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
    body: AccessRequestReject | None = None,
) -> AccessRequestDecided:
    reason = body.reason if body is not None else None
    try:
        request = service.reject(
            session, request_id, reason=reason, is_system_admin=is_admin, now=utcnow()
        )
    except service.AccessRequestNotFoundError as exc:
        raise _not_found() from exc
    except service.NotAllowedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden") from exc
    except service.NotPendingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _decided(request)
