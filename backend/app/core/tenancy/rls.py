"""Project-scoped PostgreSQL Row-Level Security (T-0007; edge spec §6, diff §4).

Ported from Inspection-ERP ``core/tenancy/rls.py`` (ADR 0001) and extended from the
tenant to the project dimension. Every table that carries ``tenant_id`` gets::

    ENABLE + FORCE ROW LEVEL SECURITY
    CREATE POLICY project_isolation_policy ... FOR ALL USING (<exact>) WITH CHECK (<exact>)
    CREATE POLICY tenant_level_read_policy ... FOR SELECT USING (<tenant-level row>)
    CREATE POLICY allowed_projects_read_policy ... FOR SELECT USING (<allowed project row>)

``<exact>`` is ``tenant_id = app.current_tenant_id AND project_id IS NOT DISTINCT FROM
app.current_project_id`` (edge spec §6). A session reads, inserts, updates and deletes rows
of exactly its scope: a project session writes only its project, and a tenant-level session
(no project) writes only tenant-level rows. Permissive policies are OR-ed per command, so
the second policy only adds *read* access to the tenant's tenant-level rows
(``project_id IS NULL``). A project session cannot create, demote a row to, change or
delete a tenant-level row. The third policy (T-0010, TD-053) adds *read* access to rows of
the projects in ``app.allowed_project_ids`` (comma-separated UUIDs bound from the user's
memberships): a portfolio session reads several projects but still writes only
``app.current_project_id``.

Tenant-level tables that *describe* a project, and the admin grants, use other policies
instead (T-0014, TD-053, edge spec §6 amendments), declared on the model's table with
``rls_table_info(RlsPolicy(...))`` and mirrored by the ORM scope:

* ``membership`` (the project row, memberships): a row is readable when the project it
  describes is the bound project, in ``app.allowed_project_ids``, in
  ``app.member_project_ids`` (every project of the caller's memberships) or the session has
  ``app.is_system_admin``; with an owner column, the caller also reads their own rows
  (``app.current_user_id``), which is how the scope resolver reads the caller's memberships.
  Inserts need the exact scope; updates and deletes need the exact scope *and* visibility.
  With a ``source_column`` (the memberships, ADR 0008, T-0073), rows whose source is not
  ``'direct'`` (team-derived grants) are inserted, updated and deleted only by
  ``app.is_system_admin`` sessions, and one extra read policy lets the census function
  ``kerp_live_team_link_grant_count()`` (migration ``0025``, the startup check) count the live
  derived rows of every tenant while it raises ``app.team_link_census`` for its own read.
* ``admin_grant``: the caller reads their own rows; an ``app.is_system_admin`` session reads
  and writes all of them; nobody else inserts, updates or deletes.
* ``access_request`` (T-0050, edge spec §6 amendments): a row is readable by its owner (the
  requester, ``app.current_user_id``), by the PER-09 holders of the project it names
  (``app.admin_project_ids``) and by ``app.is_system_admin`` sessions; nobody else. The owner
  inserts their own rows only; updates need the exact scope and visibility; nobody deletes
  (decided and withdrawn rows are history).
* ``team`` (T-0013, ADR 0008): the teams module's team, member and project-link tables. A row
  is readable when its team is *visible*: the team has a live link to a project the session
  describes (bound, ``app.allowed_project_ids`` or ``app.member_project_ids``, as for a
  membership) or ``app.current_user_id`` is a live member of it; ``app.is_system_admin``
  sessions read every row. Only ``app.is_system_admin`` sessions insert, update or delete.
  The visible team ids come from the invoker-rights function ``kerp_visible_team_ids()``
  (a policy on the link or member table cannot query its own table). The function raises
  ``app.team_lookup`` for its own read, which only *narrows* the policies (they skip the
  function call while it is raised), so nothing recurses and setting it widens nothing.

* ``tenants`` (the directory, T-0079) keeps the standard policies plus one extra read policy
  (T-0023, ADR 0012): ``tenant_directory_census_policy`` shows every directory row, of any
  tenant, but only while ``app.tenant_census`` is raised, which only the census function
  ``kerp_tenant_directory_count()`` (migration ``0029``) does, for its own count, before
  restoring the caller's value. First-run provisioning uses the count to refuse a second
  tenant in a deployment.

* ``NULLIF(..., '')``: once a connection has set a custom setting, it reads back as ``''``
  after the transaction, not ``NULL``. ``''::uuid`` would raise; ``NULL`` matches nothing.
  An unbound session therefore sees no rows and cannot write any (fail closed).
* ``FORCE``: the table owner is subject to the policy too. Only SUPERUSER and BYPASSRLS
  roles skip it, which ``rls_enforcement_problems`` reports.
* The context is applied with ``set_config(..., is_local => true)`` (``SET LOCAL``) at the
  start of every transaction of a bound session (``after_begin``), so it survives commits
  and never outlives a transaction. The pool-checkin reset also clears any session-level
  value someone set, so a pooled connection never carries a context to the next user.

On SQLite (the fast local suite) none of this applies; the ORM scope in ``scope.py`` is
the only filter there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy import MetaData, Table, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, SessionTransaction
from sqlalchemy.pool import ConnectionPoolEntry

from app.core.tenancy.context import TenancyContext

TENANT_SETTING: Final = "app.current_tenant_id"
PROJECT_SETTING: Final = "app.current_project_id"
ALLOWED_SETTING: Final = "app.allowed_project_ids"
MEMBER_SETTING: Final = "app.member_project_ids"
USER_SETTING: Final = "app.current_user_id"
SYSTEM_ADMIN_SETTING: Final = "app.is_system_admin"
ADMIN_PROJECTS_SETTING: Final = "app.admin_project_ids"
SETTINGS: Final = (
    TENANT_SETTING,
    PROJECT_SETTING,
    ALLOWED_SETTING,
    MEMBER_SETTING,
    USER_SETTING,
    SYSTEM_ADMIN_SETTING,
    ADMIN_PROJECTS_SETTING,
)
POLICY_NAME: Final = "project_isolation_policy"
READ_POLICY_NAME: Final = "tenant_level_read_policy"
ALLOWED_READ_POLICY_NAME: Final = "allowed_projects_read_policy"
POLICY_NAMES: Final = (POLICY_NAME, READ_POLICY_NAME, ALLOWED_READ_POLICY_NAME)
MEMBERSHIP_POLICY_NAMES: Final = (
    "membership_read_policy",
    "membership_insert_policy",
    "membership_update_policy",
    "membership_delete_policy",
)
# The extra read policy of a membership table with a source column (T-0073): only while the
# census function of migration 0025 raises ``app.team_link_census`` for its own count.
MEMBERSHIP_CENSUS_POLICY_NAME: Final = "membership_team_link_census_policy"
CENSUS_SETTING: Final = "app.team_link_census"
DIRECT_SOURCE: Final = "direct"
# The extra read policy of the tenants directory (T-0023, ADR 0012): only while the census
# function of migration 0029 raises ``app.tenant_census`` for its own count.
# ``app.core.db.TENANTS_TABLE`` (``db`` imports this module, so it is repeated here; a test
# keeps the two equal).
TENANTS_TABLE: Final = "tenants"
TENANT_CENSUS_POLICY_NAME: Final = "tenant_directory_census_policy"
TENANT_CENSUS_SETTING: Final = "app.tenant_census"
TENANT_CENSUS_FUNCTION: Final = "kerp_tenant_directory_count"
ADMIN_GRANT_POLICY_NAMES: Final = ("admin_grant_read_policy", "admin_grant_write_policy")
TEAM_POLICY_NAMES: Final = ("team_read_policy", "team_write_policy")
# The visible-team function of a ``team`` policy (T-0013) and the flag it raises for its own
# read; the flag only narrows the policies, so a session that sets it sees less, never more.
TEAM_FUNCTION: Final = "kerp_visible_team_ids"
TEAM_LOOKUP_SETTING: Final = "app.team_lookup"
ACCESS_REQUEST_POLICY_NAMES: Final = (
    "access_request_read_policy",
    "access_request_insert_policy",
    "access_request_update_policy",
)
SESSION_INFO_KEY: Final = "kerp_tenancy"
RLS_INFO_KEY: Final = "kerp_rls"

# Tables with tenant_id that are deliberately not under RLS, each with its reason
# (Inspection-ERP ADR 0003 pattern). Empty until a module needs one; adding an entry
# needs a reviewer-visible justification.
RLS_EXEMPT: Final[frozenset[str]] = frozenset()

_TENANT_EXPR: Final = f"NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid"
_PROJECT_EXPR: Final = f"NULLIF(current_setting('{PROJECT_SETTING}', true), '')::uuid"
# Read, insert, update and delete: exactly the bound scope.
SCOPE_ROW_SQL: Final = (
    f"tenant_id = {_TENANT_EXPR} AND project_id IS NOT DISTINCT FROM {_PROJECT_EXPR}"
)
# Read only: the tenant's tenant-level rows.
TENANT_LEVEL_ROW_SQL: Final = f"tenant_id = {_TENANT_EXPR} AND project_id IS NULL"
# Read only: rows of the allowed projects. An unset list is NULL; ANY(NULL) matches nothing.
ALLOWED_PROJECT_ROW_SQL: Final = (
    f"tenant_id = {_TENANT_EXPR} AND project_id = ANY(string_to_array("
    f"NULLIF(current_setting('{ALLOWED_SETTING}', true), ''), ',')::uuid[])"
)


_USER_EXPR: Final = f"NULLIF(current_setting('{USER_SETTING}', true), '')::uuid"
SYSTEM_ADMIN_SQL: Final = (
    f"COALESCE(NULLIF(current_setting('{SYSTEM_ADMIN_SETTING}', true), '')::boolean, false)"
)


def _id_list_sql(setting: str) -> str:
    return f"string_to_array(NULLIF(current_setting('{setting}', true), ''), ',')::uuid[]"


@dataclass(frozen=True, slots=True)
class RlsPolicy:
    """A table's non-standard RLS policy (T-0014), declared with ``rls_table_info``.

    * ``kind="membership"``: tenant-level rows describing the project in ``project_column``;
      ``owner_column`` (optional) names the user who always reads their own rows.
    * ``kind="admin_grant"``: own rows (``owner_column``) readable; only ``system_admin``
      sessions read all rows and write any.
    * ``kind="access_request"`` (T-0050): readable by the owner (``owner_column``), by the
      administrators (PER-09) of the project in ``project_column`` and by ``system_admin``
      sessions; only the owner inserts; updates need visibility; nobody deletes.
    * ``kind="team"`` (T-0013): ``team_column`` names the row's team (``id`` on the team table,
      ``team_id`` elsewhere). The team is visible when ``link_table`` (columns ``team_id``,
      ``linked_project_id``) has a live row linking it to a described project, or
      ``member_table`` (columns ``team_id``, ``user_id``) has a live row for the caller. Only
      ``system_admin`` sessions write.

    ``source_column`` (membership only, T-0073): rows whose value there is not ``'direct'``
    are written by ``system_admin`` sessions only.
    """

    kind: Literal["membership", "admin_grant", "access_request", "team"]
    owner_column: str | None = None
    project_column: str | None = None
    source_column: str | None = None
    team_column: str | None = None
    link_table: str | None = None
    member_table: str | None = None


def rls_table_info(policy: RlsPolicy) -> dict[str, Any]:
    """``__table_args__`` entry declaring ``policy``: ``{"info": {RLS_INFO_KEY: policy}}``."""
    return {"info": {RLS_INFO_KEY: policy}}


def table_policy(table: Table) -> RlsPolicy | None:
    """The table's non-standard policy, or ``None`` for the standard project policy."""
    policy = table.info.get(RLS_INFO_KEY)
    return policy if isinstance(policy, RlsPolicy) else None


def membership_visible_sql(project_column: str, owner_column: str | None = None) -> str:
    """Read rule of a ``membership`` table: the described project is one the session sees."""
    clauses = [
        f"{project_column} = {_PROJECT_EXPR}",
        f"{project_column} = ANY({_id_list_sql(ALLOWED_SETTING)})",
        f"{project_column} = ANY({_id_list_sql(MEMBER_SETTING)})",
        SYSTEM_ADMIN_SQL,
    ]
    if owner_column is not None:
        clauses.append(f"{owner_column} = {_USER_EXPR}")
    return f"tenant_id = {_TENANT_EXPR} AND ({' OR '.join(clauses)})"


def membership_census_sql(source_column: str) -> str:
    """Read rule of the census policy: live derived rows of any tenant, only while
    ``app.team_link_census`` is raised (by the census function of migration ``0025``)."""
    census = f"COALESCE(NULLIF(current_setting('{CENSUS_SETTING}', true), '')::boolean, false)"
    return f"{census} AND {source_column} <> '{DIRECT_SOURCE}' AND deleted_at IS NULL"


def tenant_census_policy_sql(table: str = TENANTS_TABLE) -> list[str]:
    """The census read policy of the tenants directory (T-0023, ADR 0012): every row of any
    tenant, only while ``app.tenant_census`` is raised (by ``kerp_tenant_directory_count()``)."""
    raised = f"NULLIF(current_setting('{TENANT_CENSUS_SETTING}', true), '')::boolean"
    census = f"COALESCE({raised}, false)"
    return [
        f"DROP POLICY IF EXISTS {TENANT_CENSUS_POLICY_NAME} ON {table}",
        f"CREATE POLICY {TENANT_CENSUS_POLICY_NAME} ON {table} FOR SELECT USING ({census})",
    ]


def access_request_visible_sql(project_column: str, owner_column: str) -> str:
    """Read rule of an ``access_request`` table: the requester, the PER-09 holders of the
    named project, or a System admin (edge spec §6 amendments, T-0050)."""
    clauses = [
        f"{owner_column} = {_USER_EXPR}",
        f"{project_column} = ANY({_id_list_sql(ADMIN_PROJECTS_SETTING)})",
        SYSTEM_ADMIN_SQL,
    ]
    return f"tenant_id = {_TENANT_EXPR} AND ({' OR '.join(clauses)})"


def _access_request_policy_sql(table: str, policy: RlsPolicy) -> list[str]:
    if policy.project_column is None or policy.owner_column is None:
        raise ValueError(f"{table}: an access_request policy needs project and owner columns")
    read, insert, update = ACCESS_REQUEST_POLICY_NAMES
    visible = access_request_visible_sql(policy.project_column, policy.owner_column)
    guarded = f"{SCOPE_ROW_SQL} AND {visible}"
    own = f"{SCOPE_ROW_SQL} AND {policy.owner_column} = {_USER_EXPR}"
    # No DELETE policy: nobody deletes a request (decided and withdrawn rows are history).
    return [
        f"CREATE POLICY {read} ON {table} FOR SELECT USING ({visible})",
        f"CREATE POLICY {insert} ON {table} FOR INSERT WITH CHECK ({own})",
        f"CREATE POLICY {update} ON {table} FOR UPDATE USING ({guarded}) WITH CHECK ({guarded})",
    ]


def team_lookup_function_sql(link_table: str, member_table: str) -> str:
    """``kerp_visible_team_ids()``: the ids of the bound tenant's teams the session sees.

    A team is visible with a live link to a described project or a live membership of the
    caller. Invoker rights: it reads only what the session's own policies show. The link and
    member reads run with ``app.team_lookup`` raised, under which their policies use only the
    row's own columns (no recursion); the caller's value is restored before it returns.
    """
    described = " OR ".join(
        [
            f"l.linked_project_id = {_PROJECT_EXPR}",
            f"l.linked_project_id = ANY({_id_list_sql(ALLOWED_SETTING)})",
            f"l.linked_project_id = ANY({_id_list_sql(MEMBER_SETTING)})",
        ]
    )
    return f"""
CREATE OR REPLACE FUNCTION {TEAM_FUNCTION}() RETURNS uuid[]
LANGUAGE plpgsql VOLATILE SET search_path = public, pg_temp AS $fn$
DECLARE
    prior text := current_setting('{TEAM_LOOKUP_SETTING}', true);
    found uuid[];
BEGIN
    PERFORM set_config('{TEAM_LOOKUP_SETTING}', 'true', true);
    SELECT COALESCE(array_agg(DISTINCT v.team_id), '{{}}') INTO found FROM (
        SELECT l.team_id FROM {link_table} l
            WHERE l.tenant_id = {_TENANT_EXPR} AND l.deleted_at IS NULL AND ({described})
        UNION
        SELECT m.team_id FROM {member_table} m
            WHERE m.tenant_id = {_TENANT_EXPR} AND m.deleted_at IS NULL
            AND m.user_id = {_USER_EXPR}
    ) v;
    PERFORM set_config('{TEAM_LOOKUP_SETTING}', COALESCE(prior, ''), true);
    RETURN found;
END
$fn$
"""


def team_visible_sql(policy: RlsPolicy, table: str) -> str:
    """Read rule of a ``team`` table (see ``RlsPolicy``)."""
    if policy.team_column is None:
        raise ValueError(f"{table}: a team policy needs team_column")
    lookup = f"COALESCE(NULLIF(current_setting('{TEAM_LOOKUP_SETTING}', true), '')::boolean, false)"
    clauses = [SYSTEM_ADMIN_SQL]
    # The row's own columns: what the lookup function itself needs to read.
    if table == policy.link_table:
        clauses += [
            f"linked_project_id = {_PROJECT_EXPR}",
            f"linked_project_id = ANY({_id_list_sql(ALLOWED_SETTING)})",
            f"linked_project_id = ANY({_id_list_sql(MEMBER_SETTING)})",
        ]
    if table == policy.member_table:
        clauses.append(f"user_id = {_USER_EXPR}")
    # CASE, not AND: the only evaluation order SQL guarantees (no call while looking up).
    clauses.append(
        f"CASE WHEN {lookup} THEN false"
        f" ELSE {policy.team_column} IN (SELECT unnest({TEAM_FUNCTION}())) END"
    )
    return f"tenant_id = {_TENANT_EXPR} AND ({' OR '.join(clauses)})"


def _team_policy_sql(table: str, policy: RlsPolicy) -> list[str]:
    if policy.link_table is None or policy.member_table is None:
        raise ValueError(f"{table}: a team policy needs link_table and member_table")
    read, write = TEAM_POLICY_NAMES
    admin = f"{SCOPE_ROW_SQL} AND {SYSTEM_ADMIN_SQL}"
    return [
        team_lookup_function_sql(policy.link_table, policy.member_table),
        f"CREATE POLICY {read} ON {table} FOR SELECT USING ({team_visible_sql(policy, table)})",
        f"CREATE POLICY {write} ON {table} FOR ALL USING ({admin}) WITH CHECK ({admin})",
    ]


def _special_policy_sql(table: str, policy: RlsPolicy) -> list[str]:
    names: tuple[str, ...]
    if policy.kind == "team":
        names = TEAM_POLICY_NAMES
        creates = _team_policy_sql(table, policy)
    elif policy.kind == "access_request":
        names = ACCESS_REQUEST_POLICY_NAMES
        creates = _access_request_policy_sql(table, policy)
    elif policy.kind == "membership":
        if policy.project_column is None:
            raise ValueError(f"{table}: a membership policy needs project_column")
        names = MEMBERSHIP_POLICY_NAMES
        read, insert, update, delete = names
        visible = membership_visible_sql(policy.project_column, policy.owner_column)
        guarded = f"{SCOPE_ROW_SQL} AND {visible}"
        insertable = SCOPE_ROW_SQL
        if policy.source_column is not None:
            writable = f"({policy.source_column} = '{DIRECT_SOURCE}' OR {SYSTEM_ADMIN_SQL})"
            guarded, insertable = f"{guarded} AND {writable}", f"{insertable} AND {writable}"
        creates = [
            f"CREATE POLICY {read} ON {table} FOR SELECT USING ({visible})",
            f"CREATE POLICY {insert} ON {table} FOR INSERT WITH CHECK ({insertable})",
            f"CREATE POLICY {update} ON {table} FOR UPDATE USING ({guarded})"
            f" WITH CHECK ({guarded})",
            f"CREATE POLICY {delete} ON {table} FOR DELETE USING ({guarded})",
        ]
        if policy.source_column is not None:
            names = (*names, MEMBERSHIP_CENSUS_POLICY_NAME)
            creates.append(
                f"CREATE POLICY {MEMBERSHIP_CENSUS_POLICY_NAME} ON {table} FOR SELECT"
                f" USING ({membership_census_sql(policy.source_column)})"
            )
    else:
        if policy.owner_column is None:
            raise ValueError(f"{table}: an admin_grant policy needs owner_column")
        names = ADMIN_GRANT_POLICY_NAMES
        read, write = names
        own = f"{policy.owner_column} = {_USER_EXPR}"
        admin = f"{SCOPE_ROW_SQL} AND {SYSTEM_ADMIN_SQL}"
        creates = [
            f"CREATE POLICY {read} ON {table} FOR SELECT"
            f" USING (tenant_id = {_TENANT_EXPR} AND ({own} OR {SYSTEM_ADMIN_SQL}))",
            f"CREATE POLICY {write} ON {table} FOR ALL USING ({admin}) WITH CHECK ({admin})",
        ]
    drops = [f"DROP POLICY IF EXISTS {name} ON {table}" for name in (*POLICY_NAMES, *names)]
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        *drops,
        *creates,
    ]


def expected_policy_names(table: Table) -> tuple[str, ...]:
    """The policies ``rls_coverage_problems`` requires on ``table``."""
    policy = table_policy(table)
    if policy is None:
        if table.name == TENANTS_TABLE:
            return (*POLICY_NAMES, TENANT_CENSUS_POLICY_NAME)
        return POLICY_NAMES
    if policy.kind == "access_request":
        return ACCESS_REQUEST_POLICY_NAMES
    if policy.kind == "team":
        return TEAM_POLICY_NAMES
    if policy.kind == "membership":
        if policy.source_column is not None:
            return (*MEMBERSHIP_POLICY_NAMES, MEMBERSHIP_CENSUS_POLICY_NAME)
        return MEMBERSHIP_POLICY_NAMES
    return ADMIN_GRANT_POLICY_NAMES


def rls_tables(metadata: MetaData, *, exempt: frozenset[str] = RLS_EXEMPT) -> list[str]:
    """Names of the tables in ``metadata`` that must be under RLS."""
    return sorted(
        name
        for name, table in metadata.tables.items()
        if "tenant_id" in table.columns and name not in exempt
    )


def rls_policy_sql(table: str, policy: RlsPolicy | None = None) -> list[str]:
    """DDL that puts ``table`` under project-scoped RLS, or ``policy`` (idempotent).

    Migrations run it with ``op.execute`` right after creating a table::

        for statement in rls_policy_sql("oe_asset_node"):
            op.execute(statement)
    """
    if policy is not None:
        return _special_policy_sql(table, policy)
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}",
        f"CREATE POLICY {POLICY_NAME} ON {table} FOR ALL "
        f"USING ({SCOPE_ROW_SQL}) WITH CHECK ({SCOPE_ROW_SQL})",
        f"DROP POLICY IF EXISTS {READ_POLICY_NAME} ON {table}",
        f"CREATE POLICY {READ_POLICY_NAME} ON {table} FOR SELECT USING ({TENANT_LEVEL_ROW_SQL})",
        f"DROP POLICY IF EXISTS {ALLOWED_READ_POLICY_NAME} ON {table}",
        f"CREATE POLICY {ALLOWED_READ_POLICY_NAME} ON {table} FOR SELECT "
        f"USING ({ALLOWED_PROJECT_ROW_SQL})",
    ]


def apply_rls_policies(connection: Connection, metadata: MetaData) -> list[str]:
    """Put every RLS table of ``metadata`` under the policy. No-op off PostgreSQL."""
    if connection.dialect.name != "postgresql":
        return []
    tables = rls_tables(metadata)
    for table in tables:
        for statement in rls_policy_sql(table, table_policy(metadata.tables[table])):
            connection.execute(text(statement))
        if table == TENANTS_TABLE:
            for statement in tenant_census_policy_sql(table):
                connection.execute(text(statement))
    return tables


def rls_coverage_problems(connection: Connection, metadata: MetaData) -> list[str]:
    """Why each RLS table in the live database is not protected (empty = OK)."""
    if connection.dialect.name != "postgresql":
        return []
    tables = rls_tables(metadata)
    if not tables:
        return []
    rows = connection.execute(
        text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,"
            " ARRAY(SELECT p.polname::text FROM pg_policy p WHERE p.polrelid = c.oid)"
            " AS policies"
            " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = current_schema() AND c.relname = ANY(:tables)"
        ),
        {"tables": tables},
    ).all()
    found = {row.relname: row for row in rows}
    problems: list[str] = []
    for table in tables:
        row = found.get(table)
        if row is None:
            problems.append(f"{table}: table not found")
            continue
        if not row.relrowsecurity:
            problems.append(f"{table}: row level security not enabled")
        if not row.relforcerowsecurity:
            problems.append(f"{table}: row level security not forced")
        expected = expected_policy_names(metadata.tables[table])
        problems.extend(
            f"{table}: policy {name} missing" for name in expected if name not in row.policies
        )
        # A leftover standard policy on a membership table would re-widen it (FOR ALL reads).
        problems.extend(
            f"{table}: unexpected policy {name}"
            for name in sorted(set(row.policies) - set(expected))
        )
    return problems


def rls_enforcement_problems(engine: Engine) -> list[str]:
    """Why RLS would be silently bypassed for the engine's role (empty = OK).

    PostgreSQL skips every policy, FORCEd ones included, for SUPERUSER and BYPASSRLS
    roles. The official ``postgres`` image's ``POSTGRES_USER`` is a superuser.
    """
    if engine.dialect.name != "postgresql":
        return []
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT current_user AS role, rolsuper, rolbypassrls"
                " FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
    problems: list[str] = []
    if row.rolsuper:
        problems.append(f"database role '{row.role}' is a SUPERUSER (bypasses RLS)")
    if row.rolbypassrls:
        problems.append(f"database role '{row.role}' has BYPASSRLS")
    return problems


# --- binding a session -------------------------------------------------------------------


def bind_tenancy(session: Session, context: TenancyContext | None) -> None:
    """Bind ``session`` to a tenant/project for RLS and the ORM scope (``None`` unbinds).

    The context lives on ``session.info`` and is re-applied at the start of every
    transaction, because after a commit the session may run on another pooled connection.

    Switching a session to a *different* context is only allowed while it holds no loaded
    objects and no pending changes. Objects loaded under the old scope stay in the
    identity map, and ``session.get`` would hand them out without any SQL (so neither
    the ORM scope nor RLS would see the call). Commit and ``expunge_all()`` first, or use
    a new session per scope.
    """
    current = session_tenancy(session)
    if context != current and (
        len(session.identity_map) or session.new or session.dirty or session.deleted
    ):
        raise RuntimeError(
            "cannot rebind a session that holds objects or pending changes; "
            "commit and expunge_all() first, or use a new session per scope"
        )
    if context is None:
        session.info.pop(SESSION_INFO_KEY, None)
    else:
        session.info[SESSION_INFO_KEY] = context
    if session.in_transaction():
        bind_connection(session.connection(), context)


def session_tenancy(session: Session) -> TenancyContext | None:
    """The context ``session`` is bound to, if any."""
    context: TenancyContext | None = session.info.get(SESSION_INFO_KEY)
    return context


def bind_connection(connection: Connection, context: TenancyContext | None) -> None:
    """Bind the current transaction of a Core ``connection`` (``SET LOCAL`` semantics).

    For code that works on a connection instead of a session, such as a reporting query.
    The binding ends with the transaction.
    """
    if connection.dialect.name != "postgresql":
        return
    # set_config, not SET LOCAL: SET cannot take bind parameters (psycopg 3 binds server-side).
    values = setting_values(context)
    calls = ", ".join(f"set_config(:n{i}, :v{i}, true)" for i in range(len(values)))
    params: dict[str, str] = {}
    for i, (name, value) in enumerate(values.items()):
        params[f"n{i}"], params[f"v{i}"] = name, value
    connection.execute(text(f"SELECT {calls}"), params)


def _ids(ids: frozenset[Any]) -> str:
    return ",".join(sorted(map(str, ids)))


def setting_values(context: TenancyContext | None) -> dict[str, str]:
    """The session settings bound for ``context``; every one empty (unset) when unbound.

    ``app.current_user_id`` and ``app.is_system_admin`` come from the context, which the
    request fills from the verified token and the admin grants table (T-0014), never from
    request input. The flag is ``"true"`` or unset, never ``"false"``.
    """
    if context is None:
        return dict.fromkeys(SETTINGS, "")
    return {
        TENANT_SETTING: str(context.tenant_id),
        PROJECT_SETTING: str(context.project_id) if context.project_id else "",
        ALLOWED_SETTING: _ids(context.allowed_project_ids),
        MEMBER_SETTING: _ids(context.member_project_ids),
        USER_SETTING: str(context.user_id) if context.user_id else "",
        SYSTEM_ADMIN_SETTING: "true" if context.is_system_admin else "",
        ADMIN_PROJECTS_SETTING: _ids(context.admin_project_ids),
    }


@event.listens_for(Session, "after_begin")
def _rebind_on_begin(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    context = session_tenancy(session)
    if context is not None:
        bind_connection(connection, context)


def install_pool_reset(engine: Engine) -> None:
    """Clear the tenancy settings whenever a connection goes back to the pool."""
    if engine.dialect.name != "postgresql":
        return

    @event.listens_for(engine, "checkin")
    def _reset_settings(
        dbapi_connection: DBAPIConnection | None, connection_record: ConnectionPoolEntry
    ) -> None:
        if dbapi_connection is None:  # pragma: no cover - invalidated connection
            return
        try:
            cursor = dbapi_connection.cursor()
            for name in SETTINGS:
                cursor.execute(f"RESET {name}")
            cursor.close()
            dbapi_connection.commit()
        except Exception:  # pragma: no cover - a connection we cannot reset is dropped
            connection_record.invalidate()
