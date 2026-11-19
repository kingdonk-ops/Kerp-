"""Project invites (T-0052, diff §4.1 Invites; edge spec §6 amendments).

A PER-09 of a project (or a System admin) invites an e-mail address with persona(s) and site
scope. The account is created without credentials if none exists; **the membership is created
only when the invite is accepted**. Authorisation of the administrators' side is the router's;
this service writes and audits.

**The token.** ``secrets.token_urlsafe(32)`` (256 random bits). Only its SHA-256 is stored
(``token_hash``); the token is returned once, to the inviter, and is never logged, audited or
kept anywhere else. Core auth carries the same SHA-256 as the invite's *reference* where a
sign-in spans two requests (``InviteHook.reference``), so no store ever holds the token.

**Single use, atomic.** Every state change of an invite (accept, revoke, re-send, the lazy
``expired`` mark) is one conditional ``UPDATE ... WHERE id AND token_hash AND status IN (...)``
(``_transition``): the database lets exactly one of two racing statements match, on PostgreSQL
(the second waits on the row lock, then re-checks the ``WHERE``) and on SQLite alike. Two
parallel accepts: one joins, the other gets ``InviteConflictError`` (409). A re-send racing an
accept with the old token: the re-send replaces ``token_hash``, so whichever statement commits
first wins and the old token never matches again (``InviteGoneError``, 410).

**The invitee's side is a narrow system path.** The table's policy shows a row only to the
project's PER-09 holders and System admins. Preview, accept, the post-authentication hook and
the magic-link eligibility check therefore run inside ``_SystemPath``: the session is re-bound
with ``is_system_admin`` for exactly the lookup by token hash (or, for eligibility, by the
caller's own e-mail), the checks (hash, expiry, single use, bound e-mail) and the writes, and
bound back before it returns. Nothing of the invite reaches a caller whose e-mail differs.

**Send cap** per project + normalised e-mail, over invite creations and re-sends together:
at most one send per ``SEND_GAP`` (60 s) and ``SEND_DAILY`` (5) per ``SEND_WINDOW`` (24 h).
It is counted from the ``sent_at`` log of *every* row of that project and e-mail (accepted and
revoked ones included; rows are never deleted), so revoking and re-creating cannot reset it.

Audit chains (tenant-level): ``project-invites:<project>`` (``create``, ``resend``,
``revoke``, ``accept``, ``refuse`` with the reason) and ``project-invites`` (``refuse`` of a
token no invite matches). Never a token, a hash or an e-mail address: the invitee is named
by account id.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import secrets
import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth import memberships
from app.core.auth import service as users
from app.core.auth.access import Grant, Persona, sod_warning
from app.core.auth.magic_link import MagicLinkCandidate, register_magic_link_eligibility
from app.core.auth.models import User, normalize_email
from app.core.auth.post_auth import PostAuthOutcome, register_post_authentication_hook
from app.core.db import utcnow
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import SESSION_INFO_KEY, bind_tenancy, session_tenancy
from app.core.tenancy.scope import OutOfScopeError
from app.modules.admin import service as admin
from app.modules.admin.models import (
    INVITE_ACCEPTED,
    INVITE_EXPIRED,
    INVITE_PENDING,
    INVITE_REVOKED,
    ProjectInvite,
)
from app.modules.admin.schemas import UserCreate
from app.modules.projects import service as projects

__all__ = [
    "INVITE_CONFLICT",
    "INVITE_GONE",
    "INVITE_NOT_RESENDABLE",
    "INVITE_WRONG_USER",
    "SEND_DAILY",
    "SEND_GAP",
    "SEND_WINDOW",
    "InviteConflictError",
    "InviteEntry",
    "InviteGoneError",
    "InviteHook",
    "InviteNotFoundError",
    "InviteNotResendableError",
    "InvitePreview",
    "InviteRateLimitedError",
    "InviteWrongUserError",
    "IssuedInvite",
    "accept_invite",
    "create_invite",
    "invited_field_worker",
    "list_invites",
    "preview_invite",
    "resend_invite",
    "revoke_invite",
    "token_digest",
]

TOKEN_BYTES: Final = 32  # 256 random bits
CHAIN_PREFIX: Final = "project-invites:"
# Refusals of a token that matches no invite (unknown, or rotated away by a re-send).
UNMATCHED_CHAIN: Final = "project-invites"
SEND_GAP: Final = timedelta(seconds=60)
SEND_DAILY: Final = 5
SEND_WINDOW: Final = timedelta(hours=24)
OPEN: Final = (INVITE_PENDING, INVITE_EXPIRED)

# The ``code`` of the invitee-facing answers (and of ``invite_error`` after a sign-in).
INVITE_WRONG_USER: Final = "INVITE_WRONG_USER"  # 403: signed in as someone else
INVITE_GONE: Final = "INVITE_GONE"  # 410: expired, revoked, used or unknown (neutral)
INVITE_CONFLICT: Final = "INVITE_CONFLICT"  # 409: lost a race (parallel accept)
INVITE_NOT_RESENDABLE: Final = "INVITE_NOT_RESENDABLE"  # 409: accepted or revoked


class InviteNotFoundError(LookupError):
    """No such invite on this project for the caller (404)."""


class InviteGoneError(Exception):
    """The token is expired, revoked, used or unknown (410, one neutral message)."""


class InviteWrongUserError(Exception):
    """The session's user is not the invited e-mail (403 ``INVITE_WRONG_USER``)."""


class InviteConflictError(Exception):
    """A concurrent request changed the invite first (409): a parallel accept, or a create,
    revoke or re-send that lost a race. Nothing was written by the loser."""


class InviteNotResendableError(Exception):
    """Only a pending or expired (never accepted) invite is re-sent or revoked (409)."""


class InviteRateLimitedError(Exception):
    """The send cap of this project + e-mail is reached (429 with ``Retry-After``)."""

    def __init__(self, retry_at: datetime, now: datetime) -> None:
        super().__init__("too many invites sent to this address for this project")
        self.retry_at = retry_at
        self.retry_after = max(1, math.ceil((retry_at - now).total_seconds()))


@dataclass(frozen=True, slots=True)
class IssuedInvite:
    """An invite right after a send, with the token: returned once, never stored."""

    entry: InviteEntry
    token: str
    sod_warning: bool = False

    def __repr__(self) -> str:  # never the token
        return f"IssuedInvite(invite_id={self.entry.invite.id})"


@dataclass(frozen=True, slots=True)
class InviteEntry:
    """An open invite as its project's administrators see it."""

    invite: ProjectInvite
    status: str  # ``pending`` or ``expired`` (derived from ``expires_at``)
    resend_available_at: datetime | None  # ``None``: a re-send is allowed now


@dataclass(frozen=True, slots=True)
class InvitePreview:
    project_id: uuid.UUID
    project_code: str
    project_name: str
    inviter_name: str


def token_digest(token: str) -> str:
    """The only form of an invite token that is ever stored or carried: its SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(moment: datetime) -> datetime:
    """SQLite hands ``TIMESTAMPTZ`` back naive; every value was stored in UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _context(session: Session) -> TenancyContext:
    context = session_tenancy(session)
    if context is None or context.project_id is not None:
        raise RuntimeError("invites need a tenant-level bound session")
    return context


def _effective_status(invite: ProjectInvite, now: datetime) -> str:
    if invite.status == INVITE_PENDING and _aware(invite.expires_at) <= now:
        return INVITE_EXPIRED
    return invite.status


def _grants(invite: ProjectInvite) -> frozenset[Grant]:
    return frozenset(
        Grant(
            invite.invited_project_id,
            Persona(g["persona"]),
            uuid.UUID(g["site_node_id"]) if g.get("site_node_id") else None,
        )
        for g in invite.grants
    )


def _grant_payload(grants: Iterable[Grant]) -> list[dict[str, Any]]:
    return [
        {
            "persona": g.persona.value,
            "site_node_id": str(g.site_node_id) if g.site_node_id else None,
        }
        for g in sorted(grants, key=lambda g: (g.persona.value, str(g.site_node_id or "")))
    ]


def _audit(
    session: Session, invite: ProjectInvite, action: str, actor: uuid.UUID, **extra: object
) -> None:
    payload = {"action": action, "invite_id": str(invite.id), **extra}
    chain = f"{CHAIN_PREFIX}{invite.invited_project_id}"
    audit.append(session, chain, payload, actor_id=actor, source="api")


def _transition(
    session: Session, invite: ProjectInvite, statuses: Collection[str], **values: object
) -> bool:
    """Change ``invite`` only if it still has the token hash and one of the ``statuses`` it
    was read with: one conditional ``UPDATE``, so of two racing changes exactly one matches.
    ``invite`` is re-read either way. A bulk statement: the tenant is in the ``WHERE`` (the
    ORM write guard does not see it) and the record contract columns are maintained here."""
    statement = (
        update(ProjectInvite)
        .where(
            ProjectInvite.id == invite.id,
            ProjectInvite.tenant_id == invite.tenant_id,
            ProjectInvite.token_hash == invite.token_hash,
            ProjectInvite.status.in_(list(statuses)),
        )
        .values(**values, updated_at=utcnow(), revision=ProjectInvite.revision + 1)
        .execution_options(synchronize_session=False)
    )
    result = cast("CursorResult[Any]", session.execute(statement))
    session.refresh(invite)
    return result.rowcount == 1


# --- the send cap ---------------------------------------------------------------------------


def _key_rows(session: Session, project_id: uuid.UUID, email: str) -> list[ProjectInvite]:
    """Every invite of this project + e-mail the session sees, whatever its status."""
    query = select(ProjectInvite).where(
        ProjectInvite.tenant_id == _context(session).tenant_id,
        ProjectInvite.invited_project_id == project_id,
        ProjectInvite.invited_email == email,
    )
    return list(session.scalars(query.order_by(ProjectInvite.created_at, ProjectInvite.id)))


def _recent_sends(rows: Iterable[ProjectInvite], now: datetime) -> list[datetime]:
    sends = (_aware(datetime.fromisoformat(stamp)) for row in rows for stamp in row.sent_at)
    return sorted(moment for moment in sends if now - moment < SEND_WINDOW)


def _next_send_at(rows: Iterable[ProjectInvite], now: datetime) -> datetime | None:
    """When the next send for these rows' project + e-mail is allowed; ``None``: now."""
    sends = _recent_sends(rows, now)
    limits = []
    if sends and now - sends[-1] < SEND_GAP:
        limits.append(sends[-1] + SEND_GAP)
    if len(sends) >= SEND_DAILY:
        limits.append(sends[-SEND_DAILY] + SEND_WINDOW)
    return max(limits) if limits else None


def _check_send_cap(rows: Iterable[ProjectInvite], now: datetime) -> None:
    retry_at = _next_send_at(rows, now)
    if retry_at is not None:
        raise InviteRateLimitedError(retry_at, now)


def _sent_log(invite: ProjectInvite | None, now: datetime) -> list[str]:
    """The row's send log with this send added (entries past the window dropped)."""
    earlier = _recent_sends([invite], now) if invite is not None else []
    return [moment.isoformat() for moment in (*earlier, now)]


# --- the administrators' side ---------------------------------------------------------------


def create_invite(
    session: Session,
    project_id: uuid.UUID,
    email: str,
    grants: Iterable[Grant],
    *,
    confirm_sod: bool,
    actor_id: uuid.UUID,
    ttl: timedelta,
    now: datetime,
    internal_domains: Collection[str] | None = None,
) -> IssuedInvite:
    """Invite ``email`` to the project with ``grants``; returns the invite and its token.

    Order: the send cap first (``InviteRateLimitedError``: nothing created or revoked), then
    the PER-09 + hold-authority rule of T-0014 over the invited personas and the grants the
    account already holds on the project (``admin.SodConfirmationError`` without
    ``confirm_sod``), then the account (created without credentials if none exists), then
    every earlier open invite of this project + e-mail is revoked, then the new row. No
    membership is created.
    """
    context = _context(session)
    projects.get_project(session, project_id)
    address = normalize_email(email)
    wanted = frozenset(grants)
    if not wanted or any(g.project_id != project_id for g in wanted):
        raise ValueError("an invite needs at least one grant, all on its project")
    rows = _key_rows(session, project_id, address)
    _check_send_cap(rows, now)
    user = users.find_user_by_email(session, address)
    held: set[Persona] = set()
    if user is not None:
        member = memberships.project_member_grants(session, project_id).get(user.id, set())
        held = {m.grant.persona for m in member}
    warn = sod_warning([*(g.persona for g in wanted), *held])
    if warn and not confirm_sod:
        raise admin.SodConfirmationError(
            "PER-09 together with a hold-authority persona: confirm with confirm_sod"
        )
    if user is None:
        try:
            user = admin.create_user(
                session,
                UserCreate(email=address),
                actor_id=actor_id,
                internal_domains=internal_domains,
            )
        except admin.DuplicateUserError as exc:  # created concurrently: the caller retries
            raise InviteConflictError("the account was created concurrently") from exc
    for earlier in rows:
        if earlier.status not in OPEN:
            continue
        if not _transition(session, earlier, OPEN, status=INVITE_REVOKED, updated_by=actor_id):
            raise InviteConflictError("an earlier invite changed concurrently")
        _audit(session, earlier, "revoke", actor_id, user_id=str(user.id), reason="reinvited")
    token = secrets.token_urlsafe(TOKEN_BYTES)
    invite = ProjectInvite(
        tenant_id=context.tenant_id,
        project_id=None,
        invited_project_id=project_id,
        invited_email=address,
        grants=_grant_payload(wanted),
        inviter_id=actor_id,
        token_hash=token_digest(token),
        expires_at=now + ttl,
        last_sent_at=now,
        sent_at=_sent_log(None, now),
        status=INVITE_PENDING,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(invite)
    try:
        session.flush()
    except IntegrityError as exc:
        # A concurrent invite of the same e-mail landed first (partial unique index). The
        # unit of work is unusable now and rolls back.
        raise InviteConflictError("this address was invited concurrently") from exc
    _audit(
        session,
        invite,
        "create",
        actor_id,
        user_id=str(user.id),
        grants=invite.grants,
        sod_warning=warn,
        expires_at=invite.expires_at.isoformat(),
    )
    entry = InviteEntry(invite, INVITE_PENDING, _next_send_at([*rows, invite], now))
    return IssuedInvite(entry, token, warn)


def list_invites(session: Session, project_id: uuid.UUID, *, now: datetime) -> list[InviteEntry]:
    """The project's open invites: pending, and expired never accepted; accepted and revoked
    ones are left out. By e-mail. ``resend_available_at`` comes from the send cap."""
    projects.get_project(session, project_id)
    rows = list(
        session.scalars(
            select(ProjectInvite)
            .where(
                ProjectInvite.tenant_id == _context(session).tenant_id,
                ProjectInvite.invited_project_id == project_id,
            )
            .order_by(ProjectInvite.invited_email, ProjectInvite.created_at)
        )
    )
    by_email: dict[str, list[ProjectInvite]] = {}
    for row in rows:
        by_email.setdefault(row.invited_email, []).append(row)
    return [
        InviteEntry(
            row, _effective_status(row, now), _next_send_at(by_email[row.invited_email], now)
        )
        for row in rows
        if row.status in OPEN
    ]


def _project_invite(session: Session, project_id: uuid.UUID, invite_id: uuid.UUID) -> ProjectInvite:
    projects.get_project(session, project_id)
    invite = session.scalars(
        select(ProjectInvite).where(
            ProjectInvite.id == invite_id,
            ProjectInvite.tenant_id == _context(session).tenant_id,
            ProjectInvite.invited_project_id == project_id,
        )
    ).one_or_none()
    if invite is None:
        raise InviteNotFoundError("invite not found")
    return invite


def revoke_invite(
    session: Session, project_id: uuid.UUID, invite_id: uuid.UUID, *, actor_id: uuid.UUID
) -> None:
    """Revoke an open invite: its token stops working at once. An accepted or already revoked
    one raises ``InviteNotResendableError``; losing a race, ``InviteConflictError``."""
    invite = _project_invite(session, project_id, invite_id)
    if invite.status not in OPEN:
        raise InviteNotResendableError(f"the invite is {invite.status}")
    if not _transition(session, invite, OPEN, status=INVITE_REVOKED, updated_by=actor_id):
        raise InviteConflictError("the invite changed concurrently")
    _audit(session, invite, "revoke", actor_id)


def resend_invite(
    session: Session,
    project_id: uuid.UUID,
    invite_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    ttl: timedelta,
    now: datetime,
) -> IssuedInvite:
    """Re-send an open invite: a fresh token (the old link is gone at once), a new expiry,
    status ``pending``; e-mail, persona(s) and site scope are kept. Returns the new token.

    ``InviteNotResendableError`` for an accepted or revoked invite, ``InviteRateLimitedError``
    at the send cap. The rotation is one conditional update on the current token hash and
    status: against a concurrent accept of the old token exactly one of the two wins.
    """
    invite = _project_invite(session, project_id, invite_id)
    if invite.status not in OPEN:
        raise InviteNotResendableError(f"the invite is {invite.status}")
    rows = _key_rows(session, project_id, invite.invited_email)
    _check_send_cap(rows, now)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    try:
        rotated = _transition(
            session,
            invite,
            OPEN,
            token_hash=token_digest(token),
            expires_at=now + ttl,
            last_sent_at=now,
            sent_at=_sent_log(invite, now),
            status=INVITE_PENDING,
            updated_by=actor_id,
        )
    except IntegrityError as exc:  # another pending invite of this address appeared
        raise InviteConflictError("this address was invited concurrently") from exc
    if not rotated:
        if invite.status not in OPEN:  # accepted or revoked first
            raise InviteNotResendableError(f"the invite is {invite.status}")
        raise InviteRateLimitedError(now + SEND_GAP, now)  # re-sent concurrently
    _audit(session, invite, "resend", actor_id, expires_at=_aware(invite.expires_at).isoformat())
    return IssuedInvite(InviteEntry(invite, INVITE_PENDING, _next_send_at(rows, now)), token)


# --- the invitee's side: the narrow system path ---------------------------------------------


class _SystemPath:
    """Re-bind the session with ``is_system_admin`` for the narrow invite lookup, and back.

    The invite policy shows a row only to its project's administrators; the invitee reaches
    exactly one row, by token hash. ``bind_tenancy`` refuses to switch a session that holds
    objects, so they are flushed, detached and re-attached (the pattern of the access-request
    notifications). The caller's own context is restored even when the block raises.
    """

    def __init__(self, session: Session, user_id: uuid.UUID) -> None:
        self.session = session
        self.context = _context(session)
        self.elevated = dataclasses.replace(self.context, user_id=user_id, is_system_admin=True)
        self.held: list[object] = []

    def __enter__(self) -> None:
        self.session.flush()
        self.held = list(self.session.identity_map.values())
        self.session.expunge_all()
        bind_tenancy(self.session, self.elevated)

    def __exit__(self, exc_type: object, *_exc: object) -> None:
        if exc_type is None:
            self.session.flush()
        self.session.expunge_all()
        try:
            bind_tenancy(self.session, self.context)
        except SQLAlchemyError:  # pragma: no cover - the transaction failed and rolls back
            self.session.info[SESSION_INFO_KEY] = self.context
        self.session.add_all(self.held)


@dataclass(frozen=True, slots=True)
class _Resolved:
    """What ``_resolve`` found: the pending invite of this user, or the refusal to raise once
    the system path is left (the refusal's audit entry is already flushed)."""

    invite: ProjectInvite | None = None
    refusal: Exception | None = None


def _refuse(
    session: Session, invite: ProjectInvite | None, user_id: uuid.UUID, stage: str, reason: str
) -> None:
    payload = {"action": "refuse", "stage": stage, "reason": reason, "user_id": str(user_id)}
    if invite is None:
        audit.append(session, UNMATCHED_CHAIN, payload, actor_id=user_id, source="api")
    else:
        _audit(session, invite, **payload, actor=user_id)


def _resolve(session: Session, digest: str, user: User, stage: str, now: datetime) -> _Resolved:
    """The pending invite ``digest`` names, if ``user`` is its invitee. Runs on the system
    path. Every refusal is audited: an unmatched token, the wrong user (checked first, so a
    stranger learns nothing about the invite's state), then expired, revoked or used."""
    invite = session.scalars(
        select(ProjectInvite).where(
            ProjectInvite.tenant_id == user.tenant_id, ProjectInvite.token_hash == digest
        )
    ).one_or_none()
    if invite is None:
        _refuse(session, None, user.id, stage, "unmatched_token")
        return _Resolved(refusal=InviteGoneError("unknown invite token"))
    if invite.invited_email != normalize_email(user.email):
        _refuse(session, invite, user.id, stage, "wrong_user")
        return _Resolved(refusal=InviteWrongUserError("invited e-mail differs"))
    status = _effective_status(invite, now)
    if status == INVITE_EXPIRED and invite.status == INVITE_PENDING:
        _transition(session, invite, [INVITE_PENDING], status=INVITE_EXPIRED)  # stored lazily
    if status != INVITE_PENDING:
        reason = "used" if status == INVITE_ACCEPTED else status
        _refuse(session, invite, user.id, stage, reason)
        return _Resolved(refusal=InviteGoneError(f"invite {reason}"))
    return _Resolved(invite=invite)


def _live_project(session: Session, project_id: uuid.UUID) -> tuple[str, str] | None:
    """Code and name of the invite's project, or ``None`` once it is deleted."""
    try:
        project = projects.get_project(session, project_id)
    except OutOfScopeError:
        return None
    return project.code, project.name


def preview_invite(session: Session, token: str, user: User, *, now: datetime) -> InvitePreview:
    """What the "Join project" confirmation shows the invitee: project code and name and the
    inviter's name. ``InviteWrongUserError`` / ``InviteGoneError`` otherwise (audited)."""
    with _SystemPath(session, user.id):
        found = _resolve(session, token_digest(token), user, "preview", now)
        invite = found.invite
        refusal = found.refusal
        if invite is not None:
            project = _live_project(session, invite.invited_project_id)
            if project is not None:
                inviter = users.users_by_id(session, [invite.inviter_id])
                names = [u.display_name or u.email for u in inviter.values()]
                return InvitePreview(invite.invited_project_id, *project, "".join(names))
            _refuse(session, invite, user.id, "preview", "project_deleted")
            refusal = InviteGoneError("project deleted")
    raise refusal or InviteGoneError("invite gone")


def _accept(session: Session, digest: str, user: User, now: datetime) -> uuid.UUID:
    with _SystemPath(session, user.id):
        found = _resolve(session, digest, user, "accept", now)
        invite = found.invite
        refusal = found.refusal
        if invite is not None and _live_project(session, invite.invited_project_id) is None:
            _refuse(session, invite, user.id, "accept", "project_deleted")
            refusal = InviteGoneError("project deleted")
        elif invite is not None and not _transition(
            session,
            invite,
            [INVITE_PENDING],
            status=INVITE_ACCEPTED,
            accepted_by=user.id,
            accepted_at=now,
            updated_by=user.id,
        ):
            # Lost a race. Rotated away by a re-send, or revoked: the token is gone. Accepted
            # by a parallel request with the same token: a conflict.
            lost = "used" if invite.status == INVITE_ACCEPTED else "revoked"
            if invite.token_hash != digest:
                lost = "rotated"
            _refuse(session, invite, user.id, "accept", f"race_{lost}")
            refusal = (
                InviteConflictError("accepted concurrently")
                if lost == "used"
                else InviteGoneError(f"invite {lost}")
            )
        elif invite is not None:
            project_id = invite.invited_project_id
            chosen = _grants(invite)
            held = memberships.project_grants(session, project_id).get(user.id, set())
            # The inviter confirmed the PER-09 + hold-authority rule at creation; whatever
            # the user's grants are by now, the outcome is flagged in both audit entries.
            _member, warn = admin.assign_member(
                session, project_id, user.id, held | chosen, confirm_sod=True, actor_id=user.id
            )
            _audit(
                session,
                invite,
                "accept",
                user.id,
                user_id=str(user.id),
                grants=invite.grants,
                sod_warning=warn,
            )
            return project_id
    raise refusal or InviteGoneError("invite gone")


def accept_invite(session: Session, token: str, user: User, *, now: datetime) -> uuid.UUID:
    """Accept the invite as ``user`` (the signed-in invitee): the direct membership and the
    invite's ``accepted`` mark in one transaction. Returns the project joined.

    The invite's grants are added to any direct grants the user already holds there.
    ``InviteWrongUserError`` (403), ``InviteGoneError`` (410) or, for the loser of two
    parallel accepts, ``InviteConflictError`` (409); each refusal is audited and grants
    nothing.
    """
    return _accept(session, token_digest(token), user, now)


# --- core auth extension points -------------------------------------------------------------


class InviteHook:
    """The post-authentication hook core auth runs for a sign-in that carried an
    ``invite_token`` (``app.core.auth.post_auth``): the reference is the token's SHA-256, and
    acceptance is the same narrow path as ``accept_invite``. A refusal is an ``invite_error``
    code; it never fails the sign-in."""

    def reference(self, invite_token: str) -> str:
        return token_digest(invite_token)

    def after_authentication(self, session: Session, user: User, reference: str) -> PostAuthOutcome:
        try:
            return PostAuthOutcome(joined_project_id=_accept(session, reference, user, utcnow()))
        except InviteWrongUserError:
            return PostAuthOutcome(invite_error=INVITE_WRONG_USER)
        except InviteGoneError:
            return PostAuthOutcome(invite_error=INVITE_GONE)
        except InviteConflictError:
            return PostAuthOutcome(invite_error=INVITE_CONFLICT)


def invited_field_worker(session: Session, candidate: MagicLinkCandidate) -> bool:
    """The magic-link eligibility provider (T-0057 extension point, diff §4.2): the account
    carries a valid pending invite for its e-mail that names PER-14 Field Worker, on a live
    project. Invites naming only other personas never make an account eligible."""
    now = utcnow()
    with _SystemPath(session, candidate.user_id):
        rows = session.scalars(
            select(ProjectInvite).where(
                ProjectInvite.tenant_id == candidate.tenant_id,
                ProjectInvite.invited_email == candidate.email,
                ProjectInvite.status == INVITE_PENDING,
            )
        ).all()
        return any(
            _effective_status(row, now) == INVITE_PENDING
            and any(g.persona is Persona.FIELD_WORKER for g in _grants(row))
            and _live_project(session, row.invited_project_id) is not None
            for row in rows
        )


HOOK: Final = InviteHook()
# Registered at import: the module loader imports the admin router (and with it this module)
# at start-up. Core auth never imports a module (CLAUDE.md §6).
register_post_authentication_hook(HOOK)
register_magic_link_eligibility(invited_field_worker)
