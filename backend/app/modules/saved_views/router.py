"""Saved views API, mounted at ``/api/v1/saved-views`` (T-0020).

The project is the one in the path. A non-member, another tenant's project or a deleted
project is 404; every member may save private views (``V``). The request session is then
bound to that project (until TD-122 binds requests generally). Who sees and edits which
view is the service's ``Viewer`` rule; the router derives it from the caller's grants:
``C`` may share, ``admin`` (PER-09) is the Project Administrator. The caller's teams are
empty until the teams module exists (T-0013, TD-242), so team views are visible to their
owner only and only a Project Administrator can create one.

* ``GET    /projects/{p}/views?register_key=...``          the views the caller sees
* ``GET    /projects/{p}/views/default?register_key=...``  the view the register opens with
* ``POST   /projects/{p}/views``                           save / save as
* ``GET    /projects/{p}/views/{id}``                      load (missing columns flagged)
* ``PATCH  /projects/{p}/views/{id}``                      edit / rename / share / default
* ``DELETE /projects/{p}/views/{id}``                      delete
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.core.auth.access import CallerGrants, Capability, Grant, project_access, resolve_scope
from app.core.auth.deps import CurrentPrincipal, Principal
from app.core.db import DbSession
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_tenancy, session_tenancy
from app.modules.projects import service as projects
from app.modules.saved_views import service
from app.modules.saved_views.schemas import (
    RegisterKey,
    SavedViewCreate,
    SavedViewRead,
    SavedViewUpdate,
)

router = APIRouter(tags=["saved_views"])

VIEWS = "/projects/{project_id}/views"
RegisterQuery = Annotated[RegisterKey, Query()]


def _bind_project(
    principal: Principal, grants: list[Grant], session: DbSession, project_id: uuid.UUID
) -> service.Viewer:
    """Bind the session to ``project_id`` and return the caller as a ``Viewer``."""
    access = project_access(grants, project_id)  # non-member: 404
    if not access.has(Capability.VIEW):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    base = session_tenancy(session) or TenancyContext(
        tenant_id=principal.tenant_id, user_id=principal.user_id
    )
    # A session holding objects cannot be rebound (``bind_tenancy``).
    session.expunge_all()
    bind_tenancy(session, resolve_scope(grants, base, project_id))
    projects.get_project(session, project_id)  # a deleted project: 404
    return service.Viewer(
        user_id=principal.user_id,
        team_ids=frozenset(),  # teams module: T-0013 (TD-242)
        can_share=access.has(Capability.CREATE),
        is_admin=access.has(Capability.ADMINISTER),
    )


def _forbidden(exc: service.ViewPermissionError) -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))


def _invalid(exc: service.InvalidViewError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@router.get(VIEWS, response_model=list[SavedViewRead])
def list_views(
    project_id: uuid.UUID,
    register_key: RegisterQuery,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> list[SavedViewRead]:
    viewer = _bind_project(principal, grants, session, project_id)
    return [service.present(session, v) for v in service.list_views(session, viewer, register_key)]


@router.get(VIEWS + "/default", response_model=SavedViewRead | None)
def default_view(
    project_id: uuid.UUID,
    register_key: RegisterQuery,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> SavedViewRead | None:
    """The caller's default view of the register, else the project default, else ``null``."""
    viewer = _bind_project(principal, grants, session, project_id)
    view = service.default_view(session, viewer, register_key)
    return None if view is None else service.present(session, view)


@router.post(VIEWS, response_model=SavedViewRead, status_code=status.HTTP_201_CREATED)
def create_view(
    project_id: uuid.UUID,
    body: SavedViewCreate,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> SavedViewRead:
    viewer = _bind_project(principal, grants, session, project_id)
    try:
        view = service.create_view(session, viewer, body)
    except service.ViewPermissionError as exc:
        raise _forbidden(exc) from exc
    except service.InvalidViewError as exc:
        raise _invalid(exc) from exc
    return service.present(session, view)


@router.get(VIEWS + "/{view_id}", response_model=SavedViewRead)
def read_view(
    project_id: uuid.UUID,
    view_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> SavedViewRead:
    viewer = _bind_project(principal, grants, session, project_id)
    return service.present(session, service.get_view(session, viewer, view_id))


@router.patch(VIEWS + "/{view_id}", response_model=SavedViewRead)
def update_view(
    project_id: uuid.UUID,
    view_id: uuid.UUID,
    body: SavedViewUpdate,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> SavedViewRead:
    viewer = _bind_project(principal, grants, session, project_id)
    try:
        view = service.update_view(session, viewer, view_id, body)
    except service.ViewPermissionError as exc:
        raise _forbidden(exc) from exc
    except service.InvalidViewError as exc:
        raise _invalid(exc) from exc
    return service.present(session, view)


@router.delete(VIEWS + "/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_view(
    project_id: uuid.UUID,
    view_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> Response:
    viewer = _bind_project(principal, grants, session, project_id)
    try:
        service.delete_view(session, viewer, view_id)
    except service.ViewPermissionError as exc:
        raise _forbidden(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
