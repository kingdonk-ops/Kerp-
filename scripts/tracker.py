#!/usr/bin/env python3
"""KERP task & TODO tracker.

Source of truth: one Markdown file per item, with a flat front-matter block.

    tracker/tasks/T-0101.md   (tasks and epics)
    tracker/todo/TD-001.md    (missed / deferred / later / decision / bug)

`docs/TASKS.md` and `docs/TODO.md` are *generated* boards (`render`). CI fails if
they are stale (`render --check`), so every PR that touches a tracker file must
re-render. One file per item means parallel agents never conflict on the source;
the only conflicts possible are on the generated boards, which are resolved by
re-running `render`.

Stdlib only (CI runs it before any dependency install).

Commands
    validate                      schema + dependency checks
    render [--check]              regenerate docs/TASKS.md + docs/TODO.md
    next [--n N] [--json]         ready tasks (status todo, all deps done), in plan order
    show ID                       print an item
    set ID [--status S] [--pr N] [--owner X] [--note TEXT]
    new-task --id ID --title T --phase P --module M --size S|M [--depends a,b] [--spec path] [--epic E]
    new-todo --title T --kind K [--task ID] [--source TEXT] [--body TEXT] [--id TD-xxx] [--offline]
                                  id is above every TD-xxx on any local/origin branch and open PR
    check-ids [--base REF] [--head REF]
                                  fail if a TODO id added on head is used on base for a different title
    stats
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_DIR = ROOT / "tracker" / "tasks"
TODO_DIR = ROOT / "tracker" / "todo"
TASKS_BOARD = ROOT / "docs" / "TASKS.md"
TODO_BOARD = ROOT / "docs" / "TODO.md"

TASK_STATUSES = ("todo", "in_progress", "in_review", "done", "blocked", "needs-breakdown", "dropped")
TASK_SIZES = ("S", "M", "L")  # L only allowed for epics (needs-breakdown)
TODO_KINDS = ("decision", "spec-gap", "missed", "deferred", "later", "bug", "risk")
TODO_STATUSES = ("open", "resolved", "wontfix")
TASK_REQUIRED = ("id", "title", "phase", "module", "size", "status")
TODO_REQUIRED = ("id", "title", "kind", "status")

TASK_ID_RE = re.compile(r"^(T-\d{4}|E-\d{2}[a-z]?-\d{2})$")
TODO_ID_RE = re.compile(r"^TD-\d{3,4}$")
STATUS_ICON = {
    "todo": "⬜", "in_progress": "🟦", "in_review": "🟪", "done": "✅",
    "blocked": "🟥", "needs-breakdown": "🧩", "dropped": "⚫",
    "open": "⬜", "resolved": "✅", "wontfix": "⚫",
}
PHASE_TITLES = {
    "0": "Platform foundations", "1": "Asset & Quality backbone", "2": "Welding quality",
    "3": "Logistics", "4": "Execution", "5": "Documents, collaboration & AI",
    "6": "Defense, claims & controls", "6b": "Work packaging (AWP)", "7": "Client portal",
}


# ----------------------------------------------------------------- parsing
@dataclass
class Item:
    path: Path
    meta: dict[str, object]
    body: str = ""

    @property
    def id(self) -> str:
        return str(self.meta.get("id", ""))

    def get(self, key: str, default: object = "") -> object:
        return self.meta.get(key, default)

    def deps(self) -> list[str]:
        v = self.meta.get("depends_on", [])
        return [str(x) for x in v] if isinstance(v, list) else []

    def refs(self, key: str) -> list[str]:
        v = self.meta.get(key, [])
        return [str(x) for x in v] if isinstance(v, list) else []


def _parse_value(raw: str) -> object:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [p.strip().strip("'\"") for p in inner.split(",") if p.strip()] if inner else []
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    return raw


def parse_text(text: str, path: Path) -> Item:
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing front matter")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError(f"{path}: unterminated front matter")
    meta: dict[str, object] = {}
    for n, line in enumerate(text[4:end].splitlines(), start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"{path}:{n}: expected 'key: value'")
        k, v = line.split(":", 1)
        meta[k.strip()] = _parse_value(v)
    body = text[end + 4 :].lstrip("\n")
    return Item(path=path, meta=meta, body=body)


def _fmt_value(v: object) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"
    s = str(v)
    return f'"{s}"' if (":" in s or s.startswith(("[", "#", "'", '"'))) else s


def dump(item: Item) -> str:
    head = "\n".join(["---"] + [f"{k}: {_fmt_value(v)}" for k, v in item.meta.items()] + ["---", ""])
    body = item.body if (not item.body or item.body.endswith("\n")) else item.body + "\n"
    return head + "\n" + body


def load_dir(d: Path) -> list[Item]:
    if not d.exists():
        return []
    return [parse_text(p.read_text(encoding="utf-8"), p) for p in sorted(d.glob("*.md"))]


def _phase_key(phase: object) -> tuple[int, str]:
    m = re.match(r"(\d+)([a-z]?)", str(phase))
    return (int(m.group(1)), m.group(2)) if m else (99, str(phase))


def _order(t: Item) -> int:
    try:
        return int(str(t.get("order", 0) or 0))
    except ValueError:
        return 0


def plan_order(tasks: list[Item]) -> list[Item]:
    return sorted(tasks, key=lambda t: (_phase_key(t.get("phase")), _order(t), t.id))


# --------------------------------------------------------------- validation
def validate(tasks: list[Item], todos: list[Item]) -> list[str]:
    errs: list[str] = []
    ids: dict[str, Path] = {}
    for t in tasks:
        name = t.path.name
        for k in TASK_REQUIRED:
            if not t.get(k):
                errs.append(f"{name}: missing '{k}'")
        if t.id and not TASK_ID_RE.match(t.id):
            errs.append(f"{name}: bad id '{t.id}'")
        if t.id and t.path.stem != t.id:
            errs.append(f"{name}: file name must equal id '{t.id}'")
        if t.id in ids:
            errs.append(f"{name}: duplicate id '{t.id}' (also {ids[t.id].name})")
        ids[t.id] = t.path
        st, size = t.get("status"), t.get("size")
        if st not in TASK_STATUSES:
            errs.append(f"{name}: bad status '{st}' (allowed: {', '.join(TASK_STATUSES)})")
        if size not in TASK_SIZES:
            errs.append(f"{name}: bad size '{size}'")
        if size == "L" and st not in ("needs-breakdown", "dropped", "done"):
            errs.append(f"{name}: size L is only for epics awaiting breakdown — split it into S/M tasks")
        if st in ("in_review", "done") and t.id.startswith("T-") and not t.get("pr"):
            errs.append(f"{name}: status '{st}' requires a pr number")
    for t in tasks:
        for d in t.deps():
            if d not in ids:
                errs.append(f"{t.path.name}: depends_on unknown id '{d}'")
            elif d == t.id:
                errs.append(f"{t.path.name}: depends on itself")
        ep = t.get("epic")
        if ep and ep not in ids:
            errs.append(f"{t.path.name}: unknown epic '{ep}'")
    errs += _cycle_errors(tasks)
    todo_ids: set[str] = set()
    for td in todos:
        name = td.path.name
        for k in TODO_REQUIRED:
            if not td.get(k):
                errs.append(f"{name}: missing '{k}'")
        if td.id and not TODO_ID_RE.match(td.id):
            errs.append(f"{name}: bad id '{td.id}'")
        if td.id and td.path.stem != td.id:
            errs.append(f"{name}: file name must equal id '{td.id}'")
        if td.id in todo_ids:
            errs.append(f"{name}: duplicate id")
        todo_ids.add(td.id)
        if td.get("kind") not in TODO_KINDS:
            errs.append(f"{name}: bad kind '{td.get('kind')}'")
        if td.get("status") not in TODO_STATUSES:
            errs.append(f"{name}: bad status '{td.get('status')}'")
        for ref in td.refs("tasks"):
            if ref not in ids:
                errs.append(f"{name}: references unknown task '{ref}'")
    for t in tasks:
        for ref in t.refs("blocked_by"):
            if ref not in todo_ids:
                errs.append(f"{t.path.name}: blocked_by unknown todo '{ref}'")
    return errs


def _cycle_errors(tasks: list[Item]) -> list[str]:
    graph = {t.id: t.deps() for t in tasks}
    state: dict[str, int] = {}
    errs: list[str] = []

    def visit(n: str, stack: list[str]) -> None:
        state[n] = 1
        for d in graph.get(n, []):
            if state.get(d) == 1:
                errs.append("dependency cycle: " + " -> ".join(stack + [n, d]))
            elif state.get(d) is None and d in graph:
                visit(d, stack + [n])
        state[n] = 2

    for n in graph:
        if n not in state:
            visit(n, [])
    return errs


# ------------------------------------------------------------------ queries
def open_blockers(t: Item, todos_by_id: dict[str, Item]) -> list[str]:
    return [r for r in t.refs("blocked_by") if r in todos_by_id and todos_by_id[r].get("status") == "open"]


def ready(tasks: list[Item], todos: list[Item]) -> list[Item]:
    by_id = {t.id: t for t in tasks}
    td = {x.id: x for x in todos}
    out = []
    for t in plan_order(tasks):
        if t.get("status") != "todo" or not t.id.startswith("T-"):
            continue
        deps_done = all(by_id[d].get("status") == "done" for d in t.deps() if d in by_id)
        if deps_done and not open_blockers(t, td):
            out.append(t)
    return out


# ---------------------------------------------------------------- rendering
def _link(item: Item) -> str:
    try:
        rel = (Path("..") / item.path.relative_to(ROOT)).as_posix()
    except ValueError:  # item outside the repo (tests)
        rel = item.path.name
    return f"[{item.id}]({rel})"


def _cell(s: object) -> str:
    return str(s).replace("|", "\\|")


def render_tasks(tasks: list[Item], todos: list[Item]) -> str:
    td = {x.id: x for x in todos}
    real = [t for t in tasks if t.id.startswith("T-")]
    done = sum(1 for t in real if t.get("status") == "done")
    pct = round(100 * done / len(real)) if real else 0
    rdy = {t.id for t in ready(tasks, todos)}
    epics = sum(1 for t in tasks if t.get("status") == "needs-breakdown")
    lines = [
        "# KERP — Task board",
        "",
        "> **Generated** by `python scripts/tracker.py render` from `tracker/tasks/*.md` — do not edit by hand.",
        "> ⬜ todo · ▶ ready to start · 🟦 in progress · 🟪 in review · ✅ done · 🟥 blocked · 🧩 epic awaiting breakdown · ⚫ dropped",
        "",
        f"**Overall:** {done}/{len(real)} tasks done ({pct}%) · **ready now:** {len(rdy)} · **epics awaiting breakdown:** {epics}",
        "",
    ]
    phases: dict[str, list[Item]] = {}
    for t in plan_order(tasks):
        phases.setdefault(str(t.get("phase")), []).append(t)
    lines += ["| Phase | Tasks done | Progress |", "|---|---|---|"]
    for ph, items in phases.items():
        r = [t for t in items if t.id.startswith("T-")]
        d = sum(1 for t in r if t.get("status") == "done")
        p = round(100 * d / len(r)) if r else 0
        bar = "█" * (p // 10) + "░" * (10 - p // 10)
        e = sum(1 for t in items if t.get("status") == "needs-breakdown")
        extra = f" (+{e} epics)" if e else ""
        lines.append(f"| {ph} · {PHASE_TITLES.get(ph, '')} | {d}/{len(r)}{extra} | `{bar}` {p}% |")
    lines.append("")
    for ph, items in phases.items():
        lines += [
            f"## Phase {ph} — {PHASE_TITLES.get(ph, '')}",
            "",
            "| | ID | Title | Module | Size | Depends on | PR |",
            "|---|---|---|---|---|---|---|",
        ]
        for t in items:
            st = str(t.get("status"))
            blk = open_blockers(t, td)
            icon = STATUS_ICON.get(st, "?")
            if t.id in rdy:
                icon = "▶"
            elif st == "todo" and blk:
                icon = "🟥"
            deps = ", ".join(t.deps()) or "—"
            pr = f"#{t.get('pr')}" if t.get("pr") else ""
            title = _cell(t.get("title")) + (f" *(blocked by {', '.join(blk)})*" if blk else "")
            lines.append(f"| {icon} | {_link(t)} | {title} | {t.get('module')} | {t.get('size')} | {deps} | {pr} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_todos(todos: list[Item]) -> str:
    lines = [
        "# KERP — TODO: decisions · spec gaps · missed · deferred · later",
        "",
        "> **Generated** by `python scripts/tracker.py render` from `tracker/todo/*.md` — do not edit by hand.",
        '> Add: `python scripts/tracker.py new-todo --kind <kind> --title "..." --task T-xxxx --source <agent>`',
        "",
    ]
    open_ = [t for t in todos if t.get("status") == "open"]
    closed = [t for t in todos if t.get("status") != "open"]
    lines += [f"**Open:** {len(open_)} · **Closed:** {len(closed)}", ""]
    for kind in TODO_KINDS:
        items = [t for t in open_ if t.get("kind") == kind]
        if not items:
            continue
        lines += [f"## {kind} ({len(items)})", "", "| ID | Title | Related tasks | Raised by |", "|---|---|---|---|"]
        for t in items:
            rel = ", ".join(t.refs("tasks")) or "—"
            lines.append(f"| {_link(t)} | {_cell(t.get('title'))} | {rel} | {t.get('source', '')} |")
        lines.append("")
    if closed:
        lines += ["## Closed", "", "| | ID | Title | Resolution |", "|---|---|---|---|"]
        for t in closed:
            icon = STATUS_ICON.get(str(t.get("status")), "")
            lines.append(f"| {icon} | {_link(t)} | {_cell(t.get('title'))} | {_cell(t.get('resolution', ''))} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------- CLI
# ---------------------------------------------- remote-aware TODO allocation
# Parallel agents branch from `dev` and each raise TODOs in their own PR, so
# "local max + 1" makes them all pick the same ids. `allocate_todo_id` takes the
# max over every TD-xxx visible anywhere: the local tree, every local and
# origin/* branch (after a fetch), and the files of every open PR (via gh).
Runner = Callable[[list[str]], str]
_GIT_ERRORS = (OSError, subprocess.SubprocessError)
TODO_FILE_RE = re.compile(r"^(?:tracker/todo/)?(TD-\d{3,4})\.md$")


def _run_cmd(cmd: list[str]) -> str:
    """Run a command in the repo root; raise OSError/SubprocessError on any failure."""
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          timeout=60, check=True).stdout


def todo_ids_in_paths(paths: Iterable[str]) -> set[str]:
    """TD ids named by paths such as `tracker/todo/TD-032.md`; other paths are ignored."""
    out: set[str] = set()
    for raw in paths:
        m = TODO_FILE_RE.match(raw.strip().replace("\\", "/"))
        if m:
            out.add(m.group(1))
    return out


def remote_todo_ids(run: Runner = _run_cmd, fetch: bool = True) -> tuple[set[str], list[str]]:
    """TD ids used on any local or origin/* branch and in any open PR. Returns (ids, warnings)."""
    ids: set[str] = set()
    warns: list[str] = []
    if fetch:
        try:
            run(["git", "fetch", "--quiet", "--prune", "origin"])
        except _GIT_ERRORS as e:
            warns.append(f"git fetch failed ({type(e).__name__}); origin/* refs may be stale")
    try:
        refs = run(["git", "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin"]).split()
    except _GIT_ERRORS as e:
        warns.append(f"cannot list git refs ({type(e).__name__}); branches not checked")
        refs = []
    for ref in refs:
        if ref.endswith("/HEAD"):
            continue
        try:
            ids |= todo_ids_in_paths(run(["git", "ls-tree", "--name-only", ref, "tracker/todo/"]).splitlines())
        except _GIT_ERRORS:
            warns.append(f"cannot list tracker/todo/ on {ref}")
    try:
        out = run(["gh", "pr", "list", "--state", "open", "--limit", "200", "--json", "number,files"])
        for pr in json.loads(out or "[]"):
            ids |= todo_ids_in_paths(str(f.get("path", "")) for f in pr.get("files") or [])
    except (*_GIT_ERRORS, ValueError, AttributeError, TypeError) as e:
        warns.append(f"gh pr list unavailable ({type(e).__name__}); open PRs not checked")
    return ids, warns


def allocate_todo_id(local: Iterable[Item], run: Runner = _run_cmd, fetch: bool = True,
                     offline: bool = False) -> tuple[str, list[str]]:
    """Next TD id above every id used locally, on any branch and in any open PR. Returns (id, warnings)."""
    used = {t.id for t in local if TODO_ID_RE.match(t.id)}
    if offline:
        warns = ["--offline: only the local tree was checked; the id may collide with other branches"]
    else:
        remote, warns = remote_todo_ids(run, fetch)
        used |= remote
    nums = [int(i.split("-")[1]) for i in used]
    return f"TD-{(max(nums) + 1) if nums else 1:03d}", warns


# ------------------------------------------------- merge collision detection
def _titles_at(ref: str, ids: Iterable[str], run: Runner) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in ids:
        path = f"tracker/todo/{i}.md"
        out[i] = str(parse_text(run(["git", "show", f"{ref}:{path}"]), Path(path)).get("title"))
    return out


def todo_id_collisions(head: dict[str, str], base: dict[str, str], ancestor: set[str]) -> list[str]:
    """Ids added on head (absent at the merge base) that base also uses for a different title."""
    errs = []
    for i in sorted((set(head) & set(base)) - ancestor):
        if head[i] != base[i]:
            errs.append(f"{i}: id collision: this branch uses it for {head[i]!r}, base uses it for "
                        f"{base[i]!r}. Re-raise this branch's TODO with `tracker.py new-todo` "
                        f"(fresh id) and update every reference to it.")
    return errs


def check_id_collisions(base: str, head: str | None, local: list[Item], run: Runner = _run_cmd) -> list[str]:
    """Compare head (a git ref, or the working tree when None) with base for TODO id collisions."""
    def ls(ref: str) -> set[str]:
        return todo_ids_in_paths(run(["git", "ls-tree", "--name-only", ref, "tracker/todo/"]).splitlines())

    ancestor = ls(run(["git", "merge-base", base, head or "HEAD"]).strip())
    base_ids = ls(base)
    head_ids = {t.id for t in local} if head is None else ls(head)
    candidates = (head_ids & base_ids) - ancestor
    if head is None:
        head_titles = {t.id: str(t.get("title")) for t in local if t.id in candidates}
    else:
        head_titles = _titles_at(head, candidates, run)
    return todo_id_collisions(head_titles, _titles_at(base, candidates, run), ancestor)


ISO_DATE_PREFIX_RE = re.compile(r"\d{4}-\d{2}-\d{2}\b")


def note_line(note: str, today: str) -> str:
    """Markdown note line for ``set --note``; keeps a note's own leading ISO date instead of doubling it."""
    note = note.strip()
    return f"- {note}" if ISO_DATE_PREFIX_RE.match(note) else f"- {today}: {note}"


def _report(errs: list[str]) -> None:
    for e in errs:
        print("ERROR", e)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    r = sub.add_parser("render")
    r.add_argument("--check", action="store_true")
    n = sub.add_parser("next")
    n.add_argument("--n", type=int, default=5)
    n.add_argument("--json", action="store_true")
    s = sub.add_parser("show")
    s.add_argument("id")
    st = sub.add_parser("set")
    st.add_argument("id")
    for a in ("--status", "--pr", "--owner", "--note", "--resolution"):
        st.add_argument(a)
    nt = sub.add_parser("new-task")
    for a in ("--id", "--title", "--phase", "--module", "--size"):
        nt.add_argument(a, required=True)
    for a in ("--depends", "--spec", "--epic", "--body"):
        nt.add_argument(a, default="")
    nt.add_argument("--order", default="0")
    nd = sub.add_parser("new-todo")
    nd.add_argument("--title", required=True)
    nd.add_argument("--kind", required=True, choices=TODO_KINDS)
    for a in ("--task", "--body"):
        nd.add_argument(a, default="")
    nd.add_argument("--source", default="agent")
    nd.add_argument("--id", default="", help="explicit TD-xxx id (skips allocation; must be unused locally)")
    nd.add_argument("--offline", action="store_true", help="skip git/gh lookups; check the local tree only")
    ci = sub.add_parser("check-ids")
    ci.add_argument("--base", default="origin/dev")
    ci.add_argument("--head", default=None, help="git ref to check (default: the working tree)")
    sub.add_parser("stats")
    args = ap.parse_args(argv)

    tasks, todos = load_dir(TASK_DIR), load_dir(TODO_DIR)

    if args.cmd == "validate":
        errs = validate(tasks, todos)
        _report(errs)
        print(f"{len(tasks)} tasks, {len(todos)} todos — {'OK' if not errs else f'{len(errs)} error(s)'}")
        return 1 if errs else 0

    if args.cmd == "render":
        errs = validate(tasks, todos)
        if errs:
            _report(errs)
            return 1
        outputs = {TASKS_BOARD: render_tasks(tasks, todos), TODO_BOARD: render_todos(todos)}
        stale = [p for p, c in outputs.items() if not p.exists() or p.read_text(encoding="utf-8") != c]
        if args.check:
            for p in stale:
                print(f"STALE {p.relative_to(ROOT)} — run: python scripts/tracker.py render")
            if not stale:
                print("boards up to date")
            return 1 if stale else 0
        for p, c in outputs.items():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(c, encoding="utf-8")
        print("rendered", ", ".join(str(p.relative_to(ROOT)) for p in outputs))
        return 0

    if args.cmd == "next":
        items = ready(tasks, todos)[: args.n]
        if args.json:
            print(json.dumps([{"id": t.id, "title": t.get("title"), "module": t.get("module"), "size": t.get("size"),
                               "path": str(t.path.relative_to(ROOT))} for t in items], indent=2))
        else:
            for t in items:
                print(f"{t.id}\t{t.get('size')}\t{t.get('module')}\t{t.get('title')}")
            if not items:
                print("(no ready tasks)")
        return 0

    if args.cmd == "show":
        for it in tasks + todos:
            if it.id == args.id:
                print(it.path.read_text(encoding="utf-8"))
                return 0
        print("not found", file=sys.stderr)
        return 1

    if args.cmd == "set":
        for it in tasks + todos:
            if it.id != args.id:
                continue
            if args.status:
                it.meta["status"] = args.status
            if args.pr:
                it.meta["pr"] = args.pr.lstrip("#")
            if args.owner:
                it.meta["owner"] = args.owner
            if args.resolution:
                it.meta["resolution"] = args.resolution
            it.meta["updated"] = _dt.date.today().isoformat()
            if args.note:
                it.body = it.body.rstrip("\n") + f"\n\n{note_line(args.note, _dt.date.today().isoformat())}\n"
            it.path.write_text(dump(it), encoding="utf-8")
            errs = validate(load_dir(TASK_DIR), load_dir(TODO_DIR))
            if errs:
                _report(errs)
                return 1
            print(f"{it.id} updated")
            return 0
        print("not found", file=sys.stderr)
        return 1

    if args.cmd == "new-task":
        path = TASK_DIR / f"{args.id}.md"
        if path.exists():
            print("exists", file=sys.stderr)
            return 1
        meta: dict[str, object] = {
            "id": args.id, "title": args.title, "phase": args.phase, "module": args.module,
            "size": args.size, "status": "todo", "order": args.order,
            "depends_on": [d for d in args.depends.split(",") if d],
        }
        if args.epic:
            meta["epic"] = args.epic
        if args.spec:
            meta["spec"] = [x for x in args.spec.split(",") if x]
        body = args.body or "## Goal\n\n## Acceptance criteria\n- [ ] \n\n## Notes\n"
        TASK_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(dump(Item(path, meta, body)), encoding="utf-8")
        errs = validate(load_dir(TASK_DIR), todos)
        if errs:
            _report(errs)
            return 1
        print(path.relative_to(ROOT))
        return 0

    if args.cmd == "new-todo":
        if args.id:
            tid = args.id
            if not TODO_ID_RE.match(tid) or any(t.id == tid for t in todos):
                print(f"--id {tid}: must match TD-xxx and be unused locally", file=sys.stderr)
                return 1
        else:
            tid, warns = allocate_todo_id(todos, offline=args.offline)
            for w in warns:
                print("WARNING", w, file=sys.stderr)
        meta = {
            "id": tid, "title": args.title, "kind": args.kind, "status": "open",
            "tasks": [x for x in args.task.split(",") if x], "source": args.source,
            "raised": _dt.date.today().isoformat(),
        }
        TODO_DIR.mkdir(parents=True, exist_ok=True)
        p = TODO_DIR / f"{tid}.md"
        p.write_text(dump(Item(p, meta, (args.body or "").strip() + "\n")), encoding="utf-8")
        print(tid)
        return 0

    if args.cmd == "check-ids":
        try:
            errs = check_id_collisions(args.base, args.head, todos)
        except (*_GIT_ERRORS, ValueError) as e:
            print(f"ERROR check-ids: cannot read git refs ({e})", file=sys.stderr)
            return 1
        _report(errs)
        print(f"TODO ids vs {args.base}: {'OK' if not errs else f'{len(errs)} collision(s)'}")
        return 1 if errs else 0

    if args.cmd == "stats":
        from collections import Counter

        c = Counter(str(t.get("status")) for t in tasks)
        print(json.dumps({"tasks": dict(c), "ready": len(ready(tasks, todos)),
                          "todos_open": sum(1 for t in todos if t.get("status") == "open")}, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
