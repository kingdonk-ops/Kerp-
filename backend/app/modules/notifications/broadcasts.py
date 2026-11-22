"""Broadcast e-mail (T-0228, diff §4.7 *Broadcast e-mail*, §4.1, TD-399).

A System admin e-mails all active users of the tenant, the users of one or more projects, an
audience category or personas; a PER-09 only the users with a live membership on its own
PER-09 projects. The audience is resolved here, on the server, from core auth's service
(``auth_service.list_users`` / ``users_by_id``, ``memberships.member_grants``): never by
reading another module's tables. The sender sees a count, never a list of addresses.

* ``preview``: the recipient count. ``create``: the broadcast row, one ``pending`` recipient
  row per resolved user, the audit entry and a ``batch`` job (``DISPATCH_KIND``), all in the
  caller's unit of work; at most ``RATE_LIMIT`` per sender per ``RATE_WINDOW``.
* ``dispatch`` (the job, in the worker): per pending recipient, re-checks that the user is
  still active, not opted out and not suppressed, then calls ``send_mail`` with the
  ``broadcast_id`` and a fresh opaque opt-out token (kept only as its SHA-256).
* ``unsubscribe``: the one-click opt-out (RFC 8058). Every token, known, used, unknown or
  malformed, gets the same answer from the router; a known unused one records the opt-out.
  An opt-out applies to broadcasts only: transactional mail never reads it.

Audit (tenant-level chain ``notifications-broadcast:<id>``): sender, scope, project ids,
audience filter and counts; never an address, the subject or the body.
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth import memberships
from app.core.auth import service as auth_service
from app.core.auth.access import Grant, Persona
from app.core.auth.models import User
from app.core.db import Database, utcnow
from app.core.digest import sha256_hex
from app.core.mail import service as mail_service
from app.core.mail.addresses import normalize_address
from app.core.mail.templates import MailTemplate, register_template
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import OutOfScopeError, get_or_404
from app.core.timestamps import aware_utc
from app.modules.jobs import service as jobs
from app.modules.jobs.registry import JOB_REGISTRY
from app.modules.notifications.models import (
    Broadcast,
    BroadcastOptOut,
    BroadcastRecipient,
)
from app.modules.projects import service as projects

logger = logging.getLogger("kerp.notifications.broadcast")

TEMPLATE_KEY: Final = "notifications.broadcast"
# The SPA's opt-out endpoint: the token in the JSON body, never in a URL.
UNSUBSCRIBE_PATH: Final = "/api/v1/notifications/broadcasts/unsubscribe"
# The RFC 8058 one-click endpoint the mail client posts to (``List-Unsubscribe``). The token
# has to be in that URI (in the query string, which the access log never records); it is a
# machine endpoint for mail providers, kept out of the OpenAPI schema like the SNS endpoint.
ONE_CLICK_PATH: Final = f"{UNSUBSCRIBE_PATH}/one-click"
# The SPA page a person opens from the link in the body (token in the fragment); it posts the
# token to ``UNSUBSCRIBE_PATH``.
UNSUBSCRIBE_PAGE: Final = "/unsubscribe"
DISPATCH_KIND: Final = "notifications.broadcast_dispatch"
DISPATCH_TIMEOUT_S: Final = 1800
DISPATCH_CHUNK: Final = 100
# Planner default (task T-0228): 5 broadcasts per sender per 24 hours.
RATE_LIMIT: Final = 5
RATE_WINDOW: Final = timedelta(hours=24)
CATEGORIES: Final = ("internal", "external", "client")
MAX_PROJECTS: Final = 100
MAX_LIST: Final = 200
TOKEN_BYTES: Final = 32  # 256 bits
_TOKEN: Final = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

BROADCAST_TEMPLATE: Final = register_template(
    MailTemplate(
        TEMPLATE_KEY,
        params=("subject", "message"),
        link_path=UNSUBSCRIBE_PAGE,
        secret="unsubscribe_token",
        multiline_param="message",
        list_unsubscribe_path=ONE_CLICK_PATH,
    )
)


class BroadcastForbiddenError(Exception):
    """The caller may not broadcast (to this scope): 403."""


class NoRecipientsError(ValueError):
    """The audience resolves to nobody: 422 (nothing is queued)."""


class BroadcastRateLimitedError(Exception):
    """The sender queued ``RATE_LIMIT`` broadcasts in the last ``RATE_WINDOW``: 429."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("broadcast rate limit")
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class Sender:
    """Who asks, from the request's verified scope: ``system_admin`` (the caller's own grant)
    and the projects where the caller holds PER-09 (its memberships)."""

    user_id: uuid.UUID
    system_admin: bool
    admin_project_ids: frozenset[uuid.UUID]

    @classmethod
    def of(cls, user_id: uuid.UUID, *, system_admin: bool, grants: Iterable[Grant]) -> Sender:
        own = frozenset(g.project_id for g in grants if g.persona is Persona.PROJECT_ADMINISTRATOR)
        return cls(user_id, system_admin, own)

    @property
    def may_broadcast(self) -> bool:
        return self.system_admin or bool(self.admin_project_ids)


@dataclass(frozen=True, slots=True)
class Audience:
    """``scope`` ``tenant`` (every active user; System admin only) or ``projects`` (the users
    with a live membership on ``project_ids``), narrowed by ``category`` (``internal`` =
    internal staff, ``external`` = not internal, ``client`` = PER-15 holders) and ``personas``
    (holds any of them), both on the targeted projects (any project for ``tenant``)."""

    scope: str
    project_ids: frozenset[uuid.UUID] = frozenset()
    category: str | None = None
    personas: frozenset[Persona] = frozenset()

    def as_record(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "personas": sorted(p.value for p in self.personas),
        }

    def project_list(self) -> list[str]:
        return sorted(str(p) for p in self.project_ids)


# --- audience -------------------------------------------------------------------------------


def check_audience(session: Session, sender: Sender, audience: Audience) -> None:
    """Refuse an audience the sender may not reach.

    Neither System admin nor PER-09 anywhere, or a PER-09 asking for the whole tenant:
    ``BroadcastForbiddenError`` (403). A project that is not one of a PER-09's own PER-09
    projects, or that the session cannot see (unknown, deleted, another tenant's):
    ``OutOfScopeError`` (404), so a foreign project's existence is never confirmed."""
    if not sender.may_broadcast:
        raise BroadcastForbiddenError("not a System admin or Project Administrator")
    if audience.scope == "tenant":
        if not sender.system_admin:
            raise BroadcastForbiddenError("only a System admin broadcasts tenant-wide")
        return
    if not sender.system_admin and not audience.project_ids <= sender.admin_project_ids:
        raise OutOfScopeError("project not found")
    visible = {p.id for p in projects.list_projects(session, audience.project_ids)}
    if visible != set(audience.project_ids):
        raise OutOfScopeError("project not found")


def _address(user: User) -> str | None:
    try:
        return normalize_address(user.email)
    except ValueError:
        return None


def opted_out_user_ids(session: Session, user_ids: Collection[uuid.UUID]) -> set[uuid.UUID]:
    """The users among ``user_ids`` who opted out of broadcasts (live opt-out rows)."""
    found: set[uuid.UUID] = set()
    ids = list(user_ids)
    for start in range(0, len(ids), mail_service.SUPPRESSION_LOOKUP_CHUNK):
        chunk = ids[start : start + mail_service.SUPPRESSION_LOOKUP_CHUNK]
        found.update(
            session.scalars(
                select(BroadcastOptOut.user_id).where(BroadcastOptOut.user_id.in_(chunk))
            )
        )
    return found


def _matches(user: User, held: set[Persona], audience: Audience) -> bool:
    if not user.is_active:
        return False
    if audience.category == "internal" and not user.is_internal:
        return False
    if audience.category == "external" and user.is_internal:
        return False
    if audience.category == "client" and Persona.CLIENT_REPRESENTATIVE not in held:
        return False
    return not audience.personas or bool(held & audience.personas)


def resolve(session: Session, audience: Audience) -> list[User]:
    """The users the broadcast goes to: active, matching the filters, not opted out and not on
    the suppression list. Through core auth's and core mail's services only."""
    by_persona = audience.category == "client" or bool(audience.personas)
    if audience.scope == "tenant":
        users = {u.id: u for u in auth_service.list_users(session)}
        grants = memberships.member_grants(session) if by_persona else {}
    else:
        grants = memberships.member_grants(session, audience.project_ids)
        users = auth_service.users_by_id(session, grants.keys())
    selected = [
        user
        for user in users.values()
        if _matches(user, {g.persona for g in grants.get(user.id, ())}, audience)
    ]
    opted = opted_out_user_ids(session, [u.id for u in selected])
    suppressed = mail_service.suppressed_addresses(session, [u.email for u in selected])
    deliverable = [u for u in selected if _skip_reason(u, opted, suppressed) is None]
    return sorted(deliverable, key=lambda user: str(user.id))


def preview(session: Session, sender: Sender, audience: Audience) -> int:
    """How many users the broadcast would reach now (a count only)."""
    check_audience(session, sender, audience)
    return len(resolve(session, audience))


# --- queueing -------------------------------------------------------------------------------


def _retry_after(session: Session, sender_id: uuid.UUID, now: datetime) -> int | None:
    """Seconds until the sender may queue again, ``None`` while under ``RATE_LIMIT``."""
    recent = sorted(
        aware_utc(moment)
        for moment in session.scalars(
            select(Broadcast.created_at).where(
                Broadcast.sender_id == sender_id,
                Broadcast.status != "draft",
                Broadcast.created_at >= now - RATE_WINDOW,
            )
        )
    )
    if len(recent) < RATE_LIMIT:
        return None
    oldest = recent[-RATE_LIMIT]
    return max(1, int((oldest + RATE_WINDOW - now).total_seconds()) + 1)


def chain_key(broadcast_id: uuid.UUID) -> str:
    return f"notifications-broadcast:{broadcast_id}"


def create(
    session: Session, sender: Sender, audience: Audience, *, subject: str, body: str
) -> Broadcast:
    """Queue a broadcast: check the audience and the rate limit, resolve the recipients,
    store them (user ids only), audit, and add the ``batch`` dispatch job (enqueued after the
    caller's commit). ``NoRecipientsError`` when nobody would get it."""
    check_audience(session, sender, audience)
    now = utcnow()
    retry_after = _retry_after(session, sender.user_id, now)
    if retry_after is not None:
        raise BroadcastRateLimitedError(retry_after)
    recipients = resolve(session, audience)
    if not recipients:
        raise NoRecipientsError("the audience has no recipients")
    context = _tenant_level(session)
    broadcast = Broadcast(
        id=uuid.uuid4(),
        tenant_id=context.tenant_id,
        sender_id=sender.user_id,
        scope=audience.scope,
        project_ids=audience.project_list(),
        audience=audience.as_record(),
        subject=subject,
        body=body,
        status="queued",
        recipient_count=len(recipients),
        created_by=sender.user_id,
        updated_by=sender.user_id,
        source="api",
    )
    session.add(broadcast)
    session.add_all(
        BroadcastRecipient(
            tenant_id=context.tenant_id,
            broadcast_id=broadcast.id,
            user_id=user.id,
            status="pending",
            created_by=sender.user_id,
            source="system",
        )
        for user in recipients
    )
    session.flush()
    # The handler module registers the kind (the module loader imports it at start-up too).
    from app.modules.notifications import jobs as _handlers  # noqa: F401

    job = jobs.create_job(
        session,
        JOB_REGISTRY.get(DISPATCH_KIND),
        {"broadcast_id": str(broadcast.id)},
        actor_id=sender.user_id,
    )
    broadcast.job_id = job.id
    audit.append(
        session,
        chain_key(broadcast.id),
        {
            "event": "broadcast_queued",
            "sender_id": str(sender.user_id),
            "sender_role": "system_admin" if sender.system_admin else "PER-09",
            "scope": audience.scope,
            "project_ids": audience.project_list(),
            "audience": audience.as_record(),
            "recipient_count": len(recipients),
        },
        source="api",
        actor_id=sender.user_id,
    )
    session.flush()
    return broadcast


def _tenant_level(session: Session) -> TenancyContext:
    context = session_tenancy(session)
    if context is None or context.project_id is not None:
        raise RuntimeError("broadcasts need a tenant-level bound session")
    return context


# --- reads ----------------------------------------------------------------------------------


def may_read(sender: Sender, broadcast: Broadcast) -> bool:
    """A System admin reads every broadcast of its tenant; a PER-09 those whose projects are
    all its own PER-09 projects (never a tenant-wide one)."""
    if sender.system_admin:
        return True
    if broadcast.scope != "projects" or not broadcast.project_ids:
        return False
    return {uuid.UUID(p) for p in broadcast.project_ids} <= sender.admin_project_ids


def get_broadcast(session: Session, sender: Sender, broadcast_id: uuid.UUID) -> Broadcast:
    """The broadcast if the sender may read it, else ``OutOfScopeError`` (404)."""
    broadcast = get_or_404(session, Broadcast, broadcast_id)
    if not may_read(sender, broadcast):
        raise OutOfScopeError("broadcast not found")
    return broadcast


def list_broadcasts(
    session: Session, sender: Sender, *, limit: int = 50, offset: int = 0
) -> list[Broadcast]:
    """The broadcasts the sender may read, newest first."""
    if not sender.may_broadcast:
        raise BroadcastForbiddenError("not a System admin or Project Administrator")
    query = select(Broadcast).order_by(Broadcast.created_at.desc(), Broadcast.id)
    readable = [b for b in session.scalars(query) if may_read(sender, b)]
    return readable[offset : offset + min(limit, MAX_LIST)]


def delivery_counts(session: Session, broadcast_id: uuid.UUID) -> mail_service.SendCounts:
    """The broadcast's sends by status, from the delivery ledger (T-0226)."""
    filters = mail_service.SendFilter(broadcast_id=broadcast_id)
    return mail_service.send_stats(session, filters).totals


# --- dispatch (the batch job) ---------------------------------------------------------------


def _skip_reason(
    user: User | None, opted: set[uuid.UUID], suppressed: frozenset[str]
) -> str | None:
    if user is None or not user.is_active:
        return "inactive"
    address = _address(user)
    if address is None:
        return "no_address"
    if user.id in opted:
        return "opted_out"
    if address in suppressed:
        return "suppressed"
    return None


def _send_chunk(session: Session, broadcast: Broadcast) -> int:
    """Queue (or skip) the next ``DISPATCH_CHUNK`` pending recipients; return how many."""
    rows: Sequence[BroadcastRecipient] = session.scalars(
        select(BroadcastRecipient)
        .where(
            BroadcastRecipient.broadcast_id == broadcast.id,
            BroadcastRecipient.status == "pending",
        )
        .order_by(BroadcastRecipient.id)
        .limit(DISPATCH_CHUNK)
        .with_for_update(skip_locked=True)
    ).all()
    if not rows:
        return 0
    users = auth_service.users_by_id(session, [r.user_id for r in rows])
    opted = opted_out_user_ids(session, [r.user_id for r in rows])
    suppressed = mail_service.suppressed_addresses(session, [u.email for u in users.values()])
    for row in rows:
        user = users.get(row.user_id)
        reason = _skip_reason(user, opted, suppressed)
        if reason is not None or user is None:
            row.status, row.skip_reason = "skipped", reason
            continue
        token = secrets.token_urlsafe(TOKEN_BYTES)
        row.unsubscribe_hash = sha256_hex(token)
        row.send_id = mail_service.send_mail(
            session,
            TEMPLATE_KEY,
            user.email,
            {"subject": broadcast.subject, "message": broadcast.body, "unsubscribe_token": token},
            recipient_user_id=user.id,
            broadcast_id=broadcast.id,
        )
        row.status = "queued"
    return len(rows)


def _status_counts(session: Session, broadcast_id: uuid.UUID) -> dict[str, int]:
    query = (
        select(BroadcastRecipient.status, func.count())
        .where(BroadcastRecipient.broadcast_id == broadcast_id)
        .group_by(BroadcastRecipient.status)
    )
    return {status: int(count) for status, count in session.execute(query)}


def dispatch(db: Database, tenancy: TenancyContext, broadcast_id: uuid.UUID) -> dict[str, Any]:
    """The job body: queue one e-mail per pending recipient, in short units of work of
    ``DISPATCH_CHUNK`` (each commit hands its mails to the queue). Resumable: a rerun picks up
    the recipients still ``pending``; a ``sent`` broadcast is left alone."""
    scope = TenancyContext(tenant_id=tenancy.tenant_id)
    with db.unit_of_work(scope) as session:
        broadcast = session.get(Broadcast, broadcast_id)
        if broadcast is None:
            raise jobs.JobError("The broadcast does not exist")
        if broadcast.status == "sent":
            return {"queued": broadcast.queued_count, "skipped": broadcast.skipped_count}
        broadcast.status = "sending"
    try:
        while True:
            with db.unit_of_work(scope) as session:
                broadcast = get_or_404(session, Broadcast, broadcast_id)
                if _send_chunk(session, broadcast) == 0:
                    break
    except Exception:
        with db.unit_of_work(scope) as session:
            get_or_404(session, Broadcast, broadcast_id).status = "failed"
        logger.exception("broadcast dispatch failed", extra={"broadcast_id": str(broadcast_id)})
        raise
    with db.unit_of_work(scope) as session:
        broadcast = get_or_404(session, Broadcast, broadcast_id)
        counts = _status_counts(session, broadcast_id)
        broadcast.queued_count = counts.get("queued", 0)
        broadcast.skipped_count = counts.get("skipped", 0)
        broadcast.status, broadcast.finished_at = "sent", utcnow()
        result = {"queued": broadcast.queued_count, "skipped": broadcast.skipped_count}
        audit.append(
            session,
            chain_key(broadcast_id),
            {"event": "broadcast_dispatched", **result},
            source="system",
            actor_id=broadcast.sender_id,
        )
    logger.info("broadcast dispatched", extra={"broadcast_id": str(broadcast_id), **result})
    return result


# --- one-click opt-out ----------------------------------------------------------------------


def unsubscribe(session: Session, token: str | None) -> bool:
    """Record the broadcast opt-out of the recipient ``token`` was mailed to.

    ``True`` only when this call recorded it; the router answers the same whatever this
    returns (no oracle). A malformed, unknown or already used token changes nothing. The
    token is compared by its SHA-256 only."""
    if token is None or not _TOKEN.match(token):
        return False
    row = session.scalars(
        select(BroadcastRecipient)
        .where(BroadcastRecipient.unsubscribe_hash == sha256_hex(token))
        .with_for_update()
    ).first()
    if row is None or row.unsubscribed_at is not None:
        return False
    row.unsubscribed_at = utcnow()
    if not opted_out_user_ids(session, [row.user_id]):
        try:
            with session.begin_nested():
                session.add(
                    BroadcastOptOut(
                        tenant_id=row.tenant_id,
                        user_id=row.user_id,
                        broadcast_id=row.broadcast_id,
                        source="api",
                    )
                )
        except IntegrityError:  # a concurrent opt-out of the same user won
            pass
    return True
