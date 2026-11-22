"""Outbound e-mail: enqueue in the request, deliver in the worker, suppress on bounces
(T-0058, diff §4.6).

* ``send_mail(session, template_key, to, context, locale)`` checks the template and the
  address, adds a ``queued`` send-log row in the caller's unit of work and stages the mail.
  Only after the caller's transaction commits is it handed to the queue (``queue.py``); a
  rollback drops it. Nothing is ever sent from a request, and a failed send can never roll
  back the caller's business action (it happens later, in the worker, in its own unit of work).
* ``deliver`` (worker) skips a suppressed address (``suppressed``), renders the template
  (with the tenant's current override, T-0227, read in the same unit of work), hands it to
  the transport, and records ``sent`` with the provider message id, ``failed``
  for a permanent error or the last attempt, or raises ``RetryMail`` for a transient error so
  the queue tries again with back-off.
* ``record_ses_event`` (SNS endpoint) marks the send-log row ``delivered`` on a delivery,
  and turns a hard bounce or a complaint into a suppression and marks the row ``bounced`` /
  ``complained``. Status changes from notifications only move forward (``STATUS_RANK``), so a
  repeated or late notification changes nothing (T-0226).
* ``list_sends`` / ``send_stats``: the searchable delivery ledger and its counts (T-0226,
  diff §4.7, ADR 0020).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import Select, event, func, select
from sqlalchemy.orm import Session, SessionTransaction

from app.core.audit import service as audit
from app.core.config import Settings
from app.core.db import Database, utcnow
from app.core.mail.addresses import address_hash, mask_address, normalize_address
from app.core.mail.models import MailSend, MailSuppression
from app.core.mail.overrides import effective_override
from app.core.mail.queue import QueuedMail, mail_enqueuer
from app.core.mail.templates import TEMPLATES, TemplateError, TemplateText, render
from app.core.mail.transport import (
    MailTransport,
    OutboundMessage,
    PermanentMailError,
    TransientMailError,
)
from app.core.request_context import get_request_id
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import get_or_404

PENDING_KEY: Final = "kerp_mail_pending"
DEV_WEB_ORIGIN: Final = "http://localhost:5173"  # the Vite dev server, outside production only
MAX_LIST: Final = 200

logger = logging.getLogger("kerp.mail")


class RetryMail(Exception):  # noqa: N818 - a signal to the queue, not an error of the caller
    """A transient transport error before the last attempt: the queue retries with back-off."""


# --- request side ---------------------------------------------------------------------------


def send_mail(
    session: Session,
    template_key: str,
    to: str,
    context: Mapping[str, object],
    locale: str | None = None,
    *,
    recipient_user_id: uuid.UUID | None = None,
    broadcast_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Queue ``template_key`` to ``to``; the send happens in the worker after the commit.

    ``recipient_user_id`` (the user the address belongs to, when the caller knows one) and
    ``broadcast_id`` (T-0228) only go into the delivery ledger; the address itself is kept
    hashed and masked, never in clear.

    Raises ``TemplateError`` (unknown template, context that does not fit) or
    ``InvalidAddressError`` before anything is written. Returns the send-log id.
    """
    tenancy = session_tenancy(session)
    if tenancy is None:
        raise RuntimeError("send_mail needs a session bound to a tenant")
    template = TEMPLATES.get(template_key)
    checked = template.check_context(context)
    address = normalize_address(to)
    send_id = uuid.uuid4()
    request_id = get_request_id()
    session.add(
        MailSend(
            id=send_id,
            tenant_id=tenancy.tenant_id,
            project_id=tenancy.project_id,
            template_key=template.key,
            recipient_hash=address_hash(address),
            recipient_masked=mask_address(address),
            locale=locale,
            status="queued",
            attempts=0,
            request_id=request_id,
            recipient_user_id=recipient_user_id,
            broadcast_id=broadcast_id,
            created_by=tenancy.user_id,
            updated_by=tenancy.user_id,
            source="system",
        )
    )
    session.flush()
    mail = QueuedMail(
        send_id=send_id,
        tenant_id=tenancy.tenant_id,
        project_id=tenancy.project_id,
        template_key=template.key,
        to=address,
        locale=locale,
        context=checked,
        request_id=request_id,
    )
    # Staged with the innermost transaction: rolling back a SAVEPOINT drops only its mails.
    transaction = session.get_nested_transaction() or session.get_transaction()
    pending: list[tuple[SessionTransaction | None, QueuedMail]] = session.info.setdefault(
        PENDING_KEY, []
    )
    pending.append((transaction, mail))
    return send_id


@event.listens_for(Session, "after_commit")
def _enqueue_after_commit(session: Session) -> None:
    if session.in_nested_transaction():  # a SAVEPOINT was released; the outer may roll back
        return
    pending: list[tuple[SessionTransaction | None, QueuedMail]] = session.info.pop(PENDING_KEY, [])
    mails = [mail for _, mail in pending]
    if not mails:
        return
    enqueuer = mail_enqueuer()
    if enqueuer is None:
        logger.warning("no mail queue installed; %d e-mail(s) stay queued", len(mails))
        return
    try:
        enqueuer(mails)
    except Exception:  # the caller's commit stands
        logger.exception("mail enqueue failed; %d e-mail(s) stay queued", len(mails))


def _within(transaction: SessionTransaction | None, ancestor: SessionTransaction) -> bool:
    while transaction is not None:
        if transaction is ancestor:
            return True
        transaction = transaction.parent
    return False


@event.listens_for(Session, "after_soft_rollback")
def _forget_after_rollback(session: Session, previous_transaction: SessionTransaction) -> None:
    pending: list[tuple[SessionTransaction | None, QueuedMail]] | None = session.info.get(
        PENDING_KEY
    )
    if pending:
        pending[:] = [(t, m) for t, m in pending if not _within(t, previous_transaction)]


# --- worker side ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Delivery:
    """What ``deliver`` needs besides the mail: the worker's database, transport, settings."""

    db: Database
    transport: MailTransport
    settings: Settings


def _scope(mail: QueuedMail) -> TenancyContext:
    return TenancyContext(tenant_id=mail.tenant_id, project_id=mail.project_id)


def _live_suppression(session: Session, recipient_hash: str) -> MailSuppression | None:
    return session.scalars(
        select(MailSuppression).where(MailSuppression.recipient_hash == recipient_hash).limit(1)
    ).first()


@dataclass(frozen=True, slots=True)
class _Begun:
    """``status``: ``None`` to go ahead, else the row's final status. ``override``: the
    tenant's current wording of the template for the mail's locale (T-0227), else ``None``."""

    status: str | None
    override: TemplateText | None = None


def _begin(db: Database, mail: QueuedMail, default_locale: str) -> _Begun:
    """Count the attempt and read the template override the send renders with."""
    with db.unit_of_work(_scope(mail)) as session:
        row = session.get(MailSend, mail.send_id)
        if row is None:
            return _Begun("missing")
        if row.status != "queued":
            return _Begun(row.status)  # delivered or settled by an earlier run (at-least-once)
        if _live_suppression(session, row.recipient_hash) is not None:
            row.status, row.status_at = "suppressed", utcnow()
            return _Begun("suppressed")
        row.attempts += 1
        template = TEMPLATES.find(mail.template_key)
        if template is None:  # unknown: ``render`` below records the failure
            return _Begun(None)
        return _Begun(None, effective_override(session, template, mail.locale, default_locale))


def _settle(
    db: Database,
    mail: QueuedMail,
    status: str,
    *,
    message_id: str | None = None,
    error_code: str | None = None,
    only_queued: bool = False,
) -> None:
    with db.unit_of_work(_scope(mail)) as session:
        row = session.get(MailSend, mail.send_id)
        if row is None:
            return
        if only_queued:
            session.refresh(row, with_for_update=True)
            if row.status != "queued":
                return  # settled meanwhile (sent, suppressed, failed): never overwritten
        now = utcnow()
        row.error_code = error_code
        if status == "queued":  # a transient error: stays queued for the retry
            return
        row.status, row.status_at = status, now
        if status == "sent":
            row.provider_message_id, row.sent_at = message_id, now


def deliver(delivery: Delivery, mail: QueuedMail, *, final_attempt: bool) -> str:
    """Send one queued mail; return its status. Raises ``RetryMail`` for a transient error
    when another attempt is left (the queue backs off and calls again)."""
    settings = delivery.settings
    begun = _begin(delivery.db, mail, settings.mail_default_locale)
    settled = begun.status
    if settled is not None:
        if settled == "missing":
            logger.warning("mail send-log row not found", extra={"send_id": str(mail.send_id)})
        return settled
    try:
        rendered = render(
            TEMPLATES.get(mail.template_key),
            mail.context,
            locale=mail.locale,
            default_locale=settings.mail_default_locale,
            app_name=settings.app_name,
            web_origin=settings.web_origin or _dev_origin(settings),
            override=begun.override,
        )
        message_id = delivery.transport.send(OutboundMessage(mail.to, rendered, mail.template_key))
    except TransientMailError as exc:
        if not final_attempt:
            _settle(delivery.db, mail, "queued", error_code=exc.code)
            raise RetryMail(exc.code) from None
        return _fail(delivery, mail, exc.code)
    except PermanentMailError as exc:
        return _fail(delivery, mail, exc.code)
    except (TemplateError, LookupError, ValueError) as exc:
        return _fail(delivery, mail, f"template:{type(exc).__name__}")
    _settle(delivery.db, mail, "sent", message_id=message_id)
    logger.info("e-mail sent", extra={"send_id": str(mail.send_id), "kind": mail.template_key})
    return "sent"


def _fail(delivery: Delivery, mail: QueuedMail, code: str) -> str:
    _settle(delivery.db, mail, "failed", error_code=code)
    logger.error(
        "e-mail not delivered (%s)",
        code,
        extra={"send_id": str(mail.send_id), "kind": mail.template_key},
    )
    return "failed"


def fail_unavailable(db: Database, mail_ref: QueuedMail, code: str) -> None:
    """Mark a send ``failed`` whose payload the worker could not load (expired, lost).

    Only a row still ``queued``: a send already settled (e.g. ``sent``, then its job ran
    again after the payload was deleted) keeps its status."""
    _settle(db, mail_ref, "failed", error_code=code, only_queued=True)


def _ref(row: MailSend) -> QueuedMail:
    """Ids only: what ``fail_unavailable`` needs (the payload is gone)."""
    return QueuedMail(
        send_id=row.id,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        template_key=row.template_key,
        to="",
        locale=row.locale,
        context={},
        request_id=row.request_id,
    )


def expire_queued(db: Database, scope: TenancyContext, cutoff: datetime, code: str) -> int:
    """Fail every send of ``scope`` still ``queued`` that was created before ``cutoff``.

    For rows whose mail can no longer go out: the enqueue after the commit failed (Redis
    down, a crash) or the payload expired before a job ran (T-0346, TD-336). The payload
    (with any link token) is never persisted, so a re-enqueue is impossible; the callers
    (magic link, reset) already answered neutrally. Returns how many rows were failed.
    """
    with db.unit_of_work(scope) as session:
        query = select(MailSend).where(
            MailSend.tenant_id == scope.tenant_id,
            MailSend.project_id.is_(None)
            if scope.project_id is None
            else MailSend.project_id == scope.project_id,
            MailSend.status == "queued",
            MailSend.created_at < cutoff,
        )
        refs = [_ref(row) for row in session.scalars(query)]
    for ref in refs:
        fail_unavailable(db, ref, code)
    if refs:
        logger.error("%d queued e-mail(s) never sent; marked failed (%s)", len(refs), code)
    return len(refs)


def _dev_origin(settings: Settings) -> str:
    if settings.environment == "production":  # check_mail_config refuses this at start-up
        raise ValueError("KERP_WEB_ORIGIN is not set")
    return DEV_WEB_ORIGIN


# --- deliveries, bounces and complaints (SNS endpoint, bound to the deployment tenant) -----


def suppress(
    session: Session,
    address: str,
    reason: str,
    *,
    detail: str | None = None,
    provider_message_id: str | None = None,
) -> MailSuppression:
    """Put ``address`` on the suppression list of the session's tenant (idempotent)."""
    tenancy = session_tenancy(session)
    if tenancy is None:
        raise RuntimeError("suppress needs a session bound to a tenant")
    digest = address_hash(address)
    existing = _live_suppression(session, digest)
    if existing is not None:
        return existing
    row = MailSuppression(
        tenant_id=tenancy.tenant_id,
        recipient_hash=digest,
        recipient_masked=mask_address(address),
        reason=reason,
        detail=detail[:100] if detail else None,
        provider_message_id=provider_message_id,
        source="system",
    )
    session.add(row)
    session.flush()
    return row


# How far a send has got. A notification moves a row forward only, so applying the same
# notification twice, or a delivery after the bounce or complaint of the same message, changes
# nothing, whatever order SNS delivers them in. ``failed`` and ``suppressed`` rows never had a
# provider message id and are never touched.
STATUS_RANK: Final = {"sent": 1, "delivered": 2, "bounced": 3, "complained": 4}


def _mark(session: Session, provider_message_id: str | None, status: str) -> int:
    """Move every row SES accepted as ``provider_message_id`` to ``status`` if that is
    forward; return how many rows changed."""
    if not provider_message_id:
        return 0
    changed = 0
    for row in session.scalars(
        select(MailSend).where(MailSend.provider_message_id == provider_message_id)
    ):
        current = STATUS_RANK.get(row.status)
        if current is None or current >= STATUS_RANK[status]:
            continue
        row.status, row.status_at = status, utcnow()
        changed += 1
    return changed


def _recipients(items: object) -> list[str]:
    found: list[str] = []
    if isinstance(items, list):
        for item in items:
            value = item.get("emailAddress") if isinstance(item, dict) else None
            if isinstance(value, str):
                try:
                    found.append(normalize_address(value))
                except ValueError:
                    continue
    return found


def record_ses_event(session: Session, event_data: Mapping[str, Any]) -> int:
    """Apply one SES notification (the ``Message`` of a verified SNS notification).

    A delivery marks the send ``delivered``. A **permanent** bounce or a complaint suppresses
    each recipient it names and marks the send ``bounced`` / ``complained``; a transient
    (soft) bounce and every other event are only logged. Status changes only move forward
    (``STATUS_RANK``). Returns how many addresses were suppressed.
    """
    kind = event_data.get("notificationType") or event_data.get("eventType")
    mail = event_data.get("mail")
    message_id = mail.get("messageId") if isinstance(mail, dict) else None
    message_id = message_id if isinstance(message_id, str) else None
    if kind == "Delivery":
        changed = _mark(session, message_id, "delivered")
        logger.info("SES delivery: %d send(s) marked delivered", changed)
        return 0
    if kind == "Bounce":
        bounce = event_data.get("bounce")
        bounce = bounce if isinstance(bounce, dict) else {}
        bounce_type = str(bounce.get("bounceType", ""))
        if bounce_type != "Permanent":
            logger.info("SES soft bounce (%s): not suppressed", bounce_type or "unknown")
            return 0
        addresses = _recipients(bounce.get("bouncedRecipients"))
        detail = f"{bounce_type}/{bounce.get('bounceSubType', '')}"
        reason, status = "bounce", "bounced"
    elif kind == "Complaint":
        complaint = event_data.get("complaint")
        complaint = complaint if isinstance(complaint, dict) else {}
        addresses = _recipients(complaint.get("complainedRecipients"))
        detail = str(complaint.get("complaintFeedbackType") or "complaint")
        reason, status = "complaint", "complained"
    else:
        logger.info("SES notification %s ignored", str(kind)[:40])
        return 0
    for address in addresses:
        suppress(session, address, reason, detail=detail, provider_message_id=message_id)
    _mark(session, message_id, status)
    logger.warning("SES %s: %d address(es) suppressed", reason, len(addresses))
    return len(addresses)


# --- System admin reads and the audited removal ---------------------------------------------


@dataclass(frozen=True, slots=True)
class SendFilter:
    """Filters of the delivery ledger (T-0226). ``created_from`` is inclusive, ``created_to``
    exclusive; ``recipient_hash`` is the SHA-256 of the address the admin typed (hashed by
    the router, the address itself is never kept)."""

    template_key: str | None = None
    status: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    broadcast_id: uuid.UUID | None = None
    recipient_hash: str | None = None


def _filtered[*Ts](query: Select[*Ts], filters: SendFilter) -> Select[*Ts]:
    if filters.template_key is not None:
        query = query.where(MailSend.template_key == filters.template_key)
    if filters.status is not None:
        query = query.where(MailSend.status == filters.status)
    if filters.created_from is not None:
        query = query.where(MailSend.created_at >= filters.created_from.astimezone(UTC))
    if filters.created_to is not None:
        query = query.where(MailSend.created_at < filters.created_to.astimezone(UTC))
    if filters.broadcast_id is not None:
        query = query.where(MailSend.broadcast_id == filters.broadcast_id)
    if filters.recipient_hash is not None:
        query = query.where(MailSend.recipient_hash == filters.recipient_hash)
    return query


def list_sends(
    session: Session,
    filters: SendFilter | None = None,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[MailSend]:
    """The delivery ledger, newest first (``status`` is shorthand for a status-only filter)."""
    filters = filters or SendFilter(status=status)
    query = select(MailSend).order_by(MailSend.created_at.desc(), MailSend.id)
    query = _filtered(query, filters)
    return session.scalars(query.limit(min(limit, MAX_LIST)).offset(offset)).all()


# Statuses a send reaches only after SES accepted it, and those that mean it arrived (a
# complaint comes from a recipient who received the mail).
ACCEPTED_STATUSES: Final = frozenset(STATUS_RANK)
ARRIVED_STATUSES: Final = frozenset({"delivered", "complained"})


@dataclass(frozen=True, slots=True)
class SendCounts:
    """Counts of the delivery ledger by current status (T-0226).

    ``sent``: accepted by SES (now ``sent``, ``delivered``, ``bounced`` or ``complained``).
    ``delivered``: SES reported the delivery (now ``delivered`` or ``complained``).
    ``bounced`` / ``complained`` / ``failed`` / ``suppressed`` / ``queued``: rows in that status.
    ``delivery_rate``: ``delivered / sent``, ``None`` when nothing was sent.
    """

    total: int = 0
    queued: int = 0
    sent: int = 0
    delivered: int = 0
    bounced: int = 0
    complained: int = 0
    failed: int = 0
    suppressed: int = 0

    @property
    def delivery_rate(self) -> float | None:
        return round(self.delivered / self.sent, 4) if self.sent else None

    def add(self, status: str, count: int) -> SendCounts:
        updates = {"total": self.total + count}
        if status in ACCEPTED_STATUSES:
            updates["sent"] = self.sent + count
        if status in ARRIVED_STATUSES:
            updates["delivered"] = self.delivered + count
        if status in ("queued", "bounced", "complained", "failed", "suppressed"):
            updates[status] = getattr(self, status) + count
        return replace(self, **updates)


@dataclass(frozen=True, slots=True)
class SendStats:
    totals: SendCounts
    by_template: dict[str, SendCounts]


def send_stats(session: Session, filters: SendFilter | None = None) -> SendStats:
    """Counts of the sends ``filters`` selects (created in the period, of the template), in
    total and per template key. Counts only: no row leaves the database."""
    query = select(MailSend.template_key, MailSend.status, func.count()).group_by(
        MailSend.template_key, MailSend.status
    )
    totals = SendCounts()
    by_template: dict[str, SendCounts] = {}
    for template_key, status, count in session.execute(_filtered(query, filters or SendFilter())):
        totals = totals.add(status, count)
        by_template[template_key] = by_template.get(template_key, SendCounts()).add(status, count)
    return SendStats(totals=totals, by_template=dict(sorted(by_template.items())))


def list_suppressions(
    session: Session, *, limit: int = 50, offset: int = 0
) -> Sequence[MailSuppression]:
    query = select(MailSuppression).order_by(MailSuppression.created_at.desc(), MailSuppression.id)
    return session.scalars(query.limit(min(limit, MAX_LIST)).offset(offset)).all()


def remove_suppression(
    session: Session, suppression_id: uuid.UUID, *, actor_id: uuid.UUID | None
) -> None:
    """Take an address off the list (soft delete) and record it in the audit ledger.
    Another tenant's row, a removed row or an unknown id: ``OutOfScopeError`` (404)."""
    row = get_or_404(session, MailSuppression, suppression_id)
    row.soft_delete()
    row.updated_by = actor_id
    audit.append(
        session,
        f"mail-suppression:{row.id}",
        {"action": "remove", "reason": row.reason, "recipient_hash": row.recipient_hash},
        source="api",
        actor_id=actor_id,
    )
