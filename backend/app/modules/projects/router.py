"""Projects API, mounted at ``/api/v1/projects`` (T-0012).

Authorisation comes from the caller's memberships in the membership store (T-0010), read
in the request's tenant-level session; the request binds no project yet (TD-122), so the
project is the one in the path:

* list / read: projects the caller is a member of; any other project (another tenant's,
  a non-member's, a deleted one) is 404.
* edit / delete: the ``admin`` capability on that project (Project Administrator), else 403.
* create: the caller's tenant-level ``create_projects`` grant ("Project Admin", appointed by
  a System admin through ``/api/v1/admin/grants``), else 403 (diff §4.1, TD-184, T-0039). The
  creator is granted PER-09 on the new project in the same transaction.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status

from app.core.auth.access import (
    CREATE_PROJECTS,
    CallerAdminGrants,
    CallerGrants,
    Capability,
    Grant,
    ProjectAccess,
    project_access,
)
from app.core.auth.deps import CurrentPrincipal
from app.core.db import DbSession
from app.modules.projects import service
from app.modules.projects.schemas import ProjectCreate, ProjectRead, ProjectUpdate

router = APIRouter(tags=["projects"])


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


def _conflict(exc: service.DuplicateProjectCodeError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


def _access(grants: list[Grant], project_id: uuid.UUID) -> ProjectAccess:
    """The caller's access to ``project_id``; ``OutOfScopeError`` (404) for a non-member."""
    return project_access(grants, project_id)


def _admin(grants: list[Grant], project_id: uuid.UUID) -> None:
    if not _access(grants, project_id).has(Capability.ADMINISTER):
        raise _forbidden()


@router.get("", response_model=list[ProjectRead])
def list_projects(grants: CallerGrants, session: DbSession) -> list[ProjectRead]:
    projects = service.list_projects(session, {g.project_id for g in grants})
    return [ProjectRead.model_validate(p) for p in projects]


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate,
    principal: CurrentPrincipal,
    admin_grants: CallerAdminGrants,
    session: DbSession,
) -> ProjectRead:
    if CREATE_PROJECTS not in admin_grants:
        raise _forbidden()
    try:
        project = service.create_project(
            session, body, actor_id=principal.user_id, creator_id=principal.user_id
        )
    except service.DuplicateProjectCodeError as exc:
        raise _conflict(exc) from exc
    return ProjectRead.model_validate(project)


@router.get("/{project_id}", response_model=ProjectRead)
def read_project(project_id: uuid.UUID, grants: CallerGrants, session: DbSession) -> ProjectRead:
    _access(grants, project_id)
    return ProjectRead.model_validate(service.get_project(session, project_id))


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> ProjectRead:
    _admin(grants, project_id)
    try:
        project = service.update_project(session, project_id, body, actor_id=principal.user_id)
    except service.DuplicateProjectCodeError as exc:
        raise _conflict(exc) from exc
    return ProjectRead.model_validate(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: uuid.UUID, principal: CurrentPrincipal, grants: CallerGrants, session: DbSession
) -> Response:
    _admin(grants, project_id)
    service.delete_project(session, project_id, actor_id=principal.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
