"""Module manifest of ``uploads`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# Platform kernel: every module stores its files through it, so a project cannot switch it
# off. Verification and the orphan sweep run as jobs; files belong to a project.
MANIFEST = ModuleManifest(
    name="uploads", version="0.1.0", depends_on=("jobs", "projects"), toggleable=False
)
