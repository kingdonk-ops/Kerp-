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
* **Seen** (``resolve_request_device``): a request whose session is bound to a device, or
  that names an enrolled device of its user in ``X-KERP-Device-Id``, runs "through" that
  device: ``last_seen_at`` is updated (at most once a minute) and the audit ledger records
  the device. A revoked device gets ``DeviceRevokedError`` and the calling session dies.
* **Revoke** (``revoke``): status ``revoked`` with who and when; every session bound to the
  device is revoked at once (``revoke_device``), marked so that its next call is answered
  ``DEVICE_REVOKED``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

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


class DeviceConflictError(ValueError):
    """The ``device_id`` is enrolled by another user, or the session by another device (409)."""


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
) -> tuple[Device, bool]:
    """Register (or refresh) the device and bind the login session to it.

    Returns the device and whether it was new. A revoked device kills the calling session
    and raises ``DeviceRevokedError``; another user's ``device_id``, or a session already
    bound to another device, raises ``DeviceConflictError``.
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
    created = device is None
    if device is None:
        device = Device(
            tenant_id=_tenant_id(session),
            project_id=None,
            user_id=user_id,
            device_id=device_id,
            enrolled_at=now,
            status=DEVICE_ACTIVE,
            created_by=user_id,
        )
        session.add(device)
    device.platform = platform
    device.os_version = os_version
    device.app_version = app_version
    device.last_seen_at = now
    device.updated_by = user_id
    session.flush()
    store.bind_device(session_id, device_id, session_ttl)
    return device, created


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


def revoke(
    session: Session,
    store: RevocationStore,
    device: Device,
    *,
    actor_id: uuid.UUID,
    session_ttl: timedelta,
) -> int:
    """Revoke ``device`` and kill every session bound to it; returns how many.

    Already revoked: nothing changes in the row, any session still bound is killed again.
    """
    if device.status != DEVICE_REVOKED:
        now = utcnow()
        device.status = DEVICE_REVOKED
        device.revoked_at = now
        device.revoked_by = actor_id
        device.updated_by = actor_id
        session.flush()
    return store.revoke_device(device.device_id, session_ttl)


def confirm_wipe(session: Session, device: Device) -> bool:
    """Record the client's wipe of a revoked device; ``False`` when it was recorded before.

    ``ValueError`` for an active device (nothing was asked to be wiped).
    """
    if device.status != DEVICE_REVOKED:
        raise ValueError("only a revoked device confirms a wipe")
    if device.wipe_confirmed_at is not None:
        return False
    device.wipe_confirmed_at = utcnow()
    session.flush()
    return True


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
