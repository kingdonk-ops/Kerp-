"""Teams tables under PostgreSQL RLS (T-0013, ADR 0008).

Raw SQL skips the ORM scope, so only the ``team`` policies of migration ``0028`` decide here:
only a ``system_admin`` session inserts, updates or deletes team, member and link rows; any
other session reads a team, its members and its links only when the team is linked to a
project in its scope or the caller is a member; another tenant's rows are invisible. Needs
real PostgreSQL and the ``NOSUPERUSER NOBYPASSRLS`` role ``kerp_app`` (CI's PostgreSQL step).
All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.auth.access import Persona
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import TEAM_FUNCTION, bind_connection, rls_enforcement_problems
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.teams import service as teams
from app.modules.teams.models import LINK_TABLE, MEMBER_TABLE, TEAM_TABLE
from app.modules.teams.schemas import TeamCreate
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, BOB, T1, T2, seed_user
from tests.db_support import ON_POSTGRES
from tests.test_rls_bypass import RLS_ERROR

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)"
)

ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
CAROL = "carol@example.test"
TABLES = (TEAM_TABLE, MEMBER_TABLE, LINK_TABLE)
_NOW = "now(), now(), '{}', 'api', 1"
_RECORD = "id, tenant_id, project_id, created_at, updated_at, custom_fields, source, revision"


@dataclass(frozen=True)
class World:
    """Team A (member BOB) linked to HOME; team B (member CAROL) linked to AWAY;
    THIRD has no team; tenant T2 has its own team linked to its project."""

    db: Database
    bob: uuid.UUID
    carol: uuid.UUID
    home: uuid.UUID
    away: uuid.UUID
    third: uuid.UUID
    team_a: uuid.UUID
    team_b: uuid.UUID
    foreign_team: uuid.UUID


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    migrated = migrated_database(tmp_path)
    database = next(migrated)
    try:
        assert rls_enforcement_problems(database.engine) == []  # really kerp_app
        yield database
    finally:
        next(migrated, None)


def _project(db: Database, ctx: TenancyContext, code: str) -> uuid.UUID:
    with db.unit_of_work(ctx) as s:
        return projects.create_project(s, ProjectCreate(code=code, name=f"Synthetic {code}")).id


def _team(
    db: Database, ctx: TenancyContext, name: str, member: uuid.UUID, project: uuid.UUID
) -> uuid.UUID:
    with db.unit_of_work(ctx) as s:
        team = teams.create_team(s, TeamCreate(name=name), actor_id=None).id
        teams.add_member(s, team, member, confirm_sod=False, actor_id=None)
        teams.set_link(
            s, team, project, Persona.FIELD_WORKER, None, confirm_sod=False, actor_id=None
        )
        return team


@pytest.fixture
def world(db: Database) -> World:
    bob, carol = seed_user(db, T1, BOB), seed_user(db, T1, CAROL)
    home, away, third = (_project(db, ROOT, c) for c in ("HOME", "AWAY", "THIRD"))
    team_a = _team(db, ROOT, "Team A", bob, home)
    team_b = _team(db, ROOT, "Team B", carol, away)
    other = TenancyContext(tenant_id=T2, is_system_admin=True)
    zed = seed_user(db, T2, ALICE)
    foreign_team = _team(db, other, "Team A", zed, _project(db, other, "HOME"))
    return World(db, bob, carol, home, away, third, team_a, team_b, foreign_team)


def _rows(db: Database, ctx: TenancyContext | None, sql: str, **params: Any) -> list[Any]:
    with db.engine.begin() as conn:
        bind_connection(conn, ctx)
        return list(conn.execute(text(sql), params).all())


def _count(db: Database, ctx: TenancyContext | None, sql: str, **params: Any) -> int:
    with db.engine.begin() as conn:
        bind_connection(conn, ctx)
        return conn.execute(text(sql), params).rowcount


def _teams_seen(db: Database, ctx: TenancyContext | None) -> dict[str, set[uuid.UUID]]:
    """The team ids each table shows the session (team table: ``id``; others: ``team_id``)."""
    return {
        TEAM_TABLE: {r[0] for r in _rows(db, ctx, f"SELECT id FROM {TEAM_TABLE}")},
        MEMBER_TABLE: {r[0] for r in _rows(db, ctx, f"SELECT team_id FROM {MEMBER_TABLE}")},
        LINK_TABLE: {r[0] for r in _rows(db, ctx, f"SELECT team_id FROM {LINK_TABLE}")},
    }


def _all(*team_ids: uuid.UUID) -> dict[str, set[uuid.UUID]]:
    return {key: set(team_ids) for key in TABLES}


def test_a_system_admin_reads_every_team_of_its_tenant_only(world: World) -> None:
    assert _teams_seen(world.db, ROOT) == _all(world.team_a, world.team_b)


def test_a_member_reads_their_team_without_any_project(world: World) -> None:
    ctx = TenancyContext(tenant_id=T1, user_id=world.bob)
    assert _teams_seen(world.db, ctx) == _all(world.team_a)


def test_a_session_of_a_linked_project_reads_the_team_its_members_and_links(
    world: World,
) -> None:
    # CAROL is in team B (AWAY). Bound to HOME (team A's project) she also reads team A.
    bound = TenancyContext(tenant_id=T1, user_id=world.carol, project_id=world.home)
    assert _teams_seen(world.db, bound) == _all(world.team_a, world.team_b)
    # The same through the allowed list or a member project, with no user at all.
    for ctx in (
        TenancyContext(tenant_id=T1, allowed_project_ids=frozenset({world.home})),
        TenancyContext(tenant_id=T1, member_project_ids=frozenset({world.home})),
    ):
        assert _teams_seen(world.db, ctx) == _all(world.team_a)


def test_a_non_member_bound_to_an_unlinked_project_reads_nothing(world: World) -> None:
    dave = uuid.uuid4()
    ctx = TenancyContext(tenant_id=T1, user_id=dave, project_id=world.third)
    assert _teams_seen(world.db, ctx) == _all()
    assert _teams_seen(world.db, None) == _all()  # unbound: fail closed


def test_another_tenants_rows_are_invisible(world: World) -> None:
    foreign = {r[0] for r in _rows(world.db, ROOT, f"SELECT id FROM {TEAM_TABLE}")}
    assert world.foreign_team not in foreign
    t2 = TenancyContext(tenant_id=T2, is_system_admin=True)
    assert _teams_seen(world.db, t2) == _all(world.foreign_team)
    # A T1 member id in a T2 session shows nothing of T1.
    ctx = TenancyContext(
        tenant_id=T2, user_id=world.bob, member_project_ids=frozenset({world.home})
    )
    assert _teams_seen(world.db, ctx) == _all()


def test_only_system_admin_sessions_write_team_rows(world: World) -> None:
    # BOB can read team A (member, and HOME is his project) but writes nothing.
    ctx = TenancyContext(
        tenant_id=T1, user_id=world.bob, member_project_ids=frozenset({world.home})
    )
    inserts = [
        f"INSERT INTO {TEAM_TABLE} ({_RECORD}, name, name_key, description)"
        f" VALUES (gen_random_uuid(), :t, NULL, {_NOW}, 'Mine', 'mine', '')",
        f"INSERT INTO {MEMBER_TABLE} ({_RECORD}, team_id, user_id)"
        f" VALUES (gen_random_uuid(), :t, NULL, {_NOW}, :team, :user)",
        f"INSERT INTO {LINK_TABLE} ({_RECORD}, team_id, linked_project_id, persona)"
        f" VALUES (gen_random_uuid(), :t, NULL, {_NOW}, :team, :project, 'PER-04')",
    ]
    params = {"t": T1, "team": world.team_a, "user": world.bob, "project": world.third}
    for sql in inserts:
        with pytest.raises(DBAPIError, match=RLS_ERROR):
            _count(world.db, ctx, sql, **params)
    for table in TABLES:
        assert _count(world.db, ctx, f"UPDATE {table} SET deleted_at = now()") == 0
        assert _count(world.db, ctx, f"DELETE FROM {table}") == 0
    assert _teams_seen(world.db, ctx) == _all(world.team_a)  # unchanged
    # A System admin session does it.
    assert _count(world.db, ROOT, inserts[0], **params) == 1
    assert _count(world.db, ROOT, f"UPDATE {LINK_TABLE} SET persona = 'PER-05'") == 2


def test_the_lookup_flag_only_narrows(world: World) -> None:
    """``app.team_lookup`` (raised by the function for its own read) can only hide rows."""
    ctx = TenancyContext(tenant_id=T1, user_id=world.carol, project_id=world.home)
    with world.db.engine.begin() as conn:
        bind_connection(conn, ctx)
        conn.execute(text("SELECT set_config('app.team_lookup', 'true', true)"))
        teams_seen = {r[0] for r in conn.execute(text(f"SELECT id FROM {TEAM_TABLE}"))}
        links = {r[0] for r in conn.execute(text(f"SELECT team_id FROM {LINK_TABLE}"))}
        members = {r[0] for r in conn.execute(text(f"SELECT user_id FROM {MEMBER_TABLE}"))}
    assert teams_seen == set()
    assert links == {world.team_a}  # only the row's own project
    assert members == {world.carol}  # only her own row
    # The function restores the caller's value and is an invoker-rights function.
    with world.db.engine.begin() as conn:
        bind_connection(conn, ctx)
        found = conn.execute(text(f"SELECT {TEAM_FUNCTION}()")).scalar_one()
        flag = conn.execute(text("SELECT current_setting('app.team_lookup', true)")).scalar_one()
    assert set(found) == {world.team_a, world.team_b}
    assert flag in (None, "")
    [(definer,)] = _rows(
        world.db, None, "SELECT prosecdef FROM pg_proc WHERE proname = :n", n=TEAM_FUNCTION
    )
    assert definer is False
