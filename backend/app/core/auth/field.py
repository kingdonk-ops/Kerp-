"""Field-worker auth building blocks: magic links, the 6-digit PIN, link delivery (T-0009).

Diff §4: "magic-link + 6-digit PIN as a *local session unlock only*". The flow:

1. ``POST /auth/magic-link`` sends a one-time link (15 minutes) to an active user's e-mail.
2. ``POST /auth/magic-link/redeem`` with the link token **and a new PIN** starts a
   *PIN-gated* login session on that device: the PIN's scrypt hash is stored against the
   session id, then the token pair is issued.
3. ``POST /auth/pin/unlock`` with the device's refresh token **and the PIN** rotates the
   session. ``/auth/refresh`` refuses PIN-gated sessions, so the refresh token kept on the
   device is useless without the PIN, and the PIN is useless without the refresh token: it
   never authenticates on its own and can never start a session.

Wrong PINs escalate a progressive lockout per session (``lockout.PIN_POLICY``); the last
allowed failure revokes the session, and the worker needs a new magic link.

Delivery of the link is a seam (``MagicLinkSender``). Outbound e-mail is TD-084; until then
development logs the link and production refuses to pretend it sent one.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Protocol

from app.core.config import Settings

MAGIC_LINK_TTL: Final = timedelta(minutes=15)
MAGIC_LINK_TOKEN_BYTES: Final = 32
PIN_LENGTH: Final = 6
PIN_PATTERN: Final = rf"^[0-9]{{{PIN_LENGTH}}}$"
# A PIN hash outlives the session's newest refresh token, so a PIN-gated session can never
# fall back to plain ``/refresh`` because its PIN entry expired first.
PIN_TTL_MARGIN: Final = timedelta(days=1)

_logger = logging.getLogger("kerp.auth.field")


def is_weak_pin(pin: str) -> bool:
    """Refuse the PINs tried first: one repeated digit or a straight run (123456, 987654)."""
    return len(set(pin)) == 1 or pin in "0123456789" or pin in "9876543210"


def new_magic_link_token() -> str:
    return secrets.token_urlsafe(MAGIC_LINK_TOKEN_BYTES)


def magic_link_digest(token: str) -> str:
    """Only the SHA-256 of a link token is stored, so a store dump cannot sign anyone in."""
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class MagicLink:
    token: str
    email: str
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    expires_at: datetime


class MagicLinkSender(Protocol):
    """Delivers a magic link to its user. Must not raise for an undeliverable address."""

    def send(self, link: MagicLink) -> None: ...


class LoggingMagicLinkSender:
    """Development only: writes the link token to the log so a developer can redeem it."""

    def send(self, link: MagicLink) -> None:
        _logger.warning(
            "magic link (development delivery, never used in production) for user %s: %s",
            link.user_id,
            link.token,
        )


class UndeliverableMagicLinkSender:
    """Production without an e-mail transport (TD-084): nothing is sent, the token is not
    logged, and the operator is told."""

    def send(self, link: MagicLink) -> None:
        _logger.error("magic link for user %s not delivered: no e-mail transport", link.user_id)


def sender_from_settings(settings: Settings) -> MagicLinkSender:
    if settings.environment == "production":
        return UndeliverableMagicLinkSender()
    return LoggingMagicLinkSender()
