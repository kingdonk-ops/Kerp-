"""Admin grants table + membership-based RLS on projects and memberships (T-0014, TD-053).

* New tenant-level table ``oe_admin_grant`` (``system_admin`` | ``create_projects`` per user)
  with its own policy: a user reads their own rows (``app.current_user_id``); an
  ``app.is_system_admin`` session reads and writes all; nobody else writes.
* ``kerp_caller_admin_grants()``: ``SECURITY DEFINER`` function returning the session user's
  own live grant kinds, used at authentication to decide ``app.is_system_admin`` (edge spec
  §6 amendments). Under ``FORCE`` RLS the owner is still subject to the own-rows policy.
* ``oe_projects_project`` and ``auth_project_membership`` lose the standard project policies
  and get the membership-based ones: readable when the described project is the bound one, in
  ``app.allowed_project_ids`` or ``app.member_project_ids``, or the session is a System admin
  (memberships also to their own user). Inserts keep the exact scope; updates and deletes
  need the exact scope and visibility.

Additive apart from the policy swap, which is the core-table change TD-053 decided (edge
spec §6 amendments). The RLS DDL is inlined (frozen at this revision). The downgrade restores
the standard policies of ``0006``/``0008`` and drops the table and function.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "oe_admin_grant"
FUNCTION = "kerp_caller_admin_grants"
# (table, column naming the described project, owner column or None)
MEMBERSHIP_TABLES = (
    ("oe_projects_project", "id", None),
    ("auth_project_membership", "member_project_id", "user_id"),
)

_TENANT = "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_PROJECT = "NULLIF(current_setting('app.current_project_id', true), '')::uuid"
_USER = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_ADMIN = "COALESCE(NULLIF(current_setting('app.is_system_admin', true), '')::boolean, false)"
_EXACT = f"tenant_id = {_TENANT} AND project_id IS NOT DISTINCT FROM {_PROJECT}"


def _ids(setting: str) -> str:
    return f"string_to_array(NULLIF(current_setting('{setting}', true), ''), ',')::uuid[]"


_STANDARD = ("project_isolation_policy", "tenant_level_read_policy", "allowed_projects_read_policy")
_MEMBERSHIP = (
    "membership_read_policy",
    "membership_insert_policy",
    "membership_update_policy",
    "membership_delete_policy",
)


def _drop(table: str, names: Sequence[str]) -> list[str]:
    return [f"DROP POLICY IF EXISTS {name} ON {table}" for name in names]


def _membership_sql(table: str, column: str, owner: str | None) -> list[str]:
    clauses = [
        f"{column} = {_PROJECT}",
        f"{column} = ANY({_ids('app.allowed_project_ids')})",
        f"{column} = ANY({_ids('app.member_project_ids')})",
        _ADMIN,
    ]
    if owner is not None:
        clauses.append(f"{owner} = {_USER}")
    visible = f"tenant_id = {_TENANT} AND ({' OR '.join(clauses)})"
    guarded = f"{_EXACT} AND {visible}"
    read, insert, update, delete = _MEMBERSHIP
    return [
        *_drop(table, (*_STANDARD, *_MEMBERSHIP)),
        f"CREATE POLICY {read} ON {table} FOR SELECT USING ({visible})",
        f"CREATE POLICY {insert} ON {table} FOR INSERT WITH CHECK ({_EXACT})",
        f"CREATE POLICY {update} ON {table} FOR UPDATE USING ({guarded}) WITH CHECK ({guarded})",
        f"CREATE POLICY {delete} ON {table} FOR DELETE USING ({guarded})",
    ]


def _standard_sql(table: str) -> list[str]:
    """The policies of ``0006``/``0008`` (T-0007, T-0010), restored on downgrade."""
    allowed = f"tenant_id = {_TENANT} AND project_id = ANY({_ids('app.allowed_project_ids')})"
    return [
        *_drop(table, (*_MEMBERSHIP, *_STANDARD)),
        f"CREATE POLICY project_isolation_policy ON {table} FOR ALL "
        f"USING ({_EXACT}) WITH CHECK ({_EXACT})",
        f"CREATE POLICY tenant_level_read_policy ON {table} FOR SELECT "
        f"USING (tenant_id = {_TENANT} AND project_id IS NULL)",
        f"CREATE POLICY allowed_projects_read_policy ON {table} FOR SELECT USING ({allowed})",
    ]


def _admin_grant_sql() -> list[str]:
    admin = f"{_EXACT} AND {_ADMIN}"
    return [
        f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY",
        *_drop(TABLE, (*_STANDARD, "admin_grant_read_policy", "admin_grant_write_policy")),
        f"CREATE POLICY admin_grant_read_policy ON {TABLE} FOR SELECT"
        f" USING (tenant_id = {_TENANT} AND (user_id = {_USER} OR {_ADMIN}))",
        f"CREATE POLICY admin_grant_write_policy ON {TABLE} FOR ALL"
        f" USING ({admin}) WITH CHECK ({admin})",
    ]


# Only the session user's own live grants of the bound tenant. SECURITY DEFINER with a fixed
# search_path; it takes no argument, so it can never be asked about another user.
_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS TABLE (kind text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
    SELECT g.kind::text FROM {TABLE} g
    WHERE g.tenant_id = {_TENANT} AND g.user_id = {_USER} AND g.deleted_at IS NULL
$fn$
"""


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
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
        sa.CheckConstraint("project_id IS NULL", name=op.f("ck_oe_admin_grant_tenant_level")),
        sa.CheckConstraint(
            "kind IN ('system_admin', 'create_projects')", name=op.f("ck_oe_admin_grant_kind")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["auth_user.id"], name=op.f("fk_oe_admin_grant_user_id_auth_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oe_admin_grant")),
    )
    for column in ("tenant_id", "project_id", "user_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])
    op.create_index(
        "uq_oe_admin_grant_live",
        TABLE,
        ["tenant_id", "user_id", "kind"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    if op.get_context().dialect.name != "postgresql":
        return
    for statement in _admin_grant_sql():
        op.execute(statement)
    op.execute(_FUNCTION_SQL)
    for table, column, owner in MEMBERSHIP_TABLES:
        for statement in _membership_sql(table, column, owner):
            op.execute(statement)


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        for table, _column, _owner in MEMBERSHIP_TABLES:
            for statement in _standard_sql(table):
                op.execute(statement)
        op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
    op.drop_table(TABLE)  # drops the table's indexes, constraints and policies with it
