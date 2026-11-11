"""Single rule store and findings log (T-0018, ADR 0004 §1, Amendment E).

Additive: three new tables, each put under project-scoped RLS (T-0007) on PostgreSQL.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType
from app.core.tenancy.rls import rls_policy_sql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULE_SET = "oe_validation_rule_set"
RULE = "oe_validation_rule"
FINDING = "oe_validation_finding"
TABLES = (RULE_SET, RULE, FINDING)


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
    op.create_table(
        RULE_SET,
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("layer", sa.String(length=10), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_pack", sa.String(length=120), nullable=True),
        *_mixin_columns(),
        sa.CheckConstraint(
            "kind IN ('check', 'digest', 'autolink', 'notify', 'p6_grammar', 'p6_eligibility')",
            name=op.f("ck_oe_validation_rule_set_kind"),
        ),
        sa.CheckConstraint(
            "layer IN ('system', 'tenant', 'project')", name=op.f("ck_oe_validation_rule_set_layer")
        ),
        sa.CheckConstraint(
            "(layer = 'project') = (project_id IS NOT NULL)",
            name=op.f("ck_oe_validation_rule_set_layer_scope"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_oe_validation_rule_set_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oe_validation_rule_set")),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "layer",
            "key",
            name=op.f("uq_oe_validation_rule_set_key"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        RULE,
        sa.Column("rule_set_id", GUID(), nullable=False),
        sa.Column("rule_key", sa.String(length=120), nullable=False),
        sa.Column("engine", sa.String(length=10), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("params", JSONType(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("origin_rule_key", sa.String(length=120), nullable=True),
        sa.Column("origin_version", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        *_mixin_columns(),
        sa.CheckConstraint("engine IN ('cel', 'regex')", name=op.f("ck_oe_validation_rule_engine")),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error')", name=op.f("ck_oe_validation_rule_severity")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_oe_validation_rule_version_positive")),
        sa.ForeignKeyConstraint(
            ["rule_set_id"],
            [f"{RULE_SET}.id"],
            name=op.f("fk_oe_validation_rule_rule_set_id_oe_validation_rule_set"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oe_validation_rule")),
        sa.UniqueConstraint("rule_set_id", "rule_key", name=op.f("uq_oe_validation_rule_key")),
    )
    op.create_index(op.f("ix_oe_validation_rule_rule_set_id"), RULE, ["rule_set_id"])
    op.create_table(
        FINDING,
        sa.Column("run_id", GUID(), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("rule_set_id", GUID(), nullable=False),
        sa.Column("rule_set_key", sa.String(length=120), nullable=False),
        sa.Column("rule_set_version", sa.Integer(), nullable=False),
        sa.Column("rule_id", GUID(), nullable=False),
        sa.Column("rule_key", sa.String(length=120), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        *_mixin_columns(),
        sa.CheckConstraint(
            "outcome IN ('fail', 'error')", name=op.f("ck_oe_validation_finding_outcome")
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_id"],
            [f"{RULE_SET}.id"],
            name=op.f("fk_oe_validation_finding_rule_set_id_oe_validation_rule_set"),
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            [f"{RULE}.id"],
            name=op.f("fk_oe_validation_finding_rule_id_oe_validation_rule"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oe_validation_finding")),
    )
    op.create_index(op.f("ix_oe_validation_finding_run_id"), FINDING, ["run_id"])
    op.create_index(op.f("ix_oe_validation_finding_subject"), FINDING, ["subject"])
    for table in TABLES:
        op.create_index(op.f(f"ix_{table}_tenant_id"), table, ["tenant_id"])
        op.create_index(op.f(f"ix_{table}_project_id"), table, ["project_id"])
    if op.get_context().dialect.name == "postgresql":
        for table in TABLES:
            for statement in rls_policy_sql(table):
                op.execute(statement)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)  # drops the table's indexes, constraints and policies with it
