"""Module toggle API for the caller's project (T-0011): ``/api/v1/modules``.

Both endpoints require an authenticated, active user (``CurrentPrincipal``: 401 otherwise)
and a project in the caller's tenancy context (404 otherwise). Binding the project per
request is TD-122; until then every authenticated call is tenant-level and answers 404.
The admin capability tier arrives with RBAC (TD-129).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.core.auth.deps import CurrentPrincipal
from app.core.db import DbSession
from app.core.module_loader import service
from app.core.module_loader.gate import not_found, registry_of
from app.core.module_loader.manifest import ModuleManifest
from app.core.module_loader.schemas import ModuleState, ModuleToggle
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy

router = APIRouter(prefix="/modules", tags=["platform"])


def _project_context(session: DbSession) -> TenancyContext:
    context = session_tenancy(session)
    if context is None or context.project_id is None:
        raise not_found()
    return context


def _state(manifest: ModuleManifest, enabled: bool) -> ModuleState:
    return ModuleState(
        name=manifest.name,
        version=manifest.version,
        depends_on=list(manifest.depends_on),
        toggleable=manifest.toggleable,
        enabled=enabled,
    )


@router.get("", response_model=list[ModuleState])
def list_modules(
    principal: CurrentPrincipal, request: Request, session: DbSession
) -> list[ModuleState]:
    """Every loaded module, dependencies first, with its state for the caller's project."""
    _project_context(session)
    registry = registry_of(request.app)
    states = service.project_states(session, registry)
    return [_state(m, states[m.name]) for m in registry]


@router.put("/{name}", response_model=ModuleState)
def set_module(
    name: str,
    body: ModuleToggle,
    principal: CurrentPrincipal,
    request: Request,
    session: DbSession,
) -> ModuleState:
    """Enable or disable module ``name`` for the caller's project.

    404 for an unknown module; 409 when the module cannot be disabled or the change would
    break a dependency (the detail says which module to change first).
    """
    _project_context(session)
    registry = registry_of(request.app)
    try:
        states = service.set_enabled(
            session, registry, name, body.enabled, actor_id=principal.user_id
        )
    except service.UnknownModuleError:
        raise not_found() from None
    except service.ModuleToggleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    return _state(registry.get(name), states[name])
