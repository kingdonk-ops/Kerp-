"""The manifest contract every domain module meets (T-0011, CLAUDE.md §6).

Each ``app/modules/<name>/manifest.py`` defines ``MANIFEST = ModuleManifest(...)``. The
loader checks it with ``manifest_problems`` before anything is mounted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# The files of a module package (CLAUDE.md §6).
REQUIRED_FILES: Final = (
    "manifest.py",
    "models.py",
    "router.py",
    "service.py",
    "schemas.py",
    "README.md",
)
# ``/api/v1/modules`` is the toggle API, so no module may take that name.
RESERVED_NAMES: Final = frozenset({"modules"})

_NAME: Final = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
_VERSION: Final = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True, slots=True, kw_only=True)
class ModuleManifest:
    """What the platform needs to know about a module before it mounts it.

    * ``name``: the package directory name (``snake_case``, 2-40 chars).
    * ``version``: ``MAJOR.MINOR.PATCH`` of the module's API.
    * ``depends_on``: names of modules this one calls (through their ``service.py``).
      The loader mounts dependencies first, refuses unknown or cyclic ones, and a module
      is only enabled for a project while all its dependencies are.
    * ``toggleable``: ``False`` for platform modules a project cannot switch off.
    * ``default_enabled``: state for a project that has never toggled the module.
    """

    name: str
    version: str
    depends_on: tuple[str, ...] = ()
    toggleable: bool = True
    default_enabled: bool = True

    @property
    def route_prefix(self) -> str:
        """Path segment under ``/api/v1``: the name with ``-`` for ``_``."""
        return "/" + self.name.replace("_", "-")


def manifest_problems(manifest: object, *, directory: str) -> list[str]:
    """Why ``manifest`` (found in ``<package>/<directory>/``) breaks the contract (empty = OK)."""
    if not isinstance(manifest, ModuleManifest):
        return [f"{directory}: MANIFEST must be a ModuleManifest"]
    problems: list[str] = []
    name = manifest.name
    if not isinstance(name, str) or not _NAME.match(name):
        problems.append(f"{directory}: name {name!r} must match {_NAME.pattern}")
    elif name != directory:
        problems.append(
            f"{directory}: manifest name {name!r} does not match its directory {directory!r}"
        )
    if name in RESERVED_NAMES:
        problems.append(f"{directory}: name {name!r} is reserved")
    if not isinstance(manifest.version, str) or not _VERSION.match(manifest.version):
        problems.append(f"{directory}: version {manifest.version!r} must be MAJOR.MINOR.PATCH")
    deps = manifest.depends_on
    if not isinstance(deps, tuple) or not all(isinstance(d, str) for d in deps):
        problems.append(f"{directory}: depends_on must be a tuple of module names")
    else:
        if name in deps:
            problems.append(f"{directory}: depends on itself")
        problems.extend(
            f"{directory}: depends_on lists {d!r} twice"
            for d in sorted({d for d in deps if deps.count(d) > 1})
        )
    if not isinstance(manifest.toggleable, bool):
        problems.append(f"{directory}: toggleable must be a bool")
    if not isinstance(manifest.default_enabled, bool):
        problems.append(f"{directory}: default_enabled must be a bool")
    elif manifest.toggleable is False and not manifest.default_enabled:
        problems.append(
            f"{directory}: the module cannot be disabled, so default_enabled must be True"
        )
    return problems
