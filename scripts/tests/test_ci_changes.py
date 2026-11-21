"""Tests for scripts/ci_changes.py: which PRs are tracker-only (app CI jobs skipped)."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location("ci_changes", ROOT / "scripts" / "ci_changes.py")
assert _spec and _spec.loader
ci_changes = importlib.util.module_from_spec(_spec)
sys.modules["ci_changes"] = ci_changes
_spec.loader.exec_module(ci_changes)


@pytest.fixture
def fixtures(tmp_path: Path) -> Path:
    """Synthetic conformance fixtures citing TD-900 in a known gap."""
    path = tmp_path / "conformance_fixtures.json"
    doc = {
        "fixtures": [
            {"id": "a", "expression": "1", "context": {}, "expect": {"value": 1}},
            {
                "id": "gap.b",
                "expression": "2",
                "context": {},
                "expect": {"value": 2},
                "known_gap": {"runtime": "js", "todo": "TD-900", "actual": {"value": 3}},
            },
        ]
    }
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_tracker_and_boards_only_is_tracker_only(fixtures: Path) -> None:
    paths = [
        "tracker/tasks/T-0001.md",
        "tracker/todo/TD-123.md",
        "tracker/tasks/E-01-01.md",
        "docs/TASKS.md",
        "docs/TODO.md",
        "docs/pr-suggestions.md",
    ]
    assert ci_changes.is_tracker_only(paths, fixtures) is True


@pytest.mark.parametrize(
    "other",
    [
        "backend/app/main.py",
        "frontend/src/App.tsx",
        "scripts/tracker.py",
        ".github/workflows/ci.yml",
        "tests/contract/conformance_fixtures.json",
        "CLAUDE.md",
        "docs/WORKFLOW.md",
    ],
)
def test_mixed_change_is_not_tracker_only(fixtures: Path, other: str) -> None:
    assert ci_changes.is_tracker_only(["tracker/tasks/T-0001.md", other], fixtures) is False


def test_docs_spec_change_is_not_tracker_only(fixtures: Path) -> None:
    assert ci_changes.is_tracker_only(["docs/spec/README.md"], fixtures) is False
    assert ci_changes.is_tracker_only(["docs/TASKS.md", "docs/spec/x.md"], fixtures) is False


def test_lookalike_paths_are_not_tracker_only(fixtures: Path) -> None:
    for path in ("trackerx/a.md", "docs/tracker/a.md", "docs/TASKS.md.bak", "x/docs/TODO.md"):
        assert ci_changes.is_tracker_only([path], fixtures) is False, path


def test_conformance_cited_todo_is_not_tracker_only(fixtures: Path) -> None:
    assert ci_changes.is_tracker_only(["tracker/todo/TD-900.md"], fixtures) is False
    assert ci_changes.is_tracker_only(["docs/TODO.md", "tracker/todo/TD-900.md"], fixtures) is False
    # An uncited TODO is still fine.
    assert ci_changes.is_tracker_only(["tracker/todo/TD-901.md"], fixtures) is True


def test_real_fixture_citations_are_found() -> None:
    cited = ci_changes.cited_todos(ci_changes.FIXTURES)
    assert cited  # the real file cites at least one TODO (TD-041 today)
    for todo in cited:
        assert ci_changes.is_tracker_only([f"tracker/todo/{todo}.md"]) is False


def test_missing_or_broken_fixtures_fail_safe(tmp_path: Path) -> None:
    missing = tmp_path / "absent.json"
    with pytest.raises(OSError):
        ci_changes.is_tracker_only(["tracker/todo/TD-001.md"], missing)
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        ci_changes.is_tracker_only(["tracker/todo/TD-001.md"], broken)


def test_empty_change_list_is_not_tracker_only(fixtures: Path) -> None:
    assert ci_changes.is_tracker_only([], fixtures) is False
    assert ci_changes.is_tracker_only(["", "  "], fixtures) is False


def test_cli_args_and_stdin(
    fixtures: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ci_changes, "FIXTURES", fixtures)
    assert ci_changes.main(["tracker/tasks/T-0001.md", "docs/TODO.md"]) == 0
    assert capsys.readouterr().out == "tracker_only=true\n"
    assert ci_changes.main(["backend/app/main.py"]) == 0
    assert capsys.readouterr().out == "tracker_only=false\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO("tracker/tasks/T-0001.md\ndocs/TASKS.md\n"))
    assert ci_changes.main([]) == 0
    assert capsys.readouterr().out == "tracker_only=true\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert ci_changes.main([]) == 0
    assert capsys.readouterr().out == "tracker_only=false\n"


def test_detect_job_uses_the_script_on_pull_requests_only() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    detect = ci.split("\n  detect:\n", 1)[1].split("\n  tracker:\n", 1)[0]
    assert "scripts/ci_changes.py" in detect
    assert "fetch-depth: 0" in detect
    assert "GITHUB_EVENT_NAME" in detect and '"pull_request"' in detect
    assert "BASE_REF: ${{ github.base_ref }}" in detect
    run = detect.split("run: |", 1)[1]
    assert "${{" not in run  # inputs reach the shell through env only
