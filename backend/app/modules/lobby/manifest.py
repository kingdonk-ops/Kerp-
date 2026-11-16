"""Module manifest of ``lobby`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# The way into a project (diff §4.1): platform, not a project feature.
MANIFEST = ModuleManifest(name="lobby", version="0.1.0", depends_on=("projects",), toggleable=False)
