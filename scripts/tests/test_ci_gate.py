"""Tests for scripts/ci_gate.py and static checks on .github/workflows/ci.yml (T-0028)."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
_spec = importlib.util.spec_from_file_location("ci_gate", ROOT / "scripts" / "ci_gate.py")
assert _spec and _spec.loader
ci_gate = importlib.util.module_from_spec(_spec)
sys.modules["ci_gate"] = ci_gate
_spec.loader.exec_module(ci_gate)


def needs(flags: str = "true", **results: str) -> dict:
    """``toJSON(needs)``-shaped dict; every job green unless overridden."""
    out = {"backend": flags, "frontend": flags, "e2e": flags}
    jobs = {"detect": {"result": "success", "outputs": out}}
    for job in ("tracker", "backend", "backend-pg", "backend-image", "backend-publish", "frontend", "perf", "e2e"):
        jobs[job] = {"result": "success", "outputs": {}}
    for job, result in results.items():
        jobs[job.replace("_", "-")]["result"] = result
    return jobs


def test_all_green_on_push_passes() -> None:
    assert ci_gate.evaluate(needs(), "push") == []


def test_publish_must_skip_on_pull_request() -> None:
    assert ci_gate.evaluate(needs(backend_publish="skipped"), "pull_request") == []
    assert ci_gate.evaluate(needs(), "pull_request") != []  # ran on a PR: not expected


def test_components_absent_may_skip() -> None:
    skipped = {
        j: "skipped"
        for j in ("backend", "backend_pg", "backend_image", "backend_publish", "frontend", "perf", "e2e")
    }
    assert ci_gate.evaluate(needs("false", **skipped), "push") == []


@pytest.mark.parametrize(
    "job", ["backend", "backend_pg", "backend_image", "backend_publish", "frontend", "perf", "e2e"]
)
def test_component_present_but_skipped_fails(job: str) -> None:
    assert ci_gate.evaluate(needs(**{job: "skipped"}), "push") != []


@pytest.mark.parametrize(
    "job", ["detect", "tracker", "backend", "backend_pg", "backend_image", "frontend", "perf", "e2e"]
)
@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
def test_any_non_success_fails(job: str, result: str) -> None:
    assert ci_gate.evaluate(needs(**{job: result}), "push") != []


def test_detect_failure_without_outputs_fails() -> None:
    jobs = needs("false")
    jobs["detect"] = {"result": "failure", "outputs": {}}
    for job in ("backend", "backend-pg", "backend-image", "backend-publish", "frontend", "perf", "e2e"):
        jobs[job]["result"] = "skipped"
    assert ci_gate.evaluate(jobs, "push") != []


def test_detect_not_green_makes_every_skip_a_problem() -> None:
    """The "detect not green" override (PR #18 review, P9): without detect's flags no skip
    is known to be legitimate, so each skipped job is reported on its own, not only detect."""
    jobs = needs("false")
    jobs["detect"] = {"result": "failure", "outputs": {}}
    for job in ci_gate.EXPECTED:
        if job != "detect":
            jobs[job]["result"] = "skipped"
    assert ci_gate.evaluate(jobs, "push") == [
        "detect: failure (expected success)",
        "tracker: skipped (expected success)",
        "backend: skipped (expected success)",
        "backend-pg: skipped (expected success)",
        "backend-image: skipped (expected success)",
        "backend-publish: skipped (expected success)",
        "frontend: skipped (expected success)",
        "perf: skipped (expected success)",
        "e2e: skipped (expected success)",
    ]
    # Even a pull_request (where publish skips by design) and "false" flags change nothing.
    jobs["detect"] = {"result": "cancelled", "outputs": {"backend": "false"}}
    problems = ci_gate.evaluate(jobs, "pull_request")
    assert "backend-publish: skipped (expected success)" in problems
    assert "backend: skipped (expected success)" in problems
    assert "backend-pg: skipped (expected success)" in problems


def test_missing_job_fails() -> None:
    jobs = needs()
    del jobs["frontend"]
    assert ci_gate.evaluate(jobs, "push") != []


def test_main_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEEDS", json.dumps(needs()))
    monkeypatch.setenv("EVENT_NAME", "push")
    assert ci_gate.main() == 0
    monkeypatch.setenv("EVENT_NAME", "pull_request")
    assert ci_gate.main() == 1


# --- static checks on ci.yml (line-based: no YAML dependency in the tracker job) --------


def _jobs() -> dict[str, str]:
    text = CI.read_text(encoding="utf-8").split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):\n", text, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def test_ci_gate_needs_every_job_and_runs_the_script() -> None:
    jobs = _jobs()
    gate = jobs.pop("ci-gate")
    listed = re.search(r"needs: \[([^\]]*)\]", gate)
    assert listed
    assert {n.strip() for n in listed.group(1).split(",")} == set(jobs)
    assert set(ci_gate.EXPECTED) == set(jobs)
    assert "python3 scripts/ci_gate.py" in gate
    assert "toJSON(needs)" in gate


def test_only_the_publish_job_can_write_packages() -> None:
    jobs = _jobs()
    writers = {name for name, body in jobs.items() if "packages: write" in body}
    assert writers == {"backend-publish"}
    assert "github.event_name == 'push'" in jobs["backend-publish"]
    assert "push: true" not in jobs["backend-image"]
    head = CI.read_text(encoding="utf-8").split("\njobs:\n", 1)[0]
    assert "packages:" not in head


def test_actions_are_pinned_to_commit_shas() -> None:
    uses = re.findall(r"uses: (\S+)", CI.read_text(encoding="utf-8"))
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref


def test_checkout_never_persists_credentials() -> None:
    lines = CI.read_text(encoding="utf-8").splitlines()
    checkouts = [i for i, line in enumerate(lines) if "uses: actions/checkout@" in line]
    assert checkouts
    for i in checkouts:
        block = "\n".join(lines[i + 1 : i + 4])
        assert "persist-credentials: false" in block, lines[i]


def test_perf_job_runs_with_the_frontend_and_is_not_skippable() -> None:
    """T-0045: the NFR job runs whenever the frontend exists (so on every PR touching
    frontend/), measures through `npm run perf`, and warns on a breach inside the reporter;
    it is never allowed to fail silently (no continue-on-error, no path filter)."""
    perf = _jobs()["perf"]
    assert "if: needs.detect.outputs.frontend == 'true'" in perf
    assert "run: npm run perf" in perf
    assert "continue-on-error" not in perf
    assert "paths" not in perf
    assert ci_gate.EXPECTED["perf"] == ("frontend", False)
    head = CI.read_text(encoding="utf-8").split("\njobs:\n", 1)[0]
    assert "paths" not in head  # the workflow itself runs on every PR to dev


def test_install_does_not_reference_postgres_extra() -> None:
    assert "postgres]" not in CI.read_text(encoding="utf-8")


def test_backend_suites_run_as_parallel_jobs() -> None:
    """T-0081: SQLite (parallel workers) and PostgreSQL + RLS run as separate jobs that both
    start right after detect; publishing and e2e wait for both."""
    jobs = _jobs()
    sqlite, pg = jobs["backend"], jobs["backend-pg"]
    assert "needs: detect\n" in sqlite and "needs: detect\n" in pg
    assert "run: pytest -n auto --durations=25" in sqlite
    assert "services:" not in sqlite and "KERP_DATABASE_URL" not in sqlite
    assert "--cov" not in sqlite  # the coverage floor comes from pyproject addopts
    assert "postgresql+psycopg://kerp_app:" in pg  # NOSUPERUSER NOBYPASSRLS runtime role
    assert "20-kerp-app.sql" in pg
    assert "pytest -n auto --durations=25" in pg  # per-test databases (T-0082)
    assert "--no-cov" not in pg  # the coverage floor applies on PostgreSQL too (T-0082)
    assert "ALTER SYSTEM SET fsync = off" in pg and "pg_reload_conf()" in pg
    assert ci_gate.EXPECTED["backend-pg"] == ("backend", False)
    for job in ("backend-publish", "e2e"):
        listed = re.search(r"needs: \[([^\]]*)\]", jobs[job])
        assert listed and {"backend", "backend-pg"} <= {n.strip() for n in listed.group(1).split(",")}


def test_pull_request_with_backend_not_needed_passes() -> None:
    """T-0083: a frontend-only PR: detect says backend=false, the backend jobs skip, the
    frontend jobs still run and must be green."""
    jobs = needs()
    jobs["detect"]["outputs"]["backend"] = "false"
    for job in ("backend", "backend-pg", "backend-image", "backend-publish"):
        jobs[job]["result"] = "skipped"
    assert ci_gate.evaluate(jobs, "pull_request") == []
    jobs["frontend"]["result"] = "failure"
    assert ci_gate.evaluate(jobs, "pull_request") != []
    jobs["frontend"]["result"] = "skipped"
    assert ci_gate.evaluate(jobs, "pull_request") != []  # frontend never skips on backend=false
