"""The ordered set of loaded modules and the dependency sort (T-0011).

Adapted from Inspection-ERP ``core/module_loader.py`` (ADR 0001): topological sort that
fails fast on unknown and cyclic dependencies. Marketplace entitlements are not ported;
per-project enable/disable (``service.py``) replaces them.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from app.core.module_loader.manifest import ModuleManifest


class ModuleLoadError(RuntimeError):
    """The modules cannot be loaded. ``problems`` lists every reason found."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("module loading failed:\n  " + "\n  ".join(problems))


def order_by_dependencies(manifests: Iterable[ModuleManifest]) -> tuple[ModuleManifest, ...]:
    """Return ``manifests`` with every module after its dependencies.

    Ties are broken by name, so the order (and the mount order) is deterministic. Raises
    ``ModuleLoadError`` for a duplicate name, an unknown dependency, a dependency cycle, or a
    module that cannot be disabled depending on one that can (it could be switched off
    through that dependency).
    """
    by_name: dict[str, ModuleManifest] = {}
    problems: list[str] = []
    for manifest in manifests:
        if manifest.name in by_name:
            problems.append(f"{manifest.name}: declared twice")
        by_name[manifest.name] = manifest
    for name, manifest in sorted(by_name.items()):
        for dep in manifest.depends_on:
            target = by_name.get(dep)
            if target is None:
                problems.append(f"{name}: depends on unknown module {dep!r}")
            elif not manifest.toggleable and target.toggleable:
                problems.append(
                    f"{name}: cannot be disabled but depends on toggleable module {dep!r}"
                )
    if problems:
        raise ModuleLoadError(problems)

    # Kahn's algorithm; the ready set is kept sorted for a stable result.
    remaining = {name: set(m.depends_on) for name, m in by_name.items()}
    ordered: list[ModuleManifest] = []
    while remaining:
        ready = sorted(name for name, deps in remaining.items() if not deps)
        if not ready:
            cycle = ", ".join(sorted(remaining))
            raise ModuleLoadError([f"dependency cycle among modules: {cycle}"])
        for name in ready:
            ordered.append(by_name[name])
            del remaining[name]
        for deps in remaining.values():
            deps.difference_update(ready)
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class ModuleRegistry:
    """The loaded modules of one package, dependencies first."""

    package: str
    manifests: tuple[ModuleManifest, ...]

    @classmethod
    def from_manifests(cls, package: str, manifests: Iterable[ModuleManifest]) -> ModuleRegistry:
        return cls(package, order_by_dependencies(manifests))

    def __iter__(self) -> Iterator[ModuleManifest]:
        return iter(self.manifests)

    def __contains__(self, name: object) -> bool:
        return any(m.name == name for m in self.manifests)

    def names(self) -> tuple[str, ...]:
        return tuple(m.name for m in self.manifests)

    def get(self, name: str) -> ModuleManifest:
        for manifest in self.manifests:
            if manifest.name == name:
                return manifest
        raise LookupError(f"no module named {name!r}")

    def dependants(self, name: str) -> tuple[ModuleManifest, ...]:
        """Modules that depend on ``name`` directly (dependency order)."""
        return tuple(m for m in self.manifests if name in m.depends_on)
