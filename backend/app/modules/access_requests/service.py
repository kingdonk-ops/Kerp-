"""The access-request lifecycle (T-0050, diff §4.1; edge spec §6 amendments).

``pending -> approved | rejected`` by a PER-09 of the project or a System admin,
``pending -> withdrawn`` by the requester; decided and withdrawn rows are history. At most one
pending request per user and project. Every function needs a tenant-level session bound to the
caller (the request session): the table's policy shows a row only to its requester, the PER-09
holders of its project and System admins.

* **Filing** (``file_request``): the project must exist (narrow read
  ``projects.lobby_projects_by_id``, the ``projects`` policy is not widened); a caller with
  effective access, or with a pending request, gets 409. A **first** request must show how the
  project was found: an internal user may name a lobby-visible project; anyone else needs a
  lookup ticket of the code lookup for this user and project (``lobby.verify_lookup_ticket``),
  else 404, so a leaked project id alone is not enough. An earlier ``rejected`` or
  ``withdrawn`` request proves the discovery, so "Request again" needs no ticket (coordinator
  decision, PR #73 review round 2). After a rejection the **cooldown**
  (``KERP_ACCESS_REQUEST_COOLDOWN_HOURS``, 0 disables) must have passed; a withdrawal has none.
* **Notification**: the project's PER-09 holders get an in-app notification of a new request
  (``notifications.notify``, kind ``access_requests.requested``) in the same transaction. The
  notification is project-scoped, so the session is re-bound to the project for the write and
  back (``_ProjectBinding``); the requester reads nothing of the project through it. The
  requester gets no notification (not a member): ``mine`` is the outcome channel.
* **Approval** creates the direct membership through the admin service (``assign_member``:
  the PER-09 + hold-authority ``sod_warning`` / ``confirm_sod`` rule, audited on
  ``project-members:<project>``) in the same transaction. A request is never decided by its
  requester.

Audit chain (tenant-level): ``access-requests:<project>``: ``request``, ``withdraw``,
``approve`` (grants, ``sod_warning``), ``reject`` (reason); the actor is the requester or the
decider.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth import memberships
from app.core.auth import service as users
from app.core.auth.access import Grant, Persona, persona_holders
from app.core.auth.models import User
from app.core.auth.tokens import TokenService
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import SESSION_INFO_KEY, bind_tenancy, session_tenancy
from app.modules.access_requests.models import (
    APPROVED,
    PENDING,
    REJECTED,
    WITHDRAWN,
    AccessRequest,
)
from app.modules.admin import service as admin
from app.modules.lobby import service as lobby
from app.modules.notifications import service as notifications
from app.modules.notifications.schemas import NotificationCreate
from app.modules.projects import service as projects

__all__ = [
    "PANEL_WINDOW",
    "AccessRequestNotFoundError",
    "AlreadyMemberError",
    "AlreadyPendingError",
    "CooldownError",
    "MineEntry",
    "NotAllowedError",
    "NotPendingError",
    "PendingEntry",
    "approve",
    "file_request",
    "list_mine",
    "list_pending",
    "reject",
    "withdraw",
]

CHAIN_PREFIX: Final = "access-requests:"
NOTIFICATION_KIND: Final = "access_requests.requested"
SUBJECT_TYPE: Final = "access_requests.request"
# The lobby panel shows decided requests of the last 30 days (planner default, diff §4.1).
PANEL_WINDOW: Final = timedelta(days=30)
_PARAM_MAX: Final = 200  # notification parameter values are at most 200 characters


class AccessRequestNotFoundError(LookupError):
    """No such project or request for the caller, or the project was not found legitimately
    (no valid lookup ticket where one is needed): 404, never a hint which."""


class AlreadyMemberError(ValueError):
    """The caller already has effective access to the project (409)."""


class AlreadyPendingError(ValueError):
    """The caller already has a pending request for the project (409)."""


class CooldownError(ValueError):
    """The re-request cooldown after a rejection has not passed yet (409)."""

    def __init__(self, retry_after: datetime) -> None:
        super().__init__(f"a new request is allowed from {retry_after.isoformat()}")
        self.retry_after = retry_after


class NotAllowedError(PermissionError):
    """The caller sees the request but may not decide it (403)."""


class NotPendingError(ValueError):
    """Only a pending request can be withdrawn, approved or rejected (409)."""


@dataclass(frozen=True, slots=True)
class MineEntry:
    request: AccessRequest
    project: projects.LobbyRef
    request_again_at: datetime | None


@dataclass(frozen=True, slots=True)
class PendingEntry:
    request: AccessRequest
    project: projects.LobbyRef
    requester: User


def _context(session: Session) -> TenancyContext:
    context = session_tenancy(session)
    if context is None or context.project_id is not None or context.user_id is None:
        raise RuntimeError("access requests need a tenant-level session bound to the caller")
    return context


def _audit(
    session: Session,
    request: AccessRequest,
    action: str,
    actor: uuid.UUID,
    **extra: object,
) -> None:
    payload = {
        "action": action,
        "request_id": str(request.id),
        "user_id": str(request.requester_id),
        **extra,
    }
    chain = f"{CHAIN_PREFIX}{request.requested_project_id}"
    audit.append(session, chain, payload, actor_id=actor, source="api")


def _grant_payload(grants: Iterable[Grant]) -> list[dict[str, str | None]]:
    return [
        {
            "persona": g.persona.value,
            "site_node_id": str(g.site_node_id) if g.site_node_id else None,
        }
        for g in sorted(grants, key=lambda g: (g.persona.value, str(g.site_node_id or "")))
    ]


def _own_requests(
    session: Session, user_id: uuid.UUID, project_id: uuid.UUID | None = None
) -> list[AccessRequest]:
    query = select(AccessRequest).where(AccessRequest.requester_id == user_id)
    if project_id is not None:
        query = query.where(AccessRequest.requested_project_id == project_id)
    return list(session.scalars(query.order_by(AccessRequest.created_at, AccessRequest.id)))


def _aware(moment: datetime) -> datetime:
    """SQLite hands ``TIMESTAMPTZ`` back naive; every value was stored in UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _retry_after(
    rows: Iterable[AccessRequest], cooldown: timedelta, now: datetime
) -> datetime | None:
    """The end of the re-request cooldown of the latest rejection while it is still running
    (``now`` before it), else ``None``. A zero cooldown never runs."""
    rejected = [
        _aware(r.decided_at) for r in rows if r.status == REJECTED and r.decided_at is not None
    ]
    if not rejected or cooldown <= timedelta(0):
        return None
    until = max(rejected) + cooldown
    return until if now < until else None


# --- filing ---------------------------------------------------------------------------------


def file_request(
    session: Session,
    project_id: uuid.UUID,
    *,
    message: str | None,
    lookup_ticket: str | None,
    caller_grants: Iterable[Grant],
    is_internal: bool,
    tokens: TokenService,
    cooldown: timedelta,
    now: datetime,
) -> tuple[AccessRequest, projects.LobbyRef]:
    """File a pending request for ``project_id`` as the session's user (see the module doc
    for the rules); notifies the project's PER-09 holders and audits it."""
    context = _context(session)
    user_id = context.user_id
    assert user_id is not None  # _context
    project = projects.lobby_projects_by_id(session, [project_id]).get(project_id)
    if project is None:
        raise AccessRequestNotFoundError("project not found")
    if any(g.project_id == project_id for g in caller_grants):
        raise AlreadyMemberError("you already have access to this project")
    earlier = _own_requests(session, user_id, project_id)
    if any(r.status == PENDING for r in earlier):
        raise AlreadyPendingError("you already have a pending request for this project")
    if not _discovered(project, earlier, lookup_ticket, is_internal, tokens, context):
        raise AccessRequestNotFoundError("project not found")
    retry_after = _retry_after(earlier, cooldown, now)
    if retry_after is not None:
        raise CooldownError(retry_after)
    request = AccessRequest(
        tenant_id=context.tenant_id,
        project_id=None,
        requester_id=user_id,
        requested_project_id=project_id,
        message=message or None,
        status=PENDING,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(request)
    try:
        session.flush()
    except IntegrityError as exc:
        # A concurrent request of the same user for the same project landed first (partial
        # unique index). The unit of work is unusable now and rolls back.
        raise AlreadyPendingError("you already have a pending request for this project") from exc
    _audit(session, request, "request", user_id, has_message=request.message is not None)
    _notify_project_admins(session, context, request, project)
    return request, project


def _discovered(
    project: projects.LobbyRef,
    earlier: Iterable[AccessRequest],
    ticket: str | None,
    is_internal: bool,
    tokens: TokenService,
    context: TenancyContext,
) -> bool:
    """Whether the caller found the project legitimately (diff §4.1)."""
    if any(r.status in (REJECTED, WITHDRAWN) for r in earlier):
        return True  # "Request again": the earlier row proves the discovery
    if is_internal and project.visible_in_lobby:
        return True  # listed in the lobby the caller can see
    if ticket is None:
        return False
    assert context.user_id is not None
    try:
        lobby.verify_lookup_ticket(
            tokens,
            ticket,
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            project_id=project.id,
        )
    except lobby.InvalidLookupTicketError:
        return False
    return True


class _ProjectBinding:
    """Re-bind the tenant-level session to one project and back, in the same transaction
    (``bind_tenancy`` refuses to switch a session holding objects, so they are flushed,
    detached and re-attached). The pattern of the validation module's project seeding."""

    def __init__(self, session: Session, context: TenancyContext, project_id: uuid.UUID) -> None:
        self.session, self.context = session, context
        self.project = dataclasses.replace(context, project_id=project_id, site_node_ids=None)
        self.held: list[object] = []

    def __enter__(self) -> None:
        self.session.flush()
        self.held = list(self.session.identity_map.values())
        self.session.expunge_all()
        bind_tenancy(self.session, self.project)

    def __exit__(self, exc_type: object, *_exc: object) -> None:
        if exc_type is None:
            self.session.flush()
        self.session.expunge_all()
        try:
            bind_tenancy(self.session, self.context)
        except SQLAlchemyError:  # pragma: no cover - the transaction failed and rolls back
            self.session.info[SESSION_INFO_KEY] = self.context
        self.session.add_all(self.held)


def _notify_project_admins(
    session: Session,
    context: TenancyContext,
    request: AccessRequest,
    project: projects.LobbyRef,
) -> None:
    """An in-app notification to every PER-09 holder of the project (T-0019)."""
    requester = users.get_user(session, request.requester_id)
    label = (requester.display_name or requester.email)[:_PARAM_MAX]
    actor = request.requester_id
    with _ProjectBinding(session, context, project.id):
        holders = persona_holders(
            session, context.tenant_id, project.id, Persona.PROJECT_ADMINISTRATOR
        )
        for holder in holders:
            data = NotificationCreate(
                recipient_id=holder,
                kind=NOTIFICATION_KIND,
                params={"project_code": project.code[:_PARAM_MAX], "requester": label},
                label="access_requests",
                priority="normal",
                subject_type=SUBJECT_TYPE,
                subject_id=request.id,
            )
            notifications.notify(session, data, actor_id=actor)


# --- the requester's side -------------------------------------------------------------------


def withdraw(session: Session, request_id: uuid.UUID, *, now: datetime) -> MineEntry:
    """Withdraw the caller's own pending request (someone else's, or one for a project since
    deleted: not found; not pending: ``NotPendingError``). No cooldown follows."""
    context = _context(session)
    request = _locked(session, request_id)
    if request is None or request.requester_id != context.user_id:
        raise AccessRequestNotFoundError("access request not found")
    project_id = request.requested_project_id
    project = projects.lobby_projects_by_id(session, [project_id]).get(project_id)
    if project is None:
        raise AccessRequestNotFoundError("project not found")
    if request.status != PENDING:
        raise NotPendingError(f"the request is {request.status}, not pending")
    request.status = WITHDRAWN
    request.withdrawn_at = now
    request.updated_by = context.user_id
    session.flush()
    assert context.user_id is not None
    _audit(session, request, "withdraw", context.user_id)
    return MineEntry(request, project, None)


def list_mine(session: Session, *, cooldown: timedelta, now: datetime) -> list[MineEntry]:
    """The caller's "Active requests": per project the latest request, when it is pending or
    was decided or withdrawn within ``PANEL_WINDOW`` (so every pending request, and the latest
    decided one of each other project), newest first. Projects since deleted are left out."""
    context = _context(session)
    assert context.user_id is not None
    latest: dict[uuid.UUID, AccessRequest] = {}
    rows = _own_requests(session, context.user_id)
    for row in rows:  # oldest first, so the last one per project wins
        latest[row.requested_project_id] = row
    shown = [
        r
        for r in latest.values()
        if r.status == PENDING
        or (r.closed_at is not None and now - _aware(r.closed_at) <= PANEL_WINDOW)
    ]
    refs = projects.lobby_projects_by_id(session, {r.requested_project_id for r in shown})
    entries = []
    for request in shown:
        project = refs.get(request.requested_project_id)
        if project is None:
            continue
        again = None
        if request.status == REJECTED:
            again = _retry_after([request], cooldown, now)
        entries.append(MineEntry(request, project, again))
    return sorted(entries, key=lambda e: e.request.created_at, reverse=True)


# --- the project administrators' side -------------------------------------------------------


def _locked(session: Session, request_id: uuid.UUID) -> AccessRequest | None:
    """The request if the session sees it, locked until the transaction ends (two deciders
    at once: the second waits, then sees it decided)."""
    query = select(AccessRequest).where(AccessRequest.id == request_id).with_for_update()
    return session.scalars(query).one_or_none()


def _decidable(
    session: Session, request_id: uuid.UUID, *, is_system_admin: bool
) -> tuple[TenancyContext, AccessRequest]:
    context = _context(session)
    request = _locked(session, request_id)
    if request is None:
        raise AccessRequestNotFoundError("access request not found")
    administers = request.requested_project_id in context.admin_project_ids
    if not (is_system_admin or administers) or request.requester_id == context.user_id:
        raise NotAllowedError("only the project's administrators decide this request")
    if request.status != PENDING:
        raise NotPendingError(f"the request is {request.status}, not pending")
    return context, request


def list_pending(
    session: Session, *, project_id: uuid.UUID | None, is_system_admin: bool
) -> list[PendingEntry]:
    """Pending requests of the projects the caller administers (PER-09), or of every project
    for a System admin; only ``project_id``'s with it. Oldest first."""
    context = _context(session)
    query = select(AccessRequest).where(AccessRequest.status == PENDING)
    if project_id is not None:
        query = query.where(AccessRequest.requested_project_id == project_id)
    rows = [
        r
        for r in session.scalars(query.order_by(AccessRequest.created_at, AccessRequest.id))
        if is_system_admin or r.requested_project_id in context.admin_project_ids
    ]
    refs = projects.lobby_projects_by_id(session, {r.requested_project_id for r in rows})
    accounts = users.users_by_id(session, {r.requester_id for r in rows})
    return [
        PendingEntry(r, refs[r.requested_project_id], accounts[r.requester_id])
        for r in rows
        if r.requested_project_id in refs and r.requester_id in accounts
    ]


def approve(
    session: Session,
    request_id: uuid.UUID,
    grants: Iterable[tuple[Persona, uuid.UUID | None]],
    *,
    confirm_sod: bool,
    is_system_admin: bool,
    now: datetime,
) -> tuple[AccessRequest, bool]:
    """Approve a pending request: the requester gets ``grants`` (persona, site node) on the
    project (added to any direct grants they hold already) in this transaction. Returns the
    request and
    ``sod_warning``; PER-09 with a hold-authority persona needs ``confirm_sod``
    (``admin.SodConfirmationError``)."""
    context, request = _decidable(session, request_id, is_system_admin=is_system_admin)
    actor = context.user_id
    assert actor is not None
    project_id, user_id = request.requested_project_id, request.requester_id
    chosen = {Grant(project_id, persona, site) for persona, site in grants}
    held = memberships.project_grants(session, project_id).get(user_id, set())
    _member, warn = admin.assign_member(
        session, project_id, user_id, held | chosen, confirm_sod=confirm_sod, actor_id=actor
    )
    request.status = APPROVED
    request.decided_by = actor
    request.decided_at = now
    request.granted = _grant_payload(chosen)
    request.updated_by = actor
    session.flush()
    _audit(session, request, "approve", actor, grants=request.granted, sod_warning=warn)
    return request, warn


def reject(
    session: Session,
    request_id: uuid.UUID,
    *,
    reason: str | None,
    is_system_admin: bool,
    now: datetime,
) -> AccessRequest:
    """Reject a pending request with an optional reason (shown to the requester); the
    re-request cooldown runs from ``now``."""
    context, request = _decidable(session, request_id, is_system_admin=is_system_admin)
    actor = context.user_id
    assert actor is not None
    request.status = REJECTED
    request.decided_by = actor
    request.decided_at = now
    request.rejection_reason = reason or None
    request.updated_by = actor
    session.flush()
    _audit(session, request, "reject", actor, reason=request.rejection_reason)
    return request
