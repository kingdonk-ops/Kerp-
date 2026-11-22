"""Identity: impersonation sessions ``auth_impersonation_session`` (T-0277, ADR 0015).

Additive: one new tenant-level table under the standard project RLS (T-0007) on PostgreSQL.
One row per impersonation session of a ``system_admin`` (``id`` = the impersonation token's
``sid``): the admin, the admin's login session, the target, start, expiry and, once ended,
when and why (``exit | expired | revoked``). The row lets exactly one path record the end
(``UPDATE ... WHERE ended_at IS NULL``) and lets the batch sweep find expired sessions. It
holds no token and no reason text (the ledger chain ``admin-impersonation:<tenant>`` has the
reason; never the token).

No existing table, row or policy changes. The downgrade drops the table.

Runtime role (ADR 0019): no grant of its own. ``0039``'s default privileges give the runtime
role ``SELECT, INSERT, UPDATE, DELETE`` on this table when the owner creates it; the table is
not append-only (the end is an ``UPDATE``), so nothing is revoked.

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "auth_impersonation_session"
USERS = "auth_user"
OPEN_INDEX = "ix_auth_impersonation_session_open"

# Frozen DDL (TD-060, T-0033): exactly what ``app.core.tenancy.rls.rls_policy_sql``
# returned for this revision's table. Never edit it: a helper change ships as a NEW
# revision with the new output frozen in it (tests/test_migration_frozen_ddl.py fails until
# then).
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {
    TABLE: (
        "ALTER TABLE auth_impersonation_session ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE auth_impersonation_session FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS project_isolation_policy ON auth_impersonation_session",
        (
            "CREATE POLICY project_isolation_policy ON auth_impersonation_session FOR ALL USING"
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
        "DROP POLICY IF EXISTS tenant_level_read_policy ON auth_impersonation_session",
        (
            "CREATE POLICY tenant_level_read_policy ON auth_impersonation_session FOR SELECT"
            " USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true),"
            " '')::uuid AND project_id IS NULL)"
        ),
        "DROP POLICY IF EXISTS allowed_projects_read_policy ON auth_impersonation_session",
        (
            "CREATE POLICY allowed_projects_read_policy ON auth_impersonation_session FOR SELECT"
            " USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true),"
            " '')::uuid AND project_id = ANY(string_to_array("
            "NULLIF(current_setting('app.allowed_project_ids', true), ''), ',')::uuid[]))"
        ),
    ),
}


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("admin_user_id", GUID(), nullable=False),
        sa.Column("admin_session_id", GUID(), nullable=False),
        sa.Column("target_user_id", GUID(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.String(length=16), nullable=True),
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
        sa.CheckConstraint(
            "end_reason IN ('exit', 'expired', 'revoked')", name=op.f(f"ck_{TABLE}_end_reason")
        ),
        sa.CheckConstraint(
            "(ended_at IS NULL) = (end_reason IS NULL)", name=op.f(f"ck_{TABLE}_ended")
        ),
        sa.CheckConstraint("admin_user_id <> target_user_id", name=op.f(f"ck_{TABLE}_not_self")),
        sa.ForeignKeyConstraint(
            ["admin_user_id"], [f"{USERS}.id"], name=op.f(f"fk_{TABLE}_admin_user_id_{USERS}")
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"], [f"{USERS}.id"], name=op.f(f"fk_{TABLE}_target_user_id_{USERS}")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f(f"fk_{TABLE}_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
    )
    for column in ("tenant_id", "project_id", "target_user_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])
    op.create_index(
        OPEN_INDEX,
        TABLE,
        ["tenant_id", "expires_at"],
        postgresql_where=sa.text("ended_at IS NULL"),
        sqlite_where=sa.text("ended_at IS NULL"),
    )
    if op.get_context().dialect.name == "postgresql":
        for statement in FROZEN_RLS_DDL[TABLE]:
            op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)  # drops the table's indexes, constraints and policies with it
