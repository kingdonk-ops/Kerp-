"""Runtime settings registry values ``oe_settings_value`` (T-0230, diff §4.7).

Additive: one new tenant-level table under the standard project RLS (T-0007) on PostgreSQL.
One live row per tenant and registry key (partial unique index), the value as JSON; no row
means the entry's code default (``app.modules.settings.registry``).

Data: every live ``oe_settings_deployment`` row (T-0044) is copied, one row per setting, with
its timestamps, actor and source. The old table is kept as it is and is read-only from here
on (nothing reads or writes it; dropping it needs an ADR). The copy is plain ``INSERT ...
SELECT`` so offline ``--sql`` output carries it too. On PostgreSQL the read runs with ``FORCE
ROW LEVEL SECURITY`` lifted on the old table (as in ``0012``): the migration role owns it and
would otherwise see no rows; it is forced again in the same transaction. The new table gets
its policies after the copy.

The downgrade drops the new table: values changed after the upgrade are lost, the old table
still holds what it held at the upgrade.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_settings_value"
LEGACY = "oe_settings_deployment"
# The seven T-0044 settings (frozen here: the registry may change later). True = integer.
COPIED: tuple[tuple[str, bool], ...] = (
    ("offline_window_days", True),
    ("offline_grace_hours", True),
    ("max_checkout_clock_skew_s", True),
    ("default_status", False),
    ("default_site_timezone", False),
    ("default_currency_code", False),
    ("default_locale", False),
)
# A random version-4 UUID as 36 characters (SQLite has no generator; ``GUID`` is CHAR(36)).
_SQLITE_UUID = (
    "lower(hex(randomblob(4)) || '-' || hex(randomblob(2)) || '-4' ||"
    " substr(hex(randomblob(2)), 2) || '-' || substr('89ab', 1 + (abs(random()) % 4), 1) ||"
    " substr(hex(randomblob(2)), 2) || '-' || hex(randomblob(6)))"
)

# Frozen DDL (TD-060, T-0033): exactly what ``app.core.tenancy.rls.rls_policy_sql``
# returned for this revision's table. Never edit it: a helper change ships as a NEW
# revision with the new output frozen in it (tests/test_migration_frozen_ddl.py fails until
# then).
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {
    TABLE: (
        "ALTER TABLE oe_settings_value ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE oe_settings_value FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS project_isolation_policy ON oe_settings_value",
        (
            "CREATE POLICY project_isolation_policy ON oe_settings_value FOR ALL USING"
            " (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND"
            " (project_id IS NOT DISTINCT FROM NULLIF(current_setting('app.current_project_id',"
            " true), '')::uuid OR (project_id IS NOT NULL AND"
            " COALESCE(NULLIF(current_setting('app.is_system_admin', true), '')::boolean,"
            " false)))) WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id',"
            " true), '')::uuid AND (project_id IS NOT DISTINCT FROM"
            " NULLIF(current_setting('app.current_project_id', true), '')::uuid OR (project_id IS"
            " NOT NULL AND COALESCE(NULLIF(current_setting('app.is_system_admin', true),"
            " '')::boolean, false))))"
        ),
        "DROP POLICY IF EXISTS tenant_level_read_policy ON oe_settings_value",
        (
            "CREATE POLICY tenant_level_read_policy ON oe_settings_value FOR SELECT USING"
            " (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND"
            " project_id IS NULL)"
        ),
        "DROP POLICY IF EXISTS allowed_projects_read_policy ON oe_settings_value",
        (
            "CREATE POLICY allowed_projects_read_policy ON oe_settings_value FOR SELECT USING"
            " (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND"
            " project_id = ANY(string_to_array(NULLIF(current_setting('app.allowed_project_ids',"
            " true), ''), ',')::uuid[]))"
        ),
    ),
}


def _copy_sql(column: str, *, integer: bool, postgres: bool) -> str:
    if postgres:
        new_id, value, empty = "gen_random_uuid()", f"to_jsonb({column})", "'{}'::jsonb"
    else:
        value = f"CAST({column} AS TEXT)" if integer else f"json_quote({column})"
        new_id, empty = _SQLITE_UUID, "'{}'"
    return (
        f"INSERT INTO {TABLE} (id, tenant_id, project_id, key, value, created_at, updated_at,"
        " deleted_at, custom_fields, created_by, updated_by, source, revision)"
        f" SELECT {new_id}, tenant_id, NULL, '{column}', {value}, created_at, updated_at,"
        f" NULL, {empty}, created_by, updated_by, source, 1"
        f" FROM {LEGACY} WHERE deleted_at IS NULL"
    )


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", JSONType(), nullable=False),
        sa.Column("id", GUID(), nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.Column("project_id", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("custom_fields", JSONType(), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("project_id IS NULL", name=op.f(f"ck_{TABLE}_tenant_level")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f(f"fk_{TABLE}_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
    )
    for column in ("tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])
    op.create_index(
        "uq_oe_settings_value_live",
        TABLE,
        ["tenant_id", "key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    postgres = op.get_context().dialect.name == "postgresql"
    if postgres:
        op.execute(f"ALTER TABLE {LEGACY} NO FORCE ROW LEVEL SECURITY")
    for column, integer in COPIED:
        op.execute(_copy_sql(column, integer=integer, postgres=postgres))
    if postgres:
        op.execute(f"ALTER TABLE {LEGACY} FORCE ROW LEVEL SECURITY")
        for statement in FROZEN_RLS_DDL[TABLE]:
            op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)  # drops the table's indexes, constraints and policies with it
