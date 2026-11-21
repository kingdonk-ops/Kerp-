"""Tests for scripts/shard_tests.py, the backend-pg shard split (T-0086, TD-512)."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
_spec = importlib.util.spec_from_file_location("shard_tests", ROOT / "scripts" / "shard_tests.py")
assert _spec
assert _spec.loader
shard_tests = importlib.util.module_from_spec(_spec)
sys.modules["shard_tests"] = shard_tests
_spec.loader.exec_module(shard_tests)


def _matrix_size() -> int:
    text = CI.read_text(encoding="utf-8")
    m = re.search(r"^        shard: \[([\d, ]+)\]", text, flags=re.MULTILINE)
    assert m, "backend-pg matrix not found"
    shards = [int(n) for n in m.group(1).split(",")]
    assert shards == list(range(1, len(shards) + 1)), shards
    return len(shards)


# --- the real tree ------------------------------------------------------------------------


def test_the_real_test_files_are_found() -> None:
    files = shard_tests.discover()
    assert len(files) > 50
    assert "tests/test_app.py" in files
    assert all(f.startswith("tests/") and f.endswith(".py") for f in files)
    assert "tests/conftest.py" not in files
    assert "tests/pg_databases.py" not in files


def test_the_backend_keeps_pytests_default_file_patterns() -> None:
    """The splitter finds files with pytest's default ``python_files``; a custom pattern or a
    second test root would need the splitter to follow (verify-collection would catch it in CI,
    this catches it before)."""
    text = (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert "python_files" not in text
    assert 'testpaths = ["tests"]' in text


def test_the_ci_shards_partition_every_real_test_file() -> None:
    files = shard_tests.discover()
    of = _matrix_size()
    shards = shard_tests.split(files, of, shard_tests.load_weights())
    assert len(shards) == of
    assert shard_tests.problems(files, shards) == []
    assert sorted(f for s in shards for f in s) == files  # union = all, no duplicates
    assert shard_tests.main(["check", "--of", str(of)]) == 0


def test_files_prints_exactly_the_shard(capsys: pytest.CaptureFixture[str]) -> None:
    of = _matrix_size()
    printed: list[str] = []
    for k in range(1, of + 1):
        assert shard_tests.main(["files", "--shard", str(k), "--of", str(of)]) == 0
        lines = capsys.readouterr().out.split()
        assert lines
        printed += lines
    assert sorted(printed) == shard_tests.discover()


def test_files_rejects_a_shard_out_of_range() -> None:
    with pytest.raises(SystemExit):
        shard_tests.main(["files", "--shard", "5", "--of", "4"])
    with pytest.raises(SystemExit):
        shard_tests.main(["files", "--shard", "0", "--of", "4"])


def test_the_stored_weights_name_real_files_and_are_positive() -> None:
    weights = shard_tests.load_weights()
    assert weights, "scripts/shard_weights.json is empty"
    files = set(shard_tests.discover())
    assert all(w >= 0 for w in weights.values())
    # Stale entries are harmless (ignored) but mean the weights need a refresh.
    stale = sorted(set(weights) - files)
    assert stale == [], f"rebalance: {stale} no longer exist (see docs/WORKFLOW.md)"


def test_the_coverage_floor_is_read_from_pyproject() -> None:
    assert shard_tests.coverage_floor() == "90"


def test_the_coverage_floor_must_be_present_and_unique(tmp_path: Path) -> None:
    def floor(addopts: str) -> str:
        p = tmp_path / "pyproject.toml"
        p.write_text(f'[tool.pytest.ini_options]\naddopts = "{addopts}"\n', encoding="utf-8")
        return shard_tests.coverage_floor(p)

    assert floor("-ra --cov=app --cov-fail-under=85.5") == "85.5"
    for bad in ("-ra --cov=app", "--cov-fail-under=90 --cov-fail-under=80", "--cov-fail-under=0"):
        with pytest.raises(SystemExit):
            floor(bad)


# --- split / problems on synthetic input --------------------------------------------------


def test_split_is_deterministic_and_balanced() -> None:
    files = [f"tests/test_{c}.py" for c in "abcdefgh"]
    weights = {"tests/test_a.py": 10.0, "tests/test_b.py": 6.0, "tests/test_c.py": 4.0}
    one = shard_tests.split(files, 3, weights)
    assert one == shard_tests.split(list(reversed(files)), 3, weights)  # input order irrelevant
    assert shard_tests.problems(files, one) == []
    assert "tests/test_a.py" in one[0]  # the heaviest file goes first, to shard 1
    assert shard_tests.split(files, 3, weights) == one  # same input, same split
    default = (10 + 6 + 4) / 3
    loads = [sum(weights.get(f, default) for f in s) for s in one]
    assert max(loads) - min(loads) <= 10.0


def test_unknown_files_get_the_mean_weight_and_no_weights_still_split() -> None:
    files = [f"tests/test_{n}.py" for n in range(10)]
    shards = shard_tests.split(files, 4, {})
    assert [len(s) for s in shards] == [3, 3, 2, 2]
    assert shard_tests.problems(files, shards) == []


def test_split_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="at least one shard"):
        shard_tests.split(["tests/test_a.py"], 0, {})
    with pytest.raises(ValueError, match="duplicate"):
        shard_tests.split(["tests/test_a.py", "tests/test_a.py"], 2, {})


def test_problems_reports_lost_duplicated_foreign_and_empty() -> None:
    files = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
    ok = [["tests/test_a.py", "tests/test_b.py"], ["tests/test_c.py"]]
    assert shard_tests.problems(files, ok) == []
    found = shard_tests.problems(
        files, [["tests/test_a.py", "tests/test_x.py"], ["tests/test_a.py"], []]
    )
    assert found == [
        "tests/test_a.py is in shard 1 and shard 2",
        "shard 3 is empty",
        "tests/test_b.py is in no shard",
        "tests/test_c.py is in no shard",
        "tests/test_x.py is not a test file",
    ]


def test_more_shards_than_files_is_refused() -> None:
    files = ["tests/test_a.py"]
    assert shard_tests.problems(files, shard_tests.split(files, 2, {})) == ["shard 2 is empty"]


def test_collection_problems() -> None:
    full = ["tests/test_a.py::t1", "tests/test_a.py::t2", "tests/test_b.py::t"]
    ok = [["tests/test_a.py::t1", "tests/test_a.py::t2"], ["tests/test_b.py::t"]]
    assert shard_tests.collection_problems(full, ok) == []
    bad = [["tests/test_a.py::t1", "tests/test_x.py::t"], ["tests/test_a.py::t1"]]
    assert shard_tests.collection_problems(full, bad) == [
        "tests/test_a.py::t1 collected by shard 1 and shard 2",
        "tests/test_a.py::t2 collected by no shard",
        "tests/test_b.py::t collected by no shard",
        "tests/test_x.py::t not in the full collection",
    ]


def test_verify_collection_compares_the_full_suite_with_the_shards(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    files = shard_tests.discover()

    def fake(arg_lists: list[list[str]], cwd: Path) -> list[list[str]]:
        assert cwd == shard_tests.BACKEND
        full, *shards = arg_lists
        assert full == []  # the whole suite, as configured (testpaths)
        return [[f"{f}::t" for f in files], *[[f"{f}::t" for f in s] for s in shards]]

    monkeypatch.setattr(shard_tests, "collect_many", fake)
    assert shard_tests.main(["verify-collection", "--of", "4"]) == 0
    assert f"collected: {len(files)} in the full suite" in capsys.readouterr().out

    def lossy(arg_lists: list[list[str]], cwd: Path) -> list[list[str]]:
        full, *rest = fake(arg_lists, cwd)
        return [[*full, "tests/test_hidden.py::t"], *rest]

    monkeypatch.setattr(shard_tests, "collect_many", lossy)
    assert shard_tests.main(["verify-collection", "--of", "4"]) == 1
    assert "::error::tests/test_hidden.py::t collected by no shard" in capsys.readouterr().out


def test_collect_many_runs_pytest_and_fails_loudly(tmp_path: Path) -> None:
    (tmp_path / "test_one.py").write_text("def test_a():\n    pass\n\ndef test_b():\n    pass\n")
    (tmp_path / "test_broken.py").write_text("import nonexistent_module_for_t0086\n")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    # The tracker job has no pytest-cov, so no --no-cov here (backend-pg-coverage passes it).
    opts = ("-p", "no:cacheprovider")
    args = [["test_one.py"], ["test_one.py::test_a"]]
    [one, a_only] = shard_tests.collect_many(args, tmp_path, opts)
    assert one == ["test_one.py::test_a", "test_one.py::test_b"]
    assert a_only == ["test_one.py::test_a"]
    with pytest.raises(SystemExit):
        shard_tests.collect_many([["test_broken.py"]], tmp_path, opts)


def test_weights_from_junit(tmp_path: Path) -> None:
    report = tmp_path / "junit-pg-1.xml"
    report.write_text(
        '<testsuites><testsuite name="pytest">'
        '<testcase file="tests/test_a.py" name="t1" time="1.25"/>'
        '<testcase file="tests/test_a.py" name="t2" time="0.5"/>'
        '<testcase file="tests/test_b.py" name="t" time="2"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    extra = shard_tests.PER_TEST_OVERHEAD
    expected = {
        "tests/test_a.py": round(1.75 + 2 * extra, 2),  # two cases
        "tests/test_b.py": round(2.0 + extra, 2),
    }
    assert shard_tests.weights_from_junit([report]) == expected
    no_file = tmp_path / "junit-xunit2.xml"
    no_file.write_text('<testsuite><testcase classname="tests.a" name="t" time="1"/></testsuite>')
    with pytest.raises(SystemExit):
        shard_tests.weights_from_junit([no_file])


def test_weights_cli_prints_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "r.xml"
    report.write_text('<testsuite><testcase file="tests/test_a.py" name="t" time="3"/></testsuite>')
    assert shard_tests.main(["weights", str(report)]) == 0
    expected = {"tests/test_a.py": 3.0 + shard_tests.PER_TEST_OVERHEAD}
    assert json.loads(capsys.readouterr().out) == expected
