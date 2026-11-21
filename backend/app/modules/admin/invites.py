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

**The invite e-mail and the ``setup_token``** (T-0059, diff §4.1 External invitees create
their own password). Every send (creation, re-send) queues the invite e-mail through the
outbound mail service (sent by the worker after the commit, never from the request). Its link
is ``/invite#<invite_token>.<setup_token>``: the ``setup_token`` is a second 256-bit secret,
stored only as its SHA-256 (``setup_token_hash``), that never leaves this module except inside
that queued e-mail. It is not in ``IssuedInvite``, so no response, audit entry or log line can
carry it. It lives and dies with the invite: same expiry, rotated by a re-send, cleared when
the invite is accepted or revoked. Both tokens together let an account **without
credentials** set its first password, accept and sign in in one request
(``accept_with_password``); the copyable link (``invite_token`` only) never can. The password
is checked, stored and the session issued by core auth
(``AuthService.start_session_with_first_password``); this module never writes the users table.

Audit chains (tenant-level): ``project-invites:<project>`` (``create`` and ``resend`` with
``email_queued``, ``revoke``, ``first_password_set``, ``accept``, ``refuse`` with the reason)
and ``project-invites`` (``refuse`` of a token no invite matches). Never a token, a hash, a
password or an e-mail address: the invitee is named by account id.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
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
from app.core.auth.access import Grant, Persona, sod_warning, system_admin_holders
from app.core.auth.magic_link import MagicLinkCandidate, register_magic_link_eligibility
from app.core.auth.models import User, normalize_email
from app.core.auth.password_policy import PasswordPolicyError
from app.core.auth.post_auth import PostAuthOutcome, register_post_authentication_hook
from app.core.db import utcnow
from app.core.mail.addresses import InvalidAddressError
from app.core.mail.service import send_mail
from app.core.mail.templates import MailTemplate, TemplateError, register_template
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
    "INVITE_SIGN_IN_REQUIRED",
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
    "InviteSignInRequiredError",
    "InviteWrongUserError",
    "IssuedInvite",
    "SetupPreview",
    "accept_invite",
    "accept_with_password",
    "create_invite",
    "invited_field_worker",
    "list_invites",
    "preview_invite",
    "resend_invite",
    "revoke_invite",
    "setup_preview",
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
# 409: the account already has credentials; it signs in (and joins through the carry-through).
INVITE_SIGN_IN_REQUIRED: Final = "INVITE_SIGN_IN_REQUIRED"

# The invite e-mail (T-0059). ``link_token``: ``<invite_token>.<setup_token>``, the fragment of
# ``/invite``; the mail service puts it nowhere else.
INVITE_TEMPLATE: Final = register_template(
    MailTemplate(
        "admin.project_invite",
        params=("project_code", "project_name", "inviter_name", "expires_at"),
        link_path="/invite",
        secret="link_token",
    )
)

# The sender's name in the e-mail when the account row is gone or has no name.
UNKNOWN_INVITER: Final = "A project administrator"

_logger = logging.getLogger("kerp.admin.invites")


class InviteNotFoundError(LookupError):
    """No such invite on this project for the caller (404)."""


class InviteGoneError(Exception):
    """The token is expired, revoked, used or unknown (410, one neutral message)."""


class InviteWrongUserError(Exception):
    """The session's user is not the invited e-mail (403 ``INVITE_WRONG_USER``)."""


class InviteConflictError(Exception):
    """A concurrent request changed the invite first (409): a parallel accept, or a create,
    revoke or re-send that lost a race. Nothing was written by the loser."""


class InviteSignInRequiredError(Exception):
    """The invited account already has credentials: no first password is set (409
    ``INVITE_SIGN_IN_REQUIRED``). It signs in and joins through the carry-through."""


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


@dataclass(frozen=True, slots=True)
class SetupPreview:
    """What the e-mailed link shows before anyone is signed in. With credentials on the
    account: ``can_set_password`` false and nothing about the project (sign in instead)."""

    can_set_password: bool
    project_code: str | None = None
    project_name: str | None = None


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
    session: Session, invite: ProjectInvite, action: str, actor: uuid.UUID | None, **extra: object
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


# A revoked or accepted invite keeps no ``setup_token``: it dies with the invite.
_REVOKED: Final[dict[str, object]] = {"status": INVITE_REVOKED, "setup_token_hash": None}


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


# --- the invite e-mail ----------------------------------------------------------------------


def _queue_invite_mail(
    session: Session, invite: ProjectInvite, token: str, setup_token: str, sender_id: uuid.UUID
) -> bool:
    """Queue the invite e-mail in the caller's unit of work (sent by the worker after the
    commit; a rollback drops it). The only place the ``setup_token`` goes.

    ``False`` when the mail service cannot take it: an address it does not send to, or a
    text it refuses (too long). The invite itself stands and the copyable link is the
    fallback; a mail problem never fails an invite. Names are put on one line first (a
    project or display name may hold line breaks, which a mail text may not)."""
    project = projects.get_project(session, invite.invited_project_id)
    sender = users.users_by_id(session, [sender_id])
    name = "".join(u.display_name or u.email for u in sender.values())
    context = {
        "project_code": project.code,
        "project_name": _one_line(project.name),
        "inviter_name": _one_line(name) or UNKNOWN_INVITER,
        "expires_at": _aware(invite.expires_at).strftime("%Y-%m-%d %H:%M UTC"),
        "link_token": f"{token}.{setup_token}",
    }
    try:
        send_mail(session, INVITE_TEMPLATE.key, invite.invited_email, context)
    except (InvalidAddressError, TemplateError) as exc:
        # Only the type: the message of neither names a token, but nothing is left to chance.
        _logger.warning("invite e-mail of invite %s not queued: %s", invite.id, type(exc).__name__)
        return False
    return True


def _one_line(text: str) -> str:
    """``text`` with every run of whitespace (line breaks included) as one space."""
    return " ".join(text.split())


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
    is_admin = False
    if user is not None:
        member = memberships.project_member_grants(session, project_id).get(user.id, set())
        held = {m.grant.persona for m in member}
        is_admin = bool(system_admin_holders(session, context.tenant_id, [user.id]))
    warn = sod_warning([*(g.persona for g in wanted), *held], system_admin=is_admin)
    if warn and not confirm_sod:
        raise admin.SodConfirmationError(
            "PER-09 or system_admin together with a hold-authority persona:"
            " confirm with confirm_sod"
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
        if not _transition(session, earlier, OPEN, **_REVOKED, updated_by=actor_id):
            raise InviteConflictError("an earlier invite changed concurrently")
        _audit(session, earlier, "revoke", actor_id, user_id=str(user.id), reason="reinvited")
    token, setup_token = secrets.token_urlsafe(TOKEN_BYTES), secrets.token_urlsafe(TOKEN_BYTES)
    invite = ProjectInvite(
        tenant_id=context.tenant_id,
        project_id=None,
        invited_project_id=project_id,
        invited_email=address,
        grants=_grant_payload(wanted),
        inviter_id=actor_id,
        token_hash=token_digest(token),
        setup_token_hash=token_digest(setup_token),
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
        email_queued=_queue_invite_mail(session, invite, token, setup_token, actor_id),
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
    if not _transition(session, invite, OPEN, **_REVOKED, updated_by=actor_id):
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
    """Re-send an open invite: a fresh token and a fresh ``setup_token`` (the old links are
    gone at once), a new expiry, status ``pending``; e-mail, persona(s) and site scope are
    kept. Queues the invite e-mail with the new link. Returns the new ``invite_token`` only.

    ``InviteNotResendableError`` for an accepted or revoked invite, ``InviteRateLimitedError``
    at the send cap. The rotation is one conditional update on the current token hash and
    status: against a concurrent accept of the old token exactly one of the two wins.
    """
    invite = _project_invite(session, project_id, invite_id)
    if invite.status not in OPEN:
        raise InviteNotResendableError(f"the invite is {invite.status}")
    rows = _key_rows(session, project_id, invite.invited_email)
    _check_send_cap(rows, now)
    token, setup_token = secrets.token_urlsafe(TOKEN_BYTES), secrets.token_urlsafe(TOKEN_BYTES)
    try:
        rotated = _transition(
            session,
            invite,
            OPEN,
            token_hash=token_digest(token),
            setup_token_hash=token_digest(setup_token),
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
    _audit(
        session,
        invite,
        "resend",
        actor_id,
        expires_at=_aware(invite.expires_at).isoformat(),
        email_queued=_queue_invite_mail(session, invite, token, setup_token, actor_id),
    )
    return IssuedInvite(InviteEntry(invite, INVITE_PENDING, _next_send_at(rows, now)), token)


# --- the invitee's side: the narrow system path ---------------------------------------------


class _SystemPath:
    """Re-bind the session with ``is_system_admin`` for the narrow invite lookup, and back.

    The invite policy shows a row only to its project's administrators; the invitee reaches
    exactly one row, by token hash. ``bind_tenancy`` refuses to switch a session that holds
    objects, so they are flushed, detached and re-attached (the pattern of the access-request
    notifications). The caller's own context is restored even when the block raises.
    """

    def __init__(self, session: Session, user_id: uuid.UUID | None) -> None:
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
        try:
            if exc_type is None:
                self.session.flush()
        finally:  # a failing flush never leaves the session elevated
            self.session.expunge_all()
            try:
                bind_tenancy(self.session, self.context)
            except SQLAlchemyError:  # pragma: no cover - the transaction failed, rolls back
                self.session.info[SESSION_INFO_KEY] = self.context
            self.session.add_all(self.held)


@dataclass(frozen=True, slots=True)
class _Resolved:
    """What ``_resolve`` found: the pending invite of this user, or the refusal to raise once
    the system path is left (the refusal's audit entry is already flushed)."""

    invite: ProjectInvite | None = None
    refusal: Exception | None = None


def _refuse(
    session: Session,
    invite: ProjectInvite | None,
    user_id: uuid.UUID | None,
    stage: str,
    reason: str,
) -> None:
    """Audit a refusal. ``user_id`` is ``None`` while nobody is identified (the e-mailed
    link, before its tokens are proven)."""
    payload = {"action": "refuse", "stage": stage, "reason": reason}
    if user_id is not None:
        payload["user_id"] = str(user_id)
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
        elif invite is not None and not _mark_accepted(session, invite, user, now):
            # Lost a race. Rotated away by a re-send, or revoked: the token is gone. Accepted
            # by a parallel request with the same token: a conflict.
            lost = _lost_to(invite, digest)
            _refuse(session, invite, user.id, "accept", f"race_{lost}")
            refusal = (
                InviteConflictError("accepted concurrently")
                if lost == "used"
                else InviteGoneError(f"invite {lost}")
            )
        elif invite is not None:
            return _join(session, invite, user)
    raise refusal or InviteGoneError("invite gone")


def _mark_accepted(session: Session, invite: ProjectInvite, user: User, now: datetime) -> bool:
    return _transition(
        session,
        invite,
        [INVITE_PENDING],
        status=INVITE_ACCEPTED,
        setup_token_hash=None,
        accepted_by=user.id,
        accepted_at=now,
        updated_by=user.id,
    )


def _lost_to(invite: ProjectInvite, digest: str) -> str:
    """What a failed conditional update lost to (``invite`` was re-read by it)."""
    if invite.token_hash != digest:
        return "rotated"
    return "used" if invite.status == INVITE_ACCEPTED else "revoked"


def _join(session: Session, invite: ProjectInvite, user: User) -> uuid.UUID:
    """The membership of an invite just marked accepted, and its audit entry."""
    project_id = invite.invited_project_id
    chosen = _grants(invite)
    held = memberships.project_grants(session, project_id).get(user.id, set())
    # The inviter confirmed the PER-09 + hold-authority rule at creation; whatever the
    # user's grants are by now, the outcome is flagged in both audit entries.
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


def accept_invite(session: Session, token: str, user: User, *, now: datetime) -> uuid.UUID:
    """Accept the invite as ``user`` (the signed-in invitee): the direct membership and the
    invite's ``accepted`` mark in one transaction. Returns the project joined.

    The invite's grants are added to any direct grants the user already holds there.
    ``InviteWrongUserError`` (403), ``InviteGoneError`` (410) or, for the loser of two
    parallel accepts, ``InviteConflictError`` (409); each refusal is audited and grants
    nothing.
    """
    return _accept(session, token_digest(token), user, now)


# --- the e-mailed link: first password at acceptance (T-0059) -------------------------------


@dataclass(frozen=True, slots=True)
class _Setup:
    invite: ProjectInvite
    user: User
    project: tuple[str, str]  # code, name


def _resolve_setup(
    session: Session,
    tenant_id: uuid.UUID,
    invite_token: str,
    setup_token: str,
    stage: str,
    now: datetime,
) -> _Setup | None:
    """The pending invite both tokens of the e-mailed link name, with its live project and
    the invited active account. Runs on the system path; nobody is signed in. ``None`` after
    auditing the refusal (every one is the same neutral 410 for the caller): an unmatched
    ``invite_token``, a missing or wrong ``setup_token`` (the copied link has none), expired,
    revoked or used, a deleted project, a deactivated account."""
    invite = session.scalars(
        select(ProjectInvite).where(
            ProjectInvite.tenant_id == tenant_id,
            ProjectInvite.token_hash == token_digest(invite_token),
        )
    ).one_or_none()
    if invite is None:
        _refuse(session, None, None, stage, "unmatched_token")
        return None
    stored = invite.setup_token_hash or ""
    status = _effective_status(invite, now)
    if status == INVITE_EXPIRED and invite.status == INVITE_PENDING:
        _transition(session, invite, [INVITE_PENDING], status=INVITE_EXPIRED)  # stored lazily
    project = _live_project(session, invite.invited_project_id)
    user = users.find_user_by_email(session, invite.invited_email)
    if status != INVITE_PENDING:
        reason = "used" if status == INVITE_ACCEPTED else status
    elif not secrets.compare_digest(stored, token_digest(setup_token)):
        reason = "setup_token"
    elif project is None:
        reason = "project_deleted"
    elif user is None or not user.is_active:
        reason = "account_inactive"
    else:
        return _Setup(invite, user, project)
    _refuse(session, invite, None, stage, reason)
    return None


def setup_preview(
    session: Session, tenant_id: uuid.UUID, invite_token: str, setup_token: str, *, now: datetime
) -> SetupPreview:
    """What the e-mailed link (both tokens) shows before sign-in: project code and name and
    that a first password can be set, or, for an account that has credentials, only that it
    must sign in. ``InviteGoneError`` for any token problem (audited). Nothing is granted.
    ``session`` is the request's unbound session: it is bound to ``tenant_id`` here."""
    bind_tenancy(session, TenancyContext(tenant_id=tenant_id))
    with _SystemPath(session, None):
        found = _resolve_setup(session, tenant_id, invite_token, setup_token, "setup_preview", now)
        if found is not None and users.has_credentials(found.user):
            return SetupPreview(can_set_password=False)
        if found is not None:
            return SetupPreview(True, *found.project)
    raise InviteGoneError("invite gone")


def accept_with_password(
    session: Session,
    auth: users.AuthService,
    tenant_id: uuid.UUID,
    invite_token: str,
    setup_token: str,
    password: str,
    *,
    device_id: uuid.UUID | None = None,
    now: datetime,
) -> tuple[users.TokenPair, uuid.UUID]:
    """One request for a brand-new invitee holding the e-mailed link: set the first
    password, accept the invite (membership) and sign in, in one transaction. Returns the
    session's tokens and the project joined.

    Order: both tokens (``InviteGoneError``, 410, for any problem); the account has no
    credentials (``InviteSignInRequiredError``, 409); the invite row is locked by a
    conditional no-op update, so of two parallel calls the second waits and then finds the
    invite used (410); core auth checks the password policy, stores the hash and issues the
    session (``PasswordPolicyError``: nothing is changed and the invite stays pending); then
    the ``accepted`` mark, which also clears the ``setup_token``, and the membership. Every
    refusal is audited. This module never touches the users table.
    """
    bind_tenancy(session, TenancyContext(tenant_id=tenant_id))
    refusal: Exception = InviteGoneError("invite gone")
    with _SystemPath(session, None):
        found = _resolve_setup(session, tenant_id, invite_token, setup_token, _STAGE, now)
        if found is not None:
            digest = token_digest(invite_token)
            outcome = _first_password(session, auth, found, digest, password, device_id, now)
            if not isinstance(outcome, Exception):
                return outcome
            refusal = outcome
    raise refusal


_STAGE: Final = "accept_with_password"


def _first_password(
    session: Session,
    auth: users.AuthService,
    found: _Setup,
    digest: str,
    password: str,
    device_id: uuid.UUID | None,
    now: datetime,
) -> tuple[users.TokenPair, uuid.UUID] | Exception:
    """The writes of ``accept_with_password``, on the system path; a refusal is returned
    (audited) so the path is left normally and the entry commits."""
    invite, user = found.invite, found.user
    if users.has_credentials(user):
        _refuse(session, invite, user.id, _STAGE, "has_credentials")
        return InviteSignInRequiredError("the account has credentials")
    if not _transition(session, invite, [INVITE_PENDING]):  # the row lock (a no-op update)
        _refuse(session, invite, user.id, _STAGE, f"race_{_lost_to(invite, digest)}")
        return InviteGoneError("invite changed concurrently")
    try:
        pair = auth.start_session_with_first_password(
            session, user.id, password, device_id=device_id
        )
    except PasswordPolicyError as exc:
        _refuse(session, invite, user.id, _STAGE, exc.code.lower())
        return exc
    except users.CredentialsExistError:  # set concurrently (a password reset)
        _refuse(session, invite, user.id, _STAGE, "has_credentials")
        return InviteSignInRequiredError("the account has credentials")
    except users.AuthenticationError:  # deactivated concurrently
        _refuse(session, invite, user.id, _STAGE, "account_inactive")
        return InviteGoneError("account inactive")
    _audit(session, invite, "first_password_set", user.id, user_id=str(user.id))
    if not _mark_accepted(session, invite, user, now):  # pragma: no cover - row is locked
        raise RuntimeError("the invite changed under its row lock")
    return pair, _join(session, invite, user)


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
