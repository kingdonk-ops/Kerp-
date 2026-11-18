"""Deployment (tenant) settings (T-0044, diff §4.4). Other modules call this, never the table.

* ``get_settings``: the effective values for the session's tenant: the stored row, or
  ``SHIPPED_DEFAULTS`` while no System admin has changed anything. Any bound session (also a
  project-bound one) may read; reading never writes.
* ``project_defaults``: what ``projects.create_project`` fills into a new project when the
  request leaves a field out (status, site timezone, currency, locale).
* ``update_settings``: apply a partial update; needs a tenant-level session. The first change
  creates the row. ``offline_grace_hours`` may be at most 24 x ``offline_window_days`` after
  the update (``InvalidSettingsError``, 422). Changes are appended to the audit chain
  ``deployment-settings:<tenant>`` with ``{field: [old, new]}``; a call that changes nothing
  writes nothing. Authorisation (``system_admin``) is the router's.
* ``ensure_record``: first-run provisioning (T-0023) stores the shipped defaults as the
  tenant's record, unless one exists; audited as ``{"action": "provision"}``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.tenancy.rls import session_tenancy
from app.modules.settings.models import (
    FIELDS,
    HOURS_PER_DAY,
    SHIPPED_DEFAULTS,
    DeploymentSettings,
    DeploymentSettingsValues,
)
from app.modules.settings.schemas import DeploymentSettingsUpdate, grace_error

__all__ = [
    "CHAIN_PREFIX",
    "ConcurrentUpdateError",
    "InvalidSettingsError",
    "ProjectDefaults",
    "ensure_record",
    "get_settings",
    "project_defaults",
    "stored_record",
    "update_settings",
]

CHAIN_PREFIX: Final = "deployment-settings:"


class InvalidSettingsError(ValueError):
    """The update would leave the settings inconsistent (the API answers 422)."""


class ConcurrentUpdateError(RuntimeError):
    """Another transaction created the record at the same time (the API answers 409)."""


@dataclass(frozen=True, slots=True)
class ProjectDefaults:
    """The values a new project gets for the fields its creator left out."""

    status: str
    site_timezone: str
    currency_code: str
    locale: str


def _context_tenant(session: Session, *, tenant_level: bool) -> uuid.UUID:
    context = session_tenancy(session)
    if context is None:
        raise RuntimeError("the settings service needs a session bound to a tenant")
    if tenant_level and context.project_id is not None:
        raise RuntimeError("changing the deployment settings needs a tenant-level session")
    return context.tenant_id


def stored_record(session: Session, *, for_update: bool = False) -> DeploymentSettings | None:
    """The tenant's live settings row, or ``None`` while the shipped defaults apply."""
    tenant_id = _context_tenant(session, tenant_level=False)
    query = select(DeploymentSettings).where(
        DeploymentSettings.tenant_id == tenant_id, DeploymentSettings.project_id.is_(None)
    )
    if for_update:
        query = query.with_for_update()
    return session.scalars(query).one_or_none()


def get_settings(session: Session) -> DeploymentSettingsValues:
    """The effective deployment settings of the session's tenant."""
    record = stored_record(session)
    return record.values() if record is not None else SHIPPED_DEFAULTS


def project_defaults(session: Session) -> ProjectDefaults:
    """The deployment's defaults for a new project (diff §4.4)."""
    values = get_settings(session)
    return ProjectDefaults(
        status=values.default_status,
        site_timezone=values.default_site_timezone,
        currency_code=values.default_currency_code,
        locale=values.default_locale,
    )


def ensure_record(session: Session, *, actor_id: uuid.UUID | None) -> bool:
    """Store ``SHIPPED_DEFAULTS`` as the tenant's record unless it has one (first-run
    provisioning, T-0023). True if it was created. Needs a tenant-level session."""
    tenant_id = _context_tenant(session, tenant_level=True)
    if stored_record(session, for_update=True) is not None:
        return False
    record = DeploymentSettings(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        project_id=None,
        created_by=actor_id,
        updated_by=actor_id,
        source="system",
        **SHIPPED_DEFAULTS.as_dict(),
    )
    session.add(record)
    try:
        session.flush()
    except IntegrityError as exc:
        raise ConcurrentUpdateError("the deployment settings changed meanwhile; retry") from exc
    payload = {"action": "provision", "values": SHIPPED_DEFAULTS.as_dict()}
    chain = f"{CHAIN_PREFIX}{tenant_id}"
    audit.append(session, chain, payload, actor_id=actor_id, source="system")
    return True


def update_settings(
    session: Session, data: DeploymentSettingsUpdate, *, actor_id: uuid.UUID | None
) -> DeploymentSettingsValues:
    """Apply the fields given in ``data`` and audit what changed."""
    tenant_id = _context_tenant(session, tenant_level=True)
    record = stored_record(session, for_update=True)
    current = record.values() if record is not None else SHIPPED_DEFAULTS
    given = {name: getattr(data, name) for name in sorted(data.model_fields_set)}
    updated = replace(current, **given)
    if updated.offline_grace_hours > HOURS_PER_DAY * updated.offline_window_days:
        raise InvalidSettingsError(grace_error(updated.offline_window_days))
    changes: dict[str, list[Any]] = {
        name: [getattr(current, name), getattr(updated, name)]
        for name in FIELDS
        if getattr(current, name) != getattr(updated, name)
    }
    if not changes:
        return current
    if record is None:
        record = DeploymentSettings(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=None,
            created_by=actor_id,
            **updated.as_dict(),
        )
        session.add(record)
    else:
        for name in changes:
            setattr(record, name, getattr(updated, name))
    record.updated_by = actor_id
    try:
        session.flush()
    except IntegrityError as exc:
        raise ConcurrentUpdateError("the deployment settings changed meanwhile; retry") from exc
    payload = {"action": "update", "changes": changes}
    audit.append(session, f"{CHAIN_PREFIX}{tenant_id}", payload, actor_id=actor_id, source="api")
    return updated
