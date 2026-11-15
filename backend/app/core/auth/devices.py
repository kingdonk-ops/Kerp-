"""The device registry's store and session binding (T-0043, diff §4.3).

``oe_identity_device`` belongs to core auth: authentication reads it on every request from a
device, and modules reach it only through these functions (CLAUDE.md §6). Who may list or
revoke a device, and the audit of those actions, is ``device_api.py``.

Every function needs a tenant-level bound session (no project): the device row is a
tenant-level record. The tenant comes from the session binding, never from an argument.

* **Enrol** (``enrol``): a signed-in field client registers its client-generated
  ``device_id``; the current login session (``sid``) is bound to it in the revocation store
  (``bind_device``). Enrolling again (every sign-in) updates platform and versions. A
  ``device_id`` already enrolled by another user is refused (``DeviceConflictError``); a
  revoked one stays revoked (``DeviceRevokedError``): a wiped client enrols under a new id.
  The enrolment that creates the row issues the device's **wipe secret** (T-0080): returned
  once, stored only as its SHA-256 (``wipe_secret_hash``). The enrolment may also set the
  device's field **PIN** (T-0038, diff §4.2): once; a device that has one keeps it
  (``DevicePinAlreadySetError``), it is changed only with ``set_pin``.
* **PIN** (``set_pin``, ``pin_device``): the scrypt hash of the device's 6-digit PIN is on
  the row (``pin_hash``). A sign-in never resets it.
* **Seen** (``resolve_request_device``): a request whose session is bound to a device, or
  that names an enrolled device of its user in ``X-KERP-Device-Id``, runs "through" that
  device: ``last_seen_at`` is updated (at most once a minute) and the audit ledger records
  the device. A revoked device gets ``DeviceRevokedError`` and the calling session dies.
* **Revoke** (``mark_revoked``): status ``revoked`` with who and when; once that is
  committed, every session bound to the device is revoked at once (``revoke_device``),
  marked so that its next call is answered ``DEVICE_REVOKED``.
* **Wipe confirmation** (``confirm_wipe``): unauthenticated, so it does the same work for any
  ``device_id`` and secret (one hash, one constant-time compare, against a dummy digest when
  the device is unknown or has no secret) and tells the caller only "confirmed" or "refused";
  the refusal reason is for the audit ledger alone (T-0080, TD-286, TD-291).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.device_signal import DeviceRevokedError
from app.core.auth.models import DEVICE_ACTIVE, DEVICE_REVOKED, Device
from app.core.auth.revocation import RevocationStore
from app.core.db import Database, utcnow
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy

# ``last_seen_at`` is written at most this often per device (not on every request).
SEEN_RESOLUTION: Final = timedelta(minutes=1)


# The wipe secret: 32 random bytes (256 bits), URL-safe base64 without padding (43 chars).
WIPE_SECRET_BYTES: Final = 32
# Compared against when there is no stored digest, so every call runs one compare.
_NO_DIGEST: Final = hashlib.sha256(b"kerp:no-wipe-secret").hexdigest()

WipeRefusal = Literal["unknown_device", "no_secret", "wrong_secret", "not_revoked"]


def new_wipe_secret() -> str:
    """A fresh per-device wipe secret (>= 256 bits from the OS CSPRNG)."""
    return secrets.token_urlsafe(WIPE_SECRET_BYTES)


def wipe_secret_digest(secret: str) -> str:
    """The stored form of a wipe secret: SHA-256, hex. A high-entropy random secret needs no
    slow hash; the digest alone does not let anyone confirm a wipe."""
    return hashlib.sha256(secret.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class WipeOutcome:
    """What ``confirm_wipe`` decided. ``refused`` is for the audit ledger only: the caller
    answers every refusal alike."""

    device: Device | None
    recorded: bool = False  # ``wipe_confirmed_at`` was set by this call
    refused: WipeRefusal | None = None


class DeviceConflictError(ValueError):
    """The ``device_id`` is enrolled by another user, or the session by another device (409)."""


class DevicePinAlreadySetError(ValueError):
    """Enrolment tried to set the PIN of a device that has one (409: change it instead)."""


def _tenant_id(session: Session) -> uuid.UUID:
    context = session_tenancy(session)
    if context is None or context.project_id is not None:
        raise RuntimeError("the device registry needs a tenant-level bound session")
    return context.tenant_id


def _aware(moment: datetime) -> datetime:
    # SQLite hands TIMESTAMPTZ back naive; it was stored as UTC.
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def find_device(session: Session, device_id: uuid.UUID) -> Device | None:
    """The tenant's device with this client ``device_id`` (active or revoked), if any."""
    query = select(Device).where(
        Device.tenant_id == _tenant_id(session), Device.device_id == device_id
    )
    return session.scalars(query).one_or_none()


def device_status(session: Session, device_id: uuid.UUID) -> str | None:
    """The status of the tenant's device ``device_id`` (``None`` if unknown). Reads the
    column only, so nothing enters the session's identity map (it may be rebound after)."""
    query = select(Device.status).where(
        Device.tenant_id == _tenant_id(session), Device.device_id == device_id
    )
    return session.scalars(query).one_or_none()


def devices_of_user(session: Session, user_id: uuid.UUID) -> list[Device]:
    """Every device the user enrolled, most recently seen first."""
    query = (
        select(Device)
        .where(Device.tenant_id == _tenant_id(session), Device.user_id == user_id)
        .order_by(Device.last_seen_at.desc(), Device.device_id)
    )
    return list(session.scalars(query))


def enrol(
    session: Session,
    store: RevocationStore,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    session_ttl: timedelta,
    device_id: uuid.UUID,
    platform: str,
    os_version: str,
    app_version: str,
    pin_hash: str | None = None,
) -> tuple[Device, str | None]:
    """Register (or refresh) the device and bind the login session to it.

    Returns the device and, only when this call created it, its new wipe secret (else
    ``None``: the secret is never issued twice). A revoked device kills the calling session
    and raises ``DeviceRevokedError``; another user's ``device_id``, or a session already
    bound to another device, raises ``DeviceConflictError``. ``pin_hash`` sets the device's
    PIN if it has none yet; a device with a PIN raises ``DevicePinAlreadySetError`` (nothing
    is changed or bound).
    """
    bound = store.session_device(session_id)
    if bound is not None and bound != device_id:
        raise DeviceConflictError("this session is bound to another device")
    now = utcnow()
    device = find_device(session, device_id)
    if device is not None and device.user_id != user_id:
        raise DeviceConflictError("this device id is enrolled by another account")
    if device is not None and device.status == DEVICE_REVOKED:
        store.revoke_device_session(session_id, session_ttl)
        raise DeviceRevokedError("device revoked")
    if pin_hash is not None and device is not None and device.pin_hash is not None:
        raise DevicePinAlreadySetError("this device already has a PIN")
    wipe_secret: str | None = None
    if device is None:
        wipe_secret = new_wipe_secret()
        device = Device(
            tenant_id=_tenant_id(session),
            project_id=None,
            user_id=user_id,
            device_id=device_id,
            enrolled_at=now,
            status=DEVICE_ACTIVE,
            wipe_secret_hash=wipe_secret_digest(wipe_secret),
            created_by=user_id,
        )
        session.add(device)
    device.platform = platform
    device.os_version = os_version
    device.app_version = app_version
    device.last_seen_at = now
    device.updated_by = user_id
    if pin_hash is not None:
        device.pin_hash = pin_hash
        device.pin_set_at = now
    session.flush()
    store.bind_device(session_id, device_id, session_ttl)
    return device, wipe_secret


def set_pin(session: Session, device: Device, pin_hash: str, *, actor_id: uuid.UUID) -> None:
    """Replace the device's PIN (the caller checked who may). Caller commits."""
    now = utcnow()
    device.pin_hash = pin_hash
    device.pin_set_at = now
    device.updated_by = actor_id
    session.flush()


def pin_device(session: Session, device_id: uuid.UUID, user_id: uuid.UUID) -> Device | None:
    """The user's active device ``device_id``, if it is one (the PIN gate's lookup)."""
    device = find_device(session, device_id)
    if device is None or device.user_id != user_id or device.status != DEVICE_ACTIVE:
        return None
    return device


def touch(device: Device, now: datetime | None = None) -> bool:
    """Move ``last_seen_at`` to now, unless it was set less than ``SEEN_RESOLUTION`` ago."""
    now = now or utcnow()
    if now - _aware(device.last_seen_at) < SEEN_RESOLUTION:
        return False
    device.last_seen_at = now
    return True


def record_sync(session: Session, device_id: uuid.UUID, *, user_id: uuid.UUID) -> Device | None:
    """Mark a successful sync of the user's active device (the sync endpoints call this)."""
    device = find_device(session, device_id)
    if device is None or device.user_id != user_id or device.status != DEVICE_ACTIVE:
        return None
    now = utcnow()
    device.last_sync_at = now
    touch(device, now)
    session.flush()
    return device


def mark_revoked(session: Session, device: Device, *, actor_id: uuid.UUID) -> bool:
    """Set ``device`` revoked with who and when; ``False`` when it was revoked before.

    Only the row: the caller commits it, *then* kills the device's sessions
    (``RevocationStore.revoke_device``). That order keeps a failed commit from telling a
    still-active device to wipe; if the kill fails after the commit, the per-request check
    (``resolve_request_device``) still refuses every session bound to the revoked device.
    """
    if device.status == DEVICE_REVOKED:
        return False
    now = utcnow()
    device.status = DEVICE_REVOKED
    device.revoked_at = now
    device.revoked_by = actor_id
    device.updated_by = actor_id
    session.flush()
    return True


def confirm_wipe(session: Session, device_id: uuid.UUID, wipe_secret: str | None) -> WipeOutcome:
    """Record the client's wipe of a revoked device that presents its wipe secret.

    Comparable work whatever the outcome: one lookup, one digest of the presented secret
    (an empty one when missing) and one constant-time compare (against a dummy digest when
    the device is unknown or was enrolled without a secret). Only a revoked device with the
    right secret sets ``wipe_confirmed_at``, once; a second confirmation changes nothing.
    """
    device = find_device(session, device_id)
    stored = device.wipe_secret_hash if device is not None else None
    presented = wipe_secret_digest(wipe_secret or "")
    matches = hmac.compare_digest(presented, stored or _NO_DIGEST)
    refused: WipeRefusal | None = None
    if device is None:
        refused = "unknown_device"
    elif stored is None:
        refused = "no_secret"
    elif not matches or not wipe_secret:
        refused = "wrong_secret"
    elif device.status != DEVICE_REVOKED:
        refused = "not_revoked"
    if device is None or refused is not None:
        return WipeOutcome(device, refused=refused)
    if device.wipe_confirmed_at is not None:
        return WipeOutcome(device)
    device.wipe_confirmed_at = utcnow()
    session.flush()
    return WipeOutcome(device, recorded=True)


def resolve_request_device(
    database: Database,
    store: RevocationStore,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    header_device_id: uuid.UUID | None,
    session_ttl: timedelta,
) -> uuid.UUID | None:
    """The enrolled device an authenticated request comes through, or ``None``.

    The session's bound device wins; the header may only repeat it (another id is a
    ``DeviceConflictError``). Without a binding, the header counts only for an enrolled
    device of the same user. A revoked device kills the session (marked as a device
    revocation) and raises ``DeviceRevokedError``. Runs in its own short tenant-level unit
    of work, so the request's own session and binding are untouched.
    """
    bound = store.session_device(session_id)
    if bound is not None and header_device_id is not None and header_device_id != bound:
        raise DeviceConflictError("the session is bound to another device")
    device_id = bound or header_device_id
    if device_id is None:
        return None
    context = TenancyContext(tenant_id=tenant_id, user_id=user_id)
    with database.unit_of_work(context) as session:
        device = find_device(session, device_id)
        if device is None or device.user_id != user_id:
            return None
        if device.status == DEVICE_REVOKED:
            store.revoke_device_session(session_id, session_ttl)
            raise DeviceRevokedError("device revoked")
        touch(device)
        return device.device_id
