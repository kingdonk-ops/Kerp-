"""Per-project module settings (T-0011).

Additive: one new table, put under project-scoped RLS (T-0007) on PostgreSQL.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "project_module_setting"

# Frozen DDL (TD-060, T-0033): exactly what ``app.core.tenancy.rls.rls_policy_sql``
# returned for this revision's table(s). Never edit it: a helper change ships as a NEW
# revision with the new output frozen in it (tests/test_migration_frozen_ddl.py fails until
# then).
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {
    TABLE: (
        "ALTER TABLE project_module_setting ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE project_module_setting FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS project_isolation_policy ON project_module_setting",
        (
            "CREATE POLICY project_isolation_policy ON project_module_setting FOR ALL USING"
            " (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND"
            " project_id IS NOT DISTINCT FROM NULLIF(current_setting('app.current_project_id',"
            " true), '')::uuid) WITH CHECK (tenant_id ="
            " NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND project_id IS"
            " NOT DISTINCT FROM NULLIF(current_setting('app.current_project_id', true), '')::uuid)"
        ),
        "DROP POLICY IF EXISTS tenant_level_read_policy ON project_module_setting",
        (
            "CREATE POLICY tenant_level_read_policy ON project_module_setting FOR SELECT USING"
            " (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND"
            " project_id IS NULL)"
        ),
        "DROP POLICY IF EXISTS allowed_projects_read_policy ON project_module_setting",
        (
            "CREATE POLICY allowed_projects_read_policy ON project_module_setting FOR SELECT USING"
            " (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND"
            " project_id = ANY(string_to_array(NULLIF(current_setting('app.allowed_project_ids',"
            " true), ''), ',')::uuid[]))"
        ),
    ),
}


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("project_id", GUID(), nullable=False),
        sa.Column("module_name", sa.String(length=40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("id", GUID(), nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("custom_fields", JSONType(), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_module_setting")),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "module_name", name=op.f("uq_project_module_setting_module")
        ),
    )
    op.create_index(op.f("ix_project_module_setting_tenant_id"), TABLE, ["tenant_id"])
    op.create_index(op.f("ix_project_module_setting_project_id"), TABLE, ["project_id"])
    if op.get_context().dialect.name == "postgresql":
        for statement in FROZEN_RLS_DDL[TABLE]:
            op.execute(statement)


def downgrade() -> None:
    op.drop_index(op.f("ix_project_module_setting_project_id"), table_name=TABLE)
    op.drop_index(op.f("ix_project_module_setting_tenant_id"), table_name=TABLE)
    op.drop_table(TABLE)
