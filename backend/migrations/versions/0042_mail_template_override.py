"""Mail: automated template overrides ``mail_template_override`` (T-0227, diff §4.7, ADR 0021).

Additive: one new tenant-level core table under the standard project RLS (T-0007) on
PostgreSQL. Each row is one revision of a System admin's wording of a registered template in
one locale: subject, intro, outro and action label as plain text with ``{name}`` placeholders.
``version`` numbers the revisions per tenant, template and locale (unique); at most one row per
tenant, template and locale is current (partial unique index). A reset clears ``is_current``
and keeps the row. No current row: the shipped i18n catalogue text.

The runtime role (``kerp_app``) gets ``SELECT, INSERT, UPDATE, DELETE`` on the table through
the default privileges of ``0039`` (ADR 0019); nothing to grant here.

The downgrade drops the table (every override and its revisions are lost; sends use the
shipped texts again).

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "mail_template_override"
KEY_COLUMNS = ["tenant_id", "template_key", "locale"]

# Frozen DDL (TD-060, T-0033): exactly what ``app.core.tenancy.rls.rls_policy_sql``
# returned for this revision's table. Never edit it: a helper change ships as a NEW
# revision with the new output frozen in it (tests/test_migration_frozen_ddl.py fails until
# then).
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {
    TABLE: (
        "ALTER TABLE mail_template_override ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE mail_template_override FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS project_isolation_policy ON mail_template_override",
        (
            "CREATE POLICY project_isolation_policy ON mail_template_override FOR ALL USING"
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
        "DROP POLICY IF EXISTS tenant_level_read_policy ON mail_template_override",
        (
            "CREATE POLICY tenant_level_read_policy ON mail_template_override FOR SELECT USING"
            " (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND"
            " project_id IS NULL)"
        ),
        "DROP POLICY IF EXISTS allowed_projects_read_policy ON mail_template_override",
        (
            "CREATE POLICY allowed_projects_read_policy ON mail_template_override FOR SELECT"
            " USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true),"
            " '')::uuid AND project_id ="
            " ANY(string_to_array(NULLIF(current_setting('app.allowed_project_ids', true), ''),"
            " ',')::uuid[]))"
        ),
    ),
}


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("template_key", sa.String(length=100), nullable=False),
        sa.Column("locale", sa.String(length=35), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("intro", sa.String(length=2000), nullable=False),
        sa.Column("outro", sa.String(length=2000), nullable=False),
        sa.Column("action_label", sa.String(length=80), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint("version >= 1", name=op.f(f"ck_{TABLE}_version")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f(f"fk_{TABLE}_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
    )
    for column in ("tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])
    op.create_index(f"uq_{TABLE}_version", TABLE, [*KEY_COLUMNS, "version"], unique=True)
    op.create_index(
        f"uq_{TABLE}_current",
        TABLE,
        KEY_COLUMNS,
        unique=True,
        postgresql_where=sa.text("is_current"),
        sqlite_where=sa.text("is_current"),
    )
    if op.get_context().dialect.name == "postgresql":
        for statement in FROZEN_RLS_DDL[TABLE]:
            op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)  # drops the table's indexes, constraints and policies with it
