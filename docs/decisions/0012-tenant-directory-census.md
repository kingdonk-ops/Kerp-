# ADR 0012 — A count-only census of the tenants directory for first-run provisioning

- **Status:** accepted (implementer, T-0023; within the access model of diff §4.1, TD-120 3a)
- **Date:** 2026-11-18
- **Affects:** T-0023 (this change), the `tenants` core table (migration `0029`: one read
  policy, one function), `app/core/tenancy` (`rls.py`, `provision.py`), `app/modules/onboarding`

## Context

T-0023's first-run command must refuse to create a second tenant in a deployment that already
has one (diff §4.1: one hidden tenant per deployment). It runs as the runtime role, which
`FORCE ROW LEVEL SECURITY` limits to its own tenant's directory row (ADR 0011): it cannot see
whether **another** tenant exists. The schema owner is subject to `FORCE` too, and neither role
may bypass RLS (`rls_enforcement_problems`). Adding a policy to a core table needs an ADR
(CLAUDE.md §6).

## Decision

Migration `0029` adds, on PostgreSQL:

1. `tenant_directory_census_policy` on `tenants`: `FOR SELECT USING (app.tenant_census)`.
   Every row, of any tenant, but only while that setting is raised. No application code
   raises it.
2. `kerp_tenant_directory_count()`: `SECURITY DEFINER`, pinned `search_path`, returns
   `bigint`, the number of directory rows (soft-deleted ones included). It raises
   `app.tenant_census` transaction-locally for its own `count(*)` and restores the caller's
   value before it returns, the same pattern as `kerp_live_team_link_grant_count()` of `0025`
   (ADR 0008). `EXECUTE` is revoked from `PUBLIC` and granted to the migration role and
   `kerp_app`.

`rls.expected_policy_names` expects the census policy on `tenants` (on top of the three
standard policies), so `rls_coverage_problems` fails a schema without it.
`provision.tenant_directory_count(database)` calls the function (SQLite: a plain count, no RLS
there). The first-run command refuses when any row other than its own tenant's exists. On
PostgreSQL it also holds a session advisory lock, so two runs at once cannot both find an empty
directory.

## Consequences

- The runtime role can learn **how many** tenants exist, never their ids or names. In a
  one-tenant deployment that number is 0 or 1.
- Like every RLS setting, `app.tenant_census` is trusted to the application: a role that runs
  arbitrary SQL could raise it itself and read the directory, as it could raise
  `app.is_system_admin`. The tenancy model already assumes the runtime role runs only KERP's
  queries.
- Additive. The downgrade drops the function and the policy.

## Alternatives considered

- **A unique index allowing one directory row.** Enforces the rule in the database, but the
  test suite (and the RLS isolation tests) need several tenants in one database. Rejected.
- **Trust `KERP_DEFAULT_TENANT_ID` only.** Refuses a different id when one is configured, but
  a fresh run without the variable would silently add a second tenant beside one created
  earlier. Rejected.
- **Run the check as a `BYPASSRLS` role.** No such role exists in a deployment, by design.
