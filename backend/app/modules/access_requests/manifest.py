"""Module manifest of ``access_requests`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# The way into a project (diff §4.1): platform, not a project feature. Calls the lobby (lookup
# tickets), projects (narrow read), admin (membership on approval) and notifications services.
MANIFEST = ModuleManifest(
    name="access_requests",
    version="0.1.0",
    depends_on=("projects", "lobby", "admin", "notifications"),
    toggleable=False,
)
