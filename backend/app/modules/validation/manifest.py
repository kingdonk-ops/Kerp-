"""Module manifest of ``validation`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# Platform kernel: every verifiable transition in every module calls ``run_checks``, so a
# project cannot switch it off. A new project gets a copy of the tenant's rule sets, and the
# Rules API is per project (``projects``, ADR 0004 §2.3, §7). Override requests and decisions
# notify the approver and the requester (``notifications``, ADR 0004 Amendment A.3, T-0035).
MANIFEST = ModuleManifest(
    name="validation",
    version="0.3.0",
    depends_on=("projects", "notifications"),
    toggleable=False,
)
