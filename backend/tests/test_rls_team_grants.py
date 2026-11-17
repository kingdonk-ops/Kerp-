"""Team-derived membership rows under PostgreSQL RLS (T-0073, ADR 0008).

Raw SQL skips the ORM scope, so only the policies of migration ``0025`` decide here: only a
``system_admin`` session inserts, updates or deletes ``team_link`` rows, a derived grant
widens the membership-based ``projects`` policy like an individual one, and the startup
census function counts live derived rows of every tenant without a bound tenant. Needs real
PostgreSQL and the ``NOSUPERUSER NOBYPASSRLS`` role ``kerp_app`` (CI's PostgreSQL step).
All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.auth.access import resolve_request_scope
from app.core.auth.team_grants import (
    COUNT_FUNCTION,
    live_team_link_grant_count,
    sync_team_link_grants,
)
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_connection, rls_enforcement_problems
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, BOB, T1, T2, seed_user
from tests.db_support import ON_POSTGRES
from tests.test_rls_bypass import RLS_ERROR

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)"
)

ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
LINK = uuid.UUID("00000000-0000-4000-8000-00000000011a")
TABLE = "auth_project_membership"
TEAM = "WHERE source_type = 'team_link'"
_INSERT = (
    "INSERT INTO auth_project_membership (id, tenant_id, project_id, user_id, member_project_id,"
    " persona, source_type, source_id, created_at, updated_at, custom_fields, source, revision)"
    " VALUES (gen_random_uuid(), :t, NULL, :u, :p, 'PER-04', :st, :sid, now(), now(), '{}',"
    " 'api', 1)"
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    migrated = migrated_database(tmp_path)
    database = next(migrated)
    try:
        assert rls_enforcement_problems(database.engine) == []  # really kerp_app
        yield database
    finally:
        next(migrated, None)


def _rows(db: Database, ctx: TenancyContext | None, sql: str, **params: Any) -> list[Any]:
    with db.engine.begin() as conn:
        bind_connection(conn, ctx)
        return list(conn.execute(text(sql), params).all())


def _count(db: Database, ctx: TenancyContext | None, sql: str, **params: Any) -> int:
    with db.engine.begin() as conn:
        bind_connection(conn, ctx)
        return conn.execute(text(sql), params).rowcount


def _project(db: Database, code: str) -> uuid.UUID:
    with db.unit_of_work(ROOT) as s:
        return projects.create_project(s, ProjectCreate(code=code, name=f"Synthetic {code}")).id


def _sync(db: Database, ctx: TenancyContext, user: uuid.UUID, project: uuid.UUID) -> None:
    with db.unit_of_work(ctx) as s:
        sync_team_link_grants(
            s,
            link_id=LINK,
            project_id=project,
            persona="PER-04",
            site_node_id=None,
            user_ids=[user],
        )


def test_only_system_admin_sessions_write_derived_rows(db: Database) -> None:
    alice, bob = seed_user(db, T1, ALICE), seed_user(db, T1, BOB)
    project = _project(db, "HOME")
    _sync(db, ROOT, alice, project)
    # BOB is PER-09 of the project: he sees its memberships and writes individual rows ...
    per09 = TenancyContext(tenant_id=T1, user_id=bob, member_project_ids=frozenset({project}))
    with db.engine.begin() as conn:
        bind_connection(conn, per09)
        conn.execute(text(_INSERT), {"t": T1, "u": bob, "p": project, "st": "direct", "sid": None})
    assert len(_rows(db, per09, f"SELECT id FROM {TABLE} {TEAM}")) == 1  # visible ...
    # ... but cannot insert, update or delete a derived row.
    with pytest.raises(DBAPIError, match=RLS_ERROR):
        _count(db, per09, _INSERT, t=T1, u=bob, p=project, st="team_link", sid=uuid.uuid4())
    assert _count(db, per09, f"UPDATE {TABLE} SET persona = 'PER-05' {TEAM}") == 0
    assert _count(db, per09, f"UPDATE {TABLE} SET deleted_at = now() {TEAM}") == 0
    assert _count(db, per09, f"DELETE FROM {TABLE} {TEAM}") == 0
    # Nor turn an individual row into a derived one.
    turn = (
        "UPDATE auth_project_membership SET source_type = 'team_link', source_id = :sid"
        " WHERE source_type = 'direct'"
    )
    with pytest.raises(DBAPIError, match=RLS_ERROR):
        _count(db, per09, turn, sid=uuid.uuid4())
    # A System admin session does all of it.
    admin = TenancyContext(tenant_id=T1, user_id=alice, is_system_admin=True)
    assert _count(db, admin, f"UPDATE {TABLE} SET deleted_at = now() {TEAM}") == 1
    assert _count(db, admin, f"DELETE FROM {TABLE} {TEAM}") == 1
    assert _count(db, admin, _INSERT, t=T1, u=bob, p=project, st="team_link", sid=uuid.uuid4()) == 1


def test_a_derived_grant_opens_its_project_row_and_no_other(db: Database) -> None:
    alice = seed_user(db, T1, ALICE)
    home = _project(db, "HOME")
    _project(db, "AWAY")
    _sync(db, ROOT, alice, home)
    scope = resolve_request_scope(db, TenancyContext(tenant_id=T1, user_id=alice))
    assert scope.member_project_ids == {home}
    assert _rows(db, scope, "SELECT id FROM oe_projects_project") == [(home,)]  # not AWAY


def test_the_census_function_counts_every_tenant_and_returns_nothing_else(db: Database) -> None:
    alice, other = seed_user(db, T1, ALICE), seed_user(db, T2, BOB)
    _sync(db, ROOT, alice, uuid.uuid4())
    _sync(db, TenancyContext(tenant_id=T2, is_system_admin=True), other, uuid.uuid4())
    assert live_team_link_grant_count(db) == 2
    # Unbound: the table itself shows nothing, the function only a number, and the census
    # setting it raised is back to the caller's value afterwards.
    assert _rows(db, None, "SELECT id FROM auth_project_membership") == []
    with db.engine.begin() as conn:
        bind_connection(conn, None)
        assert conn.execute(text(f"SELECT {COUNT_FUNCTION}()")).scalar_one() == 2
        census = "SELECT current_setting('app.team_link_census', true)"
        assert conn.execute(text(census)).scalar_one() in (None, "")
        assert conn.execute(text("SELECT id FROM auth_project_membership")).all() == []
    [(definer, config, public)] = _rows(
        db,
        None,
        "SELECT p.prosecdef, p.proconfig,"
        " has_function_privilege('public', p.oid, 'EXECUTE') FROM pg_proc p"
        " WHERE p.proname = :name",
        name=COUNT_FUNCTION,
    )
    assert definer is True
    assert config == ["search_path=public, pg_temp"]
    assert public is False
