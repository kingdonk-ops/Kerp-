"""Module manifest of ``saved_views`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# Every register (asset / work-anchor, scaffolds, NCRs, ...) saves its views here, so a
# project cannot switch it off. Views belong to a project. A ``team`` view names teams linked
# to the project through the teams service contract (T-0074).
MANIFEST = ModuleManifest(
    name="saved_views", version="0.1.0", depends_on=("projects", "teams"), toggleable=False
)
