"""Module manifest of ``admin`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# The access model (diff §4.1) is platform, not a project feature: it cannot be switched off.
MANIFEST = ModuleManifest(name="admin", version="0.1.0", depends_on=("projects",), toggleable=False)
