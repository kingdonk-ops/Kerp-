"""Discover, check and mount the domain modules (T-0011).

Adapted from Inspection-ERP ``core/module_loader.py`` (ADR 0001): manifest discovery,
topological sort, fail-fast on unknown or cyclic dependencies. Its marketplace
entitlement gate is replaced by per-project enable/disable (``service.py``, ``gate.py``).

``install_modules(app)`` runs at app start-up: it reads every ``app/modules/<name>/``,
refuses to start if any module breaks the contract (all problems in one error), mounts
each module's ``router`` under ``/api/v1/<name>`` in dependency order, and mounts the
toggle API at ``/api/v1/modules``.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Final

from fastapi import APIRouter, Depends, FastAPI

from app.core.module_loader.gate import STATE_KEY, module_gate, registry_of
from app.core.module_loader.manifest import REQUIRED_FILES, ModuleManifest, manifest_problems
from app.core.module_loader.registry import ModuleLoadError, ModuleRegistry, order_by_dependencies
from app.core.module_loader.router import router as toggle_router

__all__ = [
    "API_PREFIX",
    "DEFAULT_PACKAGE",
    "ModuleLoadError",
    "ModuleRegistry",
    "discover_modules",
    "install_modules",
    "mount_modules",
    "order_by_dependencies",
    "registry_of",
]

API_PREFIX: Final = "/api/v1"
DEFAULT_PACKAGE: Final = "app.modules"


def _module_dirs(package: str) -> list[Path]:
    pkg = importlib.import_module(package)
    dirs = {
        child
        for root in pkg.__path__
        for child in Path(root).iterdir()
        if child.is_dir() and (child / "__init__.py").is_file()
    }
    return sorted(dirs, key=lambda d: d.name)


def discover_modules(package: str = DEFAULT_PACKAGE) -> ModuleRegistry:
    """Load and check the manifest of every module package under ``package``.

    Raises ``ModuleLoadError`` listing every contract problem; an exception raised while
    importing a manifest propagates unchanged.
    """
    problems: list[str] = []
    manifests: list[ModuleManifest] = []
    for directory in _module_dirs(package):
        name = directory.name
        problems.extend(
            f"{name}: missing required file {f}"
            for f in REQUIRED_FILES
            if not (directory / f).is_file()
        )
        if not (directory / "manifest.py").is_file():
            continue
        manifest = getattr(importlib.import_module(f"{package}.{name}.manifest"), "MANIFEST", None)
        if manifest is None:
            problems.append(f"{name}: manifest.py must define MANIFEST")
            continue
        found = manifest_problems(manifest, directory=name)
        problems.extend(found)
        if not found:
            manifests.append(manifest)
    if problems:
        raise ModuleLoadError(problems)
    return ModuleRegistry.from_manifests(package, manifests)


def mount_modules(app: FastAPI, registry: ModuleRegistry) -> None:
    """Include each module's ``router`` under ``API_PREFIX``, dependencies first.

    Routers of modules that can be disabled get ``module_gate`` as a dependency.
    """
    for manifest in registry:
        module = importlib.import_module(f"{registry.package}.{manifest.name}.router")
        router = getattr(module, "router", None)
        if not isinstance(router, APIRouter):
            raise ModuleLoadError([f"{manifest.name}: router.py must define router (APIRouter)"])
        gate = [Depends(module_gate(manifest.name))] if manifest.toggleable else []
        app.include_router(router, prefix=API_PREFIX + manifest.route_prefix, dependencies=gate)


def install_modules(app: FastAPI, package: str = DEFAULT_PACKAGE) -> ModuleRegistry:
    """Discover the modules of ``package``, store the registry on ``app`` and mount them."""
    registry = discover_modules(package)
    setattr(app.state, STATE_KEY, registry)
    app.include_router(toggle_router, prefix=API_PREFIX)
    mount_modules(app, registry)
    return registry
