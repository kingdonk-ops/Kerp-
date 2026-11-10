"""Database-level append-only guard for ``audit_ledger`` (T-0006, diff §4).

Two independent layers, both PostgreSQL only:

1. **Privileges:** ``REVOKE UPDATE, DELETE, TRUNCATE`` from ``PUBLIC`` and from the role
   running the migration. That role owns the table. An owner's privileges can be revoked
   like anyone else's, so the runtime role, which owns the tables in CI (TD-056), gets
   ``permission denied``.
2. **Triggers:** an owner can always grant itself the privileges back. ``BEFORE UPDATE OR
   DELETE`` (per row) and ``BEFORE TRUNCATE`` (per statement) triggers raise, whatever the
   privileges. Only a superuser (``session_replication_role = replica``) or the owner
   disabling the trigger gets past them; ``ledger_guard_problems`` reports the latter.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.audit.models import TABLE

FUNCTION: Final = "audit_ledger_reject_change"
ROW_TRIGGER: Final = "audit_ledger_append_only"
TRUNCATE_TRIGGER: Final = "audit_ledger_no_truncate"
REVOKED: Final = ("UPDATE", "DELETE", "TRUNCATE")


def ledger_guard_sql(table: str = TABLE) -> list[str]:
    """DDL that makes ``table`` append-only. Run by the migration after creating it."""
    return [
        f"REVOKE {', '.join(REVOKED)} ON {table} FROM PUBLIC",
        f"REVOKE {', '.join(REVOKED)} ON {table} FROM CURRENT_USER",
        f"CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN RAISE EXCEPTION USING ERRCODE = 'insufficient_privilege',"
        " MESSAGE = TG_TABLE_NAME || ' is append-only (' || TG_OP || ' refused)'; END $$",
        f"CREATE TRIGGER {ROW_TRIGGER} BEFORE UPDATE OR DELETE ON {table}"
        f" FOR EACH ROW EXECUTE FUNCTION {FUNCTION}()",
        f"CREATE TRIGGER {TRUNCATE_TRIGGER} BEFORE TRUNCATE ON {table}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION}()",
    ]


def ledger_guard_drop_sql(table: str = TABLE) -> list[str]:
    """Undo ``ledger_guard_sql`` (the migration's downgrade; the table is dropped after)."""
    return [
        f"DROP TRIGGER IF EXISTS {TRUNCATE_TRIGGER} ON {table}",
        f"DROP TRIGGER IF EXISTS {ROW_TRIGGER} ON {table}",
        f"DROP FUNCTION IF EXISTS {FUNCTION}()",
    ]


def ledger_guard_problems(connection: Connection, table: str = TABLE) -> list[str]:
    """Why ``table`` is not append-only for the connection's role (empty = OK)."""
    if connection.dialect.name != "postgresql":
        return []
    problems = [
        f"{table}: current role has {privilege}"
        for privilege in REVOKED
        if connection.execute(
            text("SELECT has_table_privilege(CAST(:table AS text), CAST(:privilege AS text))"),
            {"table": table, "privilege": privilege},
        ).scalar_one()
    ]
    enabled = set(
        connection.execute(
            text(
                "SELECT tgname FROM pg_trigger"
                " WHERE tgrelid = CAST(:table AS regclass) AND tgenabled <> 'D'"
            ),
            {"table": table},
        ).scalars()
    )
    problems.extend(
        f"{table}: trigger {name} missing or disabled"
        for name in (ROW_TRIGGER, TRUNCATE_TRIGGER)
        if name not in enabled
    )
    return problems
