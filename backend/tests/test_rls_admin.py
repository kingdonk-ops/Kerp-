"""Admin grants and membership-based visibility under PostgreSQL RLS (T-0014, TD-053, edge
spec §6 amendments).

Raw SQL skips the ORM scope, so only the policies of migration ``0015`` decide here. Needs
real PostgreSQL and the ``NOSUPERUSER NOBYPASSRLS`` role ``kerp_app`` (CI's PostgreSQL step).
All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.audit import service as audit
from app.core.auth.access import SYSTEM_ADMIN
from app.core.auth.models import ProjectMembership
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_connection, rls_enforcement_problems
from app.modules.admin import service as admin
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, T1, auth_app, make_settings, seed_user
from tests.db_support import ON_POSTGRES, database_url
from tests.test_rls_bypass import RLS_ERROR

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)"
)

ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
CAROL = "carol@example.test"
DAVE = "dave@example.test"
PORTAL = "portal@example.test"


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


def _execute(db: Database, ctx: TenancyContext | None, sql: str, **params: Any) -> None:
    with db.engine.begin() as conn:
        bind_connection(conn, ctx)
        conn.execute(text(sql), params)


def _count(db: Database, ctx: TenancyContext | None, sql: str, **params: Any) -> int:
    with db.engine.begin() as conn:
        bind_connection(conn, ctx)
        return conn.execute(text(sql), params).rowcount


def _as(user: uuid.UUID, **extra: Any) -> TenancyContext:
    return TenancyContext(tenant_id=T1, user_id=user, **extra)


_INSERT_GRANT = (
    "INSERT INTO oe_admin_grant (id, tenant_id, project_id, user_id, kind, created_at,"
    " updated_at, custom_fields, source, revision)"
    " VALUES (gen_random_uuid(), :t, NULL, :u, :k, now(), now(), '{}', 'api', 1)"
)


# --- admin grants ---------------------------------------------------------------------------


def test_admin_grant_rows_are_own_rows_unless_system_admin(db: Database) -> None:
    alice, bob, carol, dave = (seed_user(db, T1, e) for e in (ALICE, BOB, CAROL, DAVE))
    with db.unit_of_work(ROOT) as s:
        admin.grant(s, alice, SYSTEM_ADMIN, actor_id=None)
        admin.grant(s, bob, "create_projects", actor_id=alice)
        admin.grant(s, carol, "create_projects", actor_id=alice)
    select_all = "SELECT user_id, kind FROM oe_admin_grant"

    # A non-admin user sees only their own row ...
    assert _rows(db, _as(bob), select_all) == [(bob, "create_projects")]
    # ... and cannot insert, update or delete another user's (or even their own).
    for user, kind in ((carol, SYSTEM_ADMIN), (bob, SYSTEM_ADMIN)):
        with pytest.raises(DBAPIError, match=RLS_ERROR):
            _execute(db, _as(bob), _INSERT_GRANT, t=T1, u=user, k=kind)
    for target in (carol, bob):
        update = "UPDATE oe_admin_grant SET kind = 'system_admin' WHERE user_id = :u"
        assert _count(db, _as(bob), update, u=target) == 0
        assert _count(db, _as(bob), "DELETE FROM oe_admin_grant WHERE user_id = :u", u=target) == 0

    # A System admin session reads and writes every row.
    admin_ctx = _as(alice, is_system_admin=True)
    assert len(_rows(db, admin_ctx, select_all)) == 3
    _execute(db, admin_ctx, _INSERT_GRANT, t=T1, u=dave, k="create_projects")
    assert _count(db, admin_ctx, "DELETE FROM oe_admin_grant WHERE user_id = :u", u=dave) == 1

    # No flag and no grant row: nothing; unbound: nothing.
    assert _rows(db, _as(dave), select_all) == []
    assert _rows(db, TenancyContext(tenant_id=T1), select_all) == []
    assert _rows(db, None, select_all) == []
    assert _rows(db, _as(bob, is_system_admin=False), select_all) == [(bob, "create_projects")]


def test_the_auth_lookup_function_returns_only_the_callers_own_grants(db: Database) -> None:
    alice, bob, dave = (seed_user(db, T1, e) for e in (ALICE, BOB, DAVE))
    with db.unit_of_work(ROOT) as s:
        admin.grant(s, alice, SYSTEM_ADMIN, actor_id=None)
        admin.grant(s, bob, "create_projects", actor_id=alice)
    lookup = "SELECT kind FROM kerp_caller_admin_grants()"
    assert _rows(db, _as(alice), lookup) == [(SYSTEM_ADMIN,)]
    assert _rows(db, _as(bob), lookup) == [("create_projects",)]
    assert _rows(db, _as(dave), lookup) == []
    # Even an admin-flagged session only gets its own grants back from the function.
    assert _rows(db, _as(dave, is_system_admin=True), lookup) == []
    assert _rows(db, None, lookup) == []


# --- membership-based visibility ------------------------------------------------------------


class Seed:
    def __init__(self, db: Database) -> None:
        self.portal, self.alice, self.bob = (seed_user(db, T1, e) for e in (PORTAL, ALICE, BOB))
        with db.unit_of_work(ROOT) as s:
            admin.grant(s, self.alice, SYSTEM_ADMIN, actor_id=None)
            self.a = projects.create_project(s, ProjectCreate(code="A", name="Synthetic A")).id
            self.b = projects.create_project(s, ProjectCreate(code="B", name="Synthetic B")).id
            for user, project, persona in (
                (self.portal, self.a, "PER-15"),
                (self.bob, self.a, "PER-09"),
                (self.bob, self.b, "PER-02"),
            ):
                s.add(
                    ProjectMembership(
                        tenant_id=T1, user_id=user, member_project_id=project, persona=persona
                    )
                )
        # A project-scoped record of B (an audit entry written in B's scope).
        with db.unit_of_work(TenancyContext(tenant_id=T1, project_id=self.b)) as s:
            audit.append(s, f"widget:{uuid.uuid4()}", {"note": "synthetic"})


@pytest.fixture
def seed(db: Database) -> Seed:
    return Seed(db)


def test_a_client_portal_user_bound_to_a_cannot_list_or_fetch_b(
    db: Database, seed: Seed, tmp_path: Path
) -> None:
    bound_a = _as(seed.portal, project_id=seed.a, member_project_ids=frozenset({seed.a}))
    assert _rows(db, bound_a, "SELECT code FROM oe_projects_project") == [("A",)]
    by_id = "SELECT code FROM oe_projects_project WHERE id = :id"
    assert _rows(db, bound_a, by_id, id=seed.b) == []
    members = "SELECT DISTINCT member_project_id FROM auth_project_membership"
    assert _rows(db, bound_a, members) == [(seed.a,)]
    # Through the API with a real token: the same answer, B is 404.
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"email": PORTAL, "password": PASSWORD})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert [p["code"] for p in client.get("/api/v1/projects", headers=headers).json()] == ["A"]
        assert client.get(f"/api/v1/projects/{seed.b}", headers=headers).status_code == 404
        assert client.get(f"/api/v1/projects/{seed.a}", headers=headers).status_code == 200
        mine = client.get("/api/v1/me/projects", headers=headers).json()
        assert [p["code"] for p in mine] == ["A"]


def test_a_system_admin_lists_every_project_but_reads_no_project_record(
    db: Database, seed: Seed
) -> None:
    root = _as(seed.alice, is_system_admin=True)
    assert sorted(_rows(db, root, "SELECT code FROM oe_projects_project")) == [("A",), ("B",)]
    members = "SELECT count(*) FROM auth_project_membership"
    assert _rows(db, root, members) == [(3,)]
    records = "SELECT count(*) FROM audit_ledger WHERE project_id = :b"
    assert _rows(db, root, records, b=seed.b) == [(0,)]
    # The record exists: a session bound to B (with a membership there) reads it.
    bound_b = _as(seed.bob, project_id=seed.b, member_project_ids=frozenset({seed.a, seed.b}))
    assert _rows(db, bound_b, records, b=seed.b) == [(1,)]


def test_a_plain_tenant_session_sees_no_project_or_foreign_membership(
    db: Database, seed: Seed
) -> None:
    plain = TenancyContext(tenant_id=T1)
    assert _rows(db, plain, "SELECT id FROM oe_projects_project") == []
    assert _rows(db, plain, "SELECT id FROM auth_project_membership") == []
    # The scope resolver's own-rows clause: a user reads their own memberships (TD-152).
    own = "SELECT member_project_id FROM auth_project_membership ORDER BY persona"
    assert _rows(db, _as(seed.bob), own) == [(seed.b,), (seed.a,)]


def test_membership_writes_need_the_project_in_view(db: Database, seed: Seed) -> None:
    # Bob administers A (PER-09) and is a member of B: he sees both projects' rows here.
    bob = _as(seed.bob, member_project_ids=frozenset({seed.a, seed.b}))
    portal = _as(seed.portal, member_project_ids=frozenset({seed.a}))
    soft_delete = (
        "UPDATE auth_project_membership SET deleted_at = now()"
        " WHERE member_project_id = :p AND persona = 'PER-02'"
    )
    assert _count(db, portal, soft_delete, p=seed.b) == 0  # B is not in view
    rename = "UPDATE oe_projects_project SET name = 'Hijacked' WHERE id = :p"
    assert _count(db, portal, rename, p=seed.b) == 0
    assert _count(db, bob, rename, p=seed.b) == 1
    move = (
        "UPDATE auth_project_membership SET member_project_id = :elsewhere"
        " WHERE member_project_id = :a"
    )
    with pytest.raises(DBAPIError, match=RLS_ERROR):
        _execute(db, bob, move, elsewhere=uuid.uuid4(), a=seed.a)
