"""Runtime database role grants: the owner/runtime split (T-0343, ADR 0019, TD-056, TD-059).

Migrations run as the schema owner (``KERP_MIGRATION_DATABASE_URL``); the API and the worker
connect as a separate runtime role (``KERP_DATABASE_URL``, ``NOSUPERUSER NOBYPASSRLS``) that
owns nothing. Until now no revision granted that role anything, so the documented two-role
setup answered "permission denied". This revision grants the role named by
``KERP_RUNTIME_DB_ROLE`` (default ``kerp_app``), on everything the migrating role owns in the
current schema:

* ``USAGE`` on the schema;
* ``SELECT, INSERT, UPDATE, DELETE`` on every table and view, but only the privileges the
  owner itself still holds: ``audit_ledger`` gets ``SELECT, INSERT`` and never ``UPDATE``,
  ``DELETE`` or ``TRUNCATE`` (``0002`` revoked those from its owner; it is also named
  explicitly here, TD-059). ``alembic_version`` gets nothing (the app never reads it);
* ``USAGE, SELECT`` on every sequence;
* ``EXECUTE`` on every function, including the ``SECURITY DEFINER`` lobby, access-request,
  team-grant and census functions of ``0015``, ``0021``, ``0023``, ``0025``, ``0029``, which
  revoke it from ``PUBLIC``; functions of extensions (PostGIS, ltree) are left alone;
* ``ALTER DEFAULT PRIVILEGES`` for the migrating role in the schema, so tables, sequences and
  functions created by later revisions get the same grants (``SELECT, INSERT, UPDATE,
  DELETE``; ``USAGE, SELECT``; ``EXECUTE``). A later append-only table must ``REVOKE`` its
  ``UPDATE, DELETE`` from the runtime role in its own revision (ADR 0019).

Skipped, with a log line, when the setting is empty, the role does not exist, or the role is
the migrating role itself (local dev and CI's PostgreSQL shards migrate as ``kerp_app``, which
then owns the tables; granting to or revoking from the owner itself would change nothing or
take its own rights away). The role checks run inside the ``DO`` blocks, so the offline
``--sql`` output skips the same way when it is applied. SQLite has no roles: no-op.

Grants only, additive. The downgrade revokes exactly these grants (and the default
privileges) from the role; revoking a privilege the role never had is a no-op. The census
functions of ``0025`` and ``0029`` keep the ``EXECUTE`` those revisions gave ``kerp_app``.

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-21
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("kerp.migrations")

LEDGER = "audit_ledger"
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")
LEDGER_PRIVILEGES = ("SELECT", "INSERT")  # TD-059: never UPDATE, DELETE, TRUNCATE
SEQUENCE_PRIVILEGES = "USAGE, SELECT"
NOT_GRANTED_TABLES = ("alembic_version",)
# ``0025`` and ``0029`` grant these two to ``kerp_app`` themselves; the downgrade leaves that
# grant in place (it is theirs, not this revision's).
EARLIER_GRANTEE = "kerp_app"
EARLIER_GRANTED_FUNCTIONS = ("kerp_live_team_link_grant_count", "kerp_tenant_directory_count")

# The objects the migrating role owns in the current schema (extension members excluded).
_OWNED_RELATIONS = """
    SELECT c.oid, c.relname, c.relkind FROM pg_class c
    WHERE c.relnamespace = current_schema()::regnamespace
      AND c.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
      AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
      AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass
                      AND d.objid = c.oid AND d.deptype = 'e')
"""
_OWNED_FUNCTIONS = """
    SELECT p.oid::regprocedure AS signature, p.proname FROM pg_proc p
    WHERE p.pronamespace = current_schema()::regnamespace
      AND p.proowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
      AND p.prokind IN ('f', 'p')
      AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass
                      AND d.objid = p.oid AND d.deptype = 'e')
"""


def _literal(role: str) -> str:
    # The setting only admits [a-z0-9_] names; quote defensively all the same.
    return "'" + role.replace("'", "''") + "'"


def _guard(role: str) -> str:
    """``DO`` prologue: stop (with a notice) unless the role exists and is not the migrator."""
    return f"""
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_literal(role)}) THEN
        RAISE NOTICE 'runtime role grants skipped: role % does not exist', {_literal(role)};
        RETURN;
    END IF;
    IF {_literal(role)} = current_user THEN
        RAISE NOTICE 'runtime role grants skipped: % is the migrating role', current_user;
        RETURN;
    END IF;
"""


def grant_sql(role: str) -> str:
    """The upgrade, one ``DO`` block (frozen: depends on nothing but the catalog)."""
    table_privileges = ", ".join(f"'{p}'" for p in TABLE_PRIVILEGES)
    ledger_privileges = ", ".join(f"'{p}'" for p in LEDGER_PRIVILEGES)
    skipped = ", ".join(f"'{t}'" for t in NOT_GRANTED_TABLES)
    role_sql = _literal(role)
    return f"""
DO $grants$
DECLARE
    r record;
    allowed text[];
    granted text;
BEGIN
{_guard(role)}
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', current_schema(), {role_sql});
    FOR r IN {_OWNED_RELATIONS} LOOP
        IF r.relkind = 'S' THEN
            EXECUTE format('GRANT {SEQUENCE_PRIVILEGES} ON SEQUENCE %I TO %I', r.relname,
                           {role_sql});
            CONTINUE;
        END IF;
        IF r.relname IN ({skipped}) THEN
            CONTINUE;
        END IF;
        allowed := CASE WHEN r.relname = '{LEDGER}' THEN ARRAY[{ledger_privileges}]
                        ELSE ARRAY[{table_privileges}] END;
        SELECT string_agg(p, ', ' ORDER BY ord) INTO granted
            FROM unnest(allowed) WITH ORDINALITY AS a(p, ord)
            WHERE has_table_privilege(current_user, r.oid, p);
        IF granted IS NOT NULL THEN
            EXECUTE format('GRANT %s ON TABLE %I TO %I', granted, r.relname, {role_sql});
        END IF;
    END LOOP;
    FOR r IN {_OWNED_FUNCTIONS} LOOP
        EXECUTE format('GRANT EXECUTE ON ROUTINE %s TO %I', r.signature, {role_sql});
    END LOOP;
    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I'
                   ' GRANT {", ".join(TABLE_PRIVILEGES)} ON TABLES TO %I',
                   current_user, current_schema(), {role_sql});
    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I'
                   ' GRANT {SEQUENCE_PRIVILEGES} ON SEQUENCES TO %I',
                   current_user, current_schema(), {role_sql});
    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I'
                   ' GRANT EXECUTE ON FUNCTIONS TO %I',
                   current_user, current_schema(), {role_sql});
END
$grants$
"""


def revoke_sql(role: str) -> str:
    """The downgrade: exactly the grants of ``grant_sql``, revoked."""
    role_sql = _literal(role)
    skipped = ", ".join(f"'{t}'" for t in NOT_GRANTED_TABLES)
    earlier = ", ".join(f"'{f}'" for f in EARLIER_GRANTED_FUNCTIONS)
    return f"""
DO $revokes$
DECLARE
    r record;
BEGIN
{_guard(role)}
    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I'
                   ' REVOKE EXECUTE ON FUNCTIONS FROM %I',
                   current_user, current_schema(), {role_sql});
    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I'
                   ' REVOKE {SEQUENCE_PRIVILEGES} ON SEQUENCES FROM %I',
                   current_user, current_schema(), {role_sql});
    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I'
                   ' REVOKE {", ".join(TABLE_PRIVILEGES)} ON TABLES FROM %I',
                   current_user, current_schema(), {role_sql});
    FOR r IN {_OWNED_FUNCTIONS} LOOP
        IF {role_sql} = '{EARLIER_GRANTEE}' AND r.proname IN ({earlier}) THEN
            CONTINUE;
        END IF;
        EXECUTE format('REVOKE EXECUTE ON ROUTINE %s FROM %I', r.signature, {role_sql});
    END LOOP;
    FOR r IN {_OWNED_RELATIONS} LOOP
        IF r.relkind = 'S' THEN
            EXECUTE format('REVOKE {SEQUENCE_PRIVILEGES} ON SEQUENCE %I FROM %I', r.relname,
                           {role_sql});
        ELSIF r.relname NOT IN ({skipped}) THEN
            EXECUTE format('REVOKE {", ".join(TABLE_PRIVILEGES)} ON TABLE %I FROM %I',
                           r.relname, {role_sql});
        END IF;
    END LOOP;
    EXECUTE format('REVOKE USAGE ON SCHEMA %I FROM %I', current_schema(), {role_sql});
END
$revokes$
"""


def _runtime_role() -> str | None:
    """The role to grant to, or ``None`` (logged) when there is nothing to do."""
    role = get_settings().runtime_db_role
    if role is None:
        logger.info("runtime role grants skipped: KERP_RUNTIME_DB_ROLE is empty")
        return None
    if op.get_context().as_sql:
        return role  # offline: the DO block decides when the script is applied
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT current_user AS migrator,"
                " EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role) AS present"
            ),
            {"role": role},
        )
        .one()
    )
    if not row.present:
        logger.info("runtime role grants skipped: role %s does not exist", role)
        return None
    if row.migrator == role:
        logger.info("runtime role grants skipped: %s is the migrating role", role)
        return None
    return role


def upgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    role = _runtime_role()
    if role is not None:
        op.execute(grant_sql(role))


def downgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    role = _runtime_role()
    if role is not None:
        op.execute(revoke_sql(role))
