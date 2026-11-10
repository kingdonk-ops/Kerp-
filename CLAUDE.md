# CLAUDE.md — KERP agent rules

KERP is the Field Operations Platform described in `docs/spec/` (start with
`docs/spec/README.md`). It is built by agents through a fixed pipeline. These
rules bind every agent and every human.

## 1. Where things are

| What | Where |
|---|---|
| Spec (source of truth) | `docs/spec/` — index in `docs/spec/README.md` |
| Development plan (phases, rules, decisions) | `docs/DEVELOPMENT-PLAN.md` |
| Task tracker — **source** | `tracker/tasks/T-xxxx.md` (tasks), `E-xx-xx.md` (epics) |
| TODO tracker — **source** | `tracker/todo/TD-xxx.md` (decisions, spec gaps, missed, deferred, later, bugs, risks) |
| Boards (generated, never hand-edit) | `docs/TASKS.md`, `docs/TODO.md` |
| Pipeline description | `docs/WORKFLOW.md` |
| Agents / commands | `.claude/agents/kerp-*.md`, `.claude/commands/kerp-*.md` |

## 2. Branches

- `main` — released. **Only the promotion workflow writes to it** (fast-forward
  of a CI-green `dev` commit after every 5 merged PRs). Never open PRs to `main`.
- `dev` — integration. Every change lands by **squash-merged PR** that has passed
  `ci-gate` **and** `review-gate`.
- `task/T-xxxx-<slug>` — one branch per task, cut from `dev`.

## 3. The pipeline (coordinator runs it — see `/kerp-run`)

1. **Spec check** — task has goal, acceptance criteria, spec refs, no open `blocked_by`. If not → planner fixes it or a `spec-gap` TODO is raised.
2. **Develop** — implementer, TDD, in its own worktree/branch.
3. **Test → fix** loop until every quality gate is green locally.
4. **Review** — a *different* agent (reviewer) reviews the PR diff against the task, spec and these rules, and posts `KERP-REVIEW: APPROVED|CHANGES_REQUESTED patch-id=<id>`.
5. Changes requested → implementer fixes → back to 3. **Max 3 review rounds**, then the task is set `blocked` with a `bug`/`decision` TODO and the coordinator moves on.
6. Approved + CI green → coordinator marks the task `done`, re-renders boards, squash-merges to `dev`.
7. Promotion workflow fast-forwards `main` after every 5 merges.

## 4. Quality gates — all green or no commit

Run what exists (CI auto-detects `backend/`, `frontend/`, `e2e/`):

```bash
python scripts/tracker.py validate && python scripts/tracker.py render --check && python -m pytest -q scripts/tests
cd backend  && ruff check . && mypy app && pytest                 # full suite, not only new tests
cd frontend && npm run typecheck && npm run test && npm run build # + npm run gen:api if the API changed (commit it)
cd e2e      && npx playwright test                                # affected suites for user-facing changes
```

No skipped/xfail tests without a linked TODO (`TD-xxx` in the skip reason).
Never weaken a test, lint rule, coverage floor or CI step to get green — that is
an automatic `CHANGES_REQUESTED`.

## 5. Tracker rules

- Implementers update **only their own task file** (status, notes, ticked acceptance criteria).
- Anything found but not done — missed requirement, deferred edge case, follow-up, bug in another module, unanswered question — becomes a TODO **in the same PR**:
  `python scripts/tracker.py new-todo --kind missed|deferred|later|bug|spec-gap|decision|risk --title "..." --task T-xxxx --source implementer`
- TODO ids are global across parallel branches. `new-todo` fetches and picks an id above every `TD-xxx` on any local/`origin/*` branch and open PR. Never hand-pick an id or copy "local max + 1". If it prints a `WARNING` (offline, no `gh`), push the branch soon so later allocations see it. CI's `check-ids` fails a PR whose new TODO id is used on `dev` for a different item; fix it by re-raising with `new-todo` and updating references.
- Status flow: `todo → in_progress → in_review → done` (or `blocked`). `done` needs a PR number.
- Every PR that touches `tracker/` runs `python scripts/tracker.py render` and commits the boards.
- Epics (`needs-breakdown`) are never implemented directly; the planner splits them into S/M tasks.
- Sizes: **S** ≈ one focused PR, < ~300 changed lines; **M** ≈ one PR, < ~800 lines. Bigger → split.

## 6. Engineering hard rules (from the spec)

- Modular monolith: `backend/app/modules/<name>/` with `manifest.py`, `models.py`, `router.py`, `service.py`, `schemas.py`, `README.md`. **No cross-module table access** — call the other module's `service.py`.
- Every table uses `BaseRecordMixin` and gets an RLS policy. Tenant comes from the JWT only; cross-tenant → 404.
- The anchor model is law (ADR 0003): work anchors to a **work-anchor node** — at most one per root-to-leaf path, never nested (RSW by default); inspections to any `inspection_anchor` level or an Item. No BOQ anchor anywhere.
- Systems of record are referenced by `external_source` + `external_id` (+ `synced_at`), never FKs. Money is a read-only mirror.
- Verification is configuration: every verifiable transition calls `run_checks(context, rule_set)`.
- AI-initiated mutations carry `source="ai"` and need human confirmation before applying.
- Heavy work (PDF, OCR, CAD, P6 parse, rollups) runs in the SAQ worker (Redis; interactive + batch queues); heavy libs are imported lazily.
- Additive migrations by default; destructive migrations and core-table changes need an ADR in `docs/decisions/`.
- Every user-visible string through i18n `t()`. Never hand-edit `frontend/src/api/` (generated).
- No secrets in the repo. No real client data in fixtures — synthetic only.

## 7. Commit / PR conventions

- Conventional commits: `feat(asset_management): T-0101 recursive hierarchy`.
- PR title: `T-xxxx: <task title>`; body uses `.github/pull_request_template.md`.
- PRs target `dev` only.
