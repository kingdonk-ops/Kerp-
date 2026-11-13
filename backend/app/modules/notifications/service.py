"""In-app notifications (T-0019): notify, list, mark read, clear pins.

Other modules call this, never the table. Every call works on a session bound to the
notification's project; the router checks the caller's membership first.

``notify`` stores the row (the in-app inbox entry) and then hands it to every configured
channel, ``in_app`` first. A channel that raises or is not registered yields a failed
``DeliveryResult`` and is logged; it never undoes the in-app row or stops the channels
after it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.core.auth.access import load_grants
from app.core.config import get_settings
from app.core.db import utcnow
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import OutOfScopeError, get_or_404
from app.modules.notifications.channels import (
    IN_APP,
    DeliveryResult,
    Recipient,
    UnknownChannelError,
    get_channel,
)
from app.modules.notifications.models import PRIORITY_RANK, Notification
from app.modules.notifications.schemas import NotificationCreate

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "Delivery",
    "RecipientNotMemberError",
    "clear_pins",
    "get_notification",
    "list_notifications",
    "mark_read",
    "notify",
]

logger = logging.getLogger("kerp.notifications")

DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200


class RecipientNotMemberError(ValueError):
    """The recipient is no member of the project (the API answers 422)."""


@dataclass(frozen=True, slots=True)
class Delivery:
    notification: Notification
    results: list[DeliveryResult]


def _scope(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    context = session_tenancy(session)
    if context is None or context.project_id is None:
        raise RuntimeError("the notifications service needs a session bound to a project")
    return context.tenant_id, context.project_id


def _channel_order(names: Sequence[str]) -> list[str]:
    """``in_app`` first and always, then the others once each, in the configured order."""
    return [IN_APP, *dict.fromkeys(n for n in names if n != IN_APP)]


def _deliver(name: str, notification: Notification, recipient: Recipient) -> DeliveryResult:
    try:
        result = get_channel(name).deliver(notification, recipient)
    except UnknownChannelError:
        logger.error("notification channel %s is not registered", name)
        return DeliveryResult(name, False, "unknown_channel")
    except Exception as exc:  # one channel's outage never blocks the others
        # The exception text may carry addresses or tokens; log its type only.
        logger.warning(
            "notification channel %s failed: %s",
            name,
            type(exc).__name__,
            extra={"notification_id": str(notification.id)},
        )
        return DeliveryResult(name, False, type(exc).__name__)
    if not result.ok:
        logger.warning("notification channel %s did not deliver: %s", name, result.detail)
    return result


def notify(
    session: Session,
    data: NotificationCreate,
    *,
    actor_id: uuid.UUID | None = None,
    channels: Sequence[str] | None = None,
) -> Delivery:
    """Store a notification for a member of the bound project and deliver it.

    ``channels`` defaults to ``KERP_NOTIFICATION_CHANNELS``. Non-in-app channels run in the
    caller's request for now (TD-238).
    """
    tenant_id, project_id = _scope(session)
    grants = load_grants(session, tenant_id, data.recipient_id)
    if not any(g.project_id == project_id for g in grants):
        raise RecipientNotMemberError("the recipient is not a member of this project")
    notification = Notification(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        project_id=project_id,
        **data.model_dump(),
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(notification)
    session.flush()
    recipient = Recipient(tenant_id, project_id, data.recipient_id)
    names = get_settings().notification_channels if channels is None else channels
    results = [_deliver(name, notification, recipient) for name in _channel_order(names)]
    return Delivery(notification, results)


def get_notification(
    session: Session, notification_id: uuid.UUID, recipient_id: uuid.UUID
) -> Notification:
    """The recipient's notification in the bound project, else ``OutOfScopeError`` (404).

    Somebody else's notification does not exist for the caller; a portfolio session's
    other projects do not count either.
    """
    _, project_id = _scope(session)
    note = get_or_404(session, Notification, notification_id)
    if note.project_id != project_id or note.recipient_id != recipient_id:
        raise OutOfScopeError("Notification not found")
    return note


def list_notifications(
    session: Session,
    recipient_id: uuid.UUID,
    *,
    unread_only: bool = False,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[Notification]:
    """The recipient's notifications in the bound project: pinned first, then priority
    (critical first), then newest."""
    _, project_id = _scope(session)
    rank = case(PRIORITY_RANK, value=Notification.priority, else_=-1)
    query = select(Notification).where(
        Notification.project_id == project_id, Notification.recipient_id == recipient_id
    )
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    query = query.order_by(
        Notification.pinned.desc(),
        rank.desc(),
        Notification.created_at.desc(),
        Notification.id.desc(),
    )
    return list(session.scalars(query.limit(min(limit, MAX_LIMIT)).offset(offset)))


def mark_read(
    session: Session, notification_id: uuid.UUID, recipient_id: uuid.UUID
) -> Notification:
    """Mark the recipient's notification read; the first read time is kept."""
    note = get_notification(session, notification_id, recipient_id)
    if note.read_at is None:
        note.read_at = utcnow()
        note.updated_by = recipient_id
        session.flush()
    return note


def clear_pins(
    session: Session,
    *,
    pin_key: str,
    recipient_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> int:
    """Unpin the bound project's notifications pinned under ``pin_key`` (one recipient's,
    or everybody's). The owning module calls it when its action happens, e.g. a handover
    acknowledged. Returns how many were unpinned."""
    _, project_id = _scope(session)
    query = select(Notification).where(
        Notification.project_id == project_id,
        Notification.pin_key == pin_key,
        Notification.pinned.is_(True),
    )
    if recipient_id is not None:
        query = query.where(Notification.recipient_id == recipient_id)
    notes = list(session.scalars(query))
    now = utcnow()
    for note in notes:
        note.pinned, note.unpinned_at, note.updated_by = False, now, actor_id
    session.flush()
    return len(notes)
