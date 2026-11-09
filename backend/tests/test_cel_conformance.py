"""Shared CEL conformance fixtures on the server runtime (T-0017, diff §3).

The same file is asserted by ``frontend/src/lib/cel/conformance.test.ts`` on the client
runtime. Schema and value encoding: ``tests/contract/README.md``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.core.cel import CelError, CelErrorCode, CelOk, evaluate

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_FILE = REPO_ROOT / "tests" / "contract" / "conformance_fixtures.json"
TODO_DIR = REPO_ROOT / "tracker" / "todo"
RUNTIME = "python"


def load_fixture_document(path: Path) -> dict[str, Any]:
    """Load and sanity-check the fixture file; raise (never skip) if missing or empty."""
    if not path.is_file():
        raise RuntimeError(f"CEL conformance fixture file not found: {path}")
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    fixtures = document.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise RuntimeError(f"CEL conformance fixture file has no fixtures: {path}")
    return document


# Loaded at collection time on purpose: a missing/empty file is a collection error (red
# suite), not an empty parametrisation (which pytest would report as skipped).
DOCUMENT = load_fixture_document(FIXTURE_FILE)
FIXTURES: list[dict[str, Any]] = DOCUMENT["fixtures"]


def canonical(value: Any) -> Any:
    """Type-tagged form for exact comparison (``True != 1.0``, ``3 == 3.0`` as doubles)."""
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int | float):
        return ("double", float(value))
    if isinstance(value, str):
        return ("string", value)
    if value is None:
        return ("null",)
    if isinstance(value, list):
        return ("list", tuple(canonical(item) for item in value))
    assert isinstance(value, dict), f"unexpected JSON value {value!r}"
    return ("map", tuple(sorted((key, canonical(item)) for key, item in value.items())))


def outcome(result: CelOk | CelError) -> Any:
    if isinstance(result, CelError):
        return ("error", result.code.value)
    return ("value", canonical(result.value))


def expected_outcome(expect: dict[str, Any]) -> Any:
    if "error" in expect:
        return ("error", expect["error"])
    return ("value", canonical(expect["value"]))


def test_loader_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not found"):
        load_fixture_document(tmp_path / "absent.json")


def test_loader_rejects_empty_fixture_list(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text('{"fixtures": []}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="no fixtures"):
        load_fixture_document(empty)


def test_error_code_enum_matches_shared_list() -> None:
    assert [code.value for code in CelErrorCode] == DOCUMENT["error_codes"]


def test_fixture_ids_are_unique() -> None:
    ids = [fixture["id"] for fixture in FIXTURES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f["id"] for f in FIXTURES])
def test_fixture_is_well_formed(fixture: dict[str, Any]) -> None:
    assert isinstance(fixture["expression"], str)
    assert isinstance(fixture["context"], dict)
    expectations = [fixture["expect"]]
    gap = fixture.get("known_gap")
    if gap is not None:
        assert gap["runtime"] in {"python", "js"}
        assert re.fullmatch(r"TD-\d{3,}", gap["todo"])
        todo = TODO_DIR / f"{gap['todo']}.md"
        assert todo.is_file(), "known_gap must cite an existing TODO"
        tasks = re.search(r"^tasks:\s*\[(.*)\]\s*$", todo.read_text(encoding="utf-8"), re.M)
        assert tasks is not None
        assert "T-0017" in [t.strip() for t in tasks.group(1).split(",")], (
            f"{gap['todo']} is not a T-0017 TODO (id collision?)"
        )
        assert expected_outcome(gap["actual"]) != expected_outcome(fixture["expect"])
        expectations.append(gap["actual"])
    for expect in expectations:
        assert len(expect) == 1
        assert set(expect) <= {"value", "error"}
        if "error" in expect:
            assert expect["error"] in DOCUMENT["error_codes"]


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f["id"] for f in FIXTURES])
def test_fixture(fixture: dict[str, Any]) -> None:
    gap = fixture.get("known_gap")
    # A documented gap on this runtime is pinned to its current (divergent) result, so a
    # library change is noticed; it is never skipped. See tests/contract/README.md.
    expect = gap["actual"] if gap is not None and gap["runtime"] == RUNTIME else fixture["expect"]
    result = evaluate(fixture["expression"], fixture["context"])
    assert outcome(result) == expected_outcome(expect), result
