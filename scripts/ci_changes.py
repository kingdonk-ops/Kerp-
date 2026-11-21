"""Is a pull request tracker-only? Used by the ``detect`` job of .github/workflows/ci.yml.

A PR is tracker-only when every changed path is under ``tracker/`` or is one of the
generated boards/digest (``docs/TASKS.md``, ``docs/TODO.md``, ``docs/pr-suggestions.md``).
Exception: a ``tracker/todo/TD-xxx.md`` cited by a ``known_gap.todo`` in
``tests/contract/conformance_fixtures.json`` is read by the CEL conformance suites
(backend and frontend), so changing it is not tracker-only. An empty change list is
not tracker-only (fail safe: run everything).

CLI: paths as arguments, or one per line on stdin; prints ``tracker_only=true|false``.
"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "contract" / "conformance_fixtures.json"
GENERATED = frozenset({"docs/TASKS.md", "docs/TODO.md", "docs/pr-suggestions.md"})
_TODO_PATH = re.compile(r"tracker/todo/(TD-\d+)\.md")


def cited_todos(fixtures: Path) -> set[str]:
    """TODO ids cited by ``known_gap.todo`` in the conformance fixtures."""
    doc = json.loads(fixtures.read_text(encoding="utf-8"))
    cited: set[str] = set()
    for fixture in doc.get("fixtures", []):
        gap = fixture.get("known_gap") or {}
        if isinstance(gap.get("todo"), str):
            cited.add(gap["todo"])
    return cited


def is_tracker_only(paths: Iterable[str], fixtures: Path | None = None) -> bool:
    changed = [p.strip() for p in paths if p.strip()]
    if not changed:
        return False
    if not all(p.startswith("tracker/") or p in GENERATED for p in changed):
        return False
    todos = {m.group(1) for p in changed if (m := _TODO_PATH.fullmatch(p))}
    if todos and todos & cited_todos(fixtures or FIXTURES):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    paths = args if args else sys.stdin.read().splitlines()
    print(f"tracker_only={'true' if is_tracker_only(paths, FIXTURES) else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
