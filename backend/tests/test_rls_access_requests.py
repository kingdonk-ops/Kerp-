"""Access requests under PostgreSQL RLS (T-0050, edge spec §6 amendments): visible to the
requester, the PER-09 holders of the request's project and System admins only; only the
requester inserts; nobody deletes. Plus the narrow read ``kerp_lobby_projects_by_id``.

Raw SQL skips the ORM scope, so only the policies of migration ``0023`` decide here. Needs real
PostgreSQL and the ``NOSUPERUSER NOBYPASSRLS`` role ``kerp_app`` (CI's PostgreSQL step). All
data is synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.auth import service as users
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_connection, rls_enforcement_problems
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import T1, T2, seed_user
from tests.db_support import ON_POSTGRES
from tests.test_rls_bypass import RLS_ERROR

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)"
)

ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
TABLE = "oe_access_requests_request"
IDS = f"SELECT id FROM {TABLE}"
BY_ID = (
    "SELECT id, code, name, visible_in_lobby FROM kerp_lobby_projects_by_id(CAST(:ids AS uuid[]))"
)
_INSERT = (
    f"INSERT INTO {TABLE} (id, tenant_id, project_id, requester_id, requested_project_id,"
    " status, created_at, updated_at, custom_fields, source, revision)"
    " VALUES (:id, :t, NULL, :u, :p, 'pending', now(), now(), '{}', 'api', 1)"
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


def _as(user: uuid.UUID, tenant: uuid.UUID = T1, **extra: Any) -> TenancyContext:
    return TenancyContext(tenant_id=tenant, user_id=user, **extra)


def _run(db: Database, ctx: TenancyContext | None, sql: str, **params: Any) -> list[Any]:
    with db.engine.begin() as conn:
        bind_connection(conn, ctx)
        return list(conn.execute(text(sql), params).all())


def _count(db: Database, ctx: TenancyContext | None, sql: str, **params: Any) -> int:
    with db.engine.begin() as conn:
        bind_connection(conn, ctx)
        return conn.execute(text(sql), params).rowcount


class Seed:
    """EXT files a request for HID. PAM is PER-09 of HID, PETE PER-09 of OTH, IVAN a member
    of HID without PER-09, EXT2 a bystander. Contexts carry what authentication binds."""

    def __init__(self, db: Database) -> None:
        self.ext = seed_user(db, T1, "ext@example.test")
        self.ext2 = seed_user(db, T1, "ext2@example.test")
        self.pam = seed_user(db, T1, "pam@example.test")
        self.pete = seed_user(db, T1, "pete@example.test")
        self.ivan = seed_user(db, T1, "ivan@example.test")
        with db.unit_of_work(ROOT) as s:
            self.hid = projects.create_project(s, ProjectCreate(code="HID", name="Synthetic H")).id
            self.oth = projects.create_project(s, ProjectCreate(code="OTH", name="Synthetic O")).id
        self.request = uuid.uuid4()
        _count(db, _as(self.ext), _INSERT, id=self.request, t=T1, u=self.ext, p=self.hid)

    def per09(self, user: uuid.UUID, project: uuid.UUID) -> TenancyContext:
        ids = frozenset({project})
        return _as(user, member_project_ids=ids, admin_project_ids=ids)


def test_a_request_is_visible_to_requester_per09_and_system_admin_only(db: Database) -> None:
    seed = Seed(db)
    mine = [(seed.request,)]
    assert _run(db, _as(seed.ext), IDS) == mine
    assert _run(db, seed.per09(seed.pam, seed.hid), IDS) == mine
    assert _run(db, _as(seed.pam, is_system_admin=True), IDS) == mine
    assert _run(db, ROOT, IDS) == mine
    # Another user, a PER-09 of a different project, a member without PER-09: nothing.
    assert _run(db, _as(seed.ext2), IDS) == []
    assert _run(db, seed.per09(seed.pete, seed.oth), IDS) == []
    member = _as(seed.ivan, member_project_ids=frozenset({seed.hid}))
    assert _run(db, member, IDS) == []
    allowed = _as(seed.ivan, allowed_project_ids=frozenset({seed.hid}), project_id=seed.hid)
    assert _run(db, allowed, IDS) == []
    assert _run(db, TenancyContext(tenant_id=T1), IDS) == []
    assert _run(db, None, IDS) == []
    # The same user id in another tenant sees nothing either.
    assert _run(db, _as(seed.ext, T2), IDS) == []


def test_only_the_requester_inserts_their_own_request(db: Database) -> None:
    seed = Seed(db)
    for ctx in (_as(seed.ext2), seed.per09(seed.pam, seed.hid), ROOT):
        with pytest.raises(DBAPIError, match=RLS_ERROR):
            _count(db, ctx, _INSERT, id=uuid.uuid4(), t=T1, u=seed.ext, p=seed.oth)
    # Own row, but from a project-bound session: not the exact tenant-level scope.
    bound = _as(seed.ext, project_id=seed.oth)
    with pytest.raises(DBAPIError, match=RLS_ERROR):
        _count(db, bound, _INSERT, id=uuid.uuid4(), t=T1, u=seed.ext, p=seed.oth)


def test_others_cannot_update_and_nobody_deletes(db: Database) -> None:
    seed = Seed(db)
    withdraw = f"UPDATE {TABLE} SET status = 'withdrawn', withdrawn_at = now() WHERE id = :id"
    for ctx in (_as(seed.ext2), seed.per09(seed.pete, seed.oth)):
        assert _count(db, ctx, withdraw, id=seed.request) == 0
    delete = f"DELETE FROM {TABLE} WHERE id = :id"
    for ctx in (_as(seed.ext), seed.per09(seed.pam, seed.hid), ROOT):
        assert _count(db, ctx, delete, id=seed.request) == 0
    assert _run(db, ROOT, f"SELECT status FROM {TABLE}") == [("pending",)]
    # A PER-09 of the project cannot move the row to another project they do not administer.
    move = f"UPDATE {TABLE} SET requested_project_id = :p WHERE id = :id"
    with pytest.raises(DBAPIError, match=RLS_ERROR):
        _count(db, seed.per09(seed.pam, seed.hid), move, p=seed.oth, id=seed.request)
    decide = (
        f"UPDATE {TABLE} SET status = 'rejected', decided_at = now(), decided_by = :u"
        " WHERE id = :id"
    )
    assert _count(db, seed.per09(seed.pam, seed.hid), decide, u=seed.pam, id=seed.request) == 1


def test_the_narrow_read_by_id_returns_live_projects_to_active_users(db: Database) -> None:
    seed = Seed(db)
    ids = [seed.hid, seed.oth, uuid.uuid4()]
    found = _run(db, _as(seed.ext2), BY_ID, ids=ids)
    assert found == [
        (seed.hid, "HID", "Synthetic H", False),
        (seed.oth, "OTH", "Synthetic O", False),
    ]
    # The full project row stays invisible to the non-member (policy not widened), and the
    # caller's System-admin flag is restored.
    with db.engine.begin() as conn:
        bind_connection(conn, _as(seed.ext2))
        conn.execute(text(BY_ID), {"ids": ids}).all()
        assert conn.execute(text("SELECT * FROM oe_projects_project")).all() == []
        flag = conn.execute(text("SELECT current_setting('app.is_system_admin', true)")).scalar()
        assert flag in (None, "")
    assert _run(db, TenancyContext(tenant_id=T1), BY_ID, ids=ids) == []  # no user bound
    assert _run(db, _as(seed.ext2, T2), BY_ID, ids=ids) == []  # another tenant
    with db.unit_of_work(ROOT) as s:
        projects.delete_project(s, seed.oth)
    assert [row.code for row in _run(db, _as(seed.ext2), BY_ID, ids=ids)] == ["HID"]
    with db.unit_of_work(ROOT) as s:
        users.get_user(s, seed.ext2).is_active = False
    assert _run(db, _as(seed.ext2), BY_ID, ids=ids) == []  # deactivated


def test_only_the_migrating_app_role_may_execute_the_narrow_read(db: Database) -> None:
    signature = "kerp_lobby_projects_by_id(uuid[])"
    can_execute = (
        f"SELECT has_function_privilege(current_user, '{signature}', 'EXECUTE'),"
        f" has_function_privilege('public', '{signature}', 'EXECUTE')"
    )
    assert _run(db, None, can_execute) == [(True, False)]
    definer = "SELECT prosecdef, proconfig FROM pg_proc WHERE proname = 'kerp_lobby_projects_by_id'"
    assert _run(db, None, definer) == [(True, ["search_path=public, pg_temp"])]
