"""Can a pull request skip the app jobs? Used by the ``detect`` job of .github/workflows/ci.yml.

A PR is docs-or-tracker-only when every changed path is under ``tracker/``, ``docs/``
(spec, plan, decisions, generated boards and digest) or ``.claude/`` (agent definitions).
No backend, frontend or e2e test reads those trees (T-0081); the ``tracker`` job, which
does read them (boards, ``docs/WORKFLOW.md``), always runs.
Exception: a ``tracker/todo/TD-xxx.md`` cited by a ``known_gap.todo`` in
``tests/contract/conformance_fixtures.json`` is read by the CEL conformance suites
(backend and frontend), so changing it runs everything. An empty change list is not
docs-or-tracker-only (fail safe: run everything).

``needs_backend`` (T-0083) is false when every changed path is under ``frontend/`` or one of
the trees above, so a frontend-only PR skips the backend jobs. Anything else, including
``backend/``, ``infra/``, ``tests/contract/``, ``.github/``, ``scripts/``, ``e2e/`` and root
files, needs them (fail safe), as does a cited conformance TODO or an empty change list.
The reverse never skips: a backend change can change the generated API client the frontend
compiles against, so the frontend jobs always run.

CLI: paths as arguments, or one per line on stdin; prints ``docs_or_tracker_only=true|false``
and ``backend_needed=true|false`` on two lines.
"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "contract" / "conformance_fixtures.json"
SKIPPABLE_PREFIXES = ("tracker/", "docs/", ".claude/")
FRONTEND_ONLY_PREFIXES = ("frontend/", *SKIPPABLE_PREFIXES)
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


def _only_under(paths: Iterable[str], prefixes: tuple[str, ...], fixtures: Path | None) -> bool:
    """Every changed path starts with one of ``prefixes`` and none is a cited TODO."""
    changed = [p.strip() for p in paths if p.strip()]
    if not changed:
        return False
    if not all(p.startswith(prefixes) for p in changed):
        return False
    todos = {m.group(1) for p in changed if (m := _TODO_PATH.fullmatch(p))}
    if todos and todos & cited_todos(fixtures or FIXTURES):
        return False
    return True


def is_docs_or_tracker_only(paths: Iterable[str], fixtures: Path | None = None) -> bool:
    return _only_under(paths, SKIPPABLE_PREFIXES, fixtures)


def needs_backend(paths: Iterable[str], fixtures: Path | None = None) -> bool:
    return not _only_under(paths, FRONTEND_ONLY_PREFIXES, fixtures)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    paths = args if args else sys.stdin.read().splitlines()
    skip = is_docs_or_tracker_only(paths, FIXTURES)
    backend = needs_backend(paths, FIXTURES)
    print(f"docs_or_tracker_only={'true' if skip else 'false'}")
    print(f"backend_needed={'true' if backend else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
