"""Tests for scripts/review_gate.py and the review-gate / write-token workflows (T-0067)."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
_spec = importlib.util.spec_from_file_location("review_gate", ROOT / "scripts" / "review_gate.py")
assert _spec and _spec.loader
review_gate = importlib.util.module_from_spec(_spec)
sys.modules["review_gate"] = review_gate
_spec.loader.exec_module(review_gate)

ALLOWED = {"owner"}
APPROVE = "KERP-REVIEW: APPROVED patch-id=abc1234"


def _verdicts(*bodies: tuple[str, str, str]) -> list:
    return review_gate.parse_markers(
        [{"author": a, "body": b, "at": t} for a, b, t in bodies], ALLOWED
    )


def test_latest_allowed_approval_for_the_current_patch_passes() -> None:
    ok, _ = review_gate.decide(_verdicts(("owner", APPROVE, "2026-01-01T00:00:00Z")), "abc1234")
    assert ok


def test_markers_from_other_logins_do_not_count() -> None:
    ok, why = review_gate.decide(_verdicts(("intruder", APPROVE, "2026-01-01T00:00:00Z")), "abc1234")
    assert not ok and "no KERP-REVIEW marker" in why


def test_a_later_change_request_wins() -> None:
    verdicts = _verdicts(
        ("owner", APPROVE, "2026-01-01T00:00:00Z"),
        ("owner", "KERP-REVIEW: CHANGES_REQUESTED patch-id=abc1234", "2026-01-02T00:00:00Z"),
    )
    assert not review_gate.decide(verdicts, "abc1234")[0]


def test_approval_for_older_code_fails() -> None:
    ok, why = review_gate.decide(_verdicts(("owner", APPROVE, "2026-01-01T00:00:00Z")), "fff9999")
    assert not ok and "re-review required" in why


def test_only_pr_reviews_are_read_never_issue_comments() -> None:
    fetched: list[str] = []

    def fake_get(url: str, token: str) -> list[dict]:
        fetched.append(url)
        return [{"user": {"login": "owner"}, "body": APPROVE, "submitted_at": "2026-01-01T00:00:00Z"}]

    entries = review_gate.review_entries("https://api.github.com/repos/o/r", "7", "t", get=fake_get)
    assert fetched == ["https://api.github.com/repos/o/r/pulls/7/reviews?per_page=100"]
    assert entries == [{"author": "owner", "body": APPROVE, "at": "2026-01-01T00:00:00Z"}]
    assert "issues/" not in Path(review_gate.__file__).read_text().split("def main", 1)[1]


def test_review_gate_runs_the_script_from_the_base_checkout() -> None:
    text = (WORKFLOWS / "review-gate.yml").read_text()
    # Base checkout of the trusted script, head checkout only read.
    assert "ref: ${{ github.event.pull_request.base.sha }}" in text
    assert re.search(r"path: base\b", text)
    assert re.search(r"path: pr\b", text)
    assert re.search(r"working-directory: pr\b", text)
    run_lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("run:")]
    assert run_lines == ["run: python ../base/scripts/review_gate.py"]


def test_every_workflow_action_is_pinned_to_a_commit_sha() -> None:
    unpinned = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for n, line in enumerate(wf.read_text().splitlines(), 1):
            m = re.match(r"\s*-?\s*uses:\s*(\S+)", line)
            if m and not m.group(1).startswith("./") and not re.search(r"@[0-9a-f]{40}$", m.group(1)):
                unpinned.append(f"{wf.name}:{n} {m.group(1)}")
    assert unpinned == []


def test_reviewer_and_coordinator_docs_describe_the_gate_computation() -> None:
    """T-0029: workflow, reviewer and coordinator docs all use the one patch-id computation."""
    docs = [
        ROOT / ".claude" / "agents" / "kerp-reviewer.md",
        ROOT / ".claude" / "commands" / "kerp-run.md",
        ROOT / "docs" / "WORKFLOW.md",
    ]
    for doc in docs:
        text = doc.read_text()
        assert "python scripts/review_gate.py --patch-id origin/dev" in text, doc.name
        assert "**normalised** `tracker/**` diff" in text, doc.name
        for key in ("`status`", "`pr`", "`owner`", "`updated`", "- YYYY-MM-DD"):
            assert key in text, (doc.name, key)
        for board in ("docs/TASKS.md", "docs/TODO.md", "docs/pr-suggestions.md"):
            assert board in text, (doc.name, board)
        assert "excludes `tracker/**`" not in text, doc.name
    # The workflow takes the id from the script (no computation of its own), and the
    # script's bookkeeping keys are exactly the ones the docs promise.
    run_lines = [ln for ln in (WORKFLOWS / "review-gate.yml").read_text().splitlines() if ln.strip().startswith("run:")]
    assert run_lines and all("review_gate.py" in ln and "patch-id" not in ln for ln in run_lines)
    assert review_gate.BOOKKEEPING_KEYS == {"status", "pr", "owner", "updated"}
