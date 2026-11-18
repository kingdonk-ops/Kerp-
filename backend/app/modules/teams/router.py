"""Teams API, mounted at ``/api/v1/teams`` (T-0013, diff §4.1 Teams, ADR 0008).

Project read API (declared first, so ``/projects/...`` never meets ``/{team_id}``):

* ``GET /projects/{p}/teams``: the teams linked to ``p``, with their role and active member
  count, for anyone with access to ``p`` (individual or team-derived).
* ``GET /projects/{p}/teams/mine``: the caller's teams on ``p`` (the ``my_teams`` filter).

  The path project is bound like ``uploads`` (``bind_project`` via ``resolve_scope``, until
  TD-122): a caller without access, another tenant's or a deleted project is 404.

Admin API (every write ``system_admin`` only: anyone else gets 403, or 404 when the team is
not visible to them):

* ``GET ""`` (the teams the caller can see), ``GET /{team_id}`` (members, links),
  ``POST ""``, ``PATCH /{team_id}``, ``DELETE /{team_id}`` (soft delete: ends memberships and
  links and revokes their derived grants).
* ``PUT /{team_id}/members/{user_id}`` (idempotent, 204) and ``DELETE`` (204). An unknown,
  deactivated or other-tenant user gets 422 with the same body every time.
* ``PUT /{team_id}/projects/{project_id}`` with ``{persona, site_node_id?, confirm_sod?}``
  links the team or changes its role (idempotent when unchanged);
  ``DELETE /{team_id}/projects/{project_id}`` unlinks (204). A deleted or other-tenant project
  is 404.

A change that would combine individual PER-09 with a hold-authority persona answers 409
``{"code": "sod_confirmation_required", "sod_warning": {"user_ids", "project_ids"}}`` unless
the body carries ``confirm_sod: true``. No endpoint takes or returns a tenant id.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, HTTPException, Response, status

from app.core.auth.access import CallerGrants, CallerIsSystemAdmin, Persona
from app.core.auth.deps import CurrentPrincipal
from app.core.auth.team_grants import SodConfirmationRequiredError
from app.core.db import DbSession
from app.modules.projects import service as projects
from app.modules.teams import service
from app.modules.teams.models import TeamProjectLink
from app.modules.teams.schemas import (
    LinkPut,
    MemberPut,
    ProjectTeamRead,
    SodWarning,
    TeamCreate,
    TeamDetail,
    TeamLinkRead,
    TeamMemberRead,
    TeamRead,
    TeamUpdate,
)

router = APIRouter(tags=["teams"])

PROJECT_TEAMS: Final = "/projects/{project_id}/teams"
INVALID_MEMBER: Final = "This user cannot be added to a team"
_SOD_RESPONSE: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_409_CONFLICT: {"description": "Duplicate name, or SoD confirmation required"}
}


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


def _require_admin_on(session: DbSession, is_admin: bool, team_id: uuid.UUID | None) -> None:
    """``system_admin`` only: a team the caller cannot see is 404, one they can see 403."""
    if is_admin:
        return
    if team_id is not None:
        service.get_team(session, team_id)  # OutOfScopeError -> 404
    raise _forbidden()


def _sod_conflict(user_ids: list[uuid.UUID], project_ids: list[uuid.UUID]) -> HTTPException:
    warning = SodWarning(user_ids=user_ids, project_ids=project_ids)
    return HTTPException(
        status.HTTP_409_CONFLICT,
        {
            "code": "sod_confirmation_required",
            "message": "individual PER-09 together with a hold-authority persona:"
            " confirm with confirm_sod",
            "sod_warning": warning.model_dump(mode="json"),
        },
    )


def _duplicate(exc: service.DuplicateTeamNameError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


def _summary(summary: service.TeamSummary) -> TeamRead:
    team = summary.team
    return TeamRead(
        id=team.id,
        name=team.name,
        description=team.description,
        member_count=summary.member_count,
        project_count=summary.project_count,
    )


def _project_team(entry: service.ProjectTeam) -> ProjectTeamRead:
    return ProjectTeamRead(
        team_id=entry.team.id,
        name=entry.team.name,
        link_id=entry.link.id,
        persona=Persona(entry.link.persona),
        site_node_id=entry.link.site_node_id,
        member_count=entry.member_count,
    )


def _link(link: TeamProjectLink) -> TeamLinkRead:
    return TeamLinkRead(
        id=link.id,
        project_id=link.linked_project_id,
        persona=Persona(link.persona),
        site_node_id=link.site_node_id,
    )


# --- project read API -----------------------------------------------------------------------


@router.get(PROJECT_TEAMS, response_model=list[ProjectTeamRead])
def project_teams(
    project_id: uuid.UUID, principal: CurrentPrincipal, grants: CallerGrants, session: DbSession
) -> list[ProjectTeamRead]:
    projects.bind_project(session, principal, grants, project_id)
    return [_project_team(t) for t in service.project_teams(session, project_id)]


@router.get(f"{PROJECT_TEAMS}/mine", response_model=list[ProjectTeamRead])
def my_project_teams(
    project_id: uuid.UUID, principal: CurrentPrincipal, grants: CallerGrants, session: DbSession
) -> list[ProjectTeamRead]:
    projects.bind_project(session, principal, grants, project_id)
    mine = service.user_team_ids(session, principal.user_id)
    return [
        _project_team(t) for t in service.project_teams(session, project_id) if t.team.id in mine
    ]


# --- teams ----------------------------------------------------------------------------------


@router.get("", response_model=list[TeamRead])
def list_teams(principal: CurrentPrincipal, session: DbSession) -> list[TeamRead]:
    return [_summary(s) for s in service.list_teams(session)]


@router.post(
    "", response_model=TeamRead, status_code=status.HTTP_201_CREATED, responses=_SOD_RESPONSE
)
def create_team(
    body: TeamCreate, principal: CurrentPrincipal, is_admin: CallerIsSystemAdmin, session: DbSession
) -> TeamRead:
    _require_admin_on(session, is_admin, None)
    try:
        team = service.create_team(session, body, actor_id=principal.user_id)
    except service.DuplicateTeamNameError as exc:
        raise _duplicate(exc) from exc
    return _summary(service.TeamSummary(team, member_count=0, project_count=0))


@router.get("/{team_id}", response_model=TeamDetail)
def get_team(team_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession) -> TeamDetail:
    detail = service.team_detail(session, team_id)
    return TeamDetail(
        id=detail.team.id,
        name=detail.team.name,
        description=detail.team.description,
        members=[
            TeamMemberRead(
                user_id=u.id, email=u.email, display_name=u.display_name, is_active=u.is_active
            )
            for u in detail.members
        ],
        links=[_link(link) for link in detail.links],
    )


@router.patch("/{team_id}", response_model=TeamRead, responses=_SOD_RESPONSE)
def update_team(
    team_id: uuid.UUID,
    body: TeamUpdate,
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> TeamRead:
    _require_admin_on(session, is_admin, team_id)
    try:
        service.update_team(session, team_id, body, actor_id=principal.user_id)
    except service.DuplicateTeamNameError as exc:
        raise _duplicate(exc) from exc
    return _summary(service.team_summary(session, team_id))


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(
    team_id: uuid.UUID,
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> Response:
    _require_admin_on(session, is_admin, team_id)
    service.delete_team(session, team_id, actor_id=principal.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- members --------------------------------------------------------------------------------


@router.put(
    "/{team_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_SOD_RESPONSE,
)
def add_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
    body: Annotated[MemberPut | None, Body()] = None,
) -> Response:
    _require_admin_on(session, is_admin, team_id)
    confirm = body.confirm_sod if body is not None else False
    try:
        service.add_member(
            session, team_id, user_id, confirm_sod=confirm, actor_id=principal.user_id
        )
    except service.InvalidMemberError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, INVALID_MEMBER) from exc
    except service.SodConfirmationError as exc:
        raise _sod_conflict(exc.user_ids, exc.project_ids) from exc
    except SodConfirmationRequiredError as exc:  # drifted rows of other members (T-0075)
        raise _sod_conflict(sorted(exc.user_ids, key=str), [exc.project_id]) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> Response:
    _require_admin_on(session, is_admin, team_id)
    service.remove_member(session, team_id, user_id, actor_id=principal.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- project links --------------------------------------------------------------------------


@router.put(
    "/{team_id}/projects/{project_id}", response_model=TeamLinkRead, responses=_SOD_RESPONSE
)
def set_link(
    team_id: uuid.UUID,
    project_id: uuid.UUID,
    body: LinkPut,
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> TeamLinkRead:
    _require_admin_on(session, is_admin, team_id)
    try:
        link, _changed = service.set_link(
            session,
            team_id,
            project_id,
            body.persona,
            body.site_node_id,
            confirm_sod=body.confirm_sod,
            actor_id=principal.user_id,
        )
    except service.SodConfirmationError as exc:
        raise _sod_conflict(exc.user_ids, exc.project_ids) from exc
    except SodConfirmationRequiredError as exc:  # drifted rows (T-0075)
        raise _sod_conflict(sorted(exc.user_ids, key=str), [exc.project_id]) from exc
    return _link(link)


@router.delete("/{team_id}/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def unlink(
    team_id: uuid.UUID,
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> Response:
    _require_admin_on(session, is_admin, team_id)
    service.unlink(session, team_id, project_id, actor_id=principal.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
