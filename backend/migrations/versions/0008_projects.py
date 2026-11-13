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
from app.core.tenancy.rls import rls_policy_sql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_projects_project"


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
        for statement in rls_policy_sql(TABLE):
            op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)  # drops the table's indexes, constraints and policies with it
