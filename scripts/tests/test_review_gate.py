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
