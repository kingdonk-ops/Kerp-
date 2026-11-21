# KERP — Agent build pipeline

```
            ┌────────────── human: decisions (docs/TODO.md), spec sign-off ───────────────┐
            ▼                                                                             │
  /kerp-plan ── kerp-planner ── spec check + epic → S/M tasks ──► PR → review → dev ──────┘

  /kerp-run  (COORDINATOR — one AI, serial merges)
     │ 0  tracker.py next          → ready tasks (deps done, no open blockers)
     │ 1  spec check               → planner fixes / spec-gap TODO
     │ 2  kerp-implementer         → task branch, TDD, all gates green, own task file + TODOs
     │    coordinator runs fast gates + affected tests (verify, don't trust); full suites run in CI
     │ 3  PR → dev                 → task in_review
     │ 4  kerp-reviewer (independent) → KERP-REVIEW: APPROVED | CHANGES_REQUESTED patch-id=…
     │      └─ changes → implementer fix round → gates → reviewer   (max 3 rounds, then blocked + TODO)
     │ 5  rebase, task done, render boards → ci-gate ✅ + review-gate ✅ → squash-merge to dev
     ▼
   dev ── CI on push ── promote.yml: ≥5 merged PRs since last promotion → fast-forward main + tag
```

## Gates GitHub enforces (not just convention)

| Branch | Rule |
|---|---|
| `dev` | PR required · required checks **`ci-gate`** and **`review-gate`** · no force-push · no deletion |
| `main` | required check **`ci-gate`** (from GitHub Actions) on the pushed commit · no force-push · no deletion. Enforced: only fast-forwards to CI-green commits. Convention (CLAUDE.md): only `promote.yml` does it |

- **`ci-gate`** (`.github/workflows/ci.yml`): tracker validation + boards fresh + pipeline tests, then backend / frontend / e2e jobs, each auto-enabled once that component exists. The backend suite runs twice, in parallel jobs: `backend` (lint, mypy, SQLite suite with `pytest -n auto`, coverage floor) and `backend-pg` (PostgreSQL + RLS as the runtime role, `pytest -n auto` on per-test copies of a migrated template database, durability off on the throwaway server; T-0082); both print the 25 slowest tests (T-0081). `backend-pg` is split into parallel shards (T-0086, see below); `backend-pg-coverage` enforces its coverage floor on all shards combined. The frontend's `perf` job measures the NFR budgets (diff §6.3, T-0045): a breach is a warning annotation + job-summary row until T-0146 makes it a failure; a broken measurement fails it.
- **`backend-pg` shards** (T-0086, TD-512). `backend-pg` is a matrix (`shard: [1, 2, 3, 4]`, `fail-fast: false`); every shard is the former job (same service containers, init script, durability settings, env, pins) on a slice of the test files. `scripts/shard_tests.py files --shard K --of N` picks the slice: the test files under `backend/tests` (pytest's default `test_*.py` / `*_test.py`), each weighted by its measured PostgreSQL worker-seconds in `scripts/shard_weights.json` (JUnit time plus `PER_TEST_OVERHEAD` per test; files not listed weigh the mean), placed heaviest first on the lightest shard, ties by path and shard number. The split is deterministic, every file lands in exactly one shard, and an empty slice fails the shard (pytest without paths would run everything). Each shard runs with `--cov-fail-under=0` and uploads its `.coverage.pg-K` plus a JUnit report (`coverage-pg-K` artifact). `backend-pg-coverage` (after all shards) first checks that `pytest --collect-only` of the whole suite equals the disjoint union of the shards' collections (node ids), then requires the data of every shard, runs `coverage combine` and `coverage report --fail-under=<floor>` with the floor read from `backend/pyproject.toml` addopts (the same 90 the SQLite job enforces), so the floor is exactly as strict as before. `ci-gate` needs both: `needs.backend-pg.result` is the matrix aggregate, so one shard failing, cancelled or skipped fails the gate. `scripts/tests/test_shard_tests.py` proves the partition on the real tree and that the shard count in `ci.yml` matches `SHARDS` in the coverage job. **Rebalance** when the shards drift apart (compare the shards' test-step times): download the `coverage-pg-*` artifacts of a green run and `python scripts/shard_tests.py weights junit-pg-*.xml > scripts/shard_weights.json`. **Change the shard count** by editing the matrix list and `SHARDS` together (the tests fail otherwise).
- **`e2e`** (T-0027): Playwright journeys in Chromium on the real stack, after `backend`, `backend-pg` (all shards), `backend-pg-coverage` and `frontend`. Services: the same PostgreSQL 16 + PostGIS container as `backend-pg` with the same init script (`infra/postgres/initdb/20-kerp-app.sql`: ltree, postgis, the NOSUPERUSER NOBYPASSRLS role `kerp_app`), and the same Redis. It installs the backend (Python 3.12), the frontend and the suite (`npm ci`, Chromium), lints and type-checks the suite, then runs `npx playwright test`, whose config starts the stack: the API (`alembic upgrade head`, then `uvicorn app.main:create_app --factory`, `KERP_ENVIRONMENT=development`, as `kerp_app`) and the frontend (`vite build` + `vite preview`, `/api` proxied), each waited on its URL. `globalSetup` runs first-run (`--demo`, asserted idempotent) and the dev-only seed `e2e/seed/seed.py` for a per-run id; no retries. On failure the HTML report is uploaded (`playwright-report`). Details and the local run: `e2e/README.md`.
- **Docs- and tracker-only PRs skip the app jobs.** On a `pull_request`, `detect` diffs against the merge base with the target branch and runs `scripts/ci_changes.py`. If every changed path is under `tracker/`, `docs/` (spec, plan, decisions, generated boards and digest) or `.claude/`, backend / backend-pg / backend-pg-coverage / backend-image / frontend / perf / e2e are skipped and `ci-gate` treats them as expected skips; the `tracker` job still runs. Planner PRs therefore cost about a minute of CI. No app test reads those trees (`scripts/tests/test_ci_changes.py` checks this; T-0081). Exception: a `tracker/todo/TD-xxx.md` cited by a `known_gap.todo` in `tests/contract/conformance_fixtures.json` counts as code (the CEL conformance suites read it). An empty diff runs everything. Pushes to `dev`/`main` and manual runs always run every job.
- **Frontend-only PRs skip the backend jobs** (T-0083). The same script also prints `backend_needed`: it is false only when every changed path is under `frontend/` or the docs/tracker/`.claude` trees above (and no cited conformance TODO changed). Then `detect` emits `backend=false`, so `backend`, `backend-pg`, `backend-pg-coverage`, `backend-image` and `backend-publish` skip as expected and `ci-gate` still requires the frontend jobs. Any change under `backend/`, `infra/`, `tests/contract/`, `.github/`, `scripts/`, `e2e/` or a root file needs the backend. The reverse never skips: a backend change can change the generated API client, so the frontend jobs run on every PR that has a frontend.
- Required checks are pinned to the GitHub Actions app, so a hand-posted commit status can't fake them.
- **`review-gate`** (`.github/workflows/review-gate.yml` → `scripts/review_gate.py`): the latest `KERP-REVIEW:` marker from an allowed reviewer must be `APPROVED` with a `patch-id` equal to the PR's current one. The patch-id is `git patch-id --stable` of the code diff (all paths except `tracker/**` and the generated `docs/TASKS.md`, `docs/TODO.md`, `docs/pr-suggestions.md`) plus the **normalised** `tracker/**` diff (front-matter `status`/`pr`/`owner`/`updated`, dated note lines `- YYYY-MM-DD…` at column 0 and blank lines dropped) — computed only by `python scripts/review_gate.py --patch-id origin/dev`. Bookkeeping commits (status, PR number, owner, dated notes, boards) don't invalidate it; any code change or tracker content change does (TD-013). The gate script runs from the PR's **base** checkout (a PR's edits to the script don't judge that PR; edits to the workflow file itself still would — TD-223), and only markers posted as PR **reviews** count — issue comments never re-run the gate.
- **Who can open PRs (product owner, 2026-11-15; TD-223).** The repo is user-owned and public. The owner is the sole collaborator and the agents act as the owner login, so the only other principals are outside fork contributors. A PR's own workflow files run on `pull_request` / `pull_request_review`, so a PR could add a workflow that emits a `review-gate` check (residual risk TD-276). Current setting: **Settings → Actions → General → fork pull request workflows from outside collaborators = "Require approval for all external contributors"** (applied by the owner, 2026-11-15; `scripts/setup_github.sh` does not set it yet, TD-277). Unexpected fork PRs are closed without approving their workflows. No human approval is required on `dev` while there is one human (GitHub forbids approving your own PR); when a second collaborator joins, add CODEOWNERS on `.github/` and `scripts/` with required code-owner review. Org-only "required workflows" rulesets are not available to a user-owned repo.
- **Workflow hardening (T-0028, T-0033).** Every `uses:` in `.github/workflows/` is pinned to a full commit SHA with the version tag in a trailing comment (Dependabot `github-actions` keeps them current); the CI service images are pinned by `@sha256:` digest with the tag in a comment (postgis: TD-432). `actions/checkout` sets `persist-credentials: false` in every job that does not push (only `promote.yml` and `pr-suggestions.yml` keep the token). No `${{ }}` expression is interpolated into a `run:` script: values go through `env:`. Workflow permissions are least privilege per job; only `backend-publish` writes packages. `scripts/tests/test_workflows.py` checks all of this; run `actionlint` on workflow changes.
- **Published image = tested image (TD-051).** On a push, `backend-image` saves the image it smoke-tested and hands its id to `backend-publish`, which loads that artifact (no rebuild), pushes it, pulls the pushed digest back and fails unless it resolves to the tested id; the job summary shows both.
- **Weekly lock refresh (TD-047)** (`.github/workflows/lock-refresh.yml`, Mondays + manual): `scripts/refresh_locks.py --upgrade` re-resolves `backend/requirements*.lock` with a pinned `uv`; if a lock changed it force-pushes branch `deps/uv-lock-refresh`, opens one PR to `dev` (or leaves the open one, now updated) and starts CI on the branch with `workflow_dispatch` (events made with `GITHUB_TOKEN` start no workflow run, so the PR would otherwise have no `ci-gate`). `review-gate` runs when the reviewer posts the review. Uses only `GITHUB_TOKEN` (`contents`, `pull-requests`, `actions: write`); needs **Settings → Actions → General → "Allow GitHub Actions to create and approve pull requests"** (TD-409).
- **Promotion** (`.github/workflows/promote.yml`): threshold in repo variable `KERP_PROMOTE_EVERY` (default 5). Manual "Run workflow" promotes now. Tags `release-YYYYMMDD-HHMM-<sha>` with the list of merged PRs.

## Trackers

| | Source (agents edit) | Board (generated) |
|---|---|---|
| Tasks & epics | `tracker/tasks/*.md` | `docs/TASKS.md` |
| TODOs — decision · spec-gap · missed · deferred · later · bug · risk | `tracker/todo/*.md` | `docs/TODO.md` |

One file per item, so parallel agents never conflict; boards are regenerated by
`python scripts/tracker.py render` and CI fails if they're stale. Agents update
the tracker inside their own PR, so the tracker on `dev` is always exactly as
current as the code.

## PR suggestions digest

Everything agents surface on a PR — review notes, must-fix items, non-blocking
suggestions, nits, follow-ups and ideas — is harvested into
[`docs/pr-suggestions.md`](pr-suggestions.md), a generated, human-readable log
(newest PR first, each item linking back to its PR/review). It's the readable
skim of what to act on; formal follow-ups still live as `TD-xxx` in
`tracker/todo/`.

- **Engine:** `python scripts/pr_suggestions.py rebuild` — idempotent, rebuilt
  from the GitHub API (needs `gh`/`GH_TOKEN`). Like the boards, it's generated,
  never hand-edited, and it's excluded from the review-gate patch-id so
  refreshing it never invalidates an approval.
- **dev copy:** the coordinator regenerates it inside each task PR (step 5), so
  `dev`'s copy stays current through the sanctioned PR path (`dev` has no bypass
  actors — nothing pushes to it directly). It lags the current PR's own review
  by one cycle.
- **Always-live copy:** `.github/workflows/pr-suggestions.yml` runs on every
  merge into `dev` (plus nightly and manual) and force-publishes the fully
  up-to-date digest to the unprotected **`pr-suggestions`** branch — read it with
  `git show pr-suggestions:docs/pr-suggestions.md`. This one includes the
  just-merged PR and catches anything merged outside the pipeline.

TODO ids must stay unique across branches that are developed in parallel.
`tracker.py new-todo` runs `git fetch` and then allocates an id above every
`TD-xxx` it can find:
- the local tree,
- every local and `origin/*` branch (`git ls-tree`),
- every open PR (`gh pr list --json files`).

If git, the network or `gh` is unavailable, it warns and falls back to the
remaining sources. `--offline` checks the local tree only, and `--id TD-xxx`
forces a specific id.

On pull requests, the CI `tracker` job runs
`tracker.py check-ids --base origin/<base> --head <PR sha>`. The check fails if
an id that the PR adds (one absent at the merge base) is already used on the
base for a different title. Run `tracker.py check-ids` locally to check your
working tree against `origin/dev`.

## Running it

- In Claude Code (local `claude` in the repo, or a cloud session with the repo attached): `/kerp-run` (default 3 tasks, 2 in parallel) · `/kerp-run T-0005` · `/kerp-plan 3` · `/kerp-plan check`.
- Unattended: a scheduled task that runs `/kerp-run 5` in this repo (e.g. nightly). It skips blocked/decision items and reports them.
- Review identity: agents act as the repo owner, so reviews are posted as `COMMENT` reviews carrying the marker (GitHub forbids self-approval). Add other reviewer logins (e.g. a bot) to repo variable `KERP_ALLOWED_REVIEWERS`.

## Limits worth knowing

- The reviewer is a separate agent with fresh context, but it is the same underlying model as the implementer. It catches a lot; it does not replace a human look at security-sensitive phases (auth, RLS, portal). Promotion to `main` is automatic as requested — consider a human skim of each release tag.
- Merges are serial by design (one coordinator) to keep `dev` green; implementation runs in parallel.
