# app/core/tenancy — project-scoped isolation (T-0007)

Ported from Inspection-ERP `core/tenancy/` (ADR 0001) and extended from tenant to project
(diff §4, edge spec §6). Two layers that mirror each other:

| Layer | File | Applies to | Off PostgreSQL |
|---|---|---|---|
| PostgreSQL RLS (`ENABLE` + `FORCE`, policies `project_isolation_policy` + `tenant_level_read_policy`) | `rls.py` | every statement: ORM, Core, raw SQL | n/a |
| ORM scope (`do_orm_execute` loader criteria + `before_flush` write guard) | `scope.py` | ORM entity selects and flushes | the only filter |

| | Project session (tenant T, project P) | Tenant-level session (tenant T, no project) |
|---|---|---|
| Reads | rows of P + T's tenant-level rows (`project_id IS NULL`) | T's tenant-level rows |
| Inserts, updates, deletes | rows of P only | T's tenant-level rows only |

`project_isolation_policy` (`FOR ALL`, `USING` and `WITH CHECK`) requires the exact scope:
`tenant_id = app.current_tenant_id AND project_id IS NOT DISTINCT FROM app.current_project_id`
(edge spec §6). `tenant_level_read_policy` (`FOR SELECT` only) adds read access to tenant-level
rows. A project session therefore cannot create a tenant-level row, demote a project row to
tenant level (which would leak it to every project), or change or delete a tenant-level row.
An unset setting reads as `NULL` (`NULLIF(..., '')`), so an unbound session sees no rows and
cannot write any. Who may read tenant-level rows (e.g. the project row) is open in TD-053.

## Binding

- **Requests:** `get_db` binds the session to `get_tenancy_context()` (`context.py`). That
  dependency is the seam auth (T-0008) fills from the verified JWT. Today it returns `None`.
  The tenant never comes from request parameters.
- **SAQ jobs, scripts:** `with database.unit_of_work(TenancyContext(tenant_id, project_id)) as s:`.
  A job never inherits the enqueuing request's context.
- **Core connections:** `bind_connection(conn, ctx)` inside the transaction.
- `bind_tenancy(session, ctx)` stores the context on `session.info`. An `after_begin` hook
  applies it with `set_config(..., true)` (`SET LOCAL`) at the start of every transaction.
  `install_pool_reset` also clears both settings when a connection returns to the pool.
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

## Contract tests

`tests/test_rls_bypass.py` covers the four bypass paths: SAQ worker, cross-module service
call, raw-SQL aggregation, DB migration. It needs PostgreSQL and runs in CI's PostgreSQL step
as the `NOSUPERUSER NOBYPASSRLS` role `kerp_app` (skipped elsewhere, TD-052).
`tests/test_tenancy.py` runs on both dialects.
