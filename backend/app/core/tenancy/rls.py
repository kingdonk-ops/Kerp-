"""Project-scoped PostgreSQL Row-Level Security (T-0007; edge spec §6, diff §4).

Ported from Inspection-ERP ``core/tenancy/rls.py`` (ADR 0001) and extended from the
tenant to the project dimension. Every table that carries ``tenant_id`` gets::

    ENABLE + FORCE ROW LEVEL SECURITY
    CREATE POLICY project_isolation_policy ... FOR ALL USING (<exact>) WITH CHECK (<exact>)
    CREATE POLICY tenant_level_read_policy ... FOR SELECT USING (<tenant-level row>)

``<exact>`` is ``tenant_id = app.current_tenant_id AND project_id IS NOT DISTINCT FROM
app.current_project_id`` (edge spec §6). A session reads, inserts, updates and deletes rows
of exactly its scope: a project session writes only its project, and a tenant-level session
(no project) writes only tenant-level rows. Permissive policies are OR-ed per command, so
the second policy only adds *read* access to the tenant's tenant-level rows
(``project_id IS NULL``). A project session cannot create, demote a row to, change or
delete a tenant-level row.

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

from typing import Final

from sqlalchemy import MetaData, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, SessionTransaction
from sqlalchemy.pool import ConnectionPoolEntry

from app.core.tenancy.context import TenancyContext

TENANT_SETTING: Final = "app.current_tenant_id"
PROJECT_SETTING: Final = "app.current_project_id"
POLICY_NAME: Final = "project_isolation_policy"
READ_POLICY_NAME: Final = "tenant_level_read_policy"
POLICY_NAMES: Final = (POLICY_NAME, READ_POLICY_NAME)
SESSION_INFO_KEY: Final = "kerp_tenancy"

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


def rls_tables(metadata: MetaData, *, exempt: frozenset[str] = RLS_EXEMPT) -> list[str]:
    """Names of the tables in ``metadata`` that must be under RLS."""
    return sorted(
        name
        for name, table in metadata.tables.items()
        if "tenant_id" in table.columns and name not in exempt
    )


def rls_policy_sql(table: str) -> list[str]:
    """DDL that puts ``table`` under project-scoped RLS (idempotent).

    Migrations run it with ``op.execute`` right after creating a table::

        for statement in rls_policy_sql("oe_asset_node"):
            op.execute(statement)
    """
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}",
        f"CREATE POLICY {POLICY_NAME} ON {table} FOR ALL "
        f"USING ({SCOPE_ROW_SQL}) WITH CHECK ({SCOPE_ROW_SQL})",
        f"DROP POLICY IF EXISTS {READ_POLICY_NAME} ON {table}",
        f"CREATE POLICY {READ_POLICY_NAME} ON {table} FOR SELECT USING ({TENANT_LEVEL_ROW_SQL})",
    ]


def apply_rls_policies(connection: Connection, metadata: MetaData) -> list[str]:
    """Put every RLS table of ``metadata`` under the policy. No-op off PostgreSQL."""
    if connection.dialect.name != "postgresql":
        return []
    tables = rls_tables(metadata)
    for table in tables:
        for statement in rls_policy_sql(table):
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
        problems.extend(
            f"{table}: policy {name} missing" for name in POLICY_NAMES if name not in row.policies
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
    connection.execute(
        text("SELECT set_config(:t_name, :tenant, true), set_config(:p_name, :project, true)"),
        {
            "t_name": TENANT_SETTING,
            "p_name": PROJECT_SETTING,
            "tenant": str(context.tenant_id) if context else "",
            "project": str(context.project_id) if context and context.project_id else "",
        },
    )


@event.listens_for(Session, "after_begin")
def _rebind_on_begin(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    context = session_tenancy(session)
    if context is not None:
        bind_connection(connection, context)


def install_pool_reset(engine: Engine) -> None:
    """Clear both settings whenever a connection goes back to the pool."""
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
            cursor.execute(f"RESET {TENANT_SETTING}")
            cursor.execute(f"RESET {PROJECT_SETTING}")
            cursor.close()
            dbapi_connection.commit()
        except Exception:  # pragma: no cover - a connection we cannot reset is dropped
            connection_record.invalidate()
