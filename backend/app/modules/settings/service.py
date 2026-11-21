"""Runtime settings (T-0230, diff §4.7) and the deployment settings built on them (T-0044,
diff §4.4). Other modules call this, never the tables.

**Registry** (entries are declared in code, ``registry.py``; values in ``oe_settings_value``):

* ``get_value`` / ``get_int`` / ``get_values`` / ``client_values``: the effective values for
  the session's tenant: the stored value, else the entry's default, never above its deployment
  cap. Any bound session (also a project-bound one) may read; reading never writes. Reads go
  through the settings cache (``cache.py``), except in a transaction that has changed a
  setting.
* ``set_values``: change entries; needs a tenant-level session. Each value is checked against
  its entry (type, bounds, deployment cap) and the rules across keys on the result:
  ``InvalidSettingError`` (422), never clamped. Changes are appended to the audit chain
  ``deployment-settings:<tenant>`` as ``{key: [old, new]}`` with the actor; a call that changes
  nothing writes nothing. The cache is invalidated after the commit. Authorisation
  (``system_admin``) is the router's.
* ``registry_entries`` / ``registry_entry``: what the System console lists.

**Deployment settings** (the seven T-0044 entries, same contract as before T-0230):
``get_settings``, ``project_defaults`` (what ``projects.create_project`` fills in),
``update_settings``, ``stored_record`` and ``ensure_record`` (first-run provisioning, T-0023:
stores the shipped defaults unless a value is stored; audited as ``{"action": "provision"}``).
``oe_settings_deployment`` is no longer read or written.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, SessionTransaction

from app.core import config as app_config
from app.core.audit import service as audit
from app.core.config import Settings
from app.core.tenancy.rls import session_tenancy
from app.modules.settings import cache
from app.modules.settings.models import (
    FIELDS,
    SHIPPED_DEFAULTS,
    DeploymentSettingsValues,
    SettingValue,
)
from app.modules.settings.registry import (
    REGISTRY,
    InvalidSettingError,
    SettingDef,
    UnknownSettingError,
    Value,
)
from app.modules.settings.schemas import DeploymentSettingsUpdate
from app.modules.settings.setting_defs import DEPLOYMENT_ENTRIES

__all__ = [
    "CHAIN_PREFIX",
    "ConcurrentUpdateError",
    "InvalidSettingError",
    "InvalidSettingsError",
    "ProjectDefaults",
    "RegistryEntry",
    "StoredSettings",
    "UnknownSettingError",
    "client_values",
    "ensure_record",
    "get_int",
    "get_settings",
    "get_value",
    "get_values",
    "project_defaults",
    "registry_entries",
    "registry_entry",
    "set_values",
    "stored_record",
    "update_settings",
]

CHAIN_PREFIX: Final = "deployment-settings:"
# ``session.info``: the tenants whose settings the open transaction has changed.
_CHANGED: Final = "kerp_settings_changed"
_DEPLOYMENT_KEYS: Final = frozenset(d.key for d in DEPLOYMENT_ENTRIES)
logger = logging.getLogger("kerp.settings")

# The T-0044 name of the 422 error; the same class, so either ``except`` catches it.
InvalidSettingsError = InvalidSettingError


class ConcurrentUpdateError(RuntimeError):
    """Another transaction stored the same setting at the same time (the API answers 409)."""


@dataclass(frozen=True, slots=True)
class ProjectDefaults:
    """The values a new project gets for the fields its creator left out."""

    status: str
    site_timezone: str
    currency_code: str
    locale: str


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """A declared entry with the tenant's effective value (``SettingEntryRead``)."""

    key: str
    category: str
    type: str
    description: str
    value: Any
    default: Any
    is_default: bool
    minimum: int | float | None
    maximum: int | float | None
    choices: list[str]
    max_length: int | None
    client_visible: bool
    cap: int | float | None
    cap_source: str | None


@dataclass(frozen=True, slots=True)
class StoredSettings:
    """The tenant's stored deployment settings (``stored_record``)."""

    tenant_id: uuid.UUID
    project_id: None
    updated_by: uuid.UUID | None
    stored: Mapping[str, Any]

    def values(self) -> DeploymentSettingsValues:
        defaults = SHIPPED_DEFAULTS.as_dict()
        return DeploymentSettingsValues(
            **{name: self.stored.get(name, defaults[name]) for name in FIELDS}
        )


# --- storage and cache ----------------------------------------------------------------------


def _context_tenant(session: Session, *, tenant_level: bool) -> uuid.UUID:
    context = session_tenancy(session)
    if context is None:
        raise RuntimeError("the settings service needs a session bound to a tenant")
    if tenant_level and context.project_id is not None:
        raise RuntimeError("changing the deployment settings needs a tenant-level session")
    return context.tenant_id


def _rows(
    session: Session, tenant_id: uuid.UUID, *, for_update: bool = False
) -> list[SettingValue]:
    query = select(SettingValue).where(
        SettingValue.tenant_id == tenant_id, SettingValue.project_id.is_(None)
    )
    if for_update:
        query = query.with_for_update()
    return list(session.scalars(query.order_by(SettingValue.updated_at, SettingValue.key)))


def _stored(session: Session) -> dict[str, Any]:
    """The tenant's stored values by key (cached, unless this transaction changed one)."""
    tenant_id = _context_tenant(session, tenant_level=False)

    def load() -> dict[str, Any]:
        return {row.key: row.value for row in _rows(session, tenant_id)}

    if tenant_id in session.info.get(_CHANGED, ()):
        return load()
    return cache.current().load(tenant_id, load)


@event.listens_for(Session, "after_commit")
def _invalidate_after_commit(session: Session) -> None:
    if session.in_nested_transaction():  # a SAVEPOINT was released; the outer may still roll back
        return
    for tenant_id in session.info.pop(_CHANGED, ()):
        try:
            cache.current().invalidate(tenant_id)
        except Exception:  # the commit stands; cached copies expire on their own
            logger.exception("settings cache invalidation failed")


@event.listens_for(Session, "after_soft_rollback")
def _forget_after_rollback(session: Session, previous_transaction: SessionTransaction) -> None:
    if previous_transaction.parent is None:  # the whole transaction: nothing was changed
        session.info.pop(_CHANGED, None)


def _effective(definition: SettingDef, stored: Mapping[str, Any], config: Settings) -> Value:
    """The stored value if the entry still accepts it, else the default; under the cap."""
    value = definition.default
    if definition.key in stored:
        try:
            value = definition.validate(stored[definition.key])
        except InvalidSettingError:  # bounds tightened in code since it was stored
            logger.warning(
                "stored setting %s is no longer valid; using the default", definition.key
            )
    return definition.effective(value, config=config)


# --- registry -------------------------------------------------------------------------------


def get_values(session: Session, *, config: Settings | None = None) -> dict[str, Value]:
    """The effective value of every declared entry for the session's tenant."""
    config = config or app_config.get_settings()
    stored = _stored(session)
    return {d.key: _effective(d, stored, config) for d in REGISTRY}


def get_value(session: Session, key: str, *, config: Settings | None = None) -> Value:
    """The effective value of one entry (``UnknownSettingError`` if it is not declared)."""
    definition = REGISTRY.get(key)
    return _effective(definition, _stored(session), config or app_config.get_settings())


def get_int(session: Session, key: str, *, config: Settings | None = None) -> int:
    """``get_value`` of an ``int`` entry, typed (``TypeError`` for any other entry)."""
    value = get_value(session, key, config=config)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} is not an int entry")
    return value


def client_values(session: Session, *, config: Settings | None = None) -> dict[str, Value]:
    """The client-visible entries only (the client bootstrap, T-0231)."""
    values = get_values(session, config=config)
    return {d.key: values[d.key] for d in REGISTRY if d.client_visible}


def registry_entries(session: Session, *, config: Settings) -> list[RegistryEntry]:
    """Every declared entry with its value, default and bounds, by category then key."""
    stored = _stored(session)
    return [
        RegistryEntry(
            key=d.key,
            category=d.category,
            type=d.type,
            description=d.description,
            value=_effective(d, stored, config),
            default=d.default,
            is_default=d.key not in stored,
            minimum=d.minimum,
            maximum=d.maximum,
            choices=list(d.choices),
            max_length=d.max_length if d.type in ("string", "int_list") else None,
            client_visible=d.client_visible,
            cap=d.cap_value(config),
            cap_source=d.cap.env if d.cap else None,
        )
        for d in REGISTRY
    ]


def registry_entry(session: Session, key: str, *, config: Settings) -> RegistryEntry:
    REGISTRY.get(key)
    return next(entry for entry in registry_entries(session, config=config) if entry.key == key)


def set_values(
    session: Session,
    values: Mapping[str, Any],
    *,
    actor_id: uuid.UUID | None,
    config: Settings | None = None,
    source: str = "api",
) -> dict[str, list[Any]]:
    """Store ``values`` (by key) and audit what changed; returns ``{key: [old, new]}``."""
    tenant_id = _context_tenant(session, tenant_level=True)
    config = config or app_config.get_settings()
    checked = {
        key: REGISTRY.get(key).validate(value, config=config) for key, value in values.items()
    }
    rows = {row.key: row for row in _rows(session, tenant_id, for_update=True)}
    stored = {key: row.value for key, row in rows.items()}
    current = {d.key: _effective(d, stored, config) for d in REGISTRY}
    changes = {
        key: [current[key], value]
        for key, value in sorted(checked.items())
        if current[key] != value
    }
    if not changes:
        return {}
    updated = {**current, **checked}
    for rule in REGISTRY.rules_for(set(changes)):
        if (problem := rule.check(updated)) is not None:
            raise InvalidSettingError(problem)
    _write(
        session,
        tenant_id,
        rows,
        {key: new for key, (_old, new) in changes.items()},
        actor_id,
        source,
    )
    payload = {"action": "update", "changes": changes}
    audit.append(session, f"{CHAIN_PREFIX}{tenant_id}", payload, actor_id=actor_id, source=source)
    return changes


def _write(
    session: Session,
    tenant_id: uuid.UUID,
    rows: dict[str, SettingValue],
    values: Mapping[str, Any],
    actor_id: uuid.UUID | None,
    source: str,
) -> None:
    for key, value in values.items():
        row = rows.get(key)
        if row is None:
            row = SettingValue(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                project_id=None,
                key=key,
                created_by=actor_id,
                source=source,
            )
            session.add(row)
        row.value = value
        row.updated_by = actor_id
    session.info.setdefault(_CHANGED, set()).add(tenant_id)
    try:
        session.flush()
    except IntegrityError as exc:
        raise ConcurrentUpdateError("the deployment settings changed meanwhile; retry") from exc


# --- deployment settings (T-0044) -----------------------------------------------------------


def stored_record(session: Session, *, for_update: bool = False) -> StoredSettings | None:
    """The tenant's stored deployment settings, or ``None`` while only the defaults apply."""
    tenant_id = _context_tenant(session, tenant_level=False)
    rows = [
        r for r in _rows(session, tenant_id, for_update=for_update) if r.key in _DEPLOYMENT_KEYS
    ]
    if not rows:
        return None
    return StoredSettings(
        tenant_id=tenant_id,
        project_id=None,
        updated_by=rows[-1].updated_by,  # of the latest change
        stored={row.key: row.value for row in rows},
    )


def get_settings(session: Session) -> DeploymentSettingsValues:
    """The effective deployment settings of the session's tenant."""
    values: dict[str, Any] = dict(get_values(session))
    return DeploymentSettingsValues(**{name: values[name] for name in FIELDS})


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
    """Store ``SHIPPED_DEFAULTS`` as the tenant's values unless it has any (first-run
    provisioning, T-0023). True if they were stored. Needs a tenant-level session."""
    tenant_id = _context_tenant(session, tenant_level=True)
    if stored_record(session, for_update=True) is not None:
        return False
    defaults = SHIPPED_DEFAULTS.as_dict()
    _write(session, tenant_id, {}, defaults, actor_id, "system")
    payload = {"action": "provision", "values": defaults}
    audit.append(session, f"{CHAIN_PREFIX}{tenant_id}", payload, actor_id=actor_id, source="system")
    return True


def update_settings(
    session: Session, data: DeploymentSettingsUpdate, *, actor_id: uuid.UUID | None
) -> DeploymentSettingsValues:
    """Apply the fields given in ``data`` and audit what changed."""
    given = {name: getattr(data, name) for name in sorted(data.model_fields_set)}
    set_values(session, given, actor_id=actor_id)
    return get_settings(session)
