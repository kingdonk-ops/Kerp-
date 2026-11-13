"""Uploaded-file record ``oe_uploads_file`` (T-0016).

Additive: one new project-scoped table under the standard project RLS (T-0007) on
PostgreSQL. The bytes live in object storage (S3/MinIO), never in the database.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType
from app.core.tenancy.rls import rls_policy_sql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_uploads_file"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("module", sa.String(length=40), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=127), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=True),
        sa.Column("rejection_reason", sa.String(length=200), nullable=True),
        sa.Column("scan_result", sa.String(length=16), nullable=True),
        sa.Column("job_id", GUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("project_id IS NOT NULL", name=op.f("ck_oe_uploads_file_project_level")),
        sa.CheckConstraint(
            "status IN ('pending', 'verifying', 'available', 'rejected', 'expired')",
            name=op.f("ck_oe_uploads_file_status"),
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_oe_uploads_file_size")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oe_uploads_file")),
    )
    for column in ("status", "tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])
    if op.get_context().dialect.name == "postgresql":
        for statement in rls_policy_sql(TABLE):
            op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)  # drops the table's indexes, constraints and policies with it
