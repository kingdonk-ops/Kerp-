"""The narrow lobby read under PostgreSQL RLS (T-0040, edge spec §6 amendments).

Raw SQL skips the ORM scope, so only the policies of migration ``0015`` and the functions of
migration ``0021`` decide here. Needs real PostgreSQL and the ``NOSUPERUSER NOBYPASSRLS``
role ``kerp_app`` (CI's PostgreSQL step). All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from app.core.auth import service as users
from app.core.auth.models import ProjectMembership
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_connection, rls_enforcement_problems
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import T1, T2, seed_user
from tests.db_support import ON_POSTGRES

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)"
)

ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
LIST = "SELECT id, code, name FROM kerp_lobby_projects()"
BY_CODE = "SELECT id, code, name FROM kerp_lobby_project_by_code(:code)"
FULL_ROWS = "SELECT * FROM oe_projects_project"
ADMIN_FLAG = "SELECT current_setting('app.is_system_admin', true)"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    migrated = migrated_database(tmp_path)
    database = next(migrated)
    try:
        assert rls_enforcement_problems(database.engine) == []  # really kerp_app
        yield database
    finally:
        next(migrated, None)


class Seed:
    """INES internal, EXT external, MEMBER internal and PER-02 of VIS. VIS lobby-visible,
    HID not; T2 has a lobby-visible project of its own."""

    def __init__(self, db: Database) -> None:
        self.ines = seed_user(db, T1, "ines@staff.example.test", is_internal=True)
        self.ext = seed_user(db, T1, "ext@example.test")
        self.member = seed_user(db, T1, "member@staff.example.test", is_internal=True)
        with db.unit_of_work(ROOT) as s:
            self.vis = projects.create_project(s, ProjectCreate(code="VIS", name="Synthetic V"))
            self.hid = projects.create_project(s, ProjectCreate(code="HID", name="Synthetic H"))
            self.vis.visible_in_lobby = True
            s.add(
                ProjectMembership(
                    tenant_id=T1,
                    user_id=self.member,
                    member_project_id=self.vis.id,
                    persona="PER-02",
                )
            )
            self.vis_id, self.hid_id = self.vis.id, self.hid.id
        other = TenancyContext(tenant_id=T2, is_system_admin=True)
        self.t2_user = seed_user(db, T2, "ines@staff.example.test", is_internal=True)
        with db.unit_of_work(other) as s:
            p = projects.create_project(s, ProjectCreate(code="VIS", name="Other tenant"))
            p.visible_in_lobby = True


def _as(user: uuid.UUID, tenant: uuid.UUID = T1, **extra: Any) -> TenancyContext:
    return TenancyContext(tenant_id=tenant, user_id=user, **extra)


def _run(db: Database, ctx: TenancyContext | None, *sql: str, **params: Any) -> list[list[Any]]:
    """Run each statement in one transaction bound to ``ctx``; the rows of each."""
    with db.engine.begin() as conn:
        bind_connection(conn, ctx)
        return [list(conn.execute(text(q), params).all()) for q in sql]


def test_the_lobby_function_lists_visible_projects_to_internal_users_only(db: Database) -> None:
    seed = Seed(db)
    expected = [(seed.vis_id, "VIS", "Synthetic V")]
    assert _run(db, _as(seed.ines), LIST) == [expected]
    assert _run(db, _as(seed.member), LIST) == [expected]
    assert _run(db, _as(seed.ext), LIST) == [[]]  # external: the function itself refuses
    assert _run(db, _as(seed.ext, is_system_admin=True), LIST) == [[]]
    assert _run(db, TenancyContext(tenant_id=T1), LIST) == [[]]  # no user bound
    assert _run(db, None, LIST) == [[]]
    # The other tenant's lobby, for its own internal user only.
    other = _run(db, _as(seed.t2_user, T2), LIST)[0]
    assert [(code, name) for _id, code, name in other] == [("VIS", "Other tenant")]
    assert _run(db, _as(seed.ines, T2), LIST) == [[]]  # T1's user id is unknown in T2


def test_the_code_function_finds_any_live_project_for_any_active_user(db: Database) -> None:
    seed = Seed(db)
    assert _run(db, _as(seed.ext), BY_CODE, code="HID") == [[(seed.hid_id, "HID", "Synthetic H")]]
    assert _run(db, _as(seed.ines), BY_CODE, code="VIS") == [[(seed.vis_id, "VIS", "Synthetic V")]]
    for code in ("hid", "NOPE", "%", "H_D", "HID' OR '1'='1"):
        assert _run(db, _as(seed.ext), BY_CODE, code=code) == [[]]
    assert _run(db, TenancyContext(tenant_id=T1), BY_CODE, code="HID") == [[]]
    with db.unit_of_work(ROOT) as s:
        projects.delete_project(s, seed.hid_id)
    assert _run(db, _as(seed.ext), BY_CODE, code="HID") == [[]]
    with db.unit_of_work(ROOT) as s:
        users.get_user(s, seed.ext).is_active = False
    assert _run(db, _as(seed.ext), BY_CODE, code="VIS") == [[]]  # deactivated


def test_a_non_member_still_cannot_read_the_project_row(db: Database) -> None:
    """The functions return three columns and leave the ``projects`` policy as it was: in the
    same transaction, before and after the call, the full row stays invisible and the
    caller's ``app.is_system_admin`` is what it was."""
    seed = Seed(db)
    before, listed, after, flag, found, again = _run(
        db,
        _as(seed.ines),
        FULL_ROWS,
        LIST,
        FULL_ROWS,
        ADMIN_FLAG,
        "SELECT * FROM kerp_lobby_project_by_code('HID')",
        f"{FULL_ROWS} WHERE code = 'HID'",
    )
    assert (before, after, again) == ([], [], [])
    assert [len(row) for row in listed] == [3]
    assert flag[0][0] in (None, "")  # not left raised by the function
    assert [len(row) for row in found] == [3]
    # A member reads its own project row (unchanged membership policy), nothing more.
    member = _as(seed.member, member_project_ids=frozenset({seed.vis_id}))
    member_rows = _run(db, member, FULL_ROWS)
    assert [row.code for row in member_rows[0]] == ["VIS"]


def test_only_the_migrating_app_role_may_execute_the_lobby_functions(db: Database) -> None:
    for signature in ("kerp_lobby_projects()", "kerp_lobby_project_by_code(text)"):
        can_execute = (
            f"SELECT has_function_privilege(current_user, '{signature}', 'EXECUTE'),"
            f" has_function_privilege('public', '{signature}', 'EXECUTE')"
        )
        assert _run(db, None, can_execute) == [[(True, False)]]
    definer = (
        "SELECT proname, prosecdef, proconfig FROM pg_proc"
        " WHERE proname IN ('kerp_lobby_projects', 'kerp_lobby_project_by_code') ORDER BY proname"
    )
    rows = _run(db, None, definer)[0]
    assert [(name, secdef) for name, secdef, _config in rows] == [
        ("kerp_lobby_project_by_code", True),
        ("kerp_lobby_projects", True),
    ]
    assert all(config == ["search_path=public, pg_temp"] for _n, _s, config in rows)
