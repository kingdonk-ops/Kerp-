#!/usr/bin/env python3
"""Harvest notes / TODOs / suggestions / ideas surfaced on merged PRs into a
single human-readable digest: docs/pr-suggestions.md.

The file is a GENERATED artifact, like the tracker boards. It is rebuilt
deterministically from the GitHub API, so it is safe to re-run any time and
diffs are stable. Nothing is hand-edited here — act on an item, then it stays
in the log as a record (tick it in your own notes, or raise/close the matching
TD-xxx in tracker/).

Sources harvested, per merged PR into the base branch (default: dev):
  * PR body  -> the "What changed" and "TODOs raised" sections
  * Reviews  -> the KERP-REVIEW verdict + actionable sections
               (Must fix / Should fix / Suggestions / Nits / Follow-ups /
                Later / Future / Non-blocking / Ideas / Notes / Observations …)
  * Issue comments and inline review comments (if any)

Every block links back to the PR / review so the full context is one click away.

Usage:
  python scripts/pr_suggestions.py rebuild                 # rebuild the whole file
  python scripts/pr_suggestions.py rebuild --check         # non-zero exit if stale (CI)
  python scripts/pr_suggestions.py rebuild --repo o/r --base dev --out docs/pr-suggestions.md

Needs `gh` authenticated (locally) or GH_TOKEN/GITHUB_TOKEN in the environment
(GitHub Actions). Read-only on GitHub.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO = os.environ.get("KERP_REPO", "kingdonk-ops/Kerp-")
DEFAULT_BASE = os.environ.get("KERP_BASE", "dev")
DEFAULT_OUT = "docs/pr-suggestions.md"

# Section headings in a review body whose content is worth surfacing.
ACTIONABLE = re.compile(
    r"\b("
    r"must[\s-]?fix|should[\s-]?fix|should|suggest\w*|nit\w*|follow[\s-]?up|"
    r"later|future|non[\s-]?blocking|recommend\w*|idea\w*|note\w*|consider|"
    r"optional|defer\w*|out[\s-]?of[\s-]?scope|observation\w*|open[\s-]?question\w*|"
    r"todo|gap|risk|caveat|concern\w*"
    r")\b",
    re.I,
)
TASK_RE = re.compile(r"\bT-\d{4}\b")
TD_RE = re.compile(r"\bTD-\d{3,}\b")
MAX_SECTION = 1600  # chars kept per extracted section
MAX_FALLBACK = 700  # chars kept when no structured section is found


def gh_api(path: str) -> object:
    """Call the GitHub REST API via `gh`, following pagination."""
    out = subprocess.run(
        ["gh", "api", "--paginate", path],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {out.stderr.strip()}")
    txt = out.stdout.strip()
    if not txt:
        return []
    # --paginate concatenates JSON arrays as separate documents; stitch them.
    decoder = json.JSONDecoder()
    items: list = []
    first = None
    idx = 0
    while idx < len(txt):
        while idx < len(txt) and txt[idx] in " \n\r\t":
            idx += 1
        if idx >= len(txt):
            break
        obj, end = decoder.raw_decode(txt, idx)
        idx = end
        if isinstance(obj, list):
            items.extend(obj)
        else:
            first = obj if first is None else first
            items.append(obj)
    if first is not None and not any(isinstance(x, list) for x in [first]):
        # single-object responses: return the first object
        return items[0] if len(items) == 1 else items
    return items


def body_section(body: str, *names: str) -> str:
    """Return the content under a `## <name>` heading from a markdown body."""
    if not body:
        return ""
    lines = body.splitlines()
    wanted = {n.lower() for n in names}
    out: list[str] = []
    capture = False
    for ln in lines:
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*$", ln)
        if m:
            head = re.sub(r"[`*_]", "", m.group(1)).strip().lower()
            capture = any(head.startswith(w) for w in wanted)
            continue
        if capture:
            out.append(ln)
    text = "\n".join(out).strip()
    return text


def extract_review_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """From a review body, return (verdict_line, [(heading, content), ...])."""
    verdict = ""
    mk = re.search(r"KERP-REVIEW:\s*(APPROVED|CHANGES_REQUESTED)[^\n]*", body or "", re.I)
    if mk:
        verdict = mk.group(0).strip()

    sections: list[tuple[str, str]] = []
    if not body:
        return verdict, sections
    lines = body.splitlines()
    cur_head: str | None = None
    cur: list[str] = []

    def flush():
        if cur_head is not None:
            content = "\n".join(cur).strip()
            if content and ACTIONABLE.search(cur_head):
                sections.append((cur_head, content[:MAX_SECTION]))

    for ln in lines:
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*$", ln)
        if m:
            flush()
            cur_head = re.sub(r"[`*_]", "", m.group(1)).strip()
            cur = []
        else:
            cur.append(ln)
    flush()
    return verdict, sections


def harvest_pr(repo: str, pr: dict) -> dict:
    n = pr["number"]
    body = pr.get("body") or ""
    title = pr.get("title") or ""
    task = (TASK_RE.search(title) or TASK_RE.search(body))
    tds = sorted(set(TD_RE.findall(title + "\n" + body)))

    reviews = gh_api(f"repos/{repo}/pulls/{n}/reviews")
    review_blocks = []
    for r in reviews:
        rbody = (r.get("body") or "").strip()
        if not rbody:
            continue
        verdict, sections = extract_review_sections(rbody)
        review_blocks.append(
            {
                "author": r.get("user", {}).get("login", "?"),
                "state": r.get("state", ""),
                "url": r.get("html_url", pr.get("html_url", "")),
                "verdict": verdict,
                "sections": sections,
                "fallback": "" if sections else rbody[:MAX_FALLBACK],
            }
        )

    issue_comments = gh_api(f"repos/{repo}/issues/{n}/comments")
    inline_comments = gh_api(f"repos/{repo}/pulls/{n}/comments")
    comments = []
    for c in list(issue_comments) + list(inline_comments):
        cbody = (c.get("body") or "").strip()
        if not cbody:
            continue
        comments.append(
            {
                "author": c.get("user", {}).get("login", "?"),
                "url": c.get("html_url", ""),
                "path": c.get("path"),
                "body": cbody[:MAX_SECTION],
            }
        )

    return {
        "number": n,
        "title": title,
        "task": task.group(0) if task else None,
        "tds": tds,
        "author": pr.get("user", {}).get("login", "?"),
        "merged_at": pr.get("merged_at") or "",
        "url": pr.get("html_url", ""),
        "what_changed": body_section(body, "what changed"),
        "todos_raised": body_section(body, "todos raised", "todos"),
        "reviews": review_blocks,
        "comments": comments,
    }


def render(repo: str, base: str, prs: list[dict]) -> str:
    out: list[str] = []
    out.append("# PR suggestions & surfaced notes\n")
    out.append(
        "> **Generated — do not hand-edit.** Rebuild with "
        "`python scripts/pr_suggestions.py rebuild`.\n"
        "> A running digest of everything agents surfaced on merged PRs — "
        "review notes, must-fix items, suggestions, nits, follow-ups and ideas — "
        "so nothing is lost after a squash-merge. Formal follow-ups also live as "
        "`TD-xxx` in `tracker/todo/` and on [TODO.md](TODO.md); this file is the "
        "readable skim. Each item links back to its PR or review for full context.\n"
    )
    with_notes = [p for p in prs if p["reviews"] or p["todos_raised"] or p["comments"]]
    out.append(
        f"_Repo `{repo}`, base `{base}` · {len(prs)} merged PRs · "
        f"{len(with_notes)} with surfaced notes._\n"
    )
    out.append("---\n")

    for p in prs:
        has = p["reviews"] or p["todos_raised"] or p["comments"] or p["what_changed"]
        if not has:
            continue
        hdr = f"## PR #{p['number']} — {p['title']}"
        out.append(hdr)
        meta = []
        if p["task"]:
            meta.append(f"task `{p['task']}`")
        if p["merged_at"]:
            meta.append(f"merged {p['merged_at'][:10]}")
        meta.append(f"by `{p['author']}`")
        meta.append(f"[PR]({p['url']})")
        out.append(" · ".join(meta) + "\n")

        if p["todos_raised"]:
            out.append("**TODOs raised**\n")
            out.append(p["todos_raised"] + "\n")

        for rb in p["reviews"]:
            label = rb["verdict"] or rb["state"] or "review"
            out.append(f"**Review by `{rb['author']}` — {label}** · [full review]({rb['url']})\n")
            if rb["sections"]:
                for head, content in rb["sections"]:
                    out.append(f"- _{head}_")
                    for cl in content.splitlines():
                        if cl.strip():
                            out.append(f"  {cl}")
                    out.append("")
            elif rb["fallback"]:
                out.append(rb["fallback"].rstrip() + " …\n")

        for c in p["comments"]:
            where = f" (`{c['path']}`)" if c.get("path") else ""
            out.append(f"**Comment by `{c['author']}`{where}** · [link]({c['url']})\n")
            out.append(c["body"].rstrip() + "\n")

        out.append("---\n")

    return "\n".join(out).rstrip() + "\n"


def build(repo: str, base: str) -> str:
    prs = gh_api(f"repos/{repo}/pulls?state=closed&base={base}&per_page=100")
    merged = [p for p in prs if p.get("merged_at")]
    merged.sort(key=lambda p: (p.get("merged_at") or "", p["number"]), reverse=True)
    harvested = [harvest_pr(repo, p) for p in merged]
    return render(repo, base, harvested)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rb = sub.add_parser("rebuild", help="rebuild docs/pr-suggestions.md from the API")
    rb.add_argument("--repo", default=DEFAULT_REPO)
    rb.add_argument("--base", default=DEFAULT_BASE)
    rb.add_argument("--out", default=DEFAULT_OUT)
    rb.add_argument("--check", action="store_true", help="exit 1 if the file would change")
    args = ap.parse_args()

    if args.cmd == "rebuild":
        content = build(args.repo, args.base)
        out = Path(args.out)
        if args.check:
            current = out.read_text() if out.exists() else ""
            if current != content:
                print(f"{args.out} is stale — run: python scripts/pr_suggestions.py rebuild")
                return 1
            print(f"{args.out} up to date")
            return 0
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content)
        n = content.count("\n## PR #")
        print(f"wrote {args.out} ({n} PR entries)")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
