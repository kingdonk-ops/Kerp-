"""Saved view teams: a ``team`` view is shared with several teams (T-0074, ADR 0008).

Additive:

* New project-scoped table ``oe_saved_view_team`` (``saved_view_id`` -> ``oe_saved_view``,
  ``team_id``: no FK, the team belongs to the teams module), ``BaseRecordMixin`` columns, the
  ``tenants`` FK, partial unique index ``uq_oe_saved_view_team_live`` on ``(saved_view_id,
  team_id)`` over live rows, and the standard project RLS policy on PostgreSQL.
* Every ``oe_saved_view.team_id`` is copied into it (one link row per view that has one).
* The CHECK ``ck_oe_saved_view_team_matches_visibility`` is relaxed from
  ``(visibility = 'team') = (team_id IS NOT NULL)`` to ``team_id IS NULL OR visibility =
  'team'``, so new team views keep ``team_id`` NULL. The column stays, nullable and unused
  (deprecated); dropping it is destructive and needs an ADR (TD-381).

On PostgreSQL ``oe_saved_view``'s row-level security is forced, so a migration role that is
not a superuser would see no rows: the copy runs with it lifted inside the migration's
transaction (``NO FORCE`` ... ``FORCE``, as in ``0012``).

The downgrade writes one live team of each team view back into ``team_id`` (the lowest id) and
turns a team view left without any team ``private`` (narrower, never wider; logged), drops
the link table and restores the strict CHECK.

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-19
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType
from app.core.tenancy.rls import rls_policy_sql

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEW = "oe_saved_view"
LINK = "oe_saved_view_team"
LINK_INDEX = "uq_oe_saved_view_team_live"
LIVE = "deleted_at IS NULL"
CHECK = "team_matches_visibility"  # ck_oe_saved_view_team_matches_visibility
STRICT = "(visibility = 'team') = (team_id IS NOT NULL)"
RELAXED = "team_id IS NULL OR visibility = 'team'"

log = logging.getLogger("alembic.runtime.migration")

_SQLITE_UUID = (
    "lower(hex(randomblob(4)) || '-' || hex(randomblob(2)) || '-4' || substr(hex(randomblob(2)),"
    " 2) || '-' || substr('89ab', 1 + (abs(random()) % 4), 1) || substr(hex(randomblob(2)), 2)"
    " || '-' || hex(randomblob(6)))"
)


def _new_id() -> str:
    return "gen_random_uuid()" if op.get_context().dialect.name == "postgresql" else _SQLITE_UUID


def _frozen_view(team_check: str) -> sa.Table:
    """``oe_saved_view`` as of this revision with ``team_check`` as the team CHECK.

    SQLite cannot alter a CHECK, so batch mode copies the table; handing it this frozen
    definition skips reflection (offline ``--sql`` works) and keeps later model changes out.
    """
    name = f"ck_{VIEW}"
    return sa.Table(
        VIEW,
        sa.MetaData(),
        sa.Column("register_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("owner_user_id", GUID(), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("team_id", GUID(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("columns", JSONType(), nullable=False),
        sa.Column("filter", JSONType(), nullable=True),
        sa.Column("sort", JSONType(), nullable=False),
        sa.Column("group_by", JSONType(), nullable=True),
        sa.Column("conditional_format", JSONType(), nullable=False),
        sa.Column("density", sa.String(length=16), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
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
        sa.CheckConstraint("project_id IS NOT NULL", name=f"{name}_project_level"),
        sa.CheckConstraint(
            "visibility IN ('private', 'team', 'project', 'portal')", name=f"{name}_visibility"
        ),
        sa.CheckConstraint(
            "density IN ('compact', 'standard', 'comfortable')", name=f"{name}_density"
        ),
        sa.CheckConstraint(team_check, name=f"{name}_{CHECK}"),
        sa.CheckConstraint(
            "NOT is_default OR visibility IN ('private', 'project')", name=f"{name}_default_kind"
        ),
        sa.CheckConstraint("schema_version >= 1", name=f"{name}_schema_version"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=f"fk_{VIEW}_tenant_id_tenants"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{VIEW}"),
        *(
            sa.Index(f"ix_{VIEW}_{column}", column)
            for column in ("owner_user_id", "tenant_id", "project_id")
        ),
        sa.Index("ix_oe_saved_view_project_register", "project_id", "register_key"),
    )


def _replace_check(old: str, new: str) -> None:
    constraint = op.f(f"ck_{VIEW}_{CHECK}")
    if op.get_context().dialect.name == "postgresql":
        op.drop_constraint(constraint, VIEW, type_="check")
        op.create_check_constraint(constraint, VIEW, new)
        return
    with op.batch_alter_table(VIEW, copy_from=_frozen_view(old), recreate="always") as batch:
        batch.drop_constraint(constraint, type_="check")
        batch.create_check_constraint(constraint, sa.text(new))


def _lift_rls(table: str, lifted: bool) -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute(f"ALTER TABLE {table} {'NO FORCE' if lifted else 'FORCE'} ROW LEVEL SECURITY")


def upgrade() -> None:
    # First, while nothing references ``oe_saved_view`` yet (SQLite rebuilds the table).
    _replace_check(STRICT, RELAXED)
    op.create_table(
        LINK,
        sa.Column("saved_view_id", GUID(), nullable=False),
        sa.Column("team_id", GUID(), nullable=False),
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
        sa.CheckConstraint("project_id IS NOT NULL", name=op.f(f"ck_{LINK}_project_level")),
        sa.ForeignKeyConstraint(
            ["saved_view_id"], [f"{VIEW}.id"], name=op.f(f"fk_{LINK}_saved_view_id_{VIEW}")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f(f"fk_{LINK}_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{LINK}")),
    )
    for column in ("saved_view_id", "team_id", "tenant_id", "project_id"):
        op.create_index(op.f(f"ix_{LINK}_{column}"), LINK, [column])
    op.create_index(
        LINK_INDEX,
        LINK,
        ["saved_view_id", "team_id"],
        unique=True,
        postgresql_where=sa.text(LIVE),
        sqlite_where=sa.text(LIVE),
    )
    _lift_rls(VIEW, True)
    op.execute(
        f"INSERT INTO {LINK} (id, tenant_id, project_id, saved_view_id, team_id, created_at,"
        " updated_at, deleted_at, custom_fields, created_by, updated_by, source, revision)"
        f" SELECT {_new_id()}, tenant_id, project_id, id, team_id, created_at, updated_at, NULL,"
        f" '{{}}', created_by, updated_by, 'system', 1 FROM {VIEW} WHERE team_id IS NOT NULL"
    )
    _lift_rls(VIEW, False)
    if op.get_context().dialect.name == "postgresql":
        for statement in rls_policy_sql(LINK):
            op.execute(statement)


def downgrade() -> None:
    context = op.get_context()
    _lift_rls(VIEW, True)
    _lift_rls(LINK, True)
    first_team = (
        f"(SELECT l.team_id FROM {LINK} l WHERE l.saved_view_id = {VIEW}.id AND l.{LIVE}"
        " ORDER BY l.team_id LIMIT 1)"
    )
    op.execute(
        f"UPDATE {VIEW} SET team_id = {first_team} WHERE visibility = 'team' AND team_id IS NULL"
    )
    orphaned = (
        f"UPDATE {VIEW} SET visibility = 'private' WHERE visibility = 'team' AND team_id IS NULL"
    )
    if context.as_sql:
        op.execute(orphaned)
    else:
        made_private = op.get_bind().execute(sa.text(orphaned)).rowcount
        if made_private:
            log.warning("0032 downgrade: %d team view(s) without a team made private", made_private)
    _lift_rls(VIEW, False)
    op.drop_table(LINK)  # drops its indexes, constraints and policies with it
    _replace_check(RELAXED, STRICT)
