"""Teams: tenant-level teams, members and team-project links (T-0013, ADR 0008).

Additive:

* ``oe_teams_team`` (``name``, ``name_key``, ``description``): ``name_key`` is the
  case-insensitive form of the name; partial unique index ``uq_oe_teams_team_name_live`` on
  ``(tenant_id, name_key) WHERE deleted_at IS NULL``.
* ``oe_teams_member`` (``team_id`` -> team, ``user_id`` -> ``auth_user``): partial unique
  index ``uq_oe_teams_member_live`` on ``(tenant_id, team_id, user_id)`` over live rows.
* ``oe_teams_project_link`` (``team_id`` -> team, ``linked_project_id`` (no FK: the project
  belongs to the projects module), ``persona``, ``site_node_id``): CHECK persona in PER-01..
  PER-14 except PER-09, plus PER-16; CHECK ``(persona = 'PER-07') = (site_node_id IS NOT
  NULL)``; partial unique index ``uq_oe_teams_project_link_live`` on ``(tenant_id, team_id,
  linked_project_id)`` over live rows.

Every table is tenant-level (CHECK ``project_id IS NULL``), carries the ``BaseRecordMixin``
columns and the ``tenants`` foreign key (T-0079), and on PostgreSQL gets the ``team`` RLS
policies: ``team_read_policy`` (``app.is_system_admin``, or the row's team is in
``kerp_visible_team_ids()``: teams with a live link to the bound, an allowed or a member
project, or with a live membership of ``app.current_user_id``; a link row also by its own
project, a member row also by its own user) and ``team_write_policy`` (exact tenant-level
scope **and** ``app.is_system_admin`` for every insert, update and delete).

``kerp_visible_team_ids()`` is an invoker-rights function (no ``SECURITY DEFINER``: it reads
only what the caller's policies show). It raises ``app.team_lookup`` transaction-locally for
its own reads, under which the read policies skip the function call (``CASE``), so the policy
never recurses; the flag only narrows what a session sees. The caller's value is restored.
The DDL is inlined (frozen at this revision; ``app.core.tenancy.rls`` emits the same).

The downgrade drops the tables and the function. Team-derived membership rows
(``auth_project_membership.source_type = 'team_link'``) are core's and are left alone; the
startup check (T-0073) refuses to run without the teams module while live ones exist.

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.schema import SchemaItem

from app.core.db import GUID, JSONType

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEAM = "oe_teams_team"
MEMBER = "oe_teams_member"
LINK = "oe_teams_project_link"
FUNCTION = "kerp_visible_team_ids"
LOOKUP = "app.team_lookup"
LIVE = "deleted_at IS NULL"
LINK_PERSONAS = tuple(f"PER-{n:02d}" for n in range(1, 17) if n not in (9, 15))

_TENANT = "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_PROJECT = "NULLIF(current_setting('app.current_project_id', true), '')::uuid"
_USER = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_ADMIN = "COALESCE(NULLIF(current_setting('app.is_system_admin', true), '')::boolean, false)"
_LOOKUP = f"COALESCE(NULLIF(current_setting('{LOOKUP}', true), '')::boolean, false)"
_EXACT = f"tenant_id = {_TENANT} AND project_id IS NOT DISTINCT FROM {_PROJECT}"


def _ids(setting: str) -> str:
    return f"string_to_array(NULLIF(current_setting('{setting}', true), ''), ',')::uuid[]"


def _described(column: str) -> list[str]:
    return [
        f"{column} = {_PROJECT}",
        f"{column} = ANY({_ids('app.allowed_project_ids')})",
        f"{column} = ANY({_ids('app.member_project_ids')})",
    ]


_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS uuid[]
LANGUAGE plpgsql VOLATILE SET search_path = public, pg_temp AS $fn$
DECLARE
    prior text := current_setting('{LOOKUP}', true);
    found uuid[];
BEGIN
    PERFORM set_config('{LOOKUP}', 'true', true);
    SELECT COALESCE(array_agg(DISTINCT v.team_id), '{{}}') INTO found FROM (
        SELECT l.team_id FROM {LINK} l
            WHERE l.tenant_id = {_TENANT} AND l.deleted_at IS NULL
            AND ({" OR ".join(_described("l.linked_project_id"))})
        UNION
        SELECT m.team_id FROM {MEMBER} m
            WHERE m.tenant_id = {_TENANT} AND m.deleted_at IS NULL
            AND m.user_id = {_USER}
    ) v;
    PERFORM set_config('{LOOKUP}', COALESCE(prior, ''), true);
    RETURN found;
END
$fn$
"""


def _policies(table: str, team_column: str) -> list[str]:
    clauses = [_ADMIN]
    if table == LINK:
        clauses += _described("linked_project_id")
    if table == MEMBER:
        clauses.append(f"user_id = {_USER}")
    clauses.append(
        f"CASE WHEN {_LOOKUP} THEN false ELSE {team_column} IN (SELECT unnest({FUNCTION}())) END"
    )
    visible = f"tenant_id = {_TENANT} AND ({' OR '.join(clauses)})"
    admin = f"{_EXACT} AND {_ADMIN}"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY team_read_policy ON {table} FOR SELECT USING ({visible})",
        f"CREATE POLICY team_write_policy ON {table} FOR ALL USING ({admin}) WITH CHECK ({admin})",
    ]


# The RLS DDL this revision applies, per table (TD-060, T-0033): frozen here, replayed by
# tests/test_migration_frozen_ddl.py against the live helpers.
FROZEN_RLS_DDL: dict[str, tuple[str, ...]] = {
    TEAM: (_FUNCTION_SQL, *_policies(TEAM, "id")),
    MEMBER: tuple(_policies(MEMBER, "team_id")),
    LINK: tuple(_policies(LINK, "team_id")),
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


def _record_constraints(table: str) -> list[SchemaItem]:
    return [
        sa.CheckConstraint("project_id IS NULL", name=op.f(f"ck_{table}_tenant_level")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f(f"fk_{table}_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def _team_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["team_id"], [f"{TEAM}.id"], name=op.f(f"fk_{table}_team_id_{TEAM}")
    )


def _indexes(table: str, *columns: str) -> None:
    for column in ("tenant_id", "project_id", *columns):
        op.create_index(op.f(f"ix_{table}_{column}"), table, [column])


def _live_unique(name: str, table: str, *columns: str) -> None:
    op.create_index(
        name,
        table,
        list(columns),
        unique=True,
        postgresql_where=sa.text(LIVE),
        sqlite_where=sa.text(LIVE),
    )


def upgrade() -> None:
    op.create_table(
        TEAM,
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("name_key", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        *_record_columns(),
        *_record_constraints(TEAM),
    )
    _indexes(TEAM)
    _live_unique("uq_oe_teams_team_name_live", TEAM, "tenant_id", "name_key")

    op.create_table(
        MEMBER,
        sa.Column("team_id", GUID(), nullable=False),
        sa.Column("user_id", GUID(), nullable=False),
        *_record_columns(),
        *_record_constraints(MEMBER),
        _team_fk(MEMBER),
        sa.ForeignKeyConstraint(
            ["user_id"], ["auth_user.id"], name=op.f(f"fk_{MEMBER}_user_id_auth_user")
        ),
    )
    _indexes(MEMBER, "team_id", "user_id")
    _live_unique("uq_oe_teams_member_live", MEMBER, "tenant_id", "team_id", "user_id")

    personas = ", ".join(f"'{p}'" for p in LINK_PERSONAS)
    op.create_table(
        LINK,
        sa.Column("team_id", GUID(), nullable=False),
        sa.Column("linked_project_id", GUID(), nullable=False),
        sa.Column("persona", sa.String(length=6), nullable=False),
        sa.Column("site_node_id", GUID(), nullable=True),
        *_record_columns(),
        *_record_constraints(LINK),
        sa.CheckConstraint(f"persona IN ({personas})", name=op.f(f"ck_{LINK}_persona")),
        sa.CheckConstraint(
            "(persona = 'PER-07') = (site_node_id IS NOT NULL)", name=op.f(f"ck_{LINK}_site")
        ),
        _team_fk(LINK),
    )
    _indexes(LINK, "team_id", "linked_project_id")
    _live_unique("uq_oe_teams_project_link_live", LINK, "tenant_id", "team_id", "linked_project_id")

    if op.get_context().dialect.name != "postgresql":
        return
    for table in (TEAM, MEMBER, LINK):  # the lookup function first, with the team table
        for statement in FROZEN_RLS_DDL[table]:
            op.execute(statement)


def downgrade() -> None:
    for table in (LINK, MEMBER, TEAM):
        op.drop_table(table)
    if op.get_context().dialect.name == "postgresql":
        op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
