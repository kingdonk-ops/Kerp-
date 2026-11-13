"""Uploads API, mounted at ``/api/v1/uploads`` (T-0016).

The project is the one in the path. The caller's memberships (T-0010) decide access:
a non-member, another tenant's project or a deleted project is 404; a member without the
needed capability is 403. The request session is then bound to that project (until TD-122
binds requests generally), so every read and write is under the project's RLS scope.

* ``POST   /projects/{p}/files``                 create (``C``): presigned ``PUT`` to staging
* ``POST   /projects/{p}/files/{id}/complete``   uploader (``C``): size + magic, queue verify
* ``GET    /projects/{p}/files/{id}``            view (``V``): status, poll until ``available``
* ``GET    /projects/{p}/files/{id}/download``   view (``V``): presigned ``GET``
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status

from app.core.auth.access import CallerGrants, Capability, Grant, project_access, resolve_scope
from app.core.auth.deps import CurrentPrincipal, Principal
from app.core.config import Settings
from app.core.db import DbSession
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_tenancy, session_tenancy
from app.modules.projects import service as projects
from app.modules.uploads import service
from app.modules.uploads.schemas import FileRead, PresignedRead, UploadCreate, UploadTicket
from app.modules.uploads.storage import get_storage

router = APIRouter(tags=["uploads"])

FILES = "/projects/{project_id}/files"


def _bind_project(
    principal: Principal,
    grants: list[Grant],
    session: DbSession,
    project_id: uuid.UUID,
    capability: Capability,
) -> None:
    if not project_access(grants, project_id).has(capability):  # non-member: 404
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    base = session_tenancy(session) or TenancyContext(
        tenant_id=principal.tenant_id, user_id=principal.user_id
    )
    # The tenant-level objects loaded so far (the principal's user) are detached first:
    # a session holding objects cannot be rebound (``bind_tenancy``).
    session.expunge_all()
    bind_tenancy(session, resolve_scope(grants, base, project_id))
    projects.get_project(session, project_id)  # a deleted project: 404


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _conflict(exc: service.UploadStateError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.post(FILES, response_model=UploadTicket, status_code=status.HTTP_201_CREATED)
def create_upload(
    project_id: uuid.UUID,
    body: UploadCreate,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
    request: Request,
) -> UploadTicket:
    _bind_project(principal, grants, session, project_id, Capability.CREATE)
    settings = _settings(request)
    try:
        file, upload = service.presign_upload(
            session,
            get_storage(),
            body,
            max_bytes=settings.upload_max_bytes,
            expires_in=settings.upload_url_seconds,
            actor_id=principal.user_id,
        )
    except service.FileTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from exc
    return UploadTicket(
        file=FileRead.model_validate(file), upload=PresignedRead.model_validate(upload)
    )


@router.post(FILES + "/{file_id}/complete", response_model=FileRead)
def complete_upload(
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> FileRead:
    """``verifying`` (poll the file or its job) or ``rejected`` with a reason code."""
    _bind_project(principal, grants, session, project_id, Capability.CREATE)
    try:
        file = service.complete_upload(session, get_storage(), file_id, actor_id=principal.user_id)
    except service.NotUploaderError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden") from exc
    except service.UploadStateError as exc:
        raise _conflict(exc) from exc
    return FileRead.model_validate(file)


@router.get(FILES + "/{file_id}", response_model=FileRead)
def read_file(
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> FileRead:
    _bind_project(principal, grants, session, project_id, Capability.VIEW)
    return FileRead.model_validate(service.get_file(session, file_id))


@router.get(FILES + "/{file_id}/download", response_model=PresignedRead)
def download_file(
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
    request: Request,
) -> PresignedRead:
    _bind_project(principal, grants, session, project_id, Capability.VIEW)
    try:
        download = service.download_request(
            session, get_storage(), file_id, expires_in=_settings(request).download_url_seconds
        )
    except service.UploadStateError as exc:
        raise _conflict(exc) from exc
    return PresignedRead.model_validate(download)
