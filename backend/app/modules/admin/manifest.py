"""Module manifest of ``admin`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# The access model (diff §4.1) is platform, not a project feature: it cannot be switched off.
# The member list names the team of every team-derived grant through the teams service (T-0013).
# The System console's queue monitor reads and acts through the jobs service (T-0218), its
# upload quarantine monitor through the uploads service (T-0221).
MANIFEST = ModuleManifest(
    name="admin",
    version="0.1.0",
    depends_on=("projects", "teams", "jobs", "uploads"),
    toggleable=False,
)
