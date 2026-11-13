"""Module manifest of ``validation`` (T-0011 contract)."""

from app.core.module_loader.manifest import ModuleManifest

# Platform kernel: every verifiable transition in every module calls ``run_checks``, so a
# project cannot switch it off.
MANIFEST = ModuleManifest(name="validation", version="0.1.0", toggleable=False)
