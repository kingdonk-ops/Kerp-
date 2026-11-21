"""Tenant directory census for first-run provisioning (T-0023, ADR 0012).

Core-table change covered by ADR 0012 (CLAUDE.md §6): one extra read policy on ``tenants``
and one function. PostgreSQL only; SQLite has no RLS and the provisioning code counts the
table directly there.

* ``tenant_directory_census_policy`` (``FOR SELECT``): every directory row, of any tenant,
  but only while ``app.tenant_census`` is raised. Nothing else raises it.
* ``kerp_tenant_directory_count()`` (``SECURITY DEFINER``, pinned ``search_path``) returns
  only the number of directory rows (soft-deleted ones included), never an id or a name. The
  first-run command calls it before it creates the deployment's tenant, to refuse a second
  tenant in a deployment (diff §4.1). In CI and local dev the definer is the app role, which
  ``FORCE ROW LEVEL SECURITY`` subjects to the policies (TD-056), so the function raises
  ``app.tenant_census`` transaction-locally for its own count and restores the caller's value
  before it returns (as ``0025`` does for its census). ``EXECUTE`` is revoked from ``PUBLIC``
  and granted to ``CURRENT_USER`` and, where it exists, to ``kerp_app``.

Additive. The downgrade drops the function and the policy.

Follows ``0028`` (T-0013 teams), which landed on ``dev`` first; the history stays linear.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "tenants"
FUNCTION = "kerp_tenant_directory_count"
POLICY = "tenant_directory_census_policy"
SETTING = "app.tenant_census"
RUNTIME_ROLE = "kerp_app"

# Frozen here (not the live ``rls`` helper), so this revision's DDL never changes later.
_CENSUS = f"COALESCE(NULLIF(current_setting('{SETTING}', true), '')::boolean, false)"
_POLICY_SQL = (
    f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}",
    f"CREATE POLICY {POLICY} ON {TABLE} FOR SELECT USING ({_CENSUS})",
)

# The RLS DDL this revision applies, per table (TD-060, T-0033): frozen here, replayed by
# tests/test_migration_frozen_ddl.py against the live helpers.
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {TABLE: _POLICY_SQL}

# Only a count, never row data, across every tenant. The census setting is raised for this
# read only and the caller's value restored.
_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE
    prior text := current_setting('{SETTING}', true);
    found bigint;
BEGIN
    PERFORM set_config('{SETTING}', 'true', true);
    SELECT count(*) INTO found FROM {TABLE};
    PERFORM set_config('{SETTING}', COALESCE(prior, ''), true);
    RETURN found;
END
$fn$
"""
_FUNCTION_PRIVILEGES = (
    f"REVOKE EXECUTE ON FUNCTION {FUNCTION}() FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION {FUNCTION}() TO CURRENT_USER",
    f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN"
    f" EXECUTE 'GRANT EXECUTE ON FUNCTION {FUNCTION}() TO {RUNTIME_ROLE}'; END IF; END $do$",
)


def upgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    for statement in FROZEN_RLS_DDL[TABLE]:
        op.execute(statement)
    op.execute(_FUNCTION_SQL)
    for statement in _FUNCTION_PRIVILEGES:
        op.execute(statement)


def downgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
