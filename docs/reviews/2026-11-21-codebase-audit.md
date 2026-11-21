# Codebase audit — 2026-11-21

Whole-repo quality and security audit of `dev` at the end of Phase 0 / start of Phase 1.
It is the source for tasks T-0341–T-0349 (one PR) and for the TODOs raised for the smaller
findings. Line numbers refer to `dev` on 2026-11-21 and may drift.

**Load assumed:** many users per tenant, several API processes and job workers running at
once, and field devices on unreliable networks.

## Must fix

1. **One small rule can freeze the server for minutes** (`backend/app/core/cel/runtime.py:217-239`, `validation/router.py:341-350`).
   CEL validation rules run during "Try it" (in the request) and on every checked status
   change. The only limit is 4096 characters. Each nested `.all()` multiplies the work by the
   list length: four nested 10-item loops take 1 s (a 172-byte rule), six take about 100 s. A
   project admin can send that repeatedly and use up the server threads shared by every
   tenant; a saved rule hangs every status change on the project.
   **Fix:** refuse rules with more than 2–3 nested loop macros (`.all`/`.exists`/`.exists_one`/`.map`/`.filter`)
   in `_check_tree`; add a step counter in the strict evaluator (`core/cel/strict.py`) that
   stops after N steps; use the same limit in the frontend CEL code.

2. **Nothing checks that the database actually enforces tenant isolation** (`app/main.py:65-76`; `core/tenancy/rls.py:552`; `core/audit/guard.py:61`).
   `rls_enforcement_problems`, `rls_coverage_problems` and `ledger_guard_problems` exist but
   only tests call them. With `KERP_DATABASE_URL` pointing at a superuser or a `BYPASSRLS`
   role the app starts, `/health` is OK, and every request can read and write every tenant.
   **Fix:** in `lifespan`, in production, raise if any of the three checks reports a problem;
   same at worker startup (TD-056, TD-059).

3. **The documented two-role database setup cannot work** (`docs/deploy/first-run.md:10-13`; migrations `0015`, `0021_project_lobby.py:98`, `0023`).
   No migration grants table rights to the runtime role; the lobby and access-request
   functions are granted only to `CURRENT_USER` (the owner). Following the docs gives
   "permission denied"; the easy workaround (run the app as the owner) lets the app disable
   RLS and bypass the audit-ledger edit block.
   **Fix:** one migration granting SELECT/INSERT/UPDATE/DELETE on all tables and EXECUTE on
   functions to a configurable runtime role, with `ALTER DEFAULT PRIVILEGES` for future
   objects; no UPDATE/DELETE on `audit_ledger`. A CI job that migrates as the owner and runs
   the tests as `kerp_app`.

4. **The next user on a shared device sees the previous user's data** (`frontend/src/auth/AuthProvider.tsx:63-69`, `lib/api/client.ts:55`, `auth/crossTab.ts:78`).
   Only `logout()` in the current tab clears the React Query cache; a sign-out from another
   tab or a session expiry leaves it full, so user B sees user A's lists (and keeps them on a
   403 reload).
   **Fix:** `queryClient.clear()` whenever the session goes signed-in → signed-out; clear
   everything in `startSession`, not only `me`.

5. **An old tab quietly starts acting as a different user** (`frontend/src/lib/api/client.ts:45-51`, `auth/crossTab.ts:79`).
   All tabs share one refresh cookie. Tab A (user 1) refreshes with user 2's cookie or takes
   user 2's broadcast token; it keeps showing user 1 while every save is made as user 2.
   **Fix:** when a refreshed or broadcast token's `sub` differs from the loaded `me.id`, treat
   it as a sign-out (clear the cache and reload).

6. **Changing the PIN clears the 10-wrong-PIN lock** (`backend/app/core/auth/device_api.py:250-270`).
   `PUT /devices/{id}/pin` works with any live access token and always clears
   `PIN_EXHAUSTED`. The product owner ruled on 2026-11-16 (TD-301) that this needs a fresh
   sign-in within the last 5 minutes; nothing builds or tests it.
   **Fix:** if the device is at the PIN-failure limit, require a session started by password
   or magic link within the last 5 minutes (not renewed by PIN); otherwise 401. Add a test.

7. **A failed or lost refresh response signs the user out completely** (`backend/app/core/auth/service.py:773-793`).
   The refresh token is consumed in Redis before the DB lock and commit; a lock timeout, a
   failed commit or a lost 200 makes the client's retry look like a replay, which kills the
   whole session.
   **Fix:** take the DB lock and the active-user check before `consume_refresh`; keep a
   30-second grace in which a retry gets a plain 401 without ending the session.

8. **A deploy or timeout permanently fails jobs that should retry, and clean uploads get rejected** (`backend/app/modules/jobs/worker.py:114-125`, `jobs/service.py:360-369`).
   On cancel (worker shutdown or timeout) the row is marked `failed`; SAQ retries, but
   `start_job` refuses `failed` rows. A scan during a deploy ends failed and the hourly sweep
   rejects the clean file as `verify_failed` despite 48 allowed attempts.
   **Fix:** in the cancel branch call `_requeue` when `ctx["job"].retryable`, like the
   `RetryLaterError` branch; change the test at `test_jobs.py:393-417`.

9. **Approving and rejecting at the same moment leaves the rule unlocked but marked "rejected"** (`backend/app/modules/validation/service.py:757-808`, `1158-1169`).
   `pending` is checked in Python with no row lock; approve unlocks, a concurrent reject
   overwrites the status; the audit log holds both. Rule-set `version += 1` has the same race
   (two edits can both become "v4"). Overrides already do it right (`overrides.py:490-507`).
   **Fix:** guarded `UPDATE … WHERE status='pending'` with a row-count check; lock the rule
   set before a version bump (as `projects._locked` does).

10. **Clearing the last display-code template leaves old codes everywhere** (`backend/app/modules/asset_management/service.py:278-283`, `427-428`).
    `_refresh_display_codes` returns early when no level has a template, so clearing the last
    one keeps every old code (`101 / 101.1 []` instead of `101.1`). No test covers it.
    **Fix:** move the early return to the move-node path only; add a test that clears the
    template.

11. **A rule accepted as valid can never run, and nobody can override the block** (`backend/app/core/cel/runtime.py:160-206`).
    The save check walks iteratively; the evaluator recurses. `((((…1…))))` 1500 deep passes
    the save check and then always fails with "maximum recursion depth"; `run_checks` treats an
    evaluation error as a block, and overrides cannot answer an error.
    **Fix:** measure nesting depth in `_check_tree`; `PARSE_ERROR` above about 100 levels.

## Should fix

12. **Magic-link requests have no per-client rate limit** (`backend/app/core/auth/router.py:260-286`).
    Only a per-address limit; one client can post a million random addresses (DB
    transactions, Redis keys, unwanted mail). Password reset already has a per-client limit
    (`password_reset.py:161`); SSO start (`sso_router.py:146`) has none either.
    **Fix:** the same per-client lockout before the per-address one, as in
    `PasswordResetService.request`.

13. **Mail that fails to enqueue stays "queued" forever** (`backend/app/core/mail/service.py:117-132`, `jobs/mail.py:85-89`).
    A Redis blip or a crash between commit and enqueue loses the mail; there is no sweep, so
    the send log shows `queued` forever.
    **Fix:** a scheduled job that marks `queued` mail older than the 24-hour payload lifetime
    `failed` (`payload_expired`), reusing `fail_unavailable`.

14. **A job row left "running" after a hard crash is never recovered** (`backend/app/modules/jobs/service.py:296-306`, `633-681`).
    The sweep only looks at `queued` rows; a single-run job killed by OOM stays `running`, and
    `create_single_run_job` keeps returning it.
    **Fix:** `sweep` also picks up `running` rows older than their timeout plus a margin that
    SAQ no longer knows; requeue or fail them.

15. **One file can get two scan jobs at once** (`backend/app/modules/uploads/service.py:221-243`, `310-313`).
    `/complete` checks status without a row lock; a client retry creates two jobs that copy
    and delete the same quarantine object, and one can reject a clean file as
    `missing_object`.
    **Fix:** lock the row before the check (`session.refresh(file, with_for_update=True)`); in
    `verify_upload`, do nothing if `file.job_id` is not the running job.

16. **A slow mail server makes people get the same email several times** (`backend/app/core/mail/transport.py:146`, `jobs/mail.py:77`).
    The SES client keeps default 60 s timeouts plus retries under a 60 s job timeout; the
    thread sends anyway after the job times out, and the retry sends again (up to 6 copies).
    **Fix:** `Config(connect_timeout=5, read_timeout=20, retries={"total_max_attempts": 2})`.

17. **Production starts and reports healthy without Redis or a signing key** (`backend/app/main.py:65-90`, `health.py:36-44`).
    Redis and JWT key checks run only on the first signed-in request.
    **Fix:** call `app_auth_runtime(app)` in `lifespan`; require `redis_url` in production.

18. **Every connection returned to the pool costs about 9 extra round trips** (`backend/app/core/tenancy/rls.py:662-680`).
    BEGIN, 7 separate `RESET`s, COMMIT; at least 3 connections per signed-in request.
    **Fix:** one `SELECT set_config('app.current_tenant_id','',false), …` for all seven, then
    commit (policies already treat an empty value as unset).

19. **A query per item in the rule-drift report and the saved-views list** (`backend/app/modules/validation/upgrades.py:243-274`, `saved_views/service.py:179-235`).
    Drift report: 2 queries per project rule set, no paging (about 6,000 queries at 300
    projects). Saved views: loads every user's private views and filters in Python, then 2
    queries plus a column-catalogue build per team view.
    **Fix:** load defaults once, fetch project rules with one `IN (...)`, add paging; filter
    private views in SQL and compute team ids and columns once per list.

20. **Copied helpers and dead code.**
    SHA-256 hex helper ×4 (`field.py:105`, `password_reset.py:87`, `sso.py:50`,
    `devices.py:71`); session re-binding classes (`_ProjectBinding` in `validation/service.py:1014`
    and `access_requests/service.py:323`, already diverged, plus `_SystemPath` in
    `admin/invites.py:602`); `_aware`/`_utc` ×5; `_unavailable` ×3 (two different 503
    bodies); tenant-scope helpers ×3 (`jobs/service.py:503`, `607`, `uploads/service.py:476`);
    dead context variables `current_tenant_id`/`current_project_id`/`current_user_id`
    (`core/tenancy/context.py:76-95`); `_VALIDATION_KEYS` assigned twice
    (`core/fields/lint.py:30-31`).
    **Fix:** one copy of each helper (session binding in `core/tenancy`); one app-wide handler
    turning `RevocationUnavailableError` into a 503; delete the dead code.

## Smaller findings (raised as TODOs)

- Review/CI tooling: the review gate counts dismissed reviews; `pr_suggestions.py` makes about
  450 API calls per merge and has no test; promotion fails red when CI runs finish out of
  order; a 7-character patch-id is accepted.
- Frontend: the login redirect drops the `?query`; the Dexie outbox is unused.
- Backend correctness: a trailing newline passes the locale check; password-change revocation
  can miss tokens if server clocks drift.
- Backend robustness: some Redis errors return 500 instead of 503; unauthenticated SNS
  requests can trigger slow certificate fetches; the orphan sweep runs a query per file;
  published files can be orphaned; a duplicate team add returns 500.
- Scale: two lists have no paging; a template change rewrites the whole asset tree inside
  the request; the database pool size cannot be configured; job rows are never deleted.

## Verdict

The foundations are solid: RLS, tokens, invites and uploads are carefully built. Fix 1
first (one user can take the server down for every tenant), then 2 and 3 before any real
deploy, and 4–5 before shared field tablets go live.

Not checked by the audit: PostgreSQL-only tests, MinIO, e2e, frontend tests; most test
bodies, schema files, `teams/reconcile.py`, the admin system/stats routers, most of the
frontend, and most migrations; celpy and SAQ internals.
