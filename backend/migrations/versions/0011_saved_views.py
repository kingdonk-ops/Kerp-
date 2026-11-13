"""Saved register view ``oe_saved_view`` (T-0020).

Additive: one new project-scoped table under the standard project RLS (T-0007) on
PostgreSQL. The view configuration is JSON (JSONB on PostgreSQL).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType
from app.core.tenancy.rls import rls_policy_sql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_saved_view"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("register_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("owner_user_id", GUID(), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("team_id", GUID(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("columns", JSONType(), nullable=False),
        sa.Column("filter", JSONType(), nullable=True),
        sa.Column("sort", JSONType(), nullable=False),
        sa.Column("group_by", JSONType(), nullable=True),
        sa.Column("conditional_format", JSONType(), nullable=False),
        sa.Column("density", sa.String(length=16), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
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
        sa.CheckConstraint("project_id IS NOT NULL", name=op.f("ck_oe_saved_view_project_level")),
        sa.CheckConstraint(
            "visibility IN ('private', 'team', 'project', 'portal')",
            name=op.f("ck_oe_saved_view_visibility"),
        ),
        sa.CheckConstraint(
            "density IN ('compact', 'standard', 'comfortable')",
            name=op.f("ck_oe_saved_view_density"),
        ),
        sa.CheckConstraint(
            "(visibility = 'team') = (team_id IS NOT NULL)",
            name=op.f("ck_oe_saved_view_team_matches_visibility"),
        ),
        sa.CheckConstraint(
            "NOT is_default OR visibility IN ('private', 'project')",
            name=op.f("ck_oe_saved_view_default_kind"),
        ),
        sa.CheckConstraint("schema_version >= 1", name=op.f("ck_oe_saved_view_schema_version")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oe_saved_view")),
    )
    for column in ("owner_user_id", "tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])
    op.create_index("ix_oe_saved_view_project_register", TABLE, ["project_id", "register_key"])
    if op.get_context().dialect.name == "postgresql":
        for statement in rls_policy_sql(TABLE):
            op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)  # drops the table's indexes, constraints and policies with it
