# e2e — Playwright journeys on the real stack (T-0027)

End-to-end journeys in Chromium against the whole stack: PostgreSQL 16 (+ PostGIS, ltree)
with row-level security as the runtime role `kerp_app` (NOSUPERUSER NOBYPASSRLS), Redis, the
API (`uvicorn`, `KERP_ENVIRONMENT=development`) and the **built** frontend (`vite build` +
`vite preview`, `/api` proxied to the API, so the app talks to its own origin). CI's `e2e` job
runs it on every PR that has an `e2e/package.json` (`.github/workflows/ci.yml`).

| File | What |
|---|---|
| `playwright.config.ts` | Chromium only, `baseURL` = `KERP_E2E_BASE_URL`, no retries, `trace: retain-on-failure`, HTML report in `playwright-report/` (CI uploads it on failure); `webServer` starts the API and the frontend, each waited on its URL (`/health`, `/`), reused locally if already running, never in CI |
| `global-setup.ts` | Prepares the database idempotently (below) and hands the run's state to the tests |
| `seed/seed.py` | Dev-only seed (below); tested by `backend/tests/test_e2e_seed.py` |
| `vite.preview.config.mjs` | `vite preview` of `frontend/dist` with the API proxy |
| `support/` | `env.ts` (every setting, one place), `i18n.ts` (`t()` over the app's catalogue), `api.ts` (API calls in a browser user's session), `state.ts` |
| `tests/phase0-exit.spec.ts` | The Phase 0 exit journey (DEVELOPMENT-PLAN.md): tenant, sign-in, project created, persona assigned, module toggle, scoped, rule set run |

## Run it locally

```bash
make up                                     # PostgreSQL + Redis (docker-compose.yml)
pip install -e "./backend[dev]"             # the API's Python environment
(cd frontend && npm ci)                     # the suite builds the frontend itself
cd e2e && npm ci && npx playwright install chromium && npx playwright test
```

`npx playwright test --ui` or `--headed` to watch; `npx playwright show-report` after a run.
`npm run lint` and `npm run typecheck` check the suite itself (CI runs both).

### Environment

Every value has a dev-only default (`support/env.ts`) matching `make up` and CI:

| Variable | Default | What |
|---|---|---|
| `KERP_E2E_DATABASE_URL` | `postgresql+psycopg://kerp_app:kerp_app@localhost:5432/kerp_test` | The database, **as the runtime role** (`globalSetup` refuses a superuser or BYPASSRLS role) |
| `KERP_E2E_REDIS_URL` | `redis://localhost:6379/0` | Redis |
| `KERP_E2E_TENANT_ID` | `00000000-0000-4000-8000-0000000e2e27` | The deployment's one tenant (`KERP_DEFAULT_TENANT_ID` of the API, first-run and seed) |
| `KERP_E2E_API_URL` | `http://127.0.0.1:8027` | Where the API listens (not the dev port, so a running dev server is never hit) |
| `KERP_E2E_BASE_URL` | `http://127.0.0.1:4127` | Where the frontend is previewed; also the API's `KERP_WEB_ORIGIN` |
| `KERP_E2E_ADMIN_PASSWORD`, `KERP_E2E_QA_PASSWORD` | throwaway dev-only values | Passwords the seed sets for the synthetic accounts |
| `KERP_E2E_PYTHON` | `python` | Interpreter with the backend installed |
| `KERP_E2E_RUN_ID` | generated | This run's id (`[a-z0-9]`, at most 24) |
| `KERP_E2E_CHROMIUM_PATH` | unset | A Chromium binary to use instead of Playwright's download (images with a browser pre-installed) |

The tenant is fixed per database: a database first-run provisioned with another tenant
refuses this one (one tenant per deployment). Use a database of its own for the suite
(`KERP_E2E_DATABASE_URL=…/kerp_e2e`, created like `infra/postgres/initdb/20-kerp-app.sql`
creates `kerp_test`), or reset.

### Reset

The suite never needs a reset: each run makes its own project and QA user, so runs pile up
side by side. To start from an empty database: `make reset && make up` (drops the compose
volumes, so every other local PostgreSQL test database too).

## What `globalSetup` does

Playwright starts the `webServer`s before `globalSetup`; the API command migrates first
(`alembic upgrade head`) because its start-up reads the schema. Then `global-setup.ts`, all as
the runtime role:

1. checks the connection role is NOSUPERUSER NOBYPASSRLS;
2. `alembic upgrade head` (a no-op by then; the setup is complete on its own);
3. `python -m app.modules.onboarding.first_run --demo --admin-email e2e-admin@example.test`
   (with a synthetic display name), checks it printed `KERP_DEFAULT_TENANT_ID=<tenant>`, runs it
   again and checks everything is `present` (idempotent);
4. `python e2e/seed/seed.py --run <run> --admin-email e2e-admin@example.test`;
5. passes the run id and both outputs to the tests in the environment (`support/state.ts`).

## Seed contract (`seed/seed.py`)

- **Development only:** exit `2` unless `KERP_ENVIRONMENT=development` (checked before the
  app's settings are loaded). Exit `1` when first-run has not created the admin; `2` for a bad
  `--run`, a missing password variable or a password the policy refuses.
- Writes only through the owning modules' services (identity `app/core/auth/service.py`,
  `admin`, `validation`, `projects`), in one tenant-level unit of work bound as a System admin.
- Sets the first-run System admin's password (first-run creates it without one, by design).
- Creates this run's QA user `e2e-qa-<run>@example.test` (`Quinn Example <run>`), a member of
  nothing, and sets its password. One per run, so "sees exactly the new project" holds on every
  re-run against the same database.
- Creates the tenant-layer `check` rule set `e2e_smoke` (no module, no rules) once; every
  project created later gets a project copy (ADR 0004 §2).
- Prints one JSON object: `tenant_id`, `admin_user_id`, `qa_user_id`, `qa_email`, `qa_name`,
  `demo_project_id`, `rule_set_key`.

Synthetic data only (CLAUDE.md §6): `@example.test` addresses, invented names, throwaway
passwords from the environment.

## Adding a journey

- One `tests/<journey>.spec.ts`, one serial `describe` per journey when its steps build on
  each other (share one `BrowserContext`, as `phase0-exit.spec.ts`).
- Find controls by role and accessible name, with the text from the app's catalogue:
  `page.getByRole("button", { name: t("admin.projects.new") })`, never a typed English string;
  a `data-testid` where there is no role or name.
- Wait on what the user sees or on a response (`expect(...)`, `page.waitForResponse`); never a
  fixed sleep (`waitForTimeout` fails lint).
- Data a journey needs that no screen creates yet goes into the seed (through a service), keyed
  by the run id so re-runs never collide; never into the production module tree.
- Every step asserts what the user sees or what the API answers. An API check in a browser
  user's session uses the token from that user's sign-in (`captureLogin`).
- A step that cannot run yet is `test.skip(condition, "TD-xxx: …")` naming its open TODO.
