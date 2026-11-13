"""Project memberships + explicit multi-project read scope (T-0010, TD-053).

Additive: one new tenant-level table (``auth_project_membership``) under project-scoped RLS,
and a third, read-only policy ``allowed_projects_read_policy`` on every RLS table: rows of
the projects listed in ``app.allowed_project_ids`` become readable. The RLS DDL is inlined
(frozen at this revision) so later changes to ``app.core.tenancy.rls`` do not rewrite it.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "auth_project_membership"
PERSONAS = ", ".join(f"'PER-{n:02d}'" for n in range(1, 16))

_TENANT = "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_PROJECT = "NULLIF(current_setting('app.current_project_id', true), '')::uuid"
_ALLOWED = (
    "string_to_array(NULLIF(current_setting('app.allowed_project_ids', true), ''), ',')::uuid[]"
)
_EXACT = f"tenant_id = {_TENANT} AND project_id IS NOT DISTINCT FROM {_PROJECT}"
_ALLOWED_ROW = f"tenant_id = {_TENANT} AND project_id = ANY({_ALLOWED})"


def _for_every_rls_table(*statements: str) -> str:
    """A DO block running ``statements`` (``%I`` = table) on every table that carries the
    T-0007 project policy, whatever revision created it. Works in offline SQL mode too."""
    body = " ".join(f"EXECUTE format($s${s}$s$, t);" for s in statements)
    return (
        "DO $do$ DECLARE t text; BEGIN"
        " FOR t IN SELECT c.relname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid"
        " JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = current_schema() AND p.polname = 'project_isolation_policy'"
        f" LOOP {body} END LOOP; END $do$"
    )


_DROP_ALLOWED = "DROP POLICY IF EXISTS allowed_projects_read_policy ON %I"
_CREATE_ALLOWED = (
    f"CREATE POLICY allowed_projects_read_policy ON %I FOR SELECT USING ({_ALLOWED_ROW})"
)


def _membership_rls_sql() -> list[str]:
    return [
        f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS project_isolation_policy ON {TABLE}",
        f"CREATE POLICY project_isolation_policy ON {TABLE} FOR ALL "
        f"USING ({_EXACT}) WITH CHECK ({_EXACT})",
        f"DROP POLICY IF EXISTS tenant_level_read_policy ON {TABLE}",
        f"CREATE POLICY tenant_level_read_policy ON {TABLE} FOR SELECT "
        f"USING (tenant_id = {_TENANT} AND project_id IS NULL)",
    ]


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("member_project_id", GUID(), nullable=False),
        sa.Column("persona", sa.String(length=6), nullable=False),
        sa.Column("site_node_id", GUID(), nullable=True),
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
            "project_id IS NULL", name=op.f("ck_auth_project_membership_tenant_level")
        ),
        sa.CheckConstraint(
            f"persona IN ({PERSONAS})", name=op.f("ck_auth_project_membership_persona")
        ),
        sa.CheckConstraint(
            "persona <> 'PER-07' OR site_node_id IS NOT NULL",
            name=op.f("ck_auth_project_membership_inspector_site"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["auth_user.id"], name=op.f("fk_auth_project_membership_user_id_auth_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_project_membership")),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "member_project_id",
            "persona",
            "site_node_id",
            name=op.f("uq_auth_project_membership_grant"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(op.f("ix_auth_project_membership_tenant_id"), TABLE, ["tenant_id"])
    op.create_index(op.f("ix_auth_project_membership_project_id"), TABLE, ["project_id"])
    op.create_index(op.f("ix_auth_project_membership_user_id"), TABLE, ["user_id"])
    op.create_index(
        op.f("ix_auth_project_membership_member_project_id"), TABLE, ["member_project_id"]
    )
    if op.get_context().dialect.name != "postgresql":
        return
    for statement in _membership_rls_sql():
        op.execute(statement)
    op.execute(_for_every_rls_table(_DROP_ALLOWED, _CREATE_ALLOWED))


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute(_for_every_rls_table(_DROP_ALLOWED))
    op.drop_index(op.f("ix_auth_project_membership_member_project_id"), table_name=TABLE)
    op.drop_index(op.f("ix_auth_project_membership_user_id"), table_name=TABLE)
    op.drop_index(op.f("ix_auth_project_membership_project_id"), table_name=TABLE)
    op.drop_index(op.f("ix_auth_project_membership_tenant_id"), table_name=TABLE)
    op.drop_table(TABLE)
