# app/core/tenancy — project-scoped isolation (T-0007)

Ported from Inspection-ERP `core/tenancy/` (ADR 0001) and extended from tenant to project
(diff §4, edge spec §6). Two layers that mirror each other:

| Layer | File | Applies to | Off PostgreSQL |
|---|---|---|---|
| PostgreSQL RLS (`ENABLE` + `FORCE`, policies `project_isolation_policy` + `tenant_level_read_policy` + `allowed_projects_read_policy`) | `rls.py` | every statement: ORM, Core, raw SQL | n/a |
| ORM scope (`do_orm_execute` loader criteria + `before_flush` write guard) | `scope.py` | ORM entity selects and flushes | the only filter |

| | Project session (tenant T, project P, allowed L) | Tenant-level session (tenant T, no project, allowed L) |
|---|---|---|
| Reads | rows of P + rows of every project in L + T's tenant-level rows (`project_id IS NULL`) | rows of every project in L + T's tenant-level rows |
| Inserts, updates, deletes | rows of P only | T's tenant-level rows only |

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
project. There is no implicit "every project of the tenant" scope.

**Asset-Site scope** (`TenancyContext.site_node_ids`, PER-07): models that mix in
`SiteScopedMixin` (`site_node_id`) are filtered to those sites by the ORM read filters,
`get_or_404` and the flush guard. It is ORM-only (no RLS clause yet, TD-150).

`project_isolation_policy` (`FOR ALL`, `USING` and `WITH CHECK`) requires the exact scope:
`tenant_id = app.current_tenant_id AND project_id IS NOT DISTINCT FROM app.current_project_id`
(edge spec §6). `tenant_level_read_policy` (`FOR SELECT` only) adds read access to tenant-level
rows. A project session therefore cannot create a tenant-level row, demote a project row to
tenant level (which would leak it to every project), or change or delete a tenant-level row.
An unset setting reads as `NULL` (`NULLIF(..., '')`), so an unbound session sees no rows and
cannot write any. The stricter membership-based policy on tenant-level tables that describe a
project (TD-053) is T-0014's.

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
  `install_pool_reset` also clears the three settings when a connection returns to the pool.
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
reason. In the migration that creates the table:

```python
from app.core.tenancy.rls import rls_policy_sql

for statement in rls_policy_sql("oe_asset_node"):
    op.execute(statement)
```

`tests/test_rls_bypass.py` fails if a migrated model table is not covered
(`rls_coverage_problems`) or the runtime role is SUPERUSER/BYPASSRLS
(`rls_enforcement_problems`). Data migrations see no rows unless they bind a context (TD-055).
Migration `0006` (T-0010) inlines its RLS DDL so it stays frozen at that revision; it adds
`allowed_projects_read_policy` to every table that already has `project_isolation_policy`
(0002–0005 still import the live helper: TD-154).

## Contract tests

`tests/test_rls_bypass.py` covers the four bypass paths: SAQ worker, cross-module service
call, raw-SQL aggregation, DB migration. It needs PostgreSQL and runs in CI's PostgreSQL step
as the `NOSUPERUSER NOBYPASSRLS` role `kerp_app` (skipped elsewhere, TD-052).
`tests/test_rls_multi_project.py` covers the allowed list under RLS the same way.
`tests/test_tenancy.py` and `tests/test_access.py` run on both dialects.
