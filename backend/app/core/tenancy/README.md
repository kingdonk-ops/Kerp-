# app/core/tenancy — project-scoped isolation (T-0007)

Ported from Inspection-ERP `core/tenancy/` (ADR 0001) and extended from tenant to project
(diff §4, edge spec §6). Two layers that mirror each other:

| Layer | File | Applies to | Off PostgreSQL |
|---|---|---|---|
| PostgreSQL RLS (`ENABLE` + `FORCE`, policies `project_isolation_policy` + `tenant_level_read_policy` + `allowed_projects_read_policy`; membership-based tables and admin grants: below) | `rls.py` | every statement: ORM, Core, raw SQL | n/a |
| ORM scope (`do_orm_execute` loader criteria + `before_flush` write guard) | `scope.py` | ORM entity selects and flushes | the only filter |

| | Project session (tenant T, project P, allowed L) | Tenant-level session (tenant T, no project, allowed L) |
|---|---|---|
| Reads | rows of P + rows of every project in L + T's tenant-level rows (`project_id IS NULL`) | rows of every project in L + T's tenant-level rows |
| Inserts, updates, deletes | rows of P only | T's tenant-level rows only |
| … with `app.is_system_admin` (ADR 0016) | reads **and writes** rows of every project of T; tenant-level rows read-only as above | reads and writes rows of every project of T; T's tenant-level rows as above |

## System admin global project visibility (ADR 0016, T-0274)

`project_isolation_policy` (`FOR ALL`, the same rule in `USING` and `WITH CHECK`) is

```
tenant_id = app.current_tenant_id
AND (project_id IS NOT DISTINCT FROM app.current_project_id
     OR (project_id IS NOT NULL AND app.is_system_admin))
```

(`rls.PROJECT_ISOLATION_SQL`, the flag read as `rls.SYSTEM_ADMIN_SQL`). A session bound with
`app.is_system_admin` therefore reads, inserts, updates and deletes the **project** rows of every
project of its own tenant without a membership (user decision 2026-11-21, diff §4.1, edge spec
§6). What does not change:

- **Tenants.** The tenant clause is outside the `OR`: an admin of tenant A sees and writes
  nothing of tenant B (404 / zero rows / `WITH CHECK` violation), and the flag with no tenant
  bound matches nothing. An unbound session still sees nothing.
- **Tenant-level rows.** The admin disjunct requires `project_id IS NOT NULL`, so a project-bound
  session, admin or not, still cannot create, change, delete or demote-to a tenant-level row; a
  tenant-level session writes them as before.
- **Other policies.** `tenant_level_read_policy`, `allowed_projects_read_policy` and the special
  policies below (membership, admin grant, access request, invite, team, tenants census) are
  unchanged; their writes still use the exact scope (`rls.SCOPE_ROW_SQL`).
- **Where the flag comes from.** Only `resolve_request_scope` sets it for a request, from the
  caller's own `system_admin` grant row looked up by the verified token's `sub` + `tid`; never
  from token claims, headers, query parameters or bodies
  (`tests/test_tenancy_system_admin.py::test_a_forged_claim_header_query_or_body_never_sets_the_flag`).
  A worker job never inherits it: `run_job` binds the job's bare tenant/project; a job that
  needs it binds it explicitly after re-checking the requester's grant at job start.
- **Authority.** RLS visibility is not capability: `V`/`C`/`E`/administer on every project and
  never `A`/`S` without a qualified persona are T-0275 (`app/core/auth/access.py`).

The ORM scope mirrors it: for an `is_system_admin` context the read filter keeps only the tenant
clause (every project of the tenant plus its tenant-level rows), `get_or_404`/`is_visible` agree,
the flush guard lets it write project rows of any project of the tenant (the old `project_id` of
an updated or deleted row must be writable too, as RLS `USING` requires), except on special-policy tables, whose writes keep the exact scope
(`SCOPE_ROW_SQL`) for everyone, and the Asset-Site
filter is off (`scope.site_filter(context)` is `None`). Migration `0035` replaced the policy on
every table that had it (frozen DDL; its downgrade restores the exact rule). Internal contexts
that bind `is_system_admin=True` for a narrow read (jobs scheduler, teams reconcile, onboarding,
the invite token lookup) now also see every project's records of the tenant; they read only
their own tables (TD-462).

**Visibility is not scope.** A service that picks one row by key from layered data (project,
else tenant/system) must filter on the session's own project explicitly, never rely on "visible =
own project + tenant": an admin (and a portfolio reader) can see other projects' rows. The
validation `resolve_rule_set` does (T-0274 review). Reads by a non-member admin are not
access-logged (TD-427, default no).

## Multi-project read scope (T-0010, TD-053, edge spec §6 amendments)

`TenancyContext.allowed_project_ids` (L) is bound as `app.allowed_project_ids` (comma-separated
UUIDs). `allowed_projects_read_policy` (`FOR SELECT` only) makes rows of those projects
readable — still only of the bound tenant, so another tenant's project id in the list matches
nothing. Writes are unchanged: `project_isolation_policy` still requires the exact
`app.current_project_id`, so a session bound to A with L = [A, B] reads B but an insert into B
fails `WITH CHECK` and an update/delete of B touches no rows. An empty list is unset → `NULL`
→ `ANY(NULL)` matches nothing, so an unbound session still sees nothing.

L comes **only** from the user's memberships (`auth_project_membership`,
`app/core/auth/access.py`), never from request parameters: `get_tenancy_context` reads the
caller's memberships in a short tenant-level unit of work and puts the projects of their
**portfolio** personas (PER-01/10/11/12/13) into L. Other personas read only the bound
project. The only "every project of the tenant" scope is `app.is_system_admin` (ADR 0016, above).

**Asset-Site scope** (`TenancyContext.site_node_ids`, PER-07): models that mix in
`SiteScopedMixin` (`site_node_id`) are filtered to those sites by the ORM read filters,
`get_or_404` and the flush guard. It is ORM-only (no RLS clause yet, TD-150).

`project_isolation_policy` (`FOR ALL`, `USING` and `WITH CHECK`) requires the exact scope:
`tenant_id = app.current_tenant_id AND project_id IS NOT DISTINCT FROM app.current_project_id`
(edge spec §6), or, for project rows only, `app.is_system_admin` (ADR 0016, above).
`tenant_level_read_policy` (`FOR SELECT` only) adds read access to tenant-level
rows. A project session therefore cannot create a tenant-level row, demote a project row to
tenant level (which would leak it to every project), or change or delete a tenant-level row.
An unset setting reads as `NULL` (`NULLIF(..., '')`), so an unbound session sees no rows and
cannot write any.

## Membership-based tables and admin grants (T-0014, TD-053, edge spec §6 amendments)

Some tenant-level tables do not use the three standard policies. The model declares its policy
on the table (`__table_args__ = (..., rls_table_info(RlsPolicy(...)))`); `rls_policy_sql(table,
policy)` emits the DDL, `rls_coverage_problems` expects that policy's names (and reports any
leftover standard policy as unexpected), and the ORM scope (`scope.py`) mirrors it.

| Policy | Tables | Read | Insert | Update / delete |
|---|---|---|---|---|
| `membership` (`membership_{read,insert,update,delete}_policy`) | `oe_projects_project` (project = `id`), `auth_project_membership` (project = `member_project_id`, owner = `user_id`) | the described project is `app.current_project_id`, in `app.allowed_project_ids` or `app.member_project_ids`, or `app.is_system_admin`; or the row's owner is `app.current_user_id` | exact scope | exact scope **and** readable (old and new row) |
| `admin_grant` (`admin_grant_{read,write}_policy`) | `oe_admin_grant` | own rows (`user_id = app.current_user_id`) or `app.is_system_admin` | `app.is_system_admin` + exact scope | `app.is_system_admin` + exact scope |
| `access_request` (`access_request_{read,insert,update}_policy`; T-0050) | `oe_access_requests_request` (project = `requested_project_id`, owner = `requester_id`) | own rows, rows of a project in `app.admin_project_ids` (the caller's PER-09 projects), or `app.is_system_admin` | exact scope **and** own row | exact scope **and** readable (old and new row); no DELETE policy: nobody deletes |
| `project_invite` (`project_invite_{read,insert,update}_policy`; T-0052) | `oe_admin_project_invite` (project = `invited_project_id`) | rows of a project in `app.admin_project_ids` (the caller's PER-09 projects), or `app.is_system_admin`; never the invitee (an invite is accepted by its token through the admin module's narrow system path) | exact scope **and** readable | exact scope **and** readable (old and new row); no DELETE policy: nobody deletes |
| `team` (`team_{read,write}_policy`; T-0013) | `oe_teams_team` (team = `id`), `oe_teams_member`, `oe_teams_project_link` (team = `team_id`) | `app.is_system_admin`, or the row's team is in `kerp_visible_team_ids()`: it has a live link to the bound project, one in `app.allowed_project_ids` or `app.member_project_ids`, or a live membership of `app.current_user_id` (a link row also by its own project, a member row by its own user) | `app.is_system_admin` + exact scope | `app.is_system_admin` + exact scope |

`kerp_visible_team_ids()` (migration `0028`) is an invoker-rights function: a policy on the
link or member table cannot query its own table, so the policies call it instead. It raises
`app.team_lookup` transaction-locally for its own two reads and restores the caller's value;
while the flag is raised the read policies skip the function call (`CASE`), so nothing
recurses. The flag can only hide rows, so a session that sets it sees less, never more. The
ORM scope mirrors the rule with `EXISTS` subqueries on the link and member tables.

So a client-portal user who is a member of project A reads A's project row and memberships but
not B's (404); a plain tenant-level session (no user, no flag) reads no project row at all; a
System admin reads every project row and membership and, through `project_isolation_policy`
(ADR 0016, T-0274), every project-scoped record of its tenant without a membership.

**Membership sources (T-0073, ADR 0008).** `auth_project_membership` rows carry a source:
`direct` (an individual assignment) or `team_link` (derived from a team-project link,
`source_id` = the link id). Access is the union of the live rows, whatever their source, so a
team-derived grant opens the project row and the project's memberships exactly like an
individual one (`app.member_project_ids`, `app.allowed_project_ids` and the site filter are
built from both). The memberships' policy declares `source_column="source_type"`: inserts,
updates and deletes of a row whose source is not `direct` (old or new row) additionally need
`app.is_system_admin`, and the ORM write guard refuses the same on SQLite. The extra read
policy `membership_team_link_census_policy` shows live derived rows of **any** tenant, but only
while `app.team_link_census` is raised, which only the `SECURITY DEFINER` census function
`kerp_live_team_link_grant_count()` of migration `0025` does, for its own count, before
restoring the caller's value (the startup check, `app/core/auth/README.md`).

Settings bound per transaction (all from the server, never from the request):

| Setting | From |
|---|---|
| `app.current_tenant_id` | token `tid` |
| `app.current_project_id` | the bound project (not from the request yet, TD-122) |
| `app.allowed_project_ids` | projects of the caller's portfolio personas (T-0010) |
| `app.member_project_ids` | every project of the caller's memberships, whatever the persona (T-0014); it widens only the `membership` tables |
| `app.admin_project_ids` | the projects where the caller holds PER-09 (T-0050); it widens only the `access_request` and `project_invite` (T-0052) tables |
| `app.current_user_id` | token `sub` (T-0014) |
| `app.is_system_admin` | `true` or unset (widens `project_isolation_policy` to every project row of the tenant, ADR 0016): the caller's own `system_admin` grant, loaded at authentication through the admin module's lookup (`access.register_admin_grant_lookup`; on PostgreSQL the `SECURITY DEFINER` function `kerp_caller_admin_grants()`) in the same unit of work that reads the memberships (T-0014). The only other writer: the narrow lobby read functions of migration `0021` (T-0040) raise it transaction-locally for their own three-column read and restore the caller's value before returning |

The scope resolver runs bound to the caller (`TenancyContext(tid, user_id=sub)`), so the
own-rows clauses let it read the caller's memberships and grants without depending on what it
decides (TD-152). Seeds, scripts and provisioning jobs that need every project row bind
`TenancyContext(tenant_id, is_system_admin=True)` explicitly.

## Tenants directory (T-0079, ADR 0011)

`tenants` (`models.py`, `Tenant`) is the directory every `tenant_id` references: the FK is
declared once on `BaseRecordMixin` (`fk_<table>_tenant_id_tenants`). The row's `tenant_id` is
its own `id` (CHECK `own_tenant`) and it is tenant-level (CHECK `tenant_level`), so the standard
policies show and let a session write only its own tenant's row. The tenant is hidden from users
(diff §4.1): no endpoint exposes it.

`provision.ensure_tenant(db, tenant_id, display_name)` is the one runtime writer: idempotent,
in a tenant-level unit of work bound to that tenant. The API start-up runs it for
`KERP_DEFAULT_TENANT_ID` (`ensure_default_tenant`); `python -m app.core.tenancy.provision` does
the same by hand; T-0023's first-run command (`app/modules/onboarding`) uses it.

**Census (T-0023, ADR 0012).** To refuse a second tenant in a deployment, the first-run command
counts the directory with `provision.tenant_directory_count(db)`: on PostgreSQL the `SECURITY
DEFINER` function `kerp_tenant_directory_count()` of migration `0029`, which returns only the
number of rows (soft-deleted ones included) of every tenant. It raises `app.tenant_census`
transaction-locally for its own read, which the extra policy `tenant_directory_census_policy`
(`FOR SELECT`) honours, and restores the caller's value; `rls_coverage_problems` expects that
policy on `tenants`. `provision.tenant_exists(db, tenant_id)` checks one tenant's own row. Migrations `0026` (table, backfill, FKs
`NOT VALID`) and `0027` (`VALIDATE CONSTRAINT`): provisioning order and upgrade steps in
`docs/deploy/tenants.md`. `tenant_fk_problems(connection)` lists tables whose `tenant_id` lacks
the (validated) FK; `tests/test_tenants.py` runs it at head. Test fixtures create their tenant
rows with `tests/db_support.seed_tenants` (synthetic ids only).

## Binding

- **Requests:** `get_db` binds the session to `get_tenancy_context()` (`context.py`), which
  reads the tenant (`tid`) and user (`sub`) of the verified, unrevoked access token
  (`app/core/auth/bearer.py`, T-0008) and the allowed projects from the user's memberships
  (T-0010). No valid token → `None` (unbound). The write project is not bound from the request
  yet (TD-122). Neither the tenant nor the allowed list ever comes from request parameters.
- **SAQ jobs, scripts:** `with database.unit_of_work(TenancyContext(tenant_id, project_id)) as s:`.
  A job never inherits the enqueuing request's context.
- **Core connections:** `bind_connection(conn, ctx)` inside the transaction.
- `bind_tenancy(session, ctx)` stores the context on `session.info`. An `after_begin` hook
  applies it with `set_config(..., true)` (`SET LOCAL`) at the start of every transaction.
  `install_pool_reset` also clears every setting (`rls.SETTINGS`) when a connection returns to the pool:
  one `SELECT set_config(name, '', false), …` (`rls.POOL_RESET_SQL`) and one commit (T-0348), so
  a return costs three round trips (the driver's `BEGIN`, the statement, `COMMIT`), not nine. The
  policies read the empty value as unset (`NULLIF(..., '')`), as after a `RESET`.
- **One scope per session.** `bind_tenancy` raises if asked to switch a session that holds
  loaded objects or pending changes to a *different* context, because `session.get` would
  hand out identity-map objects of the old scope without SQL. Commit and `expunge_all()`
  first, or use a new session (`unit_of_work`) per tenant/project batch.

## Reads, writes, 404

- Every ORM select hides soft-deleted rows. `execution_options(include_deleted=True)` opts
  back in (TD-033). It never widens the project scope.
- `get_or_404(session, Model, id, include_deleted=False)` raises `OutOfScopeError`. It also
  re-checks identity-map hits against the session's scope and `deleted_at`. A bound session
  that flushes a row outside its exact write scope raises it too. `create_app` maps it to
  `404 {"detail": "Not Found"}`.
- ORM bulk `update()`/`delete()` statements skip the loader criteria and the flush guard.
  RLS still scopes them on PostgreSQL; on SQLite nothing does. Give them an explicit
  tenant/project `WHERE`.

## Migrations

Every table that carries `tenant_id` must be under RLS unless listed in `RLS_EXEMPT` with a
reason. **Migrations never call the live helpers** (TD-060, T-0033): a revision replays exactly
the DDL that shipped, so it inlines the statements `rls_policy_sql(table, policy)` (and, for
`tenants`, `tenant_census_policy_sql`) returned when it was written, in a module-level constant,
and executes that:

```python
# Frozen DDL (TD-060): what rls_policy_sql("oe_asset_node") returned. Never edit it.
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {
    TABLE: (
        "ALTER TABLE oe_asset_node ENABLE ROW LEVEL SECURITY",
        ...  # paste the helper output verbatim, one string per statement
    ),
}

def upgrade() -> None:
    ...
    if op.get_context().dialect.name == "postgresql":
        for statement in FROZEN_RLS_DDL[TABLE]:
            op.execute(statement)
```

Generate the strings by calling the helper with the revision's exact arguments and pasting the
output (or build them from constants local to the revision, as `0015`/`0028` do); never import
`app.core.tenancy.rls` (or `app.core.audit.guard`) from `migrations/versions/`.

**Changing a policy helper** (or a model's `RlsPolicy`) therefore needs a **new** revision that
drops and re-creates the affected policies with the new output frozen in its own
`FROZEN_RLS_DDL`; a shipped revision is never edited. `tests/test_migration_frozen_ddl.py`
enforces it: it replays every revision's `FROZEN_RLS_DDL` in order, per table (`ENABLE`/`FORCE`,
`DROP POLICY IF EXISTS`, `CREATE POLICY`, and the `CREATE OR REPLACE FUNCTION` a policy calls,
compared whitespace-insensitively), and fails naming each table whose result differs from what
the live helpers emit (`apply_rls_policies`), with the newest revision touching it. It also
fails when a revision imports a live helper or a frozen statement is not in the revision's
offline (`--sql`) output.

`tests/test_rls_bypass.py` fails if a migrated model table is not covered
(`rls_coverage_problems`) or the runtime role is SUPERUSER/BYPASSRLS
(`rls_enforcement_problems`). Data migrations see no rows unless they bind a context (TD-055).
Migration `0006` (T-0010) adds `allowed_projects_read_policy` to every table that already has
`project_isolation_policy` (a `DO` loop over the catalog). The frozen DDL of `0002`–`0005` is
the helper output as replayed before T-0033 (it already includes that policy), so a fresh
`alembic upgrade head` is unchanged (TD-154).

## Contract tests

`tests/test_rls_bypass.py` covers the four bypass paths: SAQ worker, cross-module service
call, raw-SQL aggregation, DB migration, each for a non-admin and a `system_admin` session (ADR
0016: every project of its tenant, no tenant-level writes from a project session, never another
tenant; a job never inherits the flag; migration `0035` up and down). It needs PostgreSQL and
runs in CI's PostgreSQL step as the `NOSUPERUSER NOBYPASSRLS` role `kerp_app` (skipped
elsewhere, TD-052). `tests/test_rls_system_admin.py` checks every project table's policy at
head and real audit/job records; `tests/test_tenancy_system_admin.py` the ORM mirror on both
dialects and the forged-flag cases.
`tests/test_rls_multi_project.py` covers the allowed list under RLS the same way, and
`tests/test_rls_admin.py` the membership-based and admin-grant policies (T-0014),
`tests/test_rls_team_grants.py` the derived-row write rule and the census function (T-0073).
`tests/test_tenancy.py` and `tests/test_access.py` run on both dialects.
