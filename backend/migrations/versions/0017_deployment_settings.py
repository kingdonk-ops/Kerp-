"""Deployment (tenant) settings ``oe_settings_deployment`` (T-0044, diff §4.4).

Additive: one new tenant-level table under the standard project RLS (T-0007) on PostgreSQL.
At most one live row per tenant (partial unique index). Offline window 1-14 days, clock skew
1-300 s, grace 1 h to 24 x the window, project default status ``ACTIVE|ON_HOLD|CLOSED``.
No server defaults: the service writes every value (shipped defaults in
``app.modules.settings.models.SHIPPED_DEFAULTS``).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType
from app.core.tenancy.rls import rls_policy_sql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_settings_deployment"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("offline_window_days", sa.Integer(), nullable=False),
        sa.Column("offline_grace_hours", sa.Integer(), nullable=False),
        sa.Column("max_checkout_clock_skew_s", sa.Integer(), nullable=False),
        sa.Column("default_status", sa.String(length=32), nullable=False),
        sa.Column("default_site_timezone", sa.String(length=64), nullable=False),
        sa.Column("default_currency_code", sa.String(length=3), nullable=False),
        sa.Column("default_locale", sa.String(length=35), nullable=False),
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
        sa.CheckConstraint("offline_window_days BETWEEN 1 AND 14", name=op.f(f"ck_{TABLE}_window")),
        sa.CheckConstraint(
            "max_checkout_clock_skew_s BETWEEN 1 AND 300", name=op.f(f"ck_{TABLE}_clock_skew")
        ),
        sa.CheckConstraint(
            "offline_grace_hours >= 1 AND offline_grace_hours <= 24 * offline_window_days",
            name=op.f(f"ck_{TABLE}_grace"),
        ),
        sa.CheckConstraint(
            "default_status IN ('ACTIVE', 'ON_HOLD', 'CLOSED')",
            name=op.f(f"ck_{TABLE}_default_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
    )
    for column in ("tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])
    op.create_index(
        "uq_oe_settings_deployment_live",
        TABLE,
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    if op.get_context().dialect.name == "postgresql":
        for statement in rls_policy_sql(TABLE):
            op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)  # drops the table's indexes, constraints and policies with it
