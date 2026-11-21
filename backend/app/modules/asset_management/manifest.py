"""Module manifest of ``asset_management`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# The breakdown tree is the backbone every Phase 1 module anchors to (ADR 0003), so a project
# cannot switch it off.
MANIFEST = ModuleManifest(
    name="asset_management", version="0.1.0", depends_on=("projects",), toggleable=False
)
