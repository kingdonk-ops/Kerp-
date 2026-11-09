"""Contract test: heavy libraries must be imported lazily (CLAUDE.md §6, T-0022).

Two checks share the job, both driven by the same
``[tool.ruff.lint.flake8-tidy-imports].banned-module-level-imports`` list in
``backend/pyproject.toml``:

1. ruff's TID253 (``banned-module-level-imports``) flags plain top-level imports
   (fast editor / CI feedback). Synthetic samples are checked with ruff using the
   full project config in a single run; the heavy libraries need not be installed.
2. TID253 does not see imports nested at module scope (``try``/``except``, a plain
   ``if``, ``with``, a class body). The AST walk below covers those for every file
   under ``backend/app``: a banned import is only allowed inside a function or an
   ``if TYPE_CHECKING:`` body.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PYPROJECT = BACKEND / "pyproject.toml"
APP_DIR = BACKEND / "app"

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
    found = ruff_findings[_file_stem("lazy", module)]
    assert [f for f in found if f.startswith("TID253:")] == [], found


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


# --- AST check: banned imports nested at module scope (not seen by TID253) ---


def _is_banned(name: str, banned: set[str]) -> bool:
    return any(name == b or name.startswith(f"{b}.") for b in banned)


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id in {"typing", "typing_extensions"}
    )


def eager_heavy_imports(source: str, banned: set[str]) -> list[tuple[int, str]]:
    """Return ``(line, module)`` for banned imports that run at import time.

    Allowed: inside a ``def`` / ``async def`` body, or the body (not the ``else``)
    of ``if TYPE_CHECKING:`` / ``if typing.TYPE_CHECKING:``. Relative imports are
    project code and ignored.
    """
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for child in node.orelse:
                visit(child)
            return
        if isinstance(node, ast.Import):
            found.extend((node.lineno, a.name) for a in node.names if _is_banned(a.name, banned))
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
            and _is_banned(node.module, banned)
        ):
            found.append((node.lineno, node.module))
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(ast.parse(source))
    return found


EAGER_SAMPLES = {
    "top-level": "import fitz\n",
    "try-except": "try:\n    import fitz\nexcept ImportError:\n    fitz = None\n",
    "except-handler": "try:\n    pass\nexcept ImportError:\n    from fitz.utils import x\n",
    "plain-if": "import os\nif os.name:\n    import fitz\n",
    "type-checking-else": (
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    pass\nelse:\n    import fitz\n"
    ),
    "class-body": "class A:\n    import fitz.utils\n",
    "with-block": "with open('x'):\n    import fitz\n",
    "from-import": "from fitz import open as fopen\n",
}

LAZY_SAMPLES = {
    "function": "def f() -> None:\n    import fitz\n",
    "async-function": "async def f() -> None:\n    from fitz.utils import x\n",
    "method": "class A:\n    def m(self) -> None:\n        import fitz\n",
    "type-checking": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import fitz\n",
    "typing-type-checking": "import typing\nif typing.TYPE_CHECKING:\n    import fitz\n",
    "light": "import json\nimport fitzgerald\nfrom . import fitz\n",
}


@pytest.mark.parametrize("source", EAGER_SAMPLES.values(), ids=list(EAGER_SAMPLES))
def test_ast_check_flags_eager_heavy_import(source: str) -> None:
    assert len(eager_heavy_imports(source, {"fitz"})) == 1


@pytest.mark.parametrize("source", LAZY_SAMPLES.values(), ids=list(LAZY_SAMPLES))
def test_ast_check_allows_lazy_heavy_import(source: str) -> None:
    assert eager_heavy_imports(source, {"fitz"}) == []


def test_app_has_no_eager_heavy_imports() -> None:
    banned = _configured_heavy()
    assert banned, "banned-module-level-imports must not be empty"
    files = sorted(APP_DIR.rglob("*.py"))
    assert files
    violations = [
        f"{path.relative_to(BACKEND)}:{line}: {module}"
        for path in files
        for line, module in eager_heavy_imports(path.read_text(encoding="utf-8"), banned)
    ]
    assert violations == [], "heavy libs must be imported lazily:\n" + "\n".join(violations)
