"""Per-project module enable/disable (T-0011).

Replaces Inspection-ERP's marketplace entitlement gate (ADR 0001). The tenant and project
always come from the session's tenancy binding, never from arguments, so a caller can only
read and toggle the modules of its own project.

A module is **enabled** for a project when its own setting is on (the stored row, else the
manifest's ``default_enabled``; always on for a module that cannot be disabled) **and**
every module it depends on is enabled. ``set_enabled`` keeps settings consistent: it will
not enable a module whose dependencies are off, nor disable one that an enabled module
depends on.

Modules that must act when a module is switched on for a project (the validation module
copies the module's default rule sets into the project, ADR 0004 §2.3) register an
**enable listener**; ``set_enabled`` calls it, in the same session and transaction, with the
modules that the change made effectively enabled. Core code never imports a module.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.module_loader.models import ProjectModuleSetting
from app.core.module_loader.registry import ModuleRegistry
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy

# Audit chain (per project) recording every change of a module setting.
AUDIT_CHAIN: Final = "project_modules"

# ``listener(session, newly_enabled_module_names)``; the session is bound to the project.
EnableListener = Callable[[Session, frozenset[str]], None]
_ENABLE_LISTENERS: list[EnableListener] = []


def add_enable_listener(listener: EnableListener) -> None:
    """Call ``listener`` whenever ``set_enabled`` switches modules on (idempotent)."""
    if listener not in _ENABLE_LISTENERS:
        _ENABLE_LISTENERS.append(listener)


def default_states(registry: ModuleRegistry) -> dict[str, bool]:
    """Whether each module is enabled for a project that never toggled any (registry order)."""
    return _effective(registry, {})


class UnknownModuleError(LookupError):
    """No loaded module has that name. The API answers 404."""


class ModuleToggleError(ValueError):
    """The change would leave the project's modules inconsistent. The API answers 409."""


def _project_context(session: Session) -> TenancyContext:
    context = session_tenancy(session)
    if context is None or context.project_id is None:
        raise RuntimeError("module settings need a session bound to a project (bind_tenancy)")
    return context


def _settings(session: Session, context: TenancyContext) -> dict[str, ProjectModuleSetting]:
    rows = session.scalars(
        select(ProjectModuleSetting).where(
            ProjectModuleSetting.tenant_id == context.tenant_id,
            ProjectModuleSetting.project_id == context.project_id,
        )
    )
    return {row.module_name: row for row in rows}


def _effective(
    registry: ModuleRegistry, stored: dict[str, ProjectModuleSetting]
) -> dict[str, bool]:
    states: dict[str, bool] = {}
    for manifest in registry:  # dependencies come first
        row = stored.get(manifest.name)
        own = not manifest.toggleable or (
            row.enabled if row is not None else manifest.default_enabled
        )
        states[manifest.name] = own and all(states[dep] for dep in manifest.depends_on)
    return states


def project_states(session: Session, registry: ModuleRegistry) -> dict[str, bool]:
    """Whether each loaded module is enabled for the session's project (registry order)."""
    context = _project_context(session)
    return _effective(registry, _settings(session, context))


def is_enabled(session: Session, registry: ModuleRegistry, name: str) -> bool:
    """Whether module ``name`` is enabled for the session's project."""
    return project_states(session, registry)[name]


def set_enabled(
    session: Session,
    registry: ModuleRegistry,
    name: str,
    enabled: bool,
    *,
    actor_id: uuid.UUID | None = None,
    source: str = "api",
) -> dict[str, bool]:
    """Switch module ``name`` on or off for the session's project; return the new states.

    A change is recorded on the project's ``AUDIT_CHAIN``; repeating the current setting
    writes nothing. Raises ``UnknownModuleError`` or ``ModuleToggleError``.
    """
    context = _project_context(session)
    if name not in registry:
        raise UnknownModuleError(f"no module named {name!r}")
    manifest = registry.get(name)
    if not manifest.toggleable:
        raise ModuleToggleError(f"{name} cannot be disabled")
    stored = _settings(session, context)
    states = _effective(registry, stored)
    if enabled:
        off = [dep for dep in manifest.depends_on if not states[dep]]
        if off:
            raise ModuleToggleError(f"{name} depends on {', '.join(off)}: enable {off[0]} first")
    else:
        on = [m.name for m in registry.dependants(name) if states[m.name]]
        if on:
            raise ModuleToggleError(f"{name} is needed by {', '.join(on)}: disable {on[0]} first")

    row = stored.get(name)
    own = row.enabled if row is not None else manifest.default_enabled
    if own == enabled:
        return states
    actor = actor_id or context.user_id
    if row is None:
        row = ProjectModuleSetting(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            module_name=name,
            enabled=enabled,
            created_by=actor,
            updated_by=actor,
            source=source,
        )
        session.add(row)
        stored[name] = row
    else:
        row.enabled = enabled
        row.updated_by = actor
        row.source = source
    audit.append(
        session, AUDIT_CHAIN, {"module": name, "enabled": enabled}, source=source, actor_id=actor
    )
    session.flush()
    new_states = _effective(registry, stored)
    switched_on = frozenset(m for m, on in new_states.items() if on and not states[m])
    if switched_on:
        for listener in list(_ENABLE_LISTENERS):
            listener(session, switched_on)
    return new_states
