"""Users for local authentication (T-0008).

Additive: one new table, tenant-level rows under project-scoped RLS (T-0007) on PostgreSQL.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "auth_user"

# Frozen DDL (TD-060, T-0033): exactly what ``app.core.tenancy.rls.rls_policy_sql``
# returned for this revision's table(s). Never edit it: a helper change ships as a NEW
# revision with the new output frozen in it (tests/test_migration_frozen_ddl.py fails until
# then).
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {
    TABLE: (
        "ALTER TABLE auth_user ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE auth_user FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS project_isolation_policy ON auth_user",
        (
            "CREATE POLICY project_isolation_policy ON auth_user FOR ALL USING (tenant_id ="
            " NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND project_id IS"
            " NOT DISTINCT FROM NULLIF(current_setting('app.current_project_id', true), '')::uuid)"
            " WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true),"
            " '')::uuid AND project_id IS NOT DISTINCT FROM"
            " NULLIF(current_setting('app.current_project_id', true), '')::uuid)"
        ),
        "DROP POLICY IF EXISTS tenant_level_read_policy ON auth_user",
        (
            "CREATE POLICY tenant_level_read_policy ON auth_user FOR SELECT USING (tenant_id ="
            " NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND project_id IS"
            " NULL)"
        ),
        "DROP POLICY IF EXISTS allowed_projects_read_policy ON auth_user",
        (
            "CREATE POLICY allowed_projects_read_policy ON auth_user FOR SELECT USING (tenant_id ="
            " NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND project_id ="
            " ANY(string_to_array(NULLIF(current_setting('app.allowed_project_ids', true), ''),"
            " ',')::uuid[]))"
        ),
    ),
}


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("project_id IS NULL", name=op.f("ck_auth_user_tenant_level")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_user")),
        sa.UniqueConstraint("tenant_id", "email", name=op.f("uq_auth_user_tenant_email")),
    )
    op.create_index(op.f("ix_auth_user_tenant_id"), TABLE, ["tenant_id"])
    op.create_index(op.f("ix_auth_user_project_id"), TABLE, ["project_id"])
    if op.get_context().dialect.name == "postgresql":
        for statement in FROZEN_RLS_DDL[TABLE]:
            op.execute(statement)


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_user_project_id"), table_name=TABLE)
    op.drop_index(op.f("ix_auth_user_tenant_id"), table_name=TABLE)
    op.drop_table(TABLE)
