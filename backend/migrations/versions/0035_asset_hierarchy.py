"""Asset hierarchy: ``oe_asset_level_def`` and the ltree tree ``oe_asset_node`` (T-0101, ADR 0003).

Additive: two new project-scoped tables with the ``BaseRecordMixin`` columns, the ``tenants``
FK and the standard project RLS policies on PostgreSQL.

* ``CREATE EXTENSION IF NOT EXISTS ltree`` (PostgreSQL; a trusted extension, already created by
  ``infra/postgres/initdb/20-kerp-app.sql``). PostGIS must exist already, as for ``0008``.
* ``oe_asset_level_def``: unique ``(project_id, key)``.
* ``oe_asset_node``: ``tree_path ltree NOT NULL`` with a GiST index (``TEXT`` and a plain index
  off PostgreSQL), ``spatial_location geometry(PointZ,4326)``, and the partial unique index
  ``uq_oe_asset_node_code_scope`` on ``(project_id, code_scope_key, code)`` over live rows.

The downgrade drops both tables; the extension stays (other objects may use it).

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.types import UserDefinedType

from app.core.db import GUID, JSONType
from app.core.geo import GeoJSONGeometry

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEVEL = "oe_asset_level_def"
NODE = "oe_asset_node"
LIVE = "deleted_at IS NULL"

_TENANT = "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_PROJECT = "NULLIF(current_setting('app.current_project_id', true), '')::uuid"
_ALLOWED = "string_to_array(NULLIF(current_setting('app.allowed_project_ids', true), ''), ',')"
_EXACT = f"tenant_id = {_TENANT} AND project_id IS NOT DISTINCT FROM {_PROJECT}"


def _standard_rls(table: str) -> tuple[str, ...]:
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS project_isolation_policy ON {table}",
        f"CREATE POLICY project_isolation_policy ON {table} FOR ALL USING ({_EXACT})"
        f" WITH CHECK ({_EXACT})",
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
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {
    LEVEL: _standard_rls(LEVEL),
    NODE: _standard_rls(NODE),
}


class _Ltree(UserDefinedType[str]):
    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "ltree"


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
        sa.CheckConstraint("project_id IS NOT NULL", name=op.f(f"ck_{table}_project_level")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f(f"fk_{table}_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def upgrade() -> None:
    postgres = op.get_context().dialect.name == "postgresql"
    if postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS ltree")
    op.create_table(
        LEVEL,
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("allowed_parent_keys", JSONType(), nullable=False),
        sa.Column("label_key", sa.String(length=120), nullable=False),
        sa.Column("roles", JSONType(), nullable=False),
        sa.Column("code_pattern", sa.String(length=200), nullable=True),
        sa.Column("code_uniqueness", sa.String(length=16), nullable=False),
        sa.Column("field_schema_id", GUID(), nullable=True),
        sa.Column("display_code_template", sa.String(length=200), nullable=True),
        *_mixin(LEVEL),
        sa.CheckConstraint("depth >= 0", name=op.f(f"ck_{LEVEL}_depth")),
        sa.CheckConstraint(
            "code_uniqueness IN ('per_parent', 'per_level', 'per_project')",
            name=op.f(f"ck_{LEVEL}_code_uniqueness"),
        ),
    )
    for column in ("tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{LEVEL}_{column}"), LEVEL, [column])
    op.create_index("uq_oe_asset_level_def_project_key", LEVEL, ["project_id", "key"], unique=True)

    quantity = sa.Numeric(18, 4)
    op.create_table(
        NODE,
        sa.Column("level_def_id", GUID(), nullable=False),
        sa.Column("parent_id", GUID(), nullable=True),
        sa.Column("tree_path", sa.Text().with_variant(_Ltree(), "postgresql"), nullable=False),
        sa.Column("display_path", sa.Text(), nullable=False),
        sa.Column("code", sa.String(length=200), nullable=False),
        sa.Column("display_code", sa.Text(), nullable=False),
        sa.Column("code_scope_key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("discipline_code", sa.String(length=32), nullable=True),
        sa.Column("wbs_code", sa.String(length=120), nullable=True),
        sa.Column("activity_id", sa.String(length=120), nullable=True),
        sa.Column("external_source", sa.String(length=64), nullable=True),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("planned_start", sa.Date(), nullable=True),
        sa.Column("planned_finish", sa.Date(), nullable=True),
        sa.Column("spatial_location", GeoJSONGeometry("PointZ"), nullable=True),
        sa.Column("budget_qty", quantity, nullable=True),
        sa.Column("uom", sa.String(length=32), nullable=True),
        sa.Column("budget_hours", quantity, nullable=True),
        sa.Column("earned_qty", quantity, nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("superseded_by", JSONType(), nullable=False),
        sa.Column("derived_from", JSONType(), nullable=False),
        *_mixin(NODE),
        sa.CheckConstraint("status IN ('active', 'retired')", name=op.f(f"ck_{NODE}_status")),
        sa.ForeignKeyConstraint(
            ["level_def_id"], [f"{LEVEL}.id"], name=op.f(f"fk_{NODE}_level_def_id_{LEVEL}")
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], [f"{NODE}.id"], name=op.f(f"fk_{NODE}_parent_id_{NODE}")
        ),
    )
    for column in ("level_def_id", "parent_id", "tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{NODE}_{column}"), NODE, [column])
    op.create_index("ix_oe_asset_node_tree_path", NODE, ["tree_path"], postgresql_using="gist")
    op.create_index(
        "uq_oe_asset_node_code_scope",
        NODE,
        ["project_id", "code_scope_key", "code"],
        unique=True,
        postgresql_where=sa.text(LIVE),
        sqlite_where=sa.text(LIVE),
    )
    if postgres:
        for table in (LEVEL, NODE):
            for statement in FROZEN_RLS_DDL[table]:
                op.execute(statement)


def downgrade() -> None:
    # Dropping a table drops its indexes, constraints and policies with it.
    op.drop_table(NODE)
    op.drop_table(LEVEL)
