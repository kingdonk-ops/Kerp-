"""Admin API, mounted at ``/api/v1/admin`` (T-0014, diff §4.1).

Who may call what (the caller's ``system_admin`` flag is the one the request session was
bound with, loaded from the grants table at authentication, never from the request):

* ``GET/POST /users``, ``PATCH /users/{id}``: ``system_admin`` only (else 403).
* ``POST /users/lookup``: a PER-09 of any project or a ``system_admin`` (else 403); exact
  e-mail only, rate-limited per caller (429), every lookup audited.
* ``GET /projects/{p}/members``, ``PUT/DELETE /projects/{p}/members/{user}``: a PER-09 of
  ``p`` or a ``system_admin``. A non-member of ``p`` (or a deleted project) gets 404, a
  member without PER-09 403. The list shows team-derived grants (``source: team``, the link
  id) next to individual ones; PUT and DELETE change individual grants only, and DELETE of a
  user whose access is only team-derived answers 409 ``managed_through_team`` (T-0073).
  A team-derived grant carries its team's name, from the teams service (T-0013).

* ``GET/POST /grants``, ``DELETE /grants/{user}/{grant}``: ``system_admin`` only (else 403):
  appoint and remove System admins and Project Admins (``create_projects``); the last System
  admin cannot be removed (409). Every grant and revoke is audited (T-0039, diff §4.1).
  ``system_admin`` for a user holding a hold-authority persona on a live project answers 409
  ``sod_confirmation_required`` unless ``confirm_sod`` (then ``sod_warning: true``, audited;
  ADR 0016). Assigning a hold-authority persona to a ``system_admin`` through the member
  endpoints warns the same way.

* Project invites (``invites_router.py``, T-0052): ``/projects/{p}/invites`` for a PER-09 of
  ``p`` or a ``system_admin``; ``/invites/preview`` and ``/invites/accept`` for the invitee.

* System console (``system_router.py``, T-0218): ``/system/queues...``, ``system_admin`` only
  (403 for everyone else, PER-09 included).

No endpoint takes or returns a tenant id. Tenant-level grants are not personas: they are
never assignable through the membership endpoints.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from datetime import timedelta
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.auth import service as auth_service
from app.core.auth.access import (
    SYSTEM_ADMIN,
    CallerGrants,
    CallerIsSystemAdmin,
    Capability,
    Grant,
    Persona,
    project_access,
)
from app.core.auth.deps import CurrentPrincipal
from app.core.auth.lockout import LockedOutError, LockoutPolicy, ProgressiveLockout
from app.core.auth.memberships import MemberGrant
from app.core.auth.models import User
from app.core.auth.runtime import AuthRuntime, get_auth_runtime
from app.core.db import DbSession
from app.modules.admin import service
from app.modules.admin.invites_router import router as invites_router
from app.modules.admin.schemas import (
    AdminGranted,
    AdminGrantIn,
    AdminGrantKind,
    AdminGrantRead,
    GrantSource,
    MemberAssign,
    MemberAssigned,
    MemberGrantRead,
    MemberRead,
    UserCreate,
    UserLookupRequest,
    UserRead,
    UserSummary,
    UserUpdate,
)
from app.modules.admin.system_router import router as system_router
from app.modules.teams import service as teams

router = APIRouter(tags=["admin"])

MEMBERS: Final = "/projects/{project_id}/members"
# Every lookup counts (there is nothing to get right): 20 back to back, then 1 min doubling
# up to 15 min, per caller and hour. Enough to staff a project, too slow to enumerate.
LOOKUP_POLICY: Final = LockoutPolicy(
    free_attempts=20,
    base_delay=timedelta(minutes=1),
    max_delay=timedelta(minutes=15),
    window=timedelta(hours=1),
)


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


def _require_system_admin(is_admin: CallerIsSystemAdmin) -> None:
    if not is_admin:
        raise _forbidden()


SystemAdminOnly = Depends(_require_system_admin)


def _require_project_admin(grants: list[Grant], is_admin: bool, project_id: uuid.UUID) -> None:
    """PER-09 of the project or ``system_admin``; a non-member gets 404, a member 403."""
    if is_admin:
        return
    if not project_access(grants, project_id).has(Capability.ADMINISTER):
        raise _forbidden()


def _grant(row: MemberGrant, team_names: Mapping[uuid.UUID, str]) -> MemberGrantRead:
    return MemberGrantRead(
        persona=row.grant.persona,
        site_node_id=row.grant.site_node_id,
        source=GrantSource.DIRECT if row.is_direct else GrantSource.TEAM,
        source_id=row.source_id,
        # Core leaves it out (it imports no module); the teams service names the link's team.
        team_name=team_names.get(row.source_id) if row.source_id is not None else None,
    )


def _team_names(session: Session, members: Iterable[service.Member]) -> dict[uuid.UUID, str]:
    """The team name of every team-derived grant of ``members`` (T-0013)."""
    link_ids = {g.source_id for m in members for g in m.grants if g.source_id is not None}
    return teams.link_team_names(session, link_ids)


def _managed_through_team(exc: service.ManagedThroughTeamError) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        {
            "code": "managed_through_team",
            "message": str(exc),
            "link_ids": [str(link) for link in exc.link_ids],
        },
    )


def _member(member: service.Member, team_names: Mapping[uuid.UUID, str]) -> dict[str, Any]:
    grants = sorted(
        member.grants,
        key=lambda g: (
            not g.is_direct,
            g.grant.persona.value,
            str(g.grant.site_node_id or ""),
            str(g.source_id or ""),
        ),
    )
    return {
        "user_id": member.user.id,
        "email": member.user.email,
        "display_name": member.user.display_name,
        "grants": [_grant(g, team_names) for g in grants],
    }


# --- users ----------------------------------------------------------------------------------


@router.get("/users", response_model=list[UserRead], dependencies=[SystemAdminOnly])
def list_users(session: DbSession) -> list[UserRead]:
    return [UserRead.model_validate(u) for u in auth_service.list_users(session)]


@router.post(
    "/users",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[SystemAdminOnly],
)
def create_user(
    body: UserCreate,
    principal: CurrentPrincipal,
    session: DbSession,
    runtime: Annotated[AuthRuntime, Depends(get_auth_runtime)],
) -> UserRead:
    domains = runtime.settings.internal_email_domains  # the same list sign-in uses
    try:
        user = service.create_user(
            session, body, actor_id=principal.user_id, internal_domains=domains
        )
    except service.DuplicateUserError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return UserRead.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserRead, dependencies=[SystemAdminOnly])
def update_user(
    user_id: uuid.UUID, body: UserUpdate, principal: CurrentPrincipal, session: DbSession
) -> UserRead:
    try:
        user = service.update_user(session, user_id, body, actor_id=principal.user_id)
    except service.LastSystemAdminError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return UserRead.model_validate(user)


@router.post("/users/lookup", response_model=UserSummary)
def lookup_user(
    body: UserLookupRequest,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
    runtime: Annotated[AuthRuntime, Depends(get_auth_runtime)],
) -> UserSummary:
    if not is_admin and not any(g.persona is Persona.PROJECT_ADMINISTRATOR for g in grants):
        raise _forbidden()
    try:
        ProgressiveLockout(runtime.store, LOOKUP_POLICY, "admin-user-lookup").begin(
            str(principal.user_id)
        )
    except LockedOutError as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many lookups",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    user = service.lookup_user(session, body.email, actor_id=principal.user_id)
    if user is None:
        # The miss is audited: commit it, then answer 404.
        session.commit()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return UserSummary.model_validate(user)


# --- memberships ----------------------------------------------------------------------------


@router.get(MEMBERS, response_model=list[MemberRead])
def list_members(
    project_id: uuid.UUID, grants: CallerGrants, is_admin: CallerIsSystemAdmin, session: DbSession
) -> list[MemberRead]:
    _require_project_admin(grants, is_admin, project_id)
    members = service.project_members(session, project_id)
    names = _team_names(session, members)
    return [MemberRead.model_validate(_member(m, names)) for m in members]


@router.put(f"{MEMBERS}/{{user_id}}", response_model=MemberAssigned)
def assign_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MemberAssign,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> MemberAssigned:
    _require_project_admin(grants, is_admin, project_id)
    wanted = [Grant(project_id, g.persona, g.site_node_id) for g in body.grants]
    try:
        member, warn = service.assign_member(
            session,
            project_id,
            user_id,
            wanted,
            confirm_sod=body.confirm_sod,
            actor_id=principal.user_id,
        )
    except service.SodConfirmationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "sod_confirmation_required", "sod_warning": True, "message": str(exc)},
        ) from exc
    names = _team_names(session, [member])
    return MemberAssigned.model_validate({**_member(member, names), "sod_warning": warn})


@router.delete(f"{MEMBERS}/{{user_id}}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    is_admin: CallerIsSystemAdmin,
    session: DbSession,
) -> Response:
    _require_project_admin(grants, is_admin, project_id)
    try:
        service.remove_member(session, project_id, user_id, actor_id=principal.user_id)
    except service.NotAMemberError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found") from exc
    except service.ManagedThroughTeamError as exc:
        raise _managed_through_team(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- tenant-level admin grants (T-0039) -----------------------------------------------------


def _admin_grant(user: User, kind: str) -> AdminGrantRead:
    return AdminGrantRead(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        grant=AdminGrantKind(kind),
    )


@router.get("/grants", response_model=list[AdminGrantRead], dependencies=[SystemAdminOnly])
def list_grants(session: DbSession, user_id: uuid.UUID | None = None) -> list[AdminGrantRead]:
    return [_admin_grant(u, kind) for u, kind in service.list_grants(session, user_id)]


@router.post(
    "/grants",
    response_model=AdminGranted,
    status_code=status.HTTP_201_CREATED,
    dependencies=[SystemAdminOnly],
    responses={
        status.HTTP_200_OK: {"model": AdminGranted, "description": "Already held"},
        status.HTTP_409_CONFLICT: {
            "description": "system_admin for a hold-authority holder needs confirm_sod"
        },
    },
)
def grant(
    body: AdminGrantIn, principal: CurrentPrincipal, session: DbSession, response: Response
) -> AdminGranted:
    user = auth_service.get_user(session, body.user_id)
    kind = body.grant.value
    try:
        created = service.grant(
            session, user.id, kind, actor_id=principal.user_id, confirm_sod=body.confirm_sod
        )
    except service.SodConfirmationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "sod_confirmation_required", "sod_warning": True, "message": str(exc)},
        ) from exc
    except service.GrantRaceError:
        # Another request granted it first. This request wrote nothing else, so its unit of
        # work is rolled back here and the answer is the sequential one: already held.
        session.rollback()
        created = False
    if not created:
        response.status_code = status.HTTP_200_OK
    warn = (
        created and kind == SYSTEM_ADMIN and bool(service.hold_authority_projects(session, user.id))
    )
    return AdminGranted(**_admin_grant(user, kind).model_dump(), sod_warning=warn)


@router.delete(
    "/grants/{user_id}/{grant}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[SystemAdminOnly],
)
def revoke(
    user_id: uuid.UUID, grant: AdminGrantKind, principal: CurrentPrincipal, session: DbSession
) -> Response:
    try:
        revoked = service.revoke(session, user_id, grant.value, actor_id=principal.user_id)
    except service.LastSystemAdminError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if not revoked:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- project invites (T-0052): ``invites_router.py`` ------------------------------------------

router.include_router(invites_router)

# --- System console: queue monitor (T-0218): ``system_router.py`` ------------------------------

router.include_router(system_router)
