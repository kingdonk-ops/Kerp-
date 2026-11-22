"""Notifications: broadcast e-mail (T-0228, diff §4.7 Broadcast e-mail).

Additive: three new tenant-level module tables with the ``BaseRecordMixin`` columns, the
``tenants`` FK and the standard RLS policies on PostgreSQL.

* ``oe_notifications_broadcast``: sender, scope (``tenant`` | ``projects``) with the project
  ids, the audience filter, the plain-text subject and body, status and counts.
* ``oe_notifications_broadcast_recipient``: one row per resolved recipient (a user id, never
  an address), its dispatch status and the SHA-256 of its one-click opt-out token (unique).
* ``oe_notifications_broadcast_optout``: users who opted out of broadcasts (at most one live
  row per tenant and user).

The runtime role (``kerp_app``) gets ``SELECT, INSERT, UPDATE, DELETE`` on the tables through
the default privileges of ``0039`` (ADR 0019); nothing to grant here.

The downgrade drops the three tables (broadcast history, recipients and opt-outs are lost;
the send log keeps its ``broadcast_id`` references).

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BROADCAST = "oe_notifications_broadcast"
RECIPIENT = "oe_notifications_broadcast_recipient"
OPTOUT = "oe_notifications_broadcast_optout"
TABLES = (BROADCAST, RECIPIENT, OPTOUT)
LIVE = "deleted_at IS NULL"

_TENANT = "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_PROJECT = "NULLIF(current_setting('app.current_project_id', true), '')::uuid"
_ALLOWED = "string_to_array(NULLIF(current_setting('app.allowed_project_ids', true), ''), ',')"
_ADMIN = "COALESCE(NULLIF(current_setting('app.is_system_admin', true), '')::boolean, false)"
_WRITE = (
    f"tenant_id = {_TENANT} AND (project_id IS NOT DISTINCT FROM {_PROJECT}"
    f" OR (project_id IS NOT NULL AND {_ADMIN}))"
)


def _standard_rls(table: str) -> tuple[str, ...]:
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS project_isolation_policy ON {table}",
        f"CREATE POLICY project_isolation_policy ON {table} FOR ALL USING ({_WRITE})"
        f" WITH CHECK ({_WRITE})",
        f"DROP POLICY IF EXISTS tenant_level_read_policy ON {table}",
        f"CREATE POLICY tenant_level_read_policy ON {table} FOR SELECT USING"
        f" (tenant_id = {_TENANT} AND project_id IS NULL)",
        f"DROP POLICY IF EXISTS allowed_projects_read_policy ON {table}",
        f"CREATE POLICY allowed_projects_read_policy ON {table} FOR SELECT USING"
        f" (tenant_id = {_TENANT} AND project_id = ANY({_ALLOWED}::uuid[]))",
    )


# Frozen DDL (TD-060, T-0033): exactly what ``app.core.tenancy.rls.rls_policy_sql`` returned
# for this revision's tables, built from constants local to this revision. Never edit it: a
# helper change ships as a NEW revision (tests/test_migration_frozen_ddl.py fails until then).
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {table: _standard_rls(table) for table in TABLES}


def _mixin(table: str) -> list[sa.Column[object] | sa.Constraint]:
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
        sa.CheckConstraint("project_id IS NULL", name=op.f(f"ck_{table}_tenant_level")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f(f"fk_{table}_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def _indexes(table: str, *columns: str) -> None:
    for column in ("tenant_id", "project_id", *columns):
        op.create_index(op.f(f"ix_{table}_{column}"), table, [column])


def upgrade() -> None:
    op.create_table(
        BROADCAST,
        sa.Column("sender_id", GUID(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("project_ids", JSONType(), nullable=False),
        sa.Column("audience", JSONType(), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column("queued_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("job_id", GUID(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_mixin(BROADCAST),
        sa.CheckConstraint("scope IN ('tenant', 'projects')", name=op.f(f"ck_{BROADCAST}_scope")),
        sa.CheckConstraint(
            "status IN ('draft', 'queued', 'sending', 'sent', 'failed')",
            name=op.f(f"ck_{BROADCAST}_status"),
        ),
        sa.CheckConstraint(
            "recipient_count >= 0 AND queued_count >= 0 AND skipped_count >= 0",
            name=op.f(f"ck_{BROADCAST}_counts"),
        ),
    )
    _indexes(BROADCAST, "sender_id")

    op.create_table(
        RECIPIENT,
        sa.Column("broadcast_id", GUID(), nullable=False),
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("skip_reason", sa.String(length=16), nullable=True),
        sa.Column("send_id", GUID(), nullable=True),
        sa.Column("unsubscribe_hash", sa.String(length=64), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
        *_mixin(RECIPIENT),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'skipped')", name=op.f(f"ck_{RECIPIENT}_status")
        ),
        sa.CheckConstraint(
            "skip_reason IS NULL OR skip_reason IN"
            " ('opted_out', 'inactive', 'suppressed', 'no_address')",
            name=op.f(f"ck_{RECIPIENT}_skip_reason"),
        ),
    )
    _indexes(RECIPIENT, "broadcast_id", "user_id")
    op.create_index(f"uq_{RECIPIENT}_user", RECIPIENT, ["broadcast_id", "user_id"], unique=True)
    op.create_index(f"uq_{RECIPIENT}_token", RECIPIENT, ["unsubscribe_hash"], unique=True)

    op.create_table(
        OPTOUT,
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("broadcast_id", GUID(), nullable=True),
        *_mixin(OPTOUT),
    )
    _indexes(OPTOUT, "user_id")
    op.create_index(
        f"uq_{OPTOUT}_user",
        OPTOUT,
        ["tenant_id", "user_id"],
        unique=True,
        postgresql_where=sa.text(LIVE),
        sqlite_where=sa.text(LIVE),
    )

    if op.get_context().dialect.name == "postgresql":
        for table in TABLES:
            for statement in FROZEN_RLS_DDL[table]:
                op.execute(statement)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)  # drops the table's indexes, constraints and policies with it
