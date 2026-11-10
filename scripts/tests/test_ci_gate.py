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
    for job in ("tracker", "backend", "backend-image", "backend-publish", "frontend", "e2e"):
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
    skipped = {j: "skipped" for j in ("backend", "backend_image", "backend_publish", "frontend", "e2e")}
    assert ci_gate.evaluate(needs("false", **skipped), "push") == []


@pytest.mark.parametrize("job", ["backend", "backend_image", "backend_publish", "frontend", "e2e"])
def test_component_present_but_skipped_fails(job: str) -> None:
    assert ci_gate.evaluate(needs(**{job: "skipped"}), "push") != []


@pytest.mark.parametrize("job", ["detect", "tracker", "backend", "backend_image", "frontend", "e2e"])
@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
def test_any_non_success_fails(job: str, result: str) -> None:
    assert ci_gate.evaluate(needs(**{job: result}), "push") != []


def test_detect_failure_without_outputs_fails() -> None:
    jobs = needs("false")
    jobs["detect"] = {"result": "failure", "outputs": {}}
    for job in ("backend", "backend-image", "backend-publish", "frontend", "e2e"):
        jobs[job]["result"] = "skipped"
    assert ci_gate.evaluate(jobs, "push") != []


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


def test_install_does_not_reference_postgres_extra() -> None:
    assert "postgres]" not in CI.read_text(encoding="utf-8")
