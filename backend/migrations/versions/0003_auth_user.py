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
from app.core.tenancy.rls import rls_policy_sql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "auth_user"


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
        for statement in rls_policy_sql(TABLE):
            op.execute(statement)


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_user_project_id"), table_name=TABLE)
    op.drop_index(op.f("ix_auth_user_tenant_id"), table_name=TABLE)
    op.drop_table(TABLE)
