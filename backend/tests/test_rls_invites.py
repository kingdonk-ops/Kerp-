"""Project invites under PostgreSQL RLS (T-0052, edge spec §6 amendments): visible to the
PER-09 holders of the invited project and to System admins only, never to the invitee; the
same people insert and update; nobody deletes.

Raw SQL skips the ORM scope, so only the policies of migration ``0030`` decide here. Needs real
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

from app.core.db import Base, Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import (
    bind_connection,
    rls_coverage_problems,
    rls_enforcement_problems,
)
from tests.audit_support import migrated_database
from tests.auth_support import T1, T2, seed_user
from tests.db_support import ON_POSTGRES
from tests.test_rls_bypass import RLS_ERROR

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)"
)

ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
TABLE = "oe_admin_project_invite"
IDS = f"SELECT id FROM {TABLE}"
_INSERT = (
    f"INSERT INTO {TABLE} (id, tenant_id, project_id, invited_project_id, invited_email, grants,"
    " inviter_id, token_hash, expires_at, last_sent_at, sent_at, status, created_at, updated_at,"
    " custom_fields, source, revision)"
    " VALUES (:id, :t, NULL, :p, :e, '[]', :u, :h, now(), now(), '[]', 'pending', now(), now(),"
    " '{}', 'api', 1)"
)
_REVOKE = f"UPDATE {TABLE} SET status = 'revoked' WHERE id = :id"


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
    """PAM (PER-09 of HID) invites EXT to HID. PETE is PER-09 of OTH, IVAN a member of HID
    without PER-09. Contexts carry what authentication binds."""

    def __init__(self, db: Database) -> None:
        self.ext = seed_user(db, T1, "ext@example.test")
        self.pam = seed_user(db, T1, "pam@example.test")
        self.pete = seed_user(db, T1, "pete@example.test")
        self.ivan = seed_user(db, T1, "ivan@example.test")
        self.hid, self.oth = uuid.uuid4(), uuid.uuid4()
        self.invite = uuid.uuid4()
        self.insert(db, self.per09(self.pam, self.hid), self.invite, self.hid, self.pam)

    def per09(self, user: uuid.UUID, project: uuid.UUID) -> TenancyContext:
        ids = frozenset({project})
        return _as(user, member_project_ids=ids, admin_project_ids=ids)

    def insert(
        self,
        db: Database,
        ctx: TenancyContext | None,
        invite: uuid.UUID,
        project: uuid.UUID,
        inviter: uuid.UUID,
        tenant: uuid.UUID = T1,
    ) -> int:
        params = {"id": invite, "t": tenant, "p": project, "u": inviter}
        return _count(db, ctx, _INSERT, e="ext@example.test", h=uuid.uuid4().hex * 2, **params)


def test_the_migrated_schema_has_the_invite_policies(db: Database) -> None:
    with db.engine.connect() as conn:
        assert rls_coverage_problems(conn, Base.metadata) == []


def test_an_invite_is_visible_to_the_projects_per09_and_system_admins_only(db: Database) -> None:
    seed = Seed(db)
    mine = [(seed.invite,)]
    assert _run(db, seed.per09(seed.pam, seed.hid), IDS) == mine
    assert _run(db, _as(seed.pete, is_system_admin=True), IDS) == mine
    assert _run(db, ROOT, IDS) == mine
    # The invitee, a PER-09 of another project, a member without PER-09: nothing.
    assert _run(db, _as(seed.ext), IDS) == []
    assert _run(db, seed.per09(seed.pete, seed.oth), IDS) == []
    assert _run(db, _as(seed.ivan, member_project_ids=frozenset({seed.hid})), IDS) == []
    bound = _as(seed.ivan, allowed_project_ids=frozenset({seed.hid}), project_id=seed.hid)
    assert _run(db, bound, IDS) == []
    assert _run(db, TenancyContext(tenant_id=T1), IDS) == []
    assert _run(db, None, IDS) == []
    # Another tenant's System admin sees nothing either.
    assert _run(db, TenancyContext(tenant_id=T2, is_system_admin=True), IDS) == []


def test_only_the_projects_per09_and_system_admins_insert(db: Database) -> None:
    seed = Seed(db)
    for ctx in (
        _as(seed.ext),  # the invitee cannot invite themselves
        seed.per09(seed.pete, seed.oth),  # PER-09 elsewhere
        _as(seed.ivan, member_project_ids=frozenset({seed.hid})),
        TenancyContext(tenant_id=T1),
        None,
    ):
        with pytest.raises(DBAPIError, match=RLS_ERROR):
            seed.insert(db, ctx, uuid.uuid4(), seed.hid, seed.pete)
    # Not from a project-bound session (the row is tenant-level), not into another tenant.
    bound = TenancyContext(
        tenant_id=T1, user_id=seed.pam, project_id=seed.hid, admin_project_ids=frozenset({seed.hid})
    )
    with pytest.raises(DBAPIError, match=RLS_ERROR):
        seed.insert(db, bound, uuid.uuid4(), seed.hid, seed.pam)
    with pytest.raises(DBAPIError, match=RLS_ERROR):
        seed.insert(db, seed.per09(seed.pam, seed.hid), uuid.uuid4(), seed.hid, seed.pam, T2)
    assert seed.insert(db, ROOT, uuid.uuid4(), seed.oth, seed.pam) == 1
    assert len(_run(db, ROOT, IDS)) == 2


def test_only_they_update_and_nobody_deletes(db: Database) -> None:
    seed = Seed(db)
    for ctx in (
        _as(seed.ext),
        seed.per09(seed.pete, seed.oth),
        _as(seed.ivan, member_project_ids=frozenset({seed.hid})),
        None,
    ):
        assert _count(db, ctx, _REVOKE, id=seed.invite) == 0
    # An administrator cannot move an invite to a project they do not administer.
    with pytest.raises(DBAPIError, match=RLS_ERROR):
        _count(
            db,
            seed.per09(seed.pam, seed.hid),
            f"UPDATE {TABLE} SET invited_project_id = :p WHERE id = :id",
            p=seed.oth,
            id=seed.invite,
        )
    for ctx in (seed.per09(seed.pam, seed.hid), ROOT, _as(seed.ext)):
        assert _count(db, ctx, f"DELETE FROM {TABLE} WHERE id = :id", id=seed.invite) == 0
    assert _count(db, seed.per09(seed.pam, seed.hid), _REVOKE, id=seed.invite) == 1
    assert _run(db, ROOT, f"SELECT status FROM {TABLE}") == [("revoked",)]
