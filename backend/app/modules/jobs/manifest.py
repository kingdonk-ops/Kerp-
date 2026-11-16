"""Module manifest of ``jobs`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# Platform kernel: OCR, PDF, P6 parsing and rollups of every module run as jobs, so a
# project cannot switch it off.
# The scheduler (T-0214) lists the deployment's live projects through the projects service.
MANIFEST = ModuleManifest(
    name="jobs", version="0.1.0", depends_on=("projects",), toggleable=False
)
