# ADR 0016 — system_admin global visibility within the tenant

- **Status:** **accepted** (user decision, 2026-11-21; amends diff §4.1 and edge spec §6)
- **Date:** 2026-11-21
- **Affects:** `project_isolation_policy` on every project-scoped table (`app/core/tenancy/rls.py`,
  a new frozen migration after T-0033), the ORM scope (`app/core/tenancy/scope.py`),
  `TenancyContext`, project capabilities (`app/core/auth/access.py`), the project lists
  (`GET /api/v1/projects`, `GET /api/v1/me/projects`), the picker; new tasks T-0274, T-0275,
  T-0276. Resolves TD-396 and simplifies T-0219, T-0221, T-0222, T-0224, T-0233, T-0237, T-0239,
  T-0240, T-0241 and `modules/documents-and-mdr.md` §Access. ADR 0012 (cross-tenant census) is
  unaffected.

## Context

Until now diff §4.1 said System admins "read no project-scoped records without a membership
like any other user". The admin console, upload quarantine, audit explorer and project archive
(diff §4.7, `documents-and-mdr.md`, PR #137) therefore needed membership gates, count-only
`SECURITY DEFINER` functions and an audited self-grant of PER-09 to work around it (TD-396).
On 2026-11-21 the user decided that a `system_admin` sees and administers every project of
the tenant. Changing a core RLS policy needs an ADR (CLAUDE.md §6).

## Decision

1. **Visibility.** A `system_admin` lists and opens every live project of its tenant (top-bar
   picker and the All-Projects scope) and reads and administers project-scoped operational
   records of every project without a membership row. Soft-deleted projects stay 404 on the
   project endpoints, as for members; the admin surfaces that handle them (project archive,
   restore) reach them.
2. **RLS.** `project_isolation_policy` (USING and WITH CHECK) becomes
   `tenant_id = app.current_tenant_id AND (project_id IS NOT DISTINCT FROM
   app.current_project_id OR (project_id IS NOT NULL AND app.is_system_admin = 'true'))`.
   The user's single-policy SQL is mapped onto the real three-policy layout: the
   `allowed_projects_read_policy` (portfolio reads from `app.allowed_project_ids`) and the
   `tenant_level_read_policy` stay as they are; the admin disjunct applies to project rows only,
   so a project-bound session, admin or not, still never writes a tenant-level row. The
   special policies (membership, admin grants, access requests, invites, teams, the tenants
   census) already admit `app.is_system_admin` and do not change. The ORM scope mirrors the
   rule (no project filter and no site filter for a `system_admin` context of the same tenant).
3. **Where the flag comes from.** `app.is_system_admin` is bound only by
   `resolve_request_scope` from the caller's own `system_admin` admin-grant row (T-0014),
   looked up for the verified JWT's `sub` and `tid` (T-0008). It is never read from token
   claims, headers, query parameters, bodies or any other client value. Admin grants are
   tenant rows and every policy also requires `tenant_id = app.current_tenant_id`, so the flag
   is never true across tenants: an admin of tenant A sees nothing of tenant B. An
   impersonation session (ADR 0015) binds it false. *(T-0274 note:)* for **requests** that is
   the only binder; four server-internal contexts also bind it for a narrow read of their own
   tables (jobs scheduler, teams reconcile, onboarding, the invite-token lookup), never from a
   client value; their wider reach since this ADR is TD-462.
4. **Authority unchanged.** `system_admin` alone gives `V`, `C`, `E` and project administration
   (PER-09 equivalent) on every project, **never `A` or `S`**: no hold release, inspection
   approval or cryptographic co-sign. To act as an approver a `system_admin` must hold the
   qualified persona (PER-02, PER-06, PER-07, PER-11 for `A`; PER-15 for `S`) and the required
   credentials on that project. Assigning such a persona to a `system_admin`, or granting
   `system_admin` to a holder of one, returns the separation-of-duties warning and is audited
   with `sod_warning: true` (diff §4.1).
5. **Attribution as today.** Every write by a `system_admin` carries its user id in
   `created_by` / `updated_by` and in the ledger entry (`created_by`, device id per ADR 0010),
   exactly as for any member. Reads are not ledgered, as today for members; the admin console
   endpoints keep their per-call audit (diff §4.7). Whether non-member admin reads need an
   access log is TD-427 (default: no).
6. **Migration.** A new additive Alembic revision drops and re-creates
   `project_isolation_policy` on every table that has it, with the SQL frozen in the revision
   (T-0033 convention, TD-060); downgrade re-creates the previous frozen policy. It depends on
   T-0033 and must not rewrite T-0033's revisions or drift test (T-0033 is in flight).
7. **Contract tests.** The four RLS-bypass contract tests of T-0007 (SAQ workers, cross-module
   service calls, raw-SQL aggregations, migrations) are updated to assert the new rule exactly,
   never weakened: a non-admin is still 404 / zero rows cross-project; a `system_admin` reads
   and writes every project of its tenant without a membership; an admin of tenant A is still
   404 / zero rows on tenant B; an unbound session still sees nothing; a forged claim or header
   cannot set the flag.

## Consequences

- TD-396 is resolved. The count-only `SECURITY DEFINER` functions planned for the quarantine
  monitor and deployment stats are not needed for `system_admin` (normal RLS shows the rows);
  the archive drops its membership gate; the audit explorer reads every chain of the tenant.
- The `system_admin` grant becomes much more powerful: it is still granted only by another
  System admin (the last one cannot be removed), and grant changes stay audited.
- Background jobs enqueued for an admin (archive export, restore) bind `is_system_admin` only
  after re-checking at job start that the requesting user still holds the grant.
- A future multi-tenant deployment keeps isolation through the tenant clause, not through this
  flag.

## Alternatives considered

- **Keep §4.1 (membership required) with count-only functions and self-grant of PER-09**
  (TD-396 default): rejected by the user.
- **A `BYPASSRLS` role or a separate admin connection pool:** rejected; it would also bypass the
  tenant clause and the four contract tests.
