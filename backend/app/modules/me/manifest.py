"""Module manifest of ``me`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# The caller's own projects (project picker, T-0042): platform, not a project feature.
MANIFEST = ModuleManifest(name="me", version="0.1.0", depends_on=("projects",), toggleable=False)
