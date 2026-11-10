"""Route gate: a module that is off for the caller's project answers 404 (T-0011).

The loader adds ``module_gate(name)`` as a router dependency of every module that can be
disabled. The 404 body is FastAPI's own for an unknown path, so a disabled module looks
exactly like one that does not exist.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request, status

from app.core.db import DbSession
from app.core.module_loader import service
from app.core.module_loader.registry import ModuleRegistry
from app.core.tenancy.rls import session_tenancy

STATE_KEY = "modules"


def registry_of(app: FastAPI) -> ModuleRegistry:
    """The registry ``install_modules`` stored on ``app``."""
    registry = getattr(app.state, STATE_KEY, None)
    if not isinstance(registry, ModuleRegistry):
        raise RuntimeError("no module registry on this app (install_modules was not called)")
    return registry


def not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def module_gate(name: str) -> Callable[[Request, DbSession], None]:
    """Dependency that raises 404 while module ``name`` is off for the session's project.

    It shares the request's ``DbSession`` (FastAPI caches the dependency per request). A
    request without a project in its context is tenant-level and is not gated here.
    """

    def require_module_enabled(request: Request, session: DbSession) -> None:
        context = session_tenancy(session)
        if context is None or context.project_id is None:
            return
        if not service.is_enabled(session, registry_of(request.app), name):
            raise not_found()

    require_module_enabled.__name__ = f"require_{name}_enabled"
    return require_module_enabled
