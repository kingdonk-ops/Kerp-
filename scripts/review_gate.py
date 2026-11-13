#!/usr/bin/env python3
"""Review gate — a PR into `dev` may merge only when an allowed reviewer has
posted an approval marker for *this* change.

Reviewer agents post a PR *review* (`pulls/<n>/reviews`, `event=COMMENT` on own PRs)
containing exactly:

    KERP-REVIEW: APPROVED patch-id=<id>
    KERP-REVIEW: CHANGES_REQUESTED patch-id=<id>

`patch-id` is `git patch-id --stable` of the PR's diff against its merge base with the
base branch (T-0029, TD-013), built from two parts:
  1. the code diff — every path except tracker/**, docs/TASKS.md, docs/TODO.md and
     docs/pr-suggestions.md (the last three are generated and never part of the id);
  2. a diff of every changed tracker/** file after `normalise_tracker_text`, which drops
     bookkeeping only: front-matter `status`, `pr`, `owner`, `updated`, dated note lines
     (`- YYYY-MM-DD...`) and blank-line layout.
That makes the approval:
  * stable across rebases, board re-renders and coordinator bookkeeping
    (`tracker.py set --status/--pr/--owner/--note`),
  * invalidated by any code change and by any tracker *content* change (goal, acceptance
    criteria, title, deps, undated text, new/deleted tracker files) after review.
If both parts are empty (a bookkeeping-only PR), the full raw diff is used instead.

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

import difflib
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
# Front-matter keys that are pure bookkeeping (set by tracker.py / the coordinator).
BOOKKEEPING_KEYS = frozenset({"status", "pr", "owner", "updated"})
# Column 0 only: indented dated sub-bullets (e.g. under an acceptance criterion) are content.
DATED_NOTE_RE = re.compile(r"^- \d{4}-\d{2}-\d{2}\b")


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


def normalise_tracker_text(text: str) -> str:
    """Tracker file content with bookkeeping removed (see module docstring).

    Drops front-matter lines whose key is in BOOKKEEPING_KEYS, every dated note line
    (``- YYYY-MM-DD...``), and blank lines (so the blank line ``set --note`` adds before
    a note does not count). Everything else is review-relevant content.
    """
    lines = text.splitlines()
    out: list[str] = []
    in_front = bool(lines) and lines[0].strip() == "---"
    for i, line in enumerate(lines):
        if in_front and i > 0 and line.strip() == "---":
            in_front = False
            out.append(line)
            continue
        if in_front and i > 0:
            key = line.split(":", 1)[0].strip()
            if ":" in line and not line[:1].isspace() and key in BOOKKEEPING_KEYS:
                continue
        elif DATED_NOTE_RE.match(line) or not line.strip():
            continue
        out.append(line.rstrip())
    return "\n".join(out) + "\n" if out else ""


def _show(rev: str, path: str) -> str:
    try:
        return git("show", f"{rev}:{path}")
    except subprocess.CalledProcessError:  # path absent at rev (added / deleted file)
        return ""


def tracker_diff(mb: str, head: str) -> str:
    """Unified git-style diff of the normalised tracker/** files changed in mb..head."""
    paths = git("diff", "--name-only", "--no-renames", mb, head, "--", "tracker").split()
    chunks: list[str] = []
    for path in sorted(paths):
        a = normalise_tracker_text(_show(mb, path)).splitlines(keepends=True)
        b = normalise_tracker_text(_show(head, path)).splitlines(keepends=True)
        if a == b:
            continue
        hunks = "".join(difflib.unified_diff(a, b, f"a/{path}", f"b/{path}", n=3))
        chunks.append(f"diff --git a/{path} b/{path}\n{hunks}")
    return "".join(chunks)


def compute_patch_id(base: str, head: str = "HEAD") -> str:
    """Patch-id of code diff + normalised tracker diff (boards excluded), merge-base..head."""
    mb = git("merge-base", base, head).strip()
    diff = git("diff", mb, head, "--", ".", *TRACKER_EXCLUDES) + tracker_diff(mb, head)
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
