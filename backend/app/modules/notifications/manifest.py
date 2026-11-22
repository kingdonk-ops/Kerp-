"""Module manifest of ``notifications`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# Platform kernel: other modules (handover, holds, inspections) notify through it, so a
# project cannot switch it off. Notifications belong to a project. Broadcast e-mail (T-0228)
# checks the targeted projects through the projects service and dispatches on the jobs
# module's batch queue.
MANIFEST = ModuleManifest(
    name="notifications", version="0.1.0", depends_on=("projects", "jobs"), toggleable=False
)
