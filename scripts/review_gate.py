#!/usr/bin/env python3
"""Review gate — a PR into `dev` may merge only when an allowed reviewer has
posted an approval marker for *this* change.

Reviewer agents post a PR *review* (`pulls/<n>/reviews`, `event=COMMENT` on own PRs)
containing exactly:

    KERP-REVIEW: APPROVED patch-id=<id>
    KERP-REVIEW: CHANGES_REQUESTED patch-id=<id>

`patch-id` is `git patch-id --stable` of the PR's code diff against its merge
base with the base branch, **excluding tracker paths** (tracker/**, docs/TASKS.md,
docs/TODO.md, docs/pr-suggestions.md — all generated bookkeeping). That makes the approval:
  * stable across rebases and tracker re-renders (coordinator bookkeeping),
  * invalidated by any real code change after review.
If the PR changes only tracker paths, the full diff is used instead.

Only PR reviews count. Issue comments are ignored: posting one does not re-run this
workflow (no `issue_comment` trigger, whose check would attach to the default branch, not
the PR), so a marker there would leave the gate stale.

The workflow runs this file from the PR's *base* checkout, inside the PR head checkout, so a
PR's edits to this script do not apply to its own run. (GitHub still runs the PR's version of
the workflow file itself; closing that needs a GitHub-level control, TD-223.)

Compute it locally with:  python scripts/review_gate.py --patch-id [base]

Run in CI by .github/workflows/review-gate.yml. Stdlib only.

Env: GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER, BASE_REF, HEAD_SHA,
     ALLOWED_REVIEWERS (comma-separated logins, case-insensitive).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass

MARKER_RE = re.compile(
    r"KERP-REVIEW:\s*(APPROVED|CHANGES_REQUESTED)\s+patch-id=([0-9a-f]{7,40}|none)", re.I
)
TRACKER_EXCLUDES = [
    ":(exclude)tracker/**",
    ":(exclude)docs/TASKS.md",
    ":(exclude)docs/TODO.md",
    ":(exclude)docs/pr-suggestions.md",
]


@dataclass(frozen=True)
class Verdict:
    author: str
    status: str  # APPROVED | CHANGES_REQUESTED
    patch_id: str
    at: str  # ISO-8601 timestamp, for ordering


def parse_markers(entries: list[dict[str, str]], allowed: set[str]) -> list[Verdict]:
    """entries: [{author, body, at}] gathered from the PR's reviews."""
    out: list[Verdict] = []
    for e in entries:
        author = (e.get("author") or "").lower()
        if author not in allowed:
            continue
        for m in MARKER_RE.finditer(e.get("body") or ""):
            out.append(Verdict(author, m.group(1).upper(), m.group(2).lower(), e.get("at") or ""))
    return sorted(out, key=lambda v: v.at)


def decide(verdicts: list[Verdict], patch_id: str) -> tuple[bool, str]:
    if not verdicts:
        return False, "no KERP-REVIEW marker from an allowed reviewer yet"
    latest = verdicts[-1]
    if latest.status != "APPROVED":
        return False, f"latest review by {latest.author} is CHANGES_REQUESTED"
    a, b = latest.patch_id, patch_id.lower()
    if not (a == b or (a != "none" and b != "none" and (a.startswith(b) or b.startswith(a)))):
        return False, (
            f"approval by {latest.author} was for patch-id {a}, but the PR is now {b} "
            "— code changed after review; re-review required"
        )
    return True, f"approved by {latest.author} for patch-id {a}"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def compute_patch_id(base: str, head: str = "HEAD") -> str:
    """Patch-id of the code diff (tracker paths excluded) between merge-base and head."""
    mb = git("merge-base", base, head).strip()
    diff = git("diff", mb, head, "--", ".", *TRACKER_EXCLUDES)
    if not diff.strip():
        diff = git("diff", mb, head)
    if not diff.strip():
        return "none"
    pid = subprocess.run(
        ["git", "patch-id", "--stable"], input=diff, check=True, capture_output=True, text=True
    ).stdout
    return pid.split()[0] if pid.strip() else "none"


def _get(url: str, token: str) -> list[dict]:
    items: list[dict] = []
    while url:
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310 — fixed api.github.com URL
            items += json.loads(resp.read())
            link = resp.headers.get("Link", "")
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = m.group(1) if m else ""
    return items


def review_entries(api: str, pr: str, token: str, get=_get) -> list[dict[str, str]]:
    """``{author, body, at}`` of every review on the PR (issue comments are not read)."""
    return [
        {"author": r["user"]["login"], "body": r.get("body") or "", "at": r.get("submitted_at") or ""}
        for r in get(f"{api}/pulls/{pr}/reviews?per_page=100", token)
    ]


def main(argv: list[str]) -> int:
    if argv[:1] == ["--patch-id"]:
        print(compute_patch_id(argv[1] if len(argv) > 1 else "origin/dev"))
        return 0
    env = os.environ
    repo, pr, token = env["GITHUB_REPOSITORY"], env["PR_NUMBER"], env["GITHUB_TOKEN"]
    base, head = env.get("BASE_REF", "dev"), env.get("HEAD_SHA", "HEAD")
    allowed = {a.strip().lower() for a in env.get("ALLOWED_REVIEWERS", "").split(",") if a.strip()}
    api = f"https://api.github.com/repos/{repo}"
    entries = review_entries(api, pr, token)
    patch_id = compute_patch_id(f"origin/{base}", head)
    ok, why = decide(parse_markers(entries, allowed), patch_id)
    print(f"patch-id={patch_id}")
    print(("PASS: " if ok else "FAIL: ") + why)
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### Review gate: {'✅ pass' if ok else '❌ fail'}\n\n{why}\n\nCurrent `patch-id={patch_id}`\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
