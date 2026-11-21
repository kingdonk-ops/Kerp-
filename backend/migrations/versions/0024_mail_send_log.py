"""Outbound e-mail: send log and suppression list (T-0058, diff §4.6).

Additive: two new tables under the standard tenant/project RLS policies (T-0007) on
PostgreSQL.

* ``mail_send_log``: one row per e-mail (template key, recipient SHA-256 + masked display
  form, status ``queued | sent | bounced | complained | failed | suppressed``, provider
  message id, attempts, short error code, timestamps). Never an address in clear, a link, a
  token or a body.
* ``mail_suppression``: tenant-level; addresses SES reported as a hard bounce or a complaint;
  at most one live row per tenant and address (partial unique index); removal is a soft
  delete (audited by the service).

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEND = "mail_send_log"
SUPPRESSION = "mail_suppression"

# Frozen DDL (TD-060, T-0033): exactly what ``app.core.tenancy.rls.rls_policy_sql``
# returned for this revision's table(s). Never edit it: a helper change ships as a NEW
# revision with the new output frozen in it (tests/test_migration_frozen_ddl.py fails until
# then).
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {
    SEND: (
        "ALTER TABLE mail_send_log ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE mail_send_log FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS project_isolation_policy ON mail_send_log",
        (
            "CREATE POLICY project_isolation_policy ON mail_send_log FOR ALL USING (tenant_id ="
            " NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND project_id IS"
            " NOT DISTINCT FROM NULLIF(current_setting('app.current_project_id', true), '')::uuid)"
            " WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true),"
            " '')::uuid AND project_id IS NOT DISTINCT FROM"
            " NULLIF(current_setting('app.current_project_id', true), '')::uuid)"
        ),
        "DROP POLICY IF EXISTS tenant_level_read_policy ON mail_send_log",
        (
            "CREATE POLICY tenant_level_read_policy ON mail_send_log FOR SELECT USING (tenant_id ="
            " NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND project_id IS"
            " NULL)"
        ),
        "DROP POLICY IF EXISTS allowed_projects_read_policy ON mail_send_log",
        (
            "CREATE POLICY allowed_projects_read_policy ON mail_send_log FOR SELECT USING"
            " (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND"
            " project_id = ANY(string_to_array(NULLIF(current_setting('app.allowed_project_ids',"
            " true), ''), ',')::uuid[]))"
        ),
    ),
    SUPPRESSION: (
        "ALTER TABLE mail_suppression ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE mail_suppression FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS project_isolation_policy ON mail_suppression",
        (
            "CREATE POLICY project_isolation_policy ON mail_suppression FOR ALL USING (tenant_id ="
            " NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND project_id IS"
            " NOT DISTINCT FROM NULLIF(current_setting('app.current_project_id', true), '')::uuid)"
            " WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true),"
            " '')::uuid AND project_id IS NOT DISTINCT FROM"
            " NULLIF(current_setting('app.current_project_id', true), '')::uuid)"
        ),
        "DROP POLICY IF EXISTS tenant_level_read_policy ON mail_suppression",
        (
            "CREATE POLICY tenant_level_read_policy ON mail_suppression FOR SELECT USING (tenant_id"
            " = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND project_id IS"
            " NULL)"
        ),
        "DROP POLICY IF EXISTS allowed_projects_read_policy ON mail_suppression",
        (
            "CREATE POLICY allowed_projects_read_policy ON mail_suppression FOR SELECT USING"
            " (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid AND"
            " project_id = ANY(string_to_array(NULLIF(current_setting('app.allowed_project_ids',"
            " true), ''), ',')::uuid[]))"
        ),
    ),
}


def _record_columns() -> list[sa.Column[object]]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        SEND,
        sa.Column("template_key", sa.String(length=100), nullable=False),
        sa.Column("recipient_hash", sa.String(length=64), nullable=False),
        sa.Column("recipient_masked", sa.String(length=260), nullable=False),
        sa.Column("locale", sa.String(length=35), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_at", sa.DateTime(timezone=True), nullable=True),
        *_record_columns(),
        sa.CheckConstraint(
            "status IN ('queued', 'sent', 'bounced', 'complained', 'failed', 'suppressed')",
            name=op.f(f"ck_{SEND}_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{SEND}")),
    )
    for column in ("tenant_id", "project_id", "recipient_hash", "status", "provider_message_id"):
        op.create_index(op.f(f"ix_{SEND}_{column}"), SEND, [column])

    op.create_table(
        SUPPRESSION,
        sa.Column("recipient_hash", sa.String(length=64), nullable=False),
        sa.Column("recipient_masked", sa.String(length=260), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.String(length=100), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        *_record_columns(),
        sa.CheckConstraint(
            "reason IN ('bounce', 'complaint')", name=op.f(f"ck_{SUPPRESSION}_reason")
        ),
        sa.CheckConstraint("project_id IS NULL", name=op.f(f"ck_{SUPPRESSION}_tenant_level")),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{SUPPRESSION}")),
    )
    for column in ("tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{SUPPRESSION}_{column}"), SUPPRESSION, [column])
    op.create_index(
        "uq_mail_suppression_live",
        SUPPRESSION,
        ["tenant_id", "recipient_hash"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )

    if op.get_context().dialect.name == "postgresql":
        for table in (SEND, SUPPRESSION):
            for statement in FROZEN_RLS_DDL[table]:
                op.execute(statement)


def downgrade() -> None:
    op.drop_table(SUPPRESSION)  # indexes, constraints and policies go with the tables
    op.drop_table(SEND)
