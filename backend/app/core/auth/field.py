"""Field-worker auth building blocks: magic links, the device PIN, link delivery (T-0009,
reworked to the hybrid model by T-0038).

Diff §4.2 *Field PIN, hybrid* (TD-135, decision 8a). The PIN belongs to the enrolled device,
not to a server session:

1. ``POST /auth/magic-link`` sends a one-time link (15 minutes) to an active user's e-mail;
   ``POST /auth/magic-link/redeem`` (or a password login) starts an ordinary session. On an
   enrolled device (``X-KERP-Device-Id``) the session is bound to it at once; the device's
   PIN is **not** reset by a sign-in.
2. The 6-digit PIN is set once at enrolment (``POST /devices/enrol`` with ``pin``) and can
   be changed later from a session of the device (``PUT /devices/{device_id}/pin``). Its
   scrypt hash lives on the device row.
3. ``POST /auth/refresh`` rotates a session of a device with a PIN normally while its last
   renewal is less than ``KERP_PIN_IDLE_MINUTES`` (default 30) ago. After a longer idle it
   answers ``401 {code: PIN_REQUIRED}`` without spending the token, and
   ``POST /auth/pin/unlock`` (the refresh token **and** the PIN) renews it. A PIN alone never
   starts or renews a session.

Wrong PINs escalate a progressive lockout per device (``lockout.PIN_POLICY``): the 10th
wrong PIN ends PIN unlock on that device until the next online sign-in there; PIN failures
never revoke a session or a device and never trigger a wipe. The device reports failures it
made offline with its next unlock (``failed_offline``), counted with the same escalation.

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
from typing import Final, Literal, Protocol

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import Settings

MAGIC_LINK_TTL: Final = timedelta(minutes=15)
MAGIC_LINK_TOKEN_BYTES: Final = 32
PIN_LENGTH: Final = 6
PIN_PATTERN: Final = rf"^[0-9]{{{PIN_LENGTH}}}$"
# The per-device PIN failure counter outlives the longest session by this margin (``lockout``).
PIN_TTL_MARGIN: Final = timedelta(days=1)
# How many offline PIN failures one report may carry (larger reports are refused, 422).
MAX_OFFLINE_FAILURES: Final = 1000

# The ``code`` of the field-PIN answers (``401``), so the client knows what to do next.
PIN_REQUIRED: Final = "PIN_REQUIRED"  # idle too long: ask for the PIN, call ``/auth/pin/unlock``
PIN_INVALID: Final = "PIN_INVALID"  # wrong PIN: ask again (the lockout may answer 429 first)
PIN_EXHAUSTED: Final = "PIN_EXHAUSTED"  # 10th wrong PIN: sign in online on this device
PIN_DETAILS: Final = {
    PIN_REQUIRED: "PIN required",
    PIN_INVALID: "Wrong PIN",
    PIN_EXHAUSTED: "PIN unlock ended: sign in again",
}
PinCode = Literal["PIN_REQUIRED", "PIN_INVALID", "PIN_EXHAUSTED"]


class PinGateError(Exception):
    """A field-PIN answer (HTTP 401 with ``code``): only ever raised to a caller that holds a
    live refresh token of the session (the token is checked first)."""

    def __init__(self, code: PinCode) -> None:
        super().__init__(code)
        self.code: PinCode = code


def pin_gate_response(code: PinCode) -> JSONResponse:
    return JSONResponse(
        {"detail": PIN_DETAILS[code], "code": code},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def pin_gate_handler(_request: Request, exc: Exception) -> JSONResponse:
    return pin_gate_response(exc.code if isinstance(exc, PinGateError) else PIN_REQUIRED)


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
