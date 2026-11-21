# ADR 0019 — Owner and runtime database roles

- **Status:** accepted (2026-11-21; records the security-model change of every table that audit
  item 3 of `docs/reviews/2026-11-21-codebase-audit.md` requires; resolves TD-056 and TD-059)
- **Date:** 2026-11-21
- **Affects:** T-0343 (this change: migration `0039_runtime_role_grants.py`, CI job
  `backend-pg-roles`, `docs/deploy/first-run.md`), T-0342 (startup refusals), every table and
  every function the migrations create; ADR 0001 (migrations as the schema owner, the app as a
  `NOSUPERUSER NOBYPASSRLS` role); diff §4 (security, isolation and identity).

## Context

ADR 0001 says migrations run as the schema owner and the app connects as a separate
`NOSUPERUSER NOBYPASSRLS` role. Settings have had both URLs since T-0005
(`KERP_MIGRATION_DATABASE_URL`, `KERP_DATABASE_URL`). But no migration ever granted the
runtime role anything: tables were usable only by their owner, and the `SECURITY DEFINER`
lobby, access-request, team-grant and census functions (`0015`, `0021`, `0023`, `0025`,
`0029`) revoke `EXECUTE` from `PUBLIC` and grant it to `CURRENT_USER` (the owner). Following
the deploy guide gave "permission denied"; the easy workaround, running the app as the owner,
lets the app turn off `FORCE ROW LEVEL SECURITY` and re-grant itself `UPDATE` / `DELETE` on
`audit_ledger` (disabling its triggers too). CI's PostgreSQL jobs hid this: there `kerp_app`
runs the migrations and owns the tables by design (TD-056), which `FORCE` covers for RLS.

## Decision

1. **Two roles.** The *owner* (`KERP_MIGRATION_DATABASE_URL`) owns schema `public` and every
   object the migrations create, and runs `alembic upgrade`. The *runtime role*
   (`KERP_DATABASE_URL`, named by `KERP_RUNTIME_DB_ROLE`, default `kerp_app`) is what the API,
   the worker and the first-run command connect as: `NOSUPERUSER NOBYPASSRLS`, owns nothing,
   cannot create objects in the schema, never holds `GRANT OPTION`. Only the owner may grant.
2. **Migration `0039` grants the runtime role** (only privileges the owner itself holds, on
   objects it owns in the current schema, extension members excluded):
   `USAGE` on the schema; `SELECT, INSERT, UPDATE, DELETE` on every table and view;
   `USAGE, SELECT` on every sequence; `EXECUTE` on every function. Never `TRUNCATE`,
   `REFERENCES`, `TRIGGER`. `alembic_version` gets nothing.
3. **`audit_ledger` is append-only for the runtime role:** `SELECT, INSERT` only (TD-059). The
   owner lacks `UPDATE, DELETE, TRUNCATE` since `0002` revoked them, and `0039` names the table
   explicitly as well, so a superuser-run migration cannot grant them either.
4. **Future objects:** `0039` sets `ALTER DEFAULT PRIVILEGES FOR ROLE <owner> IN SCHEMA public`
   for tables (`SELECT, INSERT, UPDATE, DELETE`), sequences (`USAGE, SELECT`) and functions
   (`EXECUTE`). Consequences for later revisions:
   - a new table or function needs no grant of its own;
   - a new **append-only** table must `REVOKE UPDATE, DELETE, TRUNCATE ... FROM <runtime role>`
     in its own revision (and from `PUBLIC`, `CURRENT_USER`, as `0002` does);
   - a function the runtime role must **not** call must `REVOKE EXECUTE ... FROM` it;
   - revisions must keep running as the same owner role, because default privileges belong to
     the role that set them.
5. **Skips.** The grants are skipped, with a log line, when `KERP_RUNTIME_DB_ROLE` is empty,
   the role does not exist, or it *is* the migrating role (local dev and CI's PostgreSQL shards
   migrate as `kerp_app`; granting to oneself changes nothing and revoking from oneself on
   downgrade would remove the owner's own rights). SQLite: no-op. The checks run inside the
   `DO` blocks, so offline `--sql` output skips the same way when applied.
6. **Grants only, additive.** No table, column, policy or function changes. The downgrade
   revokes exactly what the upgrade granted (and the default privileges); `0025` and `0029`
   keep the `EXECUTE` they give `kerp_app` themselves.
7. **Enforced at startup** (T-0342): a production API or worker refuses to start when the
   runtime role is `SUPERUSER` / `BYPASSRLS`, an RLS table is not enabled and forced with its
   expected policies, or the role may change `audit_ledger`.
8. **Proved in CI:** the job `backend-pg-roles` creates `kerp_owner` (owner) and `kerp_app`
   (runtime, owns nothing; `infra/postgres/owner-runtime-split.sql`), migrates a copy as
   `kerp_owner` and asserts the three startup checks report nothing for `kerp_app`, which owns
   nothing, has no `CREATE` and cannot change the ledger; then it migrates every test template
   as `kerp_owner` and runs the full PostgreSQL suite as `kerp_app`. Only for that suite, and
   after the check, `kerp_app` gets `CREATE` on the test source's schema: some tests build
   scratch tables or migrate an empty database themselves; it gives no right on the owner's
   tables. The existing `backend-pg` shards keep the runtime-owns-the-schema layout, so both
   layouts stay green.

## Consequences

- The documented two-role setup works; running the app as the owner is no longer needed and is
  refused in production when it would be unsafe.
- Every later revision inherits the grants. The append-only and no-execute exceptions above are
  the author's job; reviewers check them.
- A deployment that changes the runtime role's name sets `KERP_RUNTIME_DB_ROLE` for the
  migration run and re-runs `0039` (downgrade to `0038`, upgrade) or grants by hand the same way.
- An ownership refusal at startup (a runtime role owning any table) is left for later (TD-535):
  local dev and CI's shards still migrate as the runtime role.
