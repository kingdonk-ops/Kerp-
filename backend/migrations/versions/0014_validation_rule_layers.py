"""Rule store layers, labels and the two-person unlock (T-0034, ADR 0004 §2, §3, §5, Amd. B).

Additive: nullable columns on ``oe_validation_rule_set`` (``module``, ``seed_version``) and
``oe_validation_rule`` (``label``, ``unlock_adr_reference``), and the new table
``oe_validation_unlock_request`` under project-scoped RLS (T-0007) on PostgreSQL.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType
from app.core.tenancy.rls import rls_policy_sql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULE_SET = "oe_validation_rule_set"
RULE = "oe_validation_rule"
UNLOCK = "oe_validation_unlock_request"


def _mixin_columns() -> list[sa.Column[object]]:
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
    op.add_column(RULE_SET, sa.Column("module", sa.String(length=40), nullable=True))
    op.add_column(RULE_SET, sa.Column("seed_version", sa.Integer(), nullable=True))
    op.add_column(RULE, sa.Column("label", sa.String(length=200), nullable=True))
    op.add_column(RULE, sa.Column("unlock_adr_reference", sa.String(length=200), nullable=True))
    op.create_table(
        UNLOCK,
        sa.Column("rule_set_id", GUID(), nullable=False),
        sa.Column("rule_id", GUID(), nullable=False),
        sa.Column("rule_key", sa.String(length=120), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("adr_reference", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("requested_by", GUID(), nullable=False),
        sa.Column("decided_by", GUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        *_mixin_columns(),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name=op.f(f"ck_{UNLOCK}_status"),
        ),
        sa.CheckConstraint("project_id IS NULL", name=op.f(f"ck_{UNLOCK}_tenant_level")),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_by IS NULL AND decided_at IS NULL)",
            name=op.f(f"ck_{UNLOCK}_decision"),
        ),
        sa.CheckConstraint(
            "status <> 'approved' OR decided_by <> requested_by",
            name=op.f(f"ck_{UNLOCK}_two_people"),
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_id"], [f"{RULE_SET}.id"], name=op.f(f"fk_{UNLOCK}_rule_set_id_{RULE_SET}")
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"], [f"{RULE}.id"], name=op.f(f"fk_{UNLOCK}_rule_id_{RULE}")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{UNLOCK}")),
    )
    for column in ("rule_set_id", "rule_id", "tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{UNLOCK}_{column}"), UNLOCK, [column])
    if op.get_context().dialect.name == "postgresql":
        for statement in rls_policy_sql(UNLOCK):
            op.execute(statement)


def downgrade() -> None:
    op.drop_table(UNLOCK)  # drops its indexes, constraints and policies with it
    with op.batch_alter_table(RULE) as batch:
        batch.drop_column("unlock_adr_reference")
        batch.drop_column("label")
    with op.batch_alter_table(RULE_SET) as batch:
        batch.drop_column("seed_version")
        batch.drop_column("module")
