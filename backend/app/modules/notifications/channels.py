"""Delivery channels (T-0019, TD-168 decision 5a).

A channel delivers one stored notification to one recipient somewhere: ``in_app`` (the
inbox row itself, the only channel built here), FCM push (T-0144), e-mail (TD-084).
Channels are registered here by name and switched on by configuration
(``KERP_NOTIFICATION_CHANNELS``), so adding one never changes a caller of
``service.notify``. A channel module registers itself on import::

    register_channel("fcm", FcmChannel)
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from app.modules.notifications.models import Notification

IN_APP: Final = "in_app"


@dataclass(frozen=True, slots=True)
class Recipient:
    """Who a notification is for. Channels look up their own addresses (device tokens,
    e-mail) by ``user_id``."""

    tenant_id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """One channel's outcome. ``detail`` is a code (never an error text or payload)."""

    channel: str
    ok: bool
    detail: str | None = None


class NotificationChannel(Protocol):
    name: str

    def deliver(self, notification: Notification, recipient: Recipient) -> DeliveryResult:
        """Deliver ``notification``; raise or return ``ok=False`` on failure."""
        ...


class InAppChannel:
    """The in-app inbox. ``service.notify`` stores the row before any channel runs, and
    the row is what the recipient lists, so delivering it in-app needs nothing more."""

    name = IN_APP

    def deliver(self, notification: Notification, recipient: Recipient) -> DeliveryResult:
        stored = notification.id is not None and notification.recipient_id == recipient.user_id
        return DeliveryResult(IN_APP, stored, None if stored else "not_stored")


class UnknownChannelError(LookupError):
    """No channel is registered under that name."""


ChannelFactory = Callable[[], NotificationChannel]
_registry: dict[str, ChannelFactory] = {IN_APP: InAppChannel}


def register_channel(name: str, factory: ChannelFactory, *, replace: bool = False) -> None:
    """Make ``name`` available to ``KERP_NOTIFICATION_CHANNELS``."""
    if name in _registry and not replace:
        raise ValueError(f"notification channel {name!r} is already registered")
    _registry[name] = factory


def unregister_channel(name: str) -> None:
    if name == IN_APP:
        raise ValueError(f"the {IN_APP} channel cannot be unregistered")
    _registry.pop(name, None)


def registered_channels() -> list[str]:
    return sorted(_registry)


def get_channel(name: str) -> NotificationChannel:
    try:
        factory = _registry[name]
    except KeyError:
        raise UnknownChannelError(f"no notification channel {name!r}") from None
    return factory()
