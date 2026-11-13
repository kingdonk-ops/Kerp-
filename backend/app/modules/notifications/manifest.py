"""Module manifest of ``notifications`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# Platform kernel: other modules (handover, holds, inspections) notify through it, so a
# project cannot switch it off. Notifications belong to a project.
MANIFEST = ModuleManifest(
    name="notifications", version="0.1.0", depends_on=("projects",), toggleable=False
)
