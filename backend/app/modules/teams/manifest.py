"""Module manifest of ``teams`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# A team-project link grants access to the project (ADR 0008): switching the module off for
# one project would silently keep or drop those grants, so it cannot be toggled per project.
# Calls the projects service (a linked project must exist and be live).
MANIFEST = ModuleManifest(name="teams", version="0.1.0", depends_on=("projects",), toggleable=False)
