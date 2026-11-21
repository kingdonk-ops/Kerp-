"""In-app notification record ``oe_notifications_notification`` (T-0019).

Additive: one new project-scoped table under the standard project RLS (T-0007) on
PostgreSQL.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_notifications_notification"

# Frozen DDL (TD-060, T-0033): exactly what ``app.core.tenancy.rls.rls_policy_sql``
# returned for this revision's table(s). Never edit it: a helper change ships as a NEW
# revision with the new output frozen in it (tests/test_migration_frozen_ddl.py fails until
# then).
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {
    TABLE: (
        "ALTER TABLE oe_notifications_notification ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE oe_notifications_notification FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS project_isolation_policy ON oe_notifications_notification",
        (
            "CREATE POLICY project_isolation_policy ON oe_notifications_notification FOR ALL USING"
            " (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND"
            " project_id IS NOT DISTINCT FROM NULLIF(current_setting('app.current_project_id',"
            " true), '')::uuid) WITH CHECK (tenant_id ="
            " NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND project_id IS"
            " NOT DISTINCT FROM NULLIF(current_setting('app.current_project_id', true), '')::uuid)"
        ),
        "DROP POLICY IF EXISTS tenant_level_read_policy ON oe_notifications_notification",
        (
            "CREATE POLICY tenant_level_read_policy ON oe_notifications_notification FOR SELECT"
            " USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
            " AND project_id IS NULL)"
        ),
        "DROP POLICY IF EXISTS allowed_projects_read_policy ON oe_notifications_notification",
        (
            "CREATE POLICY allowed_projects_read_policy ON oe_notifications_notification FOR SELECT"
            " USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
            " AND project_id ="
            " ANY(string_to_array(NULLIF(current_setting('app.allowed_project_ids', true), ''),"
            " ',')::uuid[]))"
        ),
    ),
}


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("recipient_id", GUID(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("params", JSONType(), nullable=False),
        sa.Column("label", sa.String(length=40), nullable=True),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("pin_key", sa.String(length=120), nullable=True),
        sa.Column("unpinned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subject_type", sa.String(length=64), nullable=True),
        sa.Column("subject_id", GUID(), nullable=True),
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
        sa.CheckConstraint(
            "project_id IS NOT NULL", name=op.f("ck_oe_notifications_notification_project_level")
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'critical')",
            name=op.f("ck_oe_notifications_notification_priority"),
        ),
        sa.CheckConstraint(
            "NOT pinned OR pin_key IS NOT NULL",
            name=op.f("ck_oe_notifications_notification_pin_key"),
        ),
        sa.CheckConstraint(
            "(subject_type IS NULL) = (subject_id IS NULL)",
            name=op.f("ck_oe_notifications_notification_subject"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oe_notifications_notification")),
    )
    for column in ("recipient_id", "pin_key", "tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])
    if op.get_context().dialect.name == "postgresql":
        for statement in FROZEN_RLS_DDL[TABLE]:
            op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)  # drops the table's indexes, constraints and policies with it
