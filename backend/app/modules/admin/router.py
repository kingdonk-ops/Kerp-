"""Admin API, mounted at ``/api/v1/admin`` (T-0014, diff §4.1).

Who may call what (the caller's ``system_admin`` flag is the one the request session was
bound with, loaded from the grants table at authentication, never from the request):

* ``GET/POST /users``, ``PATCH /users/{id}``: ``system_admin`` only (else 403).
* ``POST /users/lookup``: a PER-09 of any project or a ``system_admin`` (else 403); exact
  e-mail only, rate-limited per caller (429), every lookup audited.
* ``GET /projects/{p}/members``, ``PUT/DELETE /projects/{p}/members/{user}``: a PER-09 of
  ``p`` or a ``system_admin``. A non-member of ``p`` (or a deleted project) gets 404, a
  member without PER-09 403.

No endpoint takes or returns a tenant id. Tenant-level grants are not assignable here
(T-0039 adds their API, ``system_admin`` only).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.auth import service as auth_service
from app.core.auth.access import (
    CallerGrants,
    CallerIsSystemAdmin,
    Capability,
    Grant,
    Persona,
    project_access,
)
from app.core.auth.deps import CurrentPrincipal
from app.core.auth.lockout import LockedOutError, LockoutPolicy, ProgressiveLockout
from app.core.auth.runtime import AuthRuntime, get_auth_runtime
from app.core.db import DbSession
from app.modules.admin import service
from app.modules.admin.schemas import (
    GrantRead,
    MemberAssign,
    MemberAssigned,
    MemberRead,
    UserCreate,
    UserLookupRequest,
    UserRead,
    UserSummary,
    UserUpdate,
)

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


def _member(member: service.Member) -> dict[str, Any]:
    grants = sorted(member.grants, key=lambda g: (g.persona.value, str(g.site_node_id or "")))
    return {
        "user_id": member.user.id,
        "email": member.user.email,
        "display_name": member.user.display_name,
        "grants": [GrantRead(persona=g.persona, site_node_id=g.site_node_id) for g in grants],
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
    user = service.update_user(session, user_id, body, actor_id=principal.user_id)
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
    return [MemberRead.model_validate(_member(m)) for m in members]


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
    return MemberAssigned.model_validate({**_member(member), "sod_warning": warn})


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
    return Response(status_code=status.HTTP_204_NO_CONTENT)
