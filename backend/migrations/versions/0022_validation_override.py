"""Per-record override of a blocked check (T-0035, ADR 0004 Amendment A).

Additive: the new table ``oe_validation_override`` under project-scoped RLS (T-0007) on
PostgreSQL, with a partial unique index (one ``pending`` or ``approved`` override per record
and rule), and the nullable column ``checked_values_hash`` on ``oe_validation_finding`` (the
hash an override of that finding is bound to; NULL on findings logged before this revision).

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType
from app.core.tenancy.rls import rls_policy_sql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FINDING = "oe_validation_finding"
OVERRIDE = "oe_validation_override"
ACTIVE_INDEX = "uq_oe_validation_override_active"
ACTIVE = "decision IN ('pending', 'approved')"


def _mixin_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", GUID(), nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.Column("project_id", GUID(), nullable=False),  # always a project-level row
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("custom_fields", JSONType(), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    ]


def _check(sql: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(sql, name=op.f(f"ck_{OVERRIDE}_{name}"))


def upgrade() -> None:
    op.add_column(FINDING, sa.Column("checked_values_hash", sa.String(length=64), nullable=True))
    op.create_table(
        OVERRIDE,
        sa.Column("record_type", sa.String(length=60), nullable=False),
        sa.Column("record_id", GUID(), nullable=False),
        sa.Column("rule_set_key", sa.String(length=120), nullable=False),
        sa.Column("rule_key", sa.String(length=120), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("finding_id", GUID(), nullable=False),
        sa.Column("requested_by", GUID(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("approver_persona", sa.String(length=10), nullable=False),
        sa.Column("approver_user_id", GUID(), nullable=True),
        sa.Column("decision", sa.String(length=10), nullable=False),
        sa.Column("decided_by", GUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("checked_values_hash", sa.String(length=64), nullable=False),
        sa.Column("audit_chain_key", sa.String(length=200), nullable=False),
        *_mixin_columns(),
        _check("decision IN ('pending', 'approved', 'rejected', 'void')", "decision"),
        _check("approver_persona IN ('PER-06', 'PER-02')", "approver"),
        _check("length(trim(reason)) > 0", "reason_given"),
        _check("length(checked_values_hash) = 64", "hash_length"),
        _check("(decision = 'pending') = (decided_at IS NULL)", "decided"),
        _check("decision NOT IN ('approved', 'rejected') OR decided_by IS NOT NULL", "decider"),
        _check("decided_by IS NULL OR decided_by <> requested_by", "never_self_decided"),
        _check("approver_user_id IS NULL OR approver_user_id <> requested_by", "never_self_routed"),
        sa.ForeignKeyConstraint(
            ["finding_id"], [f"{FINDING}.id"], name=op.f(f"fk_{OVERRIDE}_finding_id_{FINDING}")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{OVERRIDE}")),
    )
    for column in ("record_id", "finding_id", "tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{OVERRIDE}_{column}"), OVERRIDE, [column])
    op.create_index(
        ACTIVE_INDEX,
        OVERRIDE,
        ["tenant_id", "project_id", "record_type", "record_id", "rule_set_key", "rule_key"],
        unique=True,
        postgresql_where=sa.text(ACTIVE),
        sqlite_where=sa.text(ACTIVE),
    )
    if op.get_context().dialect.name == "postgresql":
        for statement in rls_policy_sql(OVERRIDE):
            op.execute(statement)


def downgrade() -> None:
    op.drop_table(OVERRIDE)  # drops its indexes, constraints and policies with it
    with op.batch_alter_table(FINDING) as batch:
        batch.drop_column("checked_values_hash")
