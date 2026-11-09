"""Contract test: heavy libraries must be imported lazily (CLAUDE.md §6, T-0022).

The rule is ruff's TID253 (``banned-module-level-imports``) configured in
``backend/pyproject.toml``. Synthetic sample files are written to a temp dir and
checked with ruff using the full project config in a single run, so the heavy
libraries themselves need not be installed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

# The minimum set required by the task / spec; the config may list more.
REQUIRED_HEAVY = ("fitz", "pymupdf", "cv2", "rectpack", "celpy", "pyhanko", "pytesseract")

TOP_LEVEL_TEMPLATES = {
    "import": "import {m}\n",
    "import-submodule": "import {m}.sub\n",
    "from-import": "from {m} import thing\n",
    "from-submodule": "from {m}.sub import thing\n",
    "import-as": "import {m} as alias\n",
}

LAZY_TEMPLATE = """\
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import {m}


def typed(value: {m}.Thing) -> None:
    del value


def run() -> object:
    import {m}
    from {m}.sub import thing

    return ({m}, thing)
"""

LIGHT_SOURCE = "import json\n\n__all__ = ['json']\n"


def _lint_config() -> dict[str, Any]:
    config: dict[str, Any] = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return dict(config["tool"]["ruff"]["lint"])


def _configured_heavy() -> set[str]:
    tidy = _lint_config().get("flake8-tidy-imports", {})
    return set(tidy.get("banned-module-level-imports", []))


def _heavy_modules() -> list[str]:
    """Required set plus anything else configured, so new entries are covered too."""
    return sorted(set(REQUIRED_HEAVY) | _configured_heavy())


def _file_stem(kind: str, module: str) -> str:
    return f"{kind.replace('-', '_')}__{module}"


@pytest.fixture(scope="module")
def ruff_findings(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[str]]:
    """Write every sample, run ruff once, return ``{file stem: [rule codes]}``."""
    root = tmp_path_factory.mktemp("lint_samples")
    stems: list[str] = []
    for module in _heavy_modules():
        for kind, template in TOP_LEVEL_TEMPLATES.items():
            stems.append(_file_stem(kind, module))
            source = template.format(m=module) + "\n__all__: list[str] = []\n"
            (root / f"{stems[-1]}.py").write_text(source, encoding="utf-8")
        stems.append(_file_stem("lazy", module))
        (root / f"{stems[-1]}.py").write_text(LAZY_TEMPLATE.format(m=module), encoding="utf-8")
    stems.append("light")
    (root / "light.py").write_text(LIGHT_SOURCE, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--exit-zero",
            "--config",
            str(PYPROJECT),
            "--output-format",
            "json",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    findings: dict[str, list[str]] = {stem: [] for stem in stems}
    for item in json.loads(result.stdout):
        stem = Path(item["filename"]).stem
        findings[stem].append(f"{item['code']}: {item['message']}")
    return findings


def test_config_bans_required_heavy_modules() -> None:
    assert "TID253" in _lint_config()["select"]
    assert set(REQUIRED_HEAVY) <= _configured_heavy()


@pytest.mark.parametrize("module", _heavy_modules())
@pytest.mark.parametrize("kind", list(TOP_LEVEL_TEMPLATES))
def test_module_level_heavy_import_fails_lint(
    ruff_findings: dict[str, list[str]], module: str, kind: str
) -> None:
    found = ruff_findings[_file_stem(kind, module)]
    assert any(f.startswith("TID253:") and module in f for f in found), found


@pytest.mark.parametrize("module", _heavy_modules())
def test_lazy_and_type_checking_heavy_import_passes_lint(
    ruff_findings: dict[str, list[str]], module: str
) -> None:
    assert ruff_findings[_file_stem("lazy", module)] == []


def test_light_module_level_import_passes_lint(ruff_findings: dict[str, list[str]]) -> None:
    assert ruff_findings["light"] == []


def test_ruff_exit_code_fails_on_top_level_heavy_import(tmp_path: Path) -> None:
    """End-to-end: the plain ``ruff check`` CI command exits non-zero."""
    sample = tmp_path / "bad.py"
    sample.write_text("import celpy\n\n__all__ = ['celpy']\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--config", str(PYPROJECT), sample],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "TID253" in result.stdout
