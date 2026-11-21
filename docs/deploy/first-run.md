# Deploy: first run of a new deployment

A deployment serves exactly one company: **one hidden tenant** (diff §4.1). Users never see,
pick or type it; the API takes it from `KERP_DEFAULT_TENANT_ID`. First-run provisioning
(T-0023) creates that tenant, its deployment settings (diff §4.4) and the **first System
admin**. Projects come later, from Project Admins (`create_projects`) in the app.

## Database roles (ADR 0019)

Two roles, never one (ADR 0001, ADR 0019):

| Role | Setting | Owns | Does |
|---|---|---|---|
| owner, e.g. `kerp_owner` | `KERP_MIGRATION_DATABASE_URL` | schema `public` and every table, sequence and function | runs `alembic upgrade`; never serves requests |
| runtime, `kerp_app` by default | `KERP_DATABASE_URL`, named in `KERP_RUNTIME_DB_ROLE` | nothing | the API, the worker and the first-run command |

Both are `NOSUPERUSER NOBYPASSRLS`. Create them once, as the database administrator (e.g. the
RDS master user), with passwords from the secret store:

```sql
CREATE ROLE kerp_owner LOGIN PASSWORD '<secret>' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE kerp_app LOGIN PASSWORD '<secret>' NOSUPERUSER NOBYPASSRLS;
GRANT CONNECT, TEMPORARY ON DATABASE kerp TO kerp_owner, kerp_app;
-- connected to the kerp database:
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS postgis;
ALTER SCHEMA public OWNER TO kerp_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
```

The runtime role needs no further `GRANT`: migration `0039` grants it `SELECT, INSERT, UPDATE,
DELETE` on every table (`audit_ledger`: `SELECT, INSERT` only), `USAGE` on the schema and the
sequences, and `EXECUTE` on the app's functions (lobby, access requests, censuses), and sets
default privileges so later migrations' objects get the same. It reads the role name from
`KERP_RUNTIME_DB_ROLE` **in the migration run**; with another name, set it there. The migration
logs `runtime role grants skipped: …` when the setting is empty, the role does not exist yet
(create it first, then downgrade to `0038` and upgrade again) or it is the migrating role
itself (local dev, where one role does both). Running the app as the owner is never needed.
CI proves this layout in the job `backend-pg-roles` (`infra/postgres/owner-runtime-split.sql`).

Pre-flight, after migrating: `python -m app.core.startup_safety` with the runtime
`KERP_DATABASE_URL` prints `database role checks passed`, or each problem (exit code `1`): the
same checks the API and the worker run at startup (below).

## Steps

1. **Migrate** as the schema owner: `alembic upgrade head` (`KERP_MIGRATION_DATABASE_URL`;
   `KERP_RUNTIME_DB_ROLE` if the runtime role is not `kerp_app`).
   If you already chose the tenant id, set `KERP_DEFAULT_TENANT_ID` here too
   ([tenants.md](tenants.md)).
2. **First run**, as the runtime role (`KERP_DATABASE_URL`), e.g. as a one-off task with the
   backend image:

   ```bash
   KERP_ENVIRONMENT=production KERP_DATABASE_URL=… \
   KERP_INTERNAL_EMAIL_DOMAINS=company.example \
     python -m app.modules.onboarding.first_run --admin-email it.admin@company.example
   ```

   The e-mail may also come from `KERP_FIRST_ADMIN_EMAIL`. Output:

   ```text
   KERP_DEFAULT_TENANT_ID=1b0c…
   tenant: created
   deployment settings: created
   system admin account: created (it.admin@company.example, internal)
   system_admin grant: granted
   ```

3. **Configure** `KERP_DEFAULT_TENANT_ID` with the printed value for the API and the worker
   (the API refuses to start in production without it), then start them. Login needs no tenant
   input (T-0037).
4. **First sign-in.** No password is set by the command, and none is ever passed to it. The
   System admin signs in with Microsoft (Entra ID SSO, `backend/app/core/auth/README.md`):
   SSO links the account the command created by its e-mail (it never creates accounts). A
   one-time password link for deployments without SSO
   comes with the self-service password reset (T-0077, TD-360).
5. The System admin appoints Project Admins and further System admins in the app.

## Startup refusals (production)

With `KERP_ENVIRONMENT=production` (the default) the API and the SAQ worker refuse to start,
instead of failing later on the first request or silently running unprotected (T-0342):

| Log / error text | Cause | Fix |
|---|---|---|
| `KERP_DEFAULT_TENANT_ID must be set in production` | no deployment tenant | step 3 |
| `KERP_MAIL_TRANSPORT=ses …` | no outbound e-mail | mail settings (`backend/app/core/mail/README.md`) |
| `KERP_REDIS_URL must be set in production` | no Redis (token revocation, job queue, settings cache) | set `KERP_REDIS_URL` (API only; the worker always needs it) |
| `KERP_JWT_PRIVATE_KEY or KERP_JWT_PRIVATE_KEY_FILE must be set in production`, or another `AuthConfigError` | no or unusable JWT signing key (API) | set the key from the secret store |
| `refusing to start: the database role in KERP_DATABASE_URL is unsafe (N problem(s)): …` | the role checks below | connect as the runtime role, not the owner or a superuser |

The database refusal names every problem, and each is also logged on its own line as
`unsafe database role: <problem>` (logger `kerp.startup`):

- `database role '<role>' is a SUPERUSER (bypasses RLS)` / `… has BYPASSRLS`: row-level
  security is skipped for this role. Use a `NOSUPERUSER NOBYPASSRLS` role.
- `<table>: row level security not enabled` / `not forced` / `policy … missing` /
  `unexpected policy …` / `table not found`: the schema is not at `alembic upgrade head`, or
  someone changed the policies (`ALTER TABLE … NO FORCE ROW LEVEL SECURITY` lets the table's
  owner skip them). Migrate; never run the app as the table owner.
- `audit_ledger: current role has UPDATE` (`DELETE`, `TRUNCATE`) or `trigger … missing or
  disabled`: the role could rewrite the audit ledger. The runtime role gets `SELECT, INSERT`
  on it only.

Outside production the same problems are logged as warnings
(`unsafe database role (refused in production): …`) and the process starts; on SQLite the
checks are skipped (`database role checks skipped: sqlite has no row-level security`). `/health`
stays a liveness check and reports none of this: a process that refused never serves it.

## Safety

- **Idempotent.** Re-running with the same input prints `present` for every step and writes
  nothing. A run interrupted halfway is completed by the next one.
- **One tenant per deployment.** If the database already holds any other tenant (also a
  soft-deleted one) the command refuses (`refused: …`, exit code `1`) and writes nothing. It
  counts the directory through the census function of migration `0029` (ADR 0012). If a tenant
  exists but neither `--tenant-id` nor `KERP_DEFAULT_TENANT_ID` names it, it refuses too.
- **First admin only.** If another active account already holds `system_admin`, a run with a
  different e-mail is refused: further admins are appointed in the app.
- **No default project in production.** `--demo` (also `create_projects` and a synthetic
  project `DEMO` with the admin as PER-09) is refused with `KERP_ENVIRONMENT=production`
  (exit code `2`).

Details: `backend/app/modules/onboarding/README.md`.
