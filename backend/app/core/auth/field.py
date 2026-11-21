"""Field-worker auth building blocks: magic links, the device PIN, link delivery (T-0009,
reworked to the hybrid model by T-0038).

Diff §4.2 *Field PIN, hybrid* (TD-135, decision 8a). The PIN belongs to the enrolled device,
not to a server session:

1. ``POST /auth/magic-link`` sends a one-time link (15 minutes) to an active Field
   Worker's e-mail (PER-14 only, ``magic_link.py``, T-0057);
   ``POST /auth/magic-link/redeem`` (or a password login) starts an ordinary session. On an
   enrolled device (``X-KERP-Device-Id``) the session is bound to it at once; the device's
   PIN is **not** reset by a sign-in.
2. The 6-digit PIN is set once at enrolment (``POST /devices/enrol`` with ``pin``) and can
   be changed later from a session of the device (``PUT /devices/{device_id}/pin``); at the
   PIN-failure limit only from a fresh online sign-in (``FRESH_SIGN_IN_REQUIRED``, TD-301,
   T-0345). Its scrypt hash lives on the device row.
3. ``POST /auth/refresh`` rotates a session of a device with a PIN normally while its last
   renewal is less than ``KERP_PIN_IDLE_MINUTES`` (default 30) ago. After a longer idle it
   answers ``401 {code: PIN_REQUIRED}`` without spending the token, and
   ``POST /auth/pin/unlock`` (the refresh token **and** the PIN) renews it. A PIN alone never
   starts or renews a session.

Wrong PINs escalate a progressive lockout per device (``lockout.PIN_POLICY``): the 10th
wrong PIN ends PIN unlock on that device until the next online sign-in there; PIN failures
never revoke a session or a device and never trigger a wipe. The device reports failures it
made offline with its next unlock (``failed_offline``), counted with the same escalation.

Delivery of the link is a seam (``MagicLinkSender``). The default, ``MailMagicLinkSender``,
queues the ``auth.magic_link`` e-mail through the outbound e-mail service (T-0058, diff §4.6):
a send-log row in the post-response unit of work of the request (T-0057), the SAQ send after
its commit, the token only
in the link fragment. Production refuses to start without a mail transport
(``app.core.mail.transport.check_mail_config``); the development log transport prints the
link and never runs in production.
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
from sqlalchemy.orm import Session

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


# ``PUT /devices/{id}/pin`` on a device at the PIN-failure limit from a session that is not a
# fresh online sign-in (TD-301, T-0345): sign in again, then change the PIN.
FRESH_SIGN_IN_REQUIRED: Final = "FRESH_SIGN_IN_REQUIRED"


def fresh_sign_in_required() -> JSONResponse:
    return JSONResponse(
        {"detail": "Sign in again to change the PIN", "code": FRESH_SIGN_IN_REQUIRED},
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
    """Delivers a magic link to its user, in the unit of work that issued it (after the
    request's response, T-0057), so the delivery is queued only if it commits. Must not raise
    for an undeliverable address."""

    def send(self, session: Session, link: MagicLink) -> None: ...


MAGIC_LINK_TEMPLATE: Final = "auth.magic_link"


class MailMagicLinkSender:
    """Queues the sign-in e-mail (T-0058): never sent from the request, the token travels
    only in the link fragment and never reaches the send log."""

    def send(self, session: Session, link: MagicLink) -> None:
        # Imported here: the mail service needs the DB layer, which imports this module
        # while it initialises (db → tenancy → bearer → runtime → field).
        from app.core.mail.service import send_mail

        minutes = int(MAGIC_LINK_TTL.total_seconds() // 60)
        try:
            send_mail(
                session,
                MAGIC_LINK_TEMPLATE,
                link.email,
                {"token": link.token, "expires_minutes": minutes},
            )
        except ValueError:  # an address the mail service cannot send to: answer neutrally
            _logger.warning("magic link for user %s not queued: invalid address", link.user_id)


def sender_from_settings(settings: Settings) -> MagicLinkSender:
    """The magic-link delivery (``settings`` reserved for per-deployment choices)."""
    del settings
    return MailMagicLinkSender()
