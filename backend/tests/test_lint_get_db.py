"""Contract test: endpoints get their session through ``DbSession`` only (TD-036, T-0011).

A bare ``Depends(get_db)`` has FastAPI's default dependency scope, which commits *after*
the response is sent: a write that fails at commit would answer 200 and be rolled back.
``DbSession`` pins ``scope="function"`` (see ``app/core/db.py``). This walk fails when any
file under ``backend/app`` other than ``app/core/db.py`` mentions ``get_db`` at all, so
neither ``Depends(get_db)`` nor an alias of it can slip in.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
ALLOWED = APP_DIR / "core" / "db.py"
NAME = "get_db"


def get_db_uses(source: str) -> list[int]:
    """Line numbers where ``source`` names ``get_db`` (import, call, attribute or alias)."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if (
            (isinstance(node, ast.Name) and node.id == NAME)
            or (isinstance(node, ast.Attribute) and node.attr == NAME)
            or (isinstance(node, ast.alias) and NAME in (node.name, node.asname))
        ):
            lines.append(node.lineno)
    return sorted(set(lines))


def test_only_db_py_uses_get_db() -> None:
    offenders = [
        f"{path.relative_to(APP_DIR.parent).as_posix()}:{line}"
        for path in sorted(APP_DIR.rglob("*.py"))
        if path != ALLOWED
        for line in get_db_uses(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], "use DbSession, never get_db directly: " + ", ".join(offenders)


def test_db_py_still_defines_the_dependency() -> None:
    # Guards the guard: if get_db were renamed, the walk above would pass vacuously.
    assert get_db_uses(ALLOWED.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "source",
    [
        "from app.core.db import get_db\n",
        "from app.core.db import get_db as session_dep\n",
        "import app.core.db as d\nx = d.get_db\n",
        "def f(s = Depends(get_db)): ...\n",
        "S = Annotated[Session, Depends(db.get_db)]\n",
    ],
)
def test_the_walk_flags_every_spelling(source: str) -> None:
    assert get_db_uses(source)


@pytest.mark.parametrize(
    "source",
    [
        "from app.core.db import DbSession\n\ndef f(session: DbSession): ...\n",
        "get_dbx = 1\nname = 'get_db'\n",
    ],
)
def test_the_walk_allows_db_session(source: str) -> None:
    assert get_db_uses(source) == []
