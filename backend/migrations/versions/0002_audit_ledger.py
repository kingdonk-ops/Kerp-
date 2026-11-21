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

from app.core.db import GUID, JSONType

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "audit_ledger"
NIL_UUID = "00000000-0000-0000-0000-000000000000"  # scope_id of tenant-level chains

# Frozen DDL (TD-060, T-0033): exactly what ``rls_policy_sql``, ``ledger_guard_sql`` and
# ``ledger_guard_drop_sql`` returned for this revision's table. Never edit it: a helper change
# ships as a NEW revision with the new output frozen in it (tests/test_migration_frozen_ddl.py
# fails until then).
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {
    TABLE: (
        "ALTER TABLE audit_ledger ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE audit_ledger FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS project_isolation_policy ON audit_ledger",
        (
            "CREATE POLICY project_isolation_policy ON audit_ledger FOR ALL USING (tenant_id ="
            " NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND project_id IS"
            " NOT DISTINCT FROM NULLIF(current_setting('app.current_project_id', true), '')::uuid)"
            " WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true),"
            " '')::uuid AND project_id IS NOT DISTINCT FROM"
            " NULLIF(current_setting('app.current_project_id', true), '')::uuid)"
        ),
        "DROP POLICY IF EXISTS tenant_level_read_policy ON audit_ledger",
        (
            "CREATE POLICY tenant_level_read_policy ON audit_ledger FOR SELECT USING (tenant_id ="
            " NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND project_id IS"
            " NULL)"
        ),
        "DROP POLICY IF EXISTS allowed_projects_read_policy ON audit_ledger",
        (
            "CREATE POLICY allowed_projects_read_policy ON audit_ledger FOR SELECT USING (tenant_id"
            " = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND project_id ="
            " ANY(string_to_array(NULLIF(current_setting('app.allowed_project_ids', true), ''),"
            " ',')::uuid[]))"
        ),
    ),
}
FROZEN_LEDGER_GUARD_DDL: dict[str, tuple[str, ...]] = {
    TABLE: (
        "REVOKE UPDATE, DELETE, TRUNCATE ON audit_ledger FROM PUBLIC",
        "REVOKE UPDATE, DELETE, TRUNCATE ON audit_ledger FROM CURRENT_USER",
        (
            "CREATE OR REPLACE FUNCTION audit_ledger_reject_change() RETURNS trigger LANGUAGE"
            " plpgsql AS $$ BEGIN RAISE EXCEPTION USING ERRCODE = 'insufficient_privilege', MESSAGE"
            " = TG_TABLE_NAME || ' is append-only (' || TG_OP || ' refused)'; END $$"
        ),
        (
            "CREATE TRIGGER audit_ledger_append_only BEFORE UPDATE OR DELETE ON audit_ledger FOR"
            " EACH ROW EXECUTE FUNCTION audit_ledger_reject_change()"
        ),
        (
            "CREATE TRIGGER audit_ledger_no_truncate BEFORE TRUNCATE ON audit_ledger FOR EACH"
            " STATEMENT EXECUTE FUNCTION audit_ledger_reject_change()"
        ),
    ),
}
FROZEN_LEDGER_GUARD_DROP_DDL: dict[str, tuple[str, ...]] = {
    TABLE: (
        "DROP TRIGGER IF EXISTS audit_ledger_no_truncate ON audit_ledger",
        "DROP TRIGGER IF EXISTS audit_ledger_append_only ON audit_ledger",
        "DROP FUNCTION IF EXISTS audit_ledger_reject_change()",
    ),
}


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
        for statement in (*FROZEN_RLS_DDL[TABLE], *FROZEN_LEDGER_GUARD_DDL[TABLE]):
            op.execute(statement)


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        for statement in FROZEN_LEDGER_GUARD_DROP_DDL[TABLE]:
            op.execute(statement)
    op.drop_index(op.f("ix_audit_ledger_project_id"), table_name=TABLE)
    op.drop_index(op.f("ix_audit_ledger_tenant_id"), table_name=TABLE)
    op.drop_table(TABLE)
