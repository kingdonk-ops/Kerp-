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
* ``admin_grant``: the caller reads their own rows; an ``app.is_system_admin`` session reads
  and writes all of them; nobody else inserts, updates or deletes.

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
SETTINGS: Final = (
    TENANT_SETTING,
    PROJECT_SETTING,
    ALLOWED_SETTING,
    MEMBER_SETTING,
    USER_SETTING,
    SYSTEM_ADMIN_SETTING,
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
ADMIN_GRANT_POLICY_NAMES: Final = ("admin_grant_read_policy", "admin_grant_write_policy")
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
    """

    kind: Literal["membership", "admin_grant"]
    owner_column: str | None = None
    project_column: str | None = None


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


def _special_policy_sql(table: str, policy: RlsPolicy) -> list[str]:
    if policy.kind == "membership":
        if policy.project_column is None:
            raise ValueError(f"{table}: a membership policy needs project_column")
        names: tuple[str, ...] = MEMBERSHIP_POLICY_NAMES
        read, insert, update, delete = names
        visible = membership_visible_sql(policy.project_column, policy.owner_column)
        guarded = f"{SCOPE_ROW_SQL} AND {visible}"
        creates = [
            f"CREATE POLICY {read} ON {table} FOR SELECT USING ({visible})",
            f"CREATE POLICY {insert} ON {table} FOR INSERT WITH CHECK ({SCOPE_ROW_SQL})",
            f"CREATE POLICY {update} ON {table} FOR UPDATE USING ({guarded})"
            f" WITH CHECK ({guarded})",
            f"CREATE POLICY {delete} ON {table} FOR DELETE USING ({guarded})",
        ]
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
        return POLICY_NAMES
    return MEMBERSHIP_POLICY_NAMES if policy.kind == "membership" else ADMIN_GRANT_POLICY_NAMES


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
