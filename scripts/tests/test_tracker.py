"""Tests for scripts/tracker.py and scripts/review_gate.py (pure logic)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


tracker = _load("tracker")
gate = _load("review_gate")


def task(tmp: Path, id_: str, **meta) -> "tracker.Item":
    base = {"id": id_, "title": f"Task {id_}", "phase": "0", "module": "core", "size": "S", "status": "todo"}
    base.update(meta)
    p = tmp / f"{id_}.md"
    item = tracker.Item(p, base, "## Goal\n")
    p.write_text(tracker.dump(item), encoding="utf-8")
    return tracker.parse_text(p.read_text(encoding="utf-8"), p)


def todo(tmp: Path, id_: str, **meta) -> "tracker.Item":
    base = {"id": id_, "title": "x", "kind": "decision", "status": "open"}
    base.update(meta)
    p = tmp / f"{id_}.md"
    p.write_text(tracker.dump(tracker.Item(p, base, "")), encoding="utf-8")
    return tracker.parse_text(p.read_text(encoding="utf-8"), p)


# ------------------------------------------------------------------ tracker
def test_roundtrip_preserves_lists_and_colons(tmp_path):
    t = task(tmp_path, "T-0001", title="API: health", depends_on=["T-0002", "T-0003"])
    assert t.get("title") == "API: health"
    assert t.deps() == ["T-0002", "T-0003"]


def test_validate_catches_unknown_dep_cycle_and_bad_size(tmp_path):
    a = task(tmp_path, "T-0001", depends_on=["T-0002"])
    b = task(tmp_path, "T-0002", depends_on=["T-0001"])
    c = task(tmp_path, "T-0003", depends_on=["T-9999"])
    d = task(tmp_path, "T-0004", size="L")
    errs = "\n".join(tracker.validate([a, b, c, d], []))
    assert "cycle" in errs
    assert "unknown id 'T-9999'" in errs
    assert "size L is only for epics" in errs


def test_done_requires_pr(tmp_path):
    t = task(tmp_path, "T-0001", status="done")
    assert any("requires a pr" in e for e in tracker.validate([t], []))
    t2 = task(tmp_path, "T-0002", status="done", pr="7")
    assert tracker.validate([t2], []) == []


def test_ready_respects_deps_blockers_and_order(tmp_path):
    a = task(tmp_path, "T-0001", status="done", pr="1")
    b = task(tmp_path, "T-0002", depends_on=["T-0001"], order="2")
    c = task(tmp_path, "T-0003", depends_on=["T-0002"])
    d = task(tmp_path, "T-0004", order="1")
    e = task(tmp_path, "T-0005", blocked_by=["TD-001"])
    epic = task(tmp_path, "E-03-01", phase="3", size="L", status="needs-breakdown")
    td = todo(tmp_path, "TD-001")
    ids = [t.id for t in tracker.ready([a, b, c, d, e, epic], [td])]
    assert ids == ["T-0004", "T-0002"]  # order field, then id; c waits on b; e blocked; epics never ready
    td_closed = todo(tmp_path, "TD-001", status="resolved")
    assert "T-0005" in [t.id for t in tracker.ready([a, b, c, d, e], [td_closed])]


def test_render_is_deterministic_and_marks_ready(tmp_path):
    a = task(tmp_path, "T-0001", status="done", pr="3")
    b = task(tmp_path, "T-0002", depends_on=["T-0001"])
    out1 = tracker.render_tasks([a, b], [])
    out2 = tracker.render_tasks([b, a], [])
    assert out1 == out2
    assert "1/2 tasks done (50%)" in out1
    assert "| ▶ | [T-0002]" in out1


def test_render_todos_groups_by_kind(tmp_path):
    t1 = todo(tmp_path, "TD-001", kind="decision", title="Pick auth")
    t2 = todo(tmp_path, "TD-002", kind="deferred", title="Later thing")
    t3 = todo(tmp_path, "TD-003", status="resolved", resolution="done in #4")
    out = tracker.render_todos([t1, t2, t3])
    assert "## decision (1)" in out and "## deferred (1)" in out and "## Closed" in out


def test_repo_tracker_is_valid_and_boards_fresh():
    """The real tracker in this repo must validate and the boards must be current."""
    root = SCRIPTS.parent
    r = subprocess.run([sys.executable, str(SCRIPTS / "tracker.py"), "render", "--check"],
                       cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ------------------------------------------- remote-aware TODO id allocation
def fake_git(trees: dict[str, list[str]], prs: list[dict] | None = None, fail: set[str] = frozenset(),
             files: dict[tuple[str, str], str] | None = None, merge_base: str = "mb"):
    """A Runner that answers git/gh commands from in-memory data (no network, no repo)."""
    calls: list[list[str]] = []

    def run(cmd: list[str]) -> str:
        calls.append(cmd)
        key = " ".join(cmd[:2])
        if key in fail or cmd[0] in fail:
            raise subprocess.CalledProcessError(1, cmd)
        if key == "git fetch":
            return ""
        if key == "git for-each-ref":
            return "\n".join(trees) + "\n"
        if key == "git ls-tree":
            return "\n".join(f"tracker/todo/{n}" for n in trees[cmd[3]]) + "\n"
        if key == "git merge-base":
            return merge_base + "\n"
        if key == "git show":
            ref, path = cmd[2].split(":", 1)
            return files[(ref, path)]
        if key == "gh pr":
            return json.dumps(prs or [])
        raise AssertionError(f"unexpected command {cmd}")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_todo_ids_in_paths_only_counts_todo_files():
    paths = ["tracker/todo/TD-007.md", "TD-012.md", "tracker/tasks/T-0001.md", "docs/TD-999.md",
             "tracker\\todo\\TD-020.md", "tracker/todo/README.md"]
    assert tracker.todo_ids_in_paths(paths) == {"TD-007", "TD-012", "TD-020"}


def test_allocate_takes_max_over_local_branches_and_open_prs(tmp_path):
    local = [todo(tmp_path, "TD-001"), todo(tmp_path, "TD-031")]
    run = fake_git(
        {"refs/remotes/origin/dev": ["TD-001.md", "TD-039.md"],
         "refs/remotes/origin/HEAD": ["TD-900.md"],  # symbolic ref: skipped
         "refs/heads/task/T-0017-x": ["TD-001.md", "TD-041.md"]},
        prs=[{"number": 15, "files": [{"path": "tracker/todo/TD-044.md"}, {"path": "backend/x.py"}]}],
    )
    tid, warns = tracker.allocate_todo_id(local, run=run)
    assert tid == "TD-045" and warns == []
    assert ["git", "fetch", "--quiet", "--prune", "origin"] in run.calls
    assert not any("refs/remotes/origin/HEAD" in c for c in run.calls)


def test_allocate_falls_back_to_local_with_warnings_when_git_and_gh_missing(tmp_path):
    local = [todo(tmp_path, "TD-031")]
    run = fake_git({}, fail={"git", "gh"})
    tid, warns = tracker.allocate_todo_id(local, run=run)
    assert tid == "TD-032"
    text = " ".join(warns)
    assert "git fetch failed" in text and "cannot list git refs" in text and "open PRs not checked" in text


def test_allocate_uses_stale_refs_when_fetch_fails(tmp_path):
    run = fake_git({"refs/remotes/origin/dev": ["TD-050.md"]}, fail={"git fetch", "gh"})
    tid, warns = tracker.allocate_todo_id([], run=run)
    assert tid == "TD-051"
    assert any("may be stale" in w for w in warns)


def test_allocate_offline_never_runs_commands(tmp_path):
    run = fake_git({"refs/remotes/origin/dev": ["TD-050.md"]})
    tid, warns = tracker.allocate_todo_id([todo(tmp_path, "TD-003")], run=run, offline=True)
    assert tid == "TD-004" and run.calls == [] and "--offline" in warns[0]


def test_todo_id_collisions_flags_only_new_ids_with_different_titles():
    head = {"TD-032": "CEL parity", "TD-033": "Same thing", "TD-010": "Retitled on branch"}
    base = {"TD-032": "tenant FK", "TD-033": "Same thing", "TD-010": "Old title"}
    errs = tracker.todo_id_collisions(head, base, ancestor={"TD-010"})
    assert len(errs) == 1 and errs[0].startswith("TD-032: id collision")


def _td_text(id_: str, title: str) -> str:
    return tracker.dump(tracker.Item(Path(f"{id_}.md"), {"id": id_, "title": title, "kind": "bug", "status": "open"}))


def test_check_id_collisions_between_refs():
    run = fake_git(
        {"mb": ["TD-031.md"], "origin/dev": ["TD-031.md", "TD-032.md", "TD-033.md"],
         "pr": ["TD-031.md", "TD-032.md", "TD-033.md", "TD-034.md"]},
        files={("origin/dev", "tracker/todo/TD-032.md"): _td_text("TD-032", "tenant FK"),
               ("pr", "tracker/todo/TD-032.md"): _td_text("TD-032", "CEL parity"),
               ("origin/dev", "tracker/todo/TD-033.md"): _td_text("TD-033", "same"),
               ("pr", "tracker/todo/TD-033.md"): _td_text("TD-033", "same")},
    )
    errs = tracker.check_id_collisions("origin/dev", "pr", [], run=run)
    assert len(errs) == 1 and "TD-032" in errs[0] and "'CEL parity'" in errs[0] and "'tenant FK'" in errs[0]
    assert ["git", "merge-base", "origin/dev", "pr"] in run.calls


def test_check_id_collisions_against_working_tree(tmp_path):
    local = [todo(tmp_path, "TD-031"), todo(tmp_path, "TD-032", title="CEL parity")]
    run = fake_git({"mb": ["TD-031.md"], "origin/dev": ["TD-031.md", "TD-032.md"]},
                   files={("origin/dev", "tracker/todo/TD-032.md"): _td_text("TD-032", "tenant FK")})
    errs = tracker.check_id_collisions("origin/dev", None, local, run=run)
    assert len(errs) == 1 and "TD-032" in errs[0]
    assert ["git", "merge-base", "origin/dev", "HEAD"] in run.calls


# -------------------------------------------------------------- review gate
ALLOWED = {"kingdonk-ops"}


def test_gate_requires_marker_from_allowed_author():
    entries = [{"author": "rando", "body": "KERP-REVIEW: APPROVED patch-id=abc1234", "at": "2026-11-08T01:00:00Z"}]
    ok, why = gate.decide(gate.parse_markers(entries, ALLOWED), "abc1234")
    assert not ok and "no KERP-REVIEW" in why


def test_gate_latest_verdict_wins_and_patch_id_must_match():
    entries = [
        {"author": "kingdonk-ops", "body": "KERP-REVIEW: CHANGES_REQUESTED patch-id=aaaaaaa", "at": "2026-11-08T01:00:00Z"},
        {"author": "Kingdonk-Ops", "body": "LGTM\nKERP-REVIEW: APPROVED patch-id=bbbbbbbcafe", "at": "2026-11-08T02:00:00Z"},
    ]
    v = gate.parse_markers(entries, ALLOWED)
    assert gate.decide(v, "bbbbbbbcafe")[0]
    assert gate.decide(v, "bbbbbbb")[0]  # short-id prefix ok
    ok, why = gate.decide(v, "ccccccc")
    assert not ok and "re-review" in why


def test_gate_changes_requested_blocks():
    entries = [
        {"author": "kingdonk-ops", "body": "KERP-REVIEW: APPROVED patch-id=aaaaaaa", "at": "2026-11-08T01:00:00Z"},
        {"author": "kingdonk-ops", "body": "KERP-REVIEW: CHANGES_REQUESTED patch-id=aaaaaaa", "at": "2026-11-08T03:00:00Z"},
    ]
    assert not gate.decide(gate.parse_markers(entries, ALLOWED), "aaaaaaa")[0]


@pytest.fixture
def repo(tmp_path):
    def run(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

    run("init", "-q", "-b", "dev")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n")
    run("add", ".")
    run("commit", "-qm", "base")
    run("checkout", "-qb", "feature")
    (tmp_path / "a.py").write_text("x = 2\n")
    run("commit", "-qam", "change")
    return tmp_path, run


def test_patch_id_ignores_tracker_changes(repo, monkeypatch):
    path, run = repo
    monkeypatch.chdir(path)
    before = gate.compute_patch_id("dev")
    (path / "tracker").mkdir()
    (path / "tracker" / "T-0001.md").write_text("status: done\n")
    (path / "docs").mkdir()
    (path / "docs" / "TASKS.md").write_text("board\n")
    run("add", ".")
    run("commit", "-qm", "tracker")
    assert gate.compute_patch_id("dev") == before
    (path / "a.py").write_text("x = 3\n")
    run("commit", "-qam", "code")
    assert gate.compute_patch_id("dev") != before
