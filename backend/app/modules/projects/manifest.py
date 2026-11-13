"""Module manifest of ``projects`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# Every other module hangs off a project, so a project cannot switch this one off.
MANIFEST = ModuleManifest(name="projects", version="0.1.0", toggleable=False)
