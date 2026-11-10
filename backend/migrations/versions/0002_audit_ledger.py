"""Hash-chained audit ledger, append-only at the database level (T-0006, diff §4).

Additive: one new table. On PostgreSQL it is put under project-scoped RLS (T-0007) and
made append-only: ``REVOKE UPDATE, DELETE, TRUNCATE`` plus blocking triggers
(``app.core.audit.guard``).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.audit.guard import ledger_guard_drop_sql, ledger_guard_sql
from app.core.db import GUID, JSONType
from app.core.tenancy.rls import rls_policy_sql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "audit_ledger"
NIL_UUID = "00000000-0000-0000-0000-000000000000"  # scope_id of tenant-level chains


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("scope_id", GUID(), nullable=False),
        sa.Column("chain_key", sa.String(length=200), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", JSONType(), nullable=False),
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
        sa.CheckConstraint("seq >= 1", name=op.f("ck_audit_ledger_seq_positive")),
        sa.CheckConstraint(
            f"scope_id = COALESCE(project_id, '{NIL_UUID}')", name=op.f("ck_audit_ledger_scope_id")
        ),
        sa.CheckConstraint("deleted_at IS NULL", name=op.f("ck_audit_ledger_not_deleted")),
        sa.CheckConstraint("revision = 1", name=op.f("ck_audit_ledger_first_revision")),
        sa.CheckConstraint("custom_fields = '{}'", name=op.f("ck_audit_ledger_no_custom_fields")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_ledger")),
        sa.UniqueConstraint(
            "tenant_id", "scope_id", "chain_key", "seq", name=op.f("uq_audit_ledger_chain_seq")
        ),
    )
    op.create_index(op.f("ix_audit_ledger_tenant_id"), TABLE, ["tenant_id"])
    op.create_index(op.f("ix_audit_ledger_project_id"), TABLE, ["project_id"])
    if op.get_context().dialect.name == "postgresql":
        for statement in (*rls_policy_sql(TABLE), *ledger_guard_sql(TABLE)):
            op.execute(statement)


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        for statement in ledger_guard_drop_sql(TABLE):
            op.execute(statement)
    op.drop_index(op.f("ix_audit_ledger_project_id"), table_name=TABLE)
    op.drop_index(op.f("ix_audit_ledger_tenant_id"), table_name=TABLE)
    op.drop_table(TABLE)
