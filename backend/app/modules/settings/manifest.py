"""Module manifest of ``settings`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# Deployment settings (offline window, new-project defaults) are platform, read by sync, field
# clients and project creation: they cannot be switched off. The module depends on nothing,
# so the projects module can call it.
MANIFEST = ModuleManifest(name="settings", version="0.1.0", toggleable=False)
