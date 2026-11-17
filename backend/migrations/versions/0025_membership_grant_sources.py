"""Membership rows keyed by source: team-derived project grants (T-0073, ADR 0008).

Core-table change covered by ADR 0008 (CLAUDE.md §6). On ``auth_project_membership``:

* ``source_type`` (NOT NULL, server default ``'direct'``, CHECK in ``direct``, ``team_link``)
  and ``source_id`` (UUID, nullable, no FK: the link belongs to the teams module; indexed),
  with CHECK ``(source_type = 'direct') = (source_id IS NULL)``. Every existing row becomes
  ``direct``.
* ``uq_auth_project_membership_grant`` is replaced by ``uq_auth_project_membership_source_grant``
  on ``(tenant_id, member_project_id, user_id, source_type, source_id, persona, site_node_id)``
  with ``NULLS NOT DISTINCT`` (PostgreSQL 16): one row per grant and source, soft-deleted rows
  included, so a re-grant revives its row.
* Partial unique index ``uq_auth_project_membership_team_link_live`` on ``(tenant_id,
  member_project_id, user_id, source_id) WHERE source_type = 'team_link' AND deleted_at IS
  NULL``: one live persona per user and team link.
* PostgreSQL: the membership write policies of ``0015`` gain ``(source_type = 'direct' OR
  app.is_system_admin)``, so only a System admin session inserts, updates or deletes derived
  rows. ``kerp_live_team_link_grant_count()`` (``SECURITY DEFINER``, pinned ``search_path``)
  returns only the number of live ``team_link`` rows across **all tenants**, for the startup
  check that runs before any tenant is bound. In CI and local dev the definer is the app role,
  which ``FORCE ROW LEVEL SECURITY`` subjects to the policies (TD-056), so the function raises
  ``app.team_link_census`` transaction-locally for its own count, which the extra read policy
  ``membership_team_link_census_policy`` honours, and restores the caller's value before it
  returns (like the lobby functions of ``0021``). ``EXECUTE`` is revoked from ``PUBLIC`` and
  granted to ``CURRENT_USER`` and, where it exists, to ``kerp_app``.

The upgrade is additive. **The downgrade is destructive**: it deletes every ``team_link`` row
(live or revoked) first, because a user may hold the same persona directly and through a team
and the old key would reject that, then restores the old constraint and policies and drops the
columns, the index and the function.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-17
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db import GUID, JSONType

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "auth_project_membership"
OLD_KEY = f"uq_{TABLE}_grant"
NEW_KEY = f"uq_{TABLE}_source_grant"
LIVE_INDEX = f"uq_{TABLE}_team_link_live"
SOURCE_INDEX = f"ix_{TABLE}_source_id"
FUNCTION = "kerp_live_team_link_grant_count"
CENSUS_POLICY = "membership_team_link_census_policy"
CENSUS_SETTING = "app.team_link_census"
RUNTIME_ROLE = "kerp_app"
OLD_KEY_COLUMNS = ("tenant_id", "user_id", "member_project_id", "persona", "site_node_id")
NEW_KEY_COLUMNS = (
    "tenant_id",
    "member_project_id",
    "user_id",
    "source_type",
    "source_id",
    "persona",
    "site_node_id",
)
LIVE_COLUMNS = ("tenant_id", "member_project_id", "user_id", "source_id")
SOURCE_TYPE_CHECK = "source_type IN ('direct', 'team_link')"
SOURCE_ID_CHECK = "(source_type = 'direct') = (source_id IS NULL)"
_LIVE_TEAM_LINK = "source_type = 'team_link' AND deleted_at IS NULL"

log = logging.getLogger("alembic.runtime.migration")

_TENANT = "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_PROJECT = "NULLIF(current_setting('app.current_project_id', true), '')::uuid"
_USER = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_ADMIN = "COALESCE(NULLIF(current_setting('app.is_system_admin', true), '')::boolean, false)"
_CENSUS = f"COALESCE(NULLIF(current_setting('{CENSUS_SETTING}', true), '')::boolean, false)"
_EXACT = f"tenant_id = {_TENANT} AND project_id IS NOT DISTINCT FROM {_PROJECT}"


def _ids(setting: str) -> str:
    return f"string_to_array(NULLIF(current_setting('{setting}', true), ''), ',')::uuid[]"


_VISIBLE = (
    f"tenant_id = {_TENANT} AND (member_project_id = {_PROJECT}"
    f" OR member_project_id = ANY({_ids('app.allowed_project_ids')})"
    f" OR member_project_id = ANY({_ids('app.member_project_ids')})"
    f" OR {_ADMIN} OR user_id = {_USER})"
)
_WRITABLE = f"(source_type = 'direct' OR {_ADMIN})"


def _policies(*, sources: bool) -> list[str]:
    """The membership policies of ``0015``; with ``sources`` the derived-row write rule and
    the census read policy of this revision."""
    guarded, insertable = f"{_EXACT} AND {_VISIBLE}", _EXACT
    if sources:
        guarded, insertable = f"{guarded} AND {_WRITABLE}", f"{insertable} AND {_WRITABLE}"
    statements = [
        f"DROP POLICY IF EXISTS {name} ON {TABLE}"
        for name in (
            "membership_insert_policy",
            "membership_update_policy",
            "membership_delete_policy",
            CENSUS_POLICY,
        )
    ]
    statements += [
        f"CREATE POLICY membership_insert_policy ON {TABLE} FOR INSERT WITH CHECK ({insertable})",
        f"CREATE POLICY membership_update_policy ON {TABLE} FOR UPDATE USING ({guarded})"
        f" WITH CHECK ({guarded})",
        f"CREATE POLICY membership_delete_policy ON {TABLE} FOR DELETE USING ({guarded})",
    ]
    if sources:
        statements.append(
            f"CREATE POLICY {CENSUS_POLICY} ON {TABLE} FOR SELECT"
            f" USING ({_CENSUS} AND source_type <> 'direct' AND deleted_at IS NULL)"
        )
    return statements


# Only a count, never row data, across every tenant: the startup check runs before any tenant
# is bound. The census setting is raised for this read only and the caller's value restored.
_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE
    prior text := current_setting('{CENSUS_SETTING}', true);
    found bigint;
BEGIN
    PERFORM set_config('{CENSUS_SETTING}', 'true', true);
    SELECT count(*) INTO found FROM {TABLE} m
        WHERE m.source_type = 'team_link' AND m.deleted_at IS NULL;
    PERFORM set_config('{CENSUS_SETTING}', COALESCE(prior, ''), true);
    RETURN found;
END
$fn$
"""
_FUNCTION_PRIVILEGES = (
    f"REVOKE EXECUTE ON FUNCTION {FUNCTION}() FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION {FUNCTION}() TO CURRENT_USER",
    f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN"
    f" EXECUTE 'GRANT EXECUTE ON FUNCTION {FUNCTION}() TO {RUNTIME_ROLE}'; END IF; END $do$",
)


def _frozen_table(*, sources: bool) -> sa.Table:
    """The table before (``sources=False``, as of ``0012``) or after this revision.

    SQLite cannot alter constraints, so batch mode copies the table; handing it this frozen
    definition skips reflection and keeps later model changes out. SQLite only, so the
    PostgreSQL-only ``NULLS NOT DISTINCT`` is omitted.
    """
    name = f"ck_{TABLE}"
    personas = ", ".join(f"'PER-{n:02d}'" for n in range(1, 17))
    columns: list[sa.Column[object]] = [
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
    ]
    extra: list[sa.SchemaItem] = [sa.UniqueConstraint(*OLD_KEY_COLUMNS, name=OLD_KEY)]
    if sources:
        columns += [
            sa.Column("source_type", sa.String(length=16), nullable=False, server_default="direct"),
            sa.Column("source_id", GUID(), nullable=True),
        ]
        extra = [
            sa.UniqueConstraint(*NEW_KEY_COLUMNS, name=NEW_KEY),
            sa.CheckConstraint(SOURCE_TYPE_CHECK, name=f"{name}_source_type"),
            sa.CheckConstraint(SOURCE_ID_CHECK, name=f"{name}_source_id"),
        ]
    return sa.Table(
        TABLE,
        sa.MetaData(),
        *columns,
        sa.CheckConstraint("project_id IS NULL", name=f"{name}_tenant_level"),
        sa.CheckConstraint(f"persona IN ({personas})", name=f"{name}_persona"),
        sa.CheckConstraint(
            "persona <> 'PER-07' OR site_node_id IS NOT NULL", name=f"{name}_inspector_site"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["auth_user.id"], name=f"fk_{TABLE}_user_id_auth_user"
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{TABLE}"),
        *extra,
        *(
            sa.Index(f"ix_{TABLE}_{column}", column)
            for column in ("tenant_id", "project_id", "user_id", "member_project_id")
        ),
    )


def upgrade() -> None:
    postgres = op.get_context().dialect.name == "postgresql"
    if postgres:
        op.add_column(
            TABLE,
            sa.Column("source_type", sa.String(length=16), nullable=False, server_default="direct"),
        )
        op.add_column(TABLE, sa.Column("source_id", GUID(), nullable=True))
        op.create_check_constraint(op.f(f"ck_{TABLE}_source_type"), TABLE, SOURCE_TYPE_CHECK)
        op.create_check_constraint(op.f(f"ck_{TABLE}_source_id"), TABLE, SOURCE_ID_CHECK)
        op.drop_constraint(OLD_KEY, TABLE, type_="unique")
        op.create_unique_constraint(
            NEW_KEY, TABLE, list(NEW_KEY_COLUMNS), postgresql_nulls_not_distinct=True
        )
    else:
        frozen = _frozen_table(sources=False)
        with op.batch_alter_table(TABLE, copy_from=frozen, recreate="always") as batch:
            batch.add_column(
                sa.Column(
                    "source_type", sa.String(length=16), nullable=False, server_default="direct"
                )
            )
            batch.add_column(sa.Column("source_id", GUID(), nullable=True))
            batch.create_check_constraint(
                op.f(f"ck_{TABLE}_source_type"), sa.text(SOURCE_TYPE_CHECK)
            )
            batch.create_check_constraint(op.f(f"ck_{TABLE}_source_id"), sa.text(SOURCE_ID_CHECK))
            batch.drop_constraint(OLD_KEY, type_="unique")
            batch.create_unique_constraint(NEW_KEY, list(NEW_KEY_COLUMNS))
    op.create_index(SOURCE_INDEX, TABLE, ["source_id"])
    op.create_index(
        LIVE_INDEX,
        TABLE,
        list(LIVE_COLUMNS),
        unique=True,
        postgresql_where=sa.text(_LIVE_TEAM_LINK),
        sqlite_where=sa.text(_LIVE_TEAM_LINK),
    )
    if not postgres:
        return
    for statement in _policies(sources=True):
        op.execute(statement)
    op.execute(_FUNCTION_SQL)
    for statement in _FUNCTION_PRIVILEGES:
        op.execute(statement)


def downgrade() -> None:
    context = op.get_context()
    postgres = context.dialect.name == "postgresql"
    delete = f"DELETE FROM {TABLE} WHERE source_type = 'team_link'"
    if postgres:
        # RLS is forced: lift it for the table owner inside this transaction (as in 0012).
        op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    if context.as_sql:
        op.execute(delete)
    else:
        deleted = op.get_bind().execute(sa.text(delete)).rowcount
        if deleted:
            log.warning("0025 downgrade: deleted %d team-derived membership row(s)", deleted)
    if postgres:
        op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
        for statement in _policies(sources=False):
            op.execute(statement)
    op.drop_index(LIVE_INDEX, TABLE)
    op.drop_index(SOURCE_INDEX, TABLE)
    if postgres:
        op.drop_constraint(NEW_KEY, TABLE, type_="unique")
        op.create_unique_constraint(
            OLD_KEY, TABLE, list(OLD_KEY_COLUMNS), postgresql_nulls_not_distinct=True
        )
        op.drop_constraint(op.f(f"ck_{TABLE}_source_id"), TABLE, type_="check")
        op.drop_constraint(op.f(f"ck_{TABLE}_source_type"), TABLE, type_="check")
        op.drop_column(TABLE, "source_id")
        op.drop_column(TABLE, "source_type")
        return
    frozen = _frozen_table(sources=True)
    with op.batch_alter_table(TABLE, copy_from=frozen, recreate="always") as batch:
        batch.drop_constraint(NEW_KEY, type_="unique")
        batch.drop_constraint(op.f(f"ck_{TABLE}_source_id"), type_="check")
        batch.drop_constraint(op.f(f"ck_{TABLE}_source_type"), type_="check")
        batch.drop_column("source_id")
        batch.drop_column("source_type")
        batch.create_unique_constraint(OLD_KEY, list(OLD_KEY_COLUMNS))
