"""Module manifest of ``onboarding`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# First-run provisioning (the deployment's tenant, its settings, the first System admin):
# platform, run from the command line, never a project feature.
MANIFEST = ModuleManifest(
    name="onboarding",
    version="0.1.0",
    depends_on=("admin", "projects", "settings"),
    toggleable=False,
)
