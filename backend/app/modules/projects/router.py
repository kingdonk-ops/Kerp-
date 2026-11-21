"""Projects API, mounted at ``/api/v1/projects`` (T-0012).

Authorisation comes from the caller's memberships in the membership store (T-0010), read
in the request's tenant-level session; the request binds no project yet (TD-122), so the
project is the one in the path:

* list / read: projects the caller is a member of; any other project (another tenant's,
  a non-member's, a deleted one) is 404. A ``system_admin`` lists and reads every live
  project of its tenant (ADR 0016, T-0275); each project says how the caller reaches it
  (``access``: ``member`` or ``system_admin`` where it holds no membership).
* edit / delete: the ``admin`` capability on that project (Project Administrator, or a
  ``system_admin``: PER-09 equivalent), else 403.
  Delete answers 409 while team-derived members remain (unlink the teams first, T-0073).
* create: the caller's tenant-level ``create_projects`` grant ("Project Admin", appointed by
  a System admin through ``/api/v1/admin/grants``), else 403 (diff §4.1, TD-184, T-0039). The
  creator is granted PER-09 on the new project in the same transaction.
* ``PUT /{id}/lobby`` (``visible_in_lobby``): PER-09 of that project or a System admin
  (T-0040, diff §4.1); a non-member who is not a System admin gets 404, a member without
  PER-09 403. ``PATCH`` does not take the flag (422).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status

from app.core.auth import memberships
from app.core.auth.access import (
    CREATE_PROJECTS,
    CallerAdminGrants,
    CallerGrants,
    CallerIsSystemAdmin,
    Capability,
    Grant,
    Persona,
    ProjectAccess,
    project_access,
)
from app.core.auth.deps import CurrentPrincipal
from app.core.db import DbSession
from app.modules.projects import service
from app.modules.projects.models import Project
from app.modules.projects.schemas import (
    LobbyVisibility,
    ProjectAccessKind,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
)

router = APIRouter(tags=["projects"])


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


def _conflict(exc: service.DuplicateProjectCodeError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


def _access(grants: list[Grant], project_id: uuid.UUID, is_admin: bool) -> ProjectAccess:
    """The caller's access to ``project_id``; ``OutOfScopeError`` (404) for a non-member who
    is not a ``system_admin``. The project itself is loaded afterwards under the session's
    scope (unknown, deleted or another tenant's: 404)."""
    return project_access(grants, project_id, system_admin=is_admin)


def _admin(grants: list[Grant], project_id: uuid.UUID, is_admin: bool) -> None:
    if not _access(grants, project_id, is_admin).has(Capability.ADMINISTER):
        raise _forbidden()


def _read(project: Project, grants: list[Grant]) -> ProjectRead:
    """The project with how the caller reaches it (``access``, ADR 0016)."""
    member = any(g.project_id == project.id for g in grants)
    fields = {name: getattr(project, name) for name in ProjectRead.model_fields if name != "access"}
    kind = ProjectAccessKind.MEMBER if member else ProjectAccessKind.SYSTEM_ADMIN
    return ProjectRead.model_validate({**fields, "access": kind})


@router.get("", response_model=list[ProjectRead])
def list_projects(
    grants: CallerGrants, is_admin: CallerIsSystemAdmin, session: DbSession
) -> list[ProjectRead]:
    if is_admin:
        projects = service.tenant_projects(session)
    else:
        projects = service.list_projects(session, {g.project_id for g in grants})
    return [_read(p, grants) for p in projects]


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
    # The creator is granted PER-09 on it in the same transaction: a member.
    return _read(project, [Grant(project.id, Persona.PROJECT_ADMINISTRATOR)])


@router.get("/{project_id}", response_model=ProjectRead)
def read_project(
    project_id: uuid.UUID, grants: CallerGrants, is_admin: CallerIsSystemAdmin, session: DbSession
) -> ProjectRead:
    _access(grants, project_id, is_admin)
    return _read(service.get_project(session, project_id), grants)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> ProjectRead:
    _admin(grants, project_id, is_admin)
    try:
        project = service.update_project(session, project_id, body, actor_id=principal.user_id)
    except service.DuplicateProjectCodeError as exc:
        raise _conflict(exc) from exc
    return _read(project, grants)


@router.put("/{project_id}/lobby", response_model=ProjectRead)
def set_lobby_visibility(
    project_id: uuid.UUID,
    body: LobbyVisibility,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> ProjectRead:
    """List the project in the lobby for internal users, or not (T-0040, diff §4.1)."""
    _admin(grants, project_id, is_admin)
    project = service.set_lobby_visibility(
        session, project_id, body.visible_in_lobby, actor_id=principal.user_id
    )
    return _read(project, grants)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> Response:
    _admin(grants, project_id, is_admin)
    try:
        service.delete_project(session, project_id, actor_id=principal.user_id)
    except memberships.TeamGrantsPresentError as exc:
        # Team-derived members are ended by unlinking the teams (T-0073, ADR 0008).
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "team_links_present", "message": str(exc)}
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
