"""Synthetic module packages for the module loader tests (T-0011).

``ModulePackages.write`` creates a throwaway ``<package>/<module>/`` tree under ``tmp_path``
and puts it on ``sys.path``. Every call uses a fresh package name, so Python's import cache
never hands one test another test's modules.
"""

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.core.module_loader.manifest import REQUIRED_FILES

MANIFEST_TEMPLATE = """\
from app.core.module_loader.manifest import ModuleManifest

MANIFEST = ModuleManifest(
    name={name!r},
    version={version!r},
    depends_on={depends_on!r},
    toggleable={toggleable!r},
    default_enabled={default_enabled!r},
)
"""

ROUTER_TEMPLATE = """\
from fastapi import APIRouter

router = APIRouter()


@router.get("/ping")
def ping() -> dict[str, str]:
    return {{"module": {name!r}}}
"""


@dataclass
class FakeModule:
    """One synthetic module. ``manifest``/``router`` replace the generated file text."""

    depends_on: tuple[str, ...] = ()
    toggleable: bool = True
    default_enabled: bool = True
    version: str = "1.0.0"
    name: str | None = None  # manifest name, if it should differ from the directory
    manifest: str | None = None
    router: str | None = None
    omit: tuple[str, ...] = ()
    extra: dict[str, str] = field(default_factory=dict)


class ModulePackages:
    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        monkeypatch.syspath_prepend(str(root))

    def write(self, modules: dict[str, FakeModule]) -> str:
        package = f"kerp_fake_{uuid.uuid4().hex[:12]}"
        base = self.root / package
        base.mkdir(parents=True)
        (base / "__init__.py").write_text("", encoding="utf-8")
        for directory, spec in modules.items():
            files = dict.fromkeys(REQUIRED_FILES, "")
            files["README.md"] = f"# {directory}\n"
            files["manifest.py"] = spec.manifest or MANIFEST_TEMPLATE.format(
                name=spec.name or directory,
                version=spec.version,
                depends_on=spec.depends_on,
                toggleable=spec.toggleable,
                default_enabled=spec.default_enabled,
            )
            files["router.py"] = spec.router or ROUTER_TEMPLATE.format(name=directory)
            files.update(spec.extra)
            files["__init__.py"] = ""
            target = base / directory
            target.mkdir()
            for filename, content in files.items():
                if filename not in spec.omit:
                    (target / filename).write_text(content, encoding="utf-8")
        return package
