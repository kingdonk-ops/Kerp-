"""The startup role checks on real PostgreSQL (T-0342; TD-056, TD-059).

A correctly set-up runtime role (CI's ``kerp_app``: ``NOSUPERUSER NOBYPASSRLS``, every RLS
table forced, ``audit_ledger`` append-only) starts in production; a superuser, a
``BYPASSRLS`` role, a role that owns an RLS table without ``FORCE`` and a role with
``UPDATE`` / ``DELETE`` on ``audit_ledger`` are refused. The misconfigurations are made
through the owner connection (``KERP_TEST_OWNER_URL``), so the tests hold whether the runtime
role owns the tables (CI's PostgreSQL shards) or only has grants (``backend-pg-roles``,
T-0343). Synthetic roles only; each test's database is its own copy.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.startup_safety import (
    StartupRefusedError,
    check_database_role,
    database_role_problems,
    main,
)
from tests import pg_databases
from tests.audit_support import OWNER_URL, migrated_database
from tests.auth_support import PRODUCTION_MAIL, PRODUCTION_RUNTIME, T1
from tests.db_support import ON_POSTGRES

pytestmark = pytest.mark.skipif(
    not (ON_POSTGRES and OWNER_URL),
    reason="needs PostgreSQL and KERP_TEST_OWNER_URL; runs in CI's PostgreSQL jobs (TD-052)",
)

PRODUCTION = Settings(
    environment="production", default_tenant_id=T1, **PRODUCTION_MAIL, **PRODUCTION_RUNTIME
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    migrated = migrated_database(tmp_path)
    database = next(migrated)
    try:
        yield database
    finally:
        next(migrated, None)


def _owner_url(db: Database) -> str:
    """The owner role (CI: the superuser) on the test's database."""
    if not pg_databases.PER_TEST:
        return OWNER_URL
    name = make_url(str(db.engine.url)).database
    assert name, db.engine.url
    return pg_databases.owner_url(name)


def _as_owner(db: Database, *statements: str) -> None:
    engine = create_engine(_owner_url(db))
    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
    finally:
        engine.dispose()


def _runtime_role(db: Database) -> str:
    role = make_url(str(db.engine.url)).username
    assert role, db.engine.url
    return role


def _refused(database: Database) -> str:
    with pytest.raises(StartupRefusedError) as refused:
        check_database_role(database, PRODUCTION)
    return str(refused.value)


def test_the_runtime_role_starts_in_production(db: Database) -> None:
    assert database_role_problems(db) == []
    assert check_database_role(db, PRODUCTION) == []


def test_a_superuser_is_refused(db: Database) -> None:
    owner = Database(_owner_url(db))
    try:
        message = _refused(owner)
    finally:
        owner.dispose()
    assert "is a SUPERUSER (bypasses RLS)" in message
    # A superuser holds every privilege, so the ledger guard reports it too.
    assert "audit_ledger: current role has UPDATE" in message


def test_a_bypassrls_role_is_refused(db: Database) -> None:
    role = f"kerp_t_bypass_{uuid.uuid4().hex[:12]}"
    password = "synthetic-test-only"  # a throwaway role, dropped below
    _as_owner(db, f"CREATE ROLE {role} LOGIN NOSUPERUSER BYPASSRLS PASSWORD '{password}'")
    url = make_url(str(db.engine.url)).set(username=role, password=password)
    bypassing = Database(url.render_as_string(hide_password=False))
    try:
        message = _refused(bypassing)
        assert f"database role '{role}' has BYPASSRLS" in message
        assert "is a SUPERUSER" not in message
    finally:
        bypassing.dispose()
        _as_owner(db, f"DROP ROLE {role}")


def test_an_rls_table_without_force_is_refused(db: Database) -> None:
    # The owner of a table that is not FORCEd skips its policies (TD-056).
    _as_owner(db, "ALTER TABLE oe_projects_project NO FORCE ROW LEVEL SECURITY")
    assert "oe_projects_project: row level security not forced" in _refused(db)


def test_update_or_delete_on_the_ledger_is_refused(db: Database) -> None:
    _as_owner(db, f"GRANT UPDATE, DELETE ON audit_ledger TO {_runtime_role(db)}")
    message = _refused(db)
    assert "audit_ledger: current role has UPDATE" in message
    assert "audit_ledger: current role has DELETE" in message


def test_a_missing_ledger_is_reported_not_a_crash(db: Database) -> None:
    _as_owner(db, "ALTER TABLE audit_ledger RENAME TO audit_ledger_moved")
    assert "audit_ledger: table not found" in _refused(db)


def test_a_disabled_ledger_trigger_is_refused(db: Database) -> None:
    _as_owner(db, "ALTER TABLE audit_ledger DISABLE TRIGGER audit_ledger_append_only")
    assert "trigger audit_ledger_append_only missing or disabled" in _refused(db)


@pytest.mark.parametrize("environment", ["development", "test"])
def test_outside_production_the_same_problems_only_warn(db: Database, environment: str) -> None:
    _as_owner(db, "ALTER TABLE oe_projects_project NO FORCE ROW LEVEL SECURITY")
    settings: Any = Settings(environment=environment)  # type: ignore[arg-type]
    assert check_database_role(db, settings) == [
        "oe_projects_project: row level security not forced"
    ]


def test_the_preflight_reports_and_exits_non_zero(
    db: Database, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KERP_DATABASE_URL", db.engine.url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    assert main([]) == 0
    assert capsys.readouterr().out == "database role checks passed\n"
    _as_owner(db, "ALTER TABLE oe_projects_project NO FORCE ROW LEVEL SECURITY")
    assert main([]) == 1
    out = capsys.readouterr().out
    assert out == "unsafe database role: oe_projects_project: row level security not forced\n"
