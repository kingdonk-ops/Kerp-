"""Module manifest of ``admin`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# The access model (diff §4.1) is platform, not a project feature: it cannot be switched off.
# The member list names the team of every team-derived grant through the teams service (T-0013).
MANIFEST = ModuleManifest(
    name="admin", version="0.1.0", depends_on=("projects", "teams"), toggleable=False
)
