"""Project record ``oe_projects_project`` (T-0012).

Additive: one new tenant-level table under the standard project-scoped RLS (T-0007) on
PostgreSQL; T-0014 replaces its policy with the membership-based one (TD-053).
``spatial_bounds`` is PostGIS ``geometry(Polygon,4326)`` on PostgreSQL (the extension is
created by ``infra/postgres/initdb/20-kerp-app.sql``) and GeoJSON text elsewhere.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType
from app.core.geo import GeoJSONGeometry

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_projects_project"

# Frozen DDL (TD-060, T-0033): exactly what ``app.core.tenancy.rls.rls_policy_sql``
# returned for this revision's table(s). Never edit it: a helper change ships as a NEW
# revision with the new output frozen in it (tests/test_migration_frozen_ddl.py fails until
# then).
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {
    TABLE: (
        "ALTER TABLE oe_projects_project ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE oe_projects_project FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS project_isolation_policy ON oe_projects_project",
        (
            "CREATE POLICY project_isolation_policy ON oe_projects_project FOR ALL USING (tenant_id"
            " = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND project_id IS"
            " NOT DISTINCT FROM NULLIF(current_setting('app.current_project_id', true), '')::uuid)"
            " WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true),"
            " '')::uuid AND project_id IS NOT DISTINCT FROM"
            " NULLIF(current_setting('app.current_project_id', true), '')::uuid)"
        ),
        "DROP POLICY IF EXISTS tenant_level_read_policy ON oe_projects_project",
        (
            "CREATE POLICY tenant_level_read_policy ON oe_projects_project FOR SELECT USING"
            " (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND"
            " project_id IS NULL)"
        ),
        "DROP POLICY IF EXISTS allowed_projects_read_policy ON oe_projects_project",
        (
            "CREATE POLICY allowed_projects_read_policy ON oe_projects_project FOR SELECT USING"
            " (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND"
            " project_id = ANY(string_to_array(NULLIF(current_setting('app.allowed_project_ids',"
            " true), ''), ',')::uuid[]))"
        ),
    ),
}


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("site_timezone", sa.String(length=64), nullable=False),
        sa.Column("spatial_bounds", GeoJSONGeometry("Polygon"), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("locale", sa.String(length=35), nullable=False),
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
        sa.CheckConstraint("project_id IS NULL", name=op.f("ck_oe_projects_project_tenant_level")),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'ON_HOLD', 'CLOSED')", name=op.f("ck_oe_projects_project_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oe_projects_project")),
        sa.UniqueConstraint("tenant_id", "code", name="uq_oe_projects_project_tenant_code"),
    )
    for column in ("tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])
    if op.get_context().dialect.name == "postgresql":
        for statement in FROZEN_RLS_DDL[TABLE]:
            op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)  # drops the table's indexes, constraints and policies with it
