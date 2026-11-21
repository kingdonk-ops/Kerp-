"""Saved views shared with several teams linked to the project (T-0074, ADR 0008).

Register-views spec §3 team visibility: a ``team`` view names one or more teams, all linked to
its project; it is visible to its owner, the project's PER-09 and members of any named team
still linked there. Teams come from the teams service contract only. Covers the service, the
API, the module-availability rule and migration ``0032`` (legacy ``team_id`` rows). Runs on
SQLite and, in CI, on PostgreSQL under RLS. All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi import Request
from fastapi.testclient import TestClient

from app.core.auth.access import Persona
from app.core.auth.models import ProjectMembership
from app.core.db import GUID, Database, JSONType
from app.core.module_loader import service as module_service
from app.core.module_loader.loader import discover_modules
from app.core.module_loader.registry import ModuleRegistry
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.scope import OutOfScopeError
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.saved_views import router as views_router
from app.modules.saved_views import service
from app.modules.saved_views.models import SavedView
from app.modules.saved_views.schemas import SavedViewCreate, SavedViewUpdate
from app.modules.saved_views.service import Viewer
from app.modules.teams import service as teams
from app.modules.teams.schemas import TeamCreate
from tests.audit_support import ALEMBIC_INI, migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, T1, T2, auth_app, make_settings, seed_user
from tests.db_support import FIXTURE_TENANTS, database_url, seed_tenants

ADMIN_T1 = TenancyContext(tenant_id=T1, is_system_admin=True)
ADMIN_T2 = TenancyContext(tenant_id=T2, is_system_admin=True)
REGISTER = "asset_management.work_anchor"
OWNER = uuid.UUID("00000000-0000-4000-8000-00000000a001")
ADMIN = uuid.UUID("00000000-0000-4000-8000-00000000a003")
SHARER = Viewer(user_id=OWNER, can_share=True)
PER09 = Viewer(user_id=ADMIN, can_share=True, is_admin=True)
NOT_LINKED = "every team must be a team linked to this project"
SOURCES = Path(service.__file__).parent


def _project(db: Database, code: str, ctx: TenancyContext = ADMIN_T1) -> TenancyContext:
    with db.unit_of_work(ctx) as s:
        project_id = projects.create_project(
            s, ProjectCreate.model_validate({"code": code, "name": code})
        ).id
    return TenancyContext(tenant_id=ctx.tenant_id, project_id=project_id)


def _pid(ctx: TenancyContext) -> uuid.UUID:
    assert ctx.project_id is not None
    return ctx.project_id


def _team(
    db: Database,
    name: str,
    *linked: TenancyContext,
    members: tuple[uuid.UUID, ...] = (),
    ctx: TenancyContext = ADMIN_T1,
) -> uuid.UUID:
    """A team of ``ctx``'s tenant with ``members``, linked to ``linked`` as Field Workers."""
    with db.unit_of_work(ctx) as s:
        team_id = teams.create_team(s, TeamCreate(name=name), actor_id=None).id
        for user_id in members:
            teams.add_member(s, team_id, user_id, confirm_sod=False, actor_id=None)
        for project in linked:
            teams.set_link(
                s,
                team_id,
                _pid(project),
                Persona.FIELD_WORKER,
                None,
                confirm_sod=False,
                actor_id=None,
            )
    return team_id


def _ids(*team_ids: uuid.UUID) -> list[uuid.UUID]:
    return sorted(team_ids, key=str)


@dataclass
class World:
    """Project HOME with teams X and Y linked; Z linked to project OTHER only."""

    db: Database
    home: TenancyContext
    other: TenancyContext
    x: uuid.UUID
    y: uuid.UUID
    z: uuid.UUID
    x_member: uuid.UUID
    y_member: uuid.UUID
    no_team: uuid.UUID

    def viewer(self, user_id: uuid.UUID, **fields: Any) -> Viewer:
        """``user_id`` as the router builds it: their teams from the teams contract."""
        with self.db.unit_of_work(self.home) as s:
            team_ids = teams.user_team_ids(s, user_id)
        return Viewer(user_id=user_id, team_ids=team_ids, **fields)

    def save(self, viewer: Viewer = SHARER, **fields: Any) -> uuid.UUID:
        values: dict[str, Any] = {"register_key": REGISTER, "name": "Shared", **fields}
        with self.db.unit_of_work(self.home) as s:
            return service.create_view(s, viewer, SavedViewCreate.model_validate(values)).id

    def update(self, view_id: uuid.UUID, viewer: Viewer = SHARER, **fields: Any) -> Any:
        with self.db.unit_of_work(self.home) as s:
            view = service.update_view(s, viewer, view_id, SavedViewUpdate.model_validate(fields))
            return service.present(s, view)

    def load(self, view_id: uuid.UUID, viewer: Viewer = SHARER) -> Any:
        with self.db.unit_of_work(self.home) as s:
            return service.present(s, service.get_view(s, viewer, view_id))

    def visible(self, viewer: Viewer) -> list[str]:
        with self.db.unit_of_work(self.home) as s:
            return [v.name for v in service.list_views(s, viewer, REGISTER)]

    def links(self, view_id: uuid.UUID) -> frozenset[uuid.UUID]:
        with self.db.unit_of_work(self.home) as s:
            return service.view_team_ids(s, [view_id])[view_id]


def _world(db: Database) -> World:
    home, other = _project(db, "HOME"), _project(db, "OTHER")
    x_member, y_member, no_team = (
        seed_user(db, T1, f"{n}@example.test") for n in ("xena", "yuri", "nora")
    )
    with db.unit_of_work(ADMIN_T1) as s:  # nora: a direct project member in neither team
        s.add(
            ProjectMembership(
                tenant_id=T1,
                user_id=no_team,
                member_project_id=_pid(home),
                persona=Persona.FIELD_WORKER.value,
            )
        )
    x = _team(db, "Crew X", home, members=(x_member,))
    y = _team(db, "Crew Y", home, other, members=(y_member,))
    z = _team(db, "Crew Z", other)
    return World(db, home, other, x, y, z, x_member, y_member, no_team)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    for db in migrated_database(tmp_path):
        yield _world(db)


# --- module wiring --------------------------------------------------------------------------


def test_saved_views_depends_on_teams() -> None:
    registry = discover_modules()
    assert "teams" in registry.get("saved_views").depends_on
    names = [m.name for m in registry]
    assert names.index("teams") < names.index("saved_views")


def test_saved_views_never_reads_teams_tables() -> None:
    """Teams are reached through ``teams.service`` only (CLAUDE.md §6)."""
    for path in sorted(SOURCES.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "teams.models" not in source, path.name
        assert "oe_teams_" not in source, path.name
        if "app.modules.teams" in source:
            assert "from app.modules.teams import service as teams" in source, path.name


# --- visibility -----------------------------------------------------------------------------


def test_a_team_view_is_seen_by_members_of_any_named_team(world: World) -> None:
    view_id = world.save(name="Shared", visibility="team", team_ids=[str(world.x), str(world.y)])
    world.save(name="Private")
    assert world.visible(world.viewer(world.x_member)) == ["Shared"]
    assert world.visible(world.viewer(world.y_member)) == ["Shared"]
    assert world.visible(world.viewer(world.no_team)) == []
    assert world.visible(PER09) == ["Shared"]
    assert world.visible(SHARER) == ["Private", "Shared"]
    assert world.load(view_id).team_ids == _ids(world.x, world.y)
    with pytest.raises(OutOfScopeError):
        world.load(view_id, world.viewer(world.no_team))


def test_an_unlinked_or_deleted_team_stops_seeing_the_view(world: World) -> None:
    view_id = world.save(name="Shared", visibility="team", team_ids=[str(world.x), str(world.y)])
    with world.db.unit_of_work(ADMIN_T1) as s:
        assert teams.unlink(s, world.x, _pid(world.home), actor_id=None)
    assert world.visible(world.viewer(world.x_member)) == []
    assert world.visible(world.viewer(world.y_member)) == ["Shared"]
    assert world.load(view_id).team_ids == [world.y]

    with world.db.unit_of_work(ADMIN_T1) as s:
        teams.delete_team(s, world.y, actor_id=None)
    # No named team left: owner and PER-09 only, no error.
    assert world.visible(world.viewer(world.y_member)) == []
    assert world.visible(SHARER) == ["Shared"]
    assert world.visible(PER09) == ["Shared"]
    assert world.load(view_id, PER09).team_ids == []
    assert world.update(view_id, name="Still editable").name == "Still editable"


# --- sharing --------------------------------------------------------------------------------


def test_any_member_who_may_share_names_any_linked_team(world: World) -> None:
    sharer = world.viewer(world.no_team, can_share=True)  # in neither team
    view_id = world.save(sharer, visibility="team", team_ids=[str(world.y), str(world.x)])
    assert world.load(view_id, sharer).team_ids == _ids(world.x, world.y)
    reader = world.viewer(world.x_member)  # a member of X, but no C capability
    with pytest.raises(service.ViewPermissionError, match="share"):
        world.save(reader, visibility="team", team_ids=[str(world.x)])


def test_only_teams_linked_to_the_project_can_be_named(world: World) -> None:
    with world.db.unit_of_work(ADMIN_T2) as s:
        foreign_project = projects.create_project(
            s, ProjectCreate.model_validate({"code": "FOREIGN", "name": "Foreign"})
        )
        foreign = teams.create_team(s, TeamCreate(name="Theirs"), actor_id=None).id
        teams.set_link(
            s,
            foreign,
            foreign_project.id,
            Persona.FIELD_WORKER,
            None,
            confirm_sod=False,
            actor_id=None,
        )
    view_id = world.save(visibility="team", team_ids=[str(world.x)])
    for bad in (world.z, uuid.uuid4(), foreign):  # other project, unknown, other tenant
        with pytest.raises(service.InvalidViewError) as created:
            world.save(visibility="team", team_ids=[str(world.x), str(bad)])
        with pytest.raises(service.InvalidViewError) as updated:
            world.update(view_id, team_ids=[str(bad)])
        assert str(created.value) == str(updated.value) == NOT_LINKED
    assert world.links(view_id) == {world.x}


def test_team_ids_follow_the_visibility(world: World) -> None:
    x, y = str(world.x), str(world.y)
    with pytest.raises(service.InvalidViewError, match="team_ids"):
        world.save(visibility="team")
    with pytest.raises(service.InvalidViewError, match="team_ids"):
        world.save(visibility="team", team_ids=[])
    for visibility in ("private", "project", "portal"):
        with pytest.raises(service.InvalidViewError, match="team_ids"):
            world.save(visibility=visibility, team_ids=[x])

    view_id = world.save(visibility="team", team_ids=[x, x])  # repeats count once
    assert world.links(view_id) == {world.x}
    assert world.update(view_id, team_ids=[y, x]).team_ids == _ids(world.x, world.y)
    assert world.update(view_id, team_ids=[y]).team_ids == [world.y]
    assert world.update(view_id, name="Renamed").team_ids == [world.y]  # kept
    with pytest.raises(service.InvalidViewError, match="team_ids"):
        world.update(view_id, team_ids=[])
    with pytest.raises(service.InvalidViewError, match="team_ids"):
        world.update(view_id, visibility="project", team_ids=[y])
    # Leaving ``team`` drops the teams; coming back needs them again.
    assert world.update(view_id, visibility="project").team_ids == []
    assert world.links(view_id) == frozenset()
    with pytest.raises(service.InvalidViewError, match="team_ids"):
        world.update(view_id, visibility="team")
    assert world.update(view_id, visibility="team", team_ids=[x]).team_ids == [world.x]
    # A re-added team is a new live link (the unique index covers live rows only).
    assert world.update(view_id, team_ids=[x, y]).team_ids == _ids(world.x, world.y)

    with world.db.unit_of_work(world.home) as s:
        service.delete_view(s, SHARER, view_id)
    assert world.links(view_id) == frozenset()


def test_without_the_teams_module_team_views_are_refused(world: World) -> None:
    offline = Viewer(user_id=OWNER, can_share=True, teams_available=False)
    with pytest.raises(service.InvalidViewError, match="teams module"):
        world.save(offline, visibility="team", team_ids=[str(world.x)])
    view_id = world.save(offline, visibility="project")
    with pytest.raises(service.InvalidViewError, match="teams module"):
        world.update(view_id, offline, visibility="team", team_ids=[str(world.x)])


def test_the_router_sees_whether_the_teams_module_is_available(world: World) -> None:
    full = discover_modules()
    dropped, grown = {"teams"}, True
    while grown:  # teams and everything that needs it
        more = {m.name for m in full if dropped & set(m.depends_on)} - dropped
        dropped, grown = dropped | more, bool(more)
    kept = [m for m in full if m.name not in dropped]
    without = ModuleRegistry.from_manifests(full.package, kept)

    def request(registry: ModuleRegistry) -> Request:
        app = SimpleNamespace(state=SimpleNamespace(modules=registry))
        return cast(Request, SimpleNamespace(app=app))

    with world.db.unit_of_work(world.home) as s:
        assert views_router._teams_available(request(full), s)
        assert not views_router._teams_available(request(without), s)


# --- API ------------------------------------------------------------------------------------


class Api:
    def __init__(self, client: TestClient, world: World) -> None:
        self.client, self.world = client, world
        db, home = world.db, world.home
        self.alice = seed_user(db, T1, ALICE)  # Field Worker: may share, in neither team
        self.bob = seed_user(db, T1, BOB)  # client representative: view only, in neither team
        self.erin = seed_user(db, T1, "erin@example.test")  # in team Y only (team access)
        with db.unit_of_work(ADMIN_T1) as s:
            for user, persona in (
                (self.alice, Persona.FIELD_WORKER),
                (self.bob, Persona.CLIENT_REPRESENTATIVE),
            ):
                s.add(
                    ProjectMembership(
                        tenant_id=T1,
                        user_id=user,
                        member_project_id=_pid(home),
                        persona=persona.value,
                    )
                )
            teams.add_member(s, world.y, self.erin, confirm_sod=False, actor_id=None)

    def headers(self, email: str) -> dict[str, str]:
        body = {"email": email, "password": PASSWORD}
        response = self.client.post("/api/v1/auth/login", json=body)
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def views(self) -> str:
        return f"/api/v1/saved-views/projects/{_pid(self.world.home)}/views"


@pytest.fixture
def api(tmp_path: Path, world: World) -> Iterator[Api]:
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        yield Api(client, world)


def _body(**fields: Any) -> dict[str, Any]:
    return {"register_key": REGISTER, "name": "Crew view", **fields}


def test_the_api_shares_a_view_with_several_teams(api: Api) -> None:
    w = api.world
    alice, bob, erin = (api.headers(e) for e in (ALICE, BOB, "erin@example.test"))
    team_ids = [str(w.y), str(w.x)]
    body = _body(visibility="team", team_ids=team_ids)
    created = api.client.post(api.views(), json=body, headers=alice)
    assert created.status_code == 201, created.text
    view = created.json()
    assert view["team_ids"] == [str(t) for t in _ids(w.x, w.y)]
    assert "team_id" not in view
    url, params = f"{api.views()}/{view['id']}", {"register_key": REGISTER}

    listed = api.client.get(api.views(), params=params, headers=erin)
    assert [v["id"] for v in listed.json()] == [view["id"]]
    assert api.client.get(url, headers=erin).status_code == 200
    assert api.client.get(api.views(), params=params, headers=bob).json() == []
    assert api.client.get(url, headers=bob).status_code == 404

    patched = api.client.patch(url, json={"team_ids": [str(w.x)]}, headers=alice)
    assert (patched.status_code, patched.json()["team_ids"]) == (200, [str(w.x)])
    assert api.client.get(url, headers=erin).status_code == 404  # Y no longer named


def test_the_api_answers_422_alike_for_teams_not_linked_to_the_project(api: Api) -> None:
    w = api.world
    alice = api.headers(ALICE)
    with w.db.unit_of_work(ADMIN_T2) as s:
        theirs = teams.create_team(s, TeamCreate(name="Theirs"), actor_id=None).id
    bodies = []
    for bad in (w.z, uuid.uuid4(), theirs):
        response = api.client.post(
            api.views(), json=_body(visibility="team", team_ids=[str(bad)]), headers=alice
        )
        assert response.status_code == 422
        bodies.append(response.json())
    assert bodies == [{"detail": NOT_LINKED}] * 3
    for body in (_body(visibility="team"), _body(visibility="project", team_ids=[str(w.x)])):
        assert api.client.post(api.views(), json=body, headers=alice).status_code == 422


def test_the_api_refuses_team_views_without_the_teams_module(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = module_service.is_enabled

    def is_enabled(session: Any, registry: ModuleRegistry, name: str) -> bool:
        return name != "teams" and real(session, registry, name)

    monkeypatch.setattr(module_service, "is_enabled", is_enabled)
    alice = api.headers(ALICE)
    body = _body(visibility="team", team_ids=[str(api.world.x)])
    refused = api.client.post(api.views(), json=body, headers=alice)
    assert refused.status_code == 422
    assert refused.json() == {"detail": "team views need the teams module"}
    assert api.client.post(api.views(), json=_body(), headers=alice).status_code == 201


# --- migration 0032: legacy ``team_id`` rows ------------------------------------------------

# ``oe_saved_view`` as of revision 0031 (the test writes below head, not through the model).
_LEGACY = sa.Table(
    "oe_saved_view",
    sa.MetaData(),
    *(sa.Column(c, GUID()) for c in ("id", "tenant_id", "project_id", "owner_user_id", "team_id")),
    *(
        sa.Column(c, sa.String())
        for c in ("register_key", "name", "visibility", "density", "source")
    ),
    *(sa.Column(c, JSONType()) for c in ("columns", "sort", "conditional_format", "custom_fields")),
    sa.Column("is_default", sa.Boolean()),
    sa.Column("schema_version", sa.Integer()),
    sa.Column("revision", sa.Integer()),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)


def _legacy_view(
    db: Database, ctx: TenancyContext, name: str, team_id: uuid.UUID | None
) -> uuid.UUID:
    view_id, now = uuid.uuid4(), datetime.now(UTC)
    with db.unit_of_work(ctx) as s:
        s.execute(
            sa.insert(_LEGACY).values(
                id=view_id,
                tenant_id=ctx.tenant_id,
                project_id=ctx.project_id,
                owner_user_id=OWNER,
                team_id=team_id,
                register_key=REGISTER,
                name=name,
                visibility="private" if team_id is None else "team",
                density="standard",
                source="api",
                columns=[],
                sort=[],
                conditional_format=[],
                custom_fields={},
                is_default=False,
                schema_version=1,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
    return view_id


@pytest.fixture
def legacy(tmp_path: Path) -> Iterator[tuple[Config, World, uuid.UUID]]:
    """A database at 0031 with a legacy team view (``team_id`` = X), then upgraded to head."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=database_url(tmp_path), configure_logger=False)
    command.upgrade(cfg, "0031")
    db = Database(database_url(tmp_path))
    try:
        seed_tenants(db, *FIXTURE_TENANTS)
        world = _world(db)
        view_id = _legacy_view(db, world.home, "Legacy", world.x)
        _legacy_view(db, world.home, "Legacy private", None)
        command.upgrade(cfg, "head")
        yield cfg, world, view_id
    finally:
        db.dispose()
        command.downgrade(cfg, "base")


def test_legacy_team_id_rows_behave_like_link_rows(
    legacy: tuple[Config, World, uuid.UUID],
) -> None:
    _cfg, world, view_id = legacy
    assert world.links(view_id) == {world.x}
    assert world.visible(world.viewer(world.x_member)) == ["Legacy"]
    assert world.visible(world.viewer(world.y_member)) == []
    assert world.visible(PER09) == ["Legacy"]
    assert world.load(view_id).team_ids == [world.x]
    # The first write clears the deprecated column; the link carries the team on.
    assert world.update(view_id, team_ids=[str(world.x), str(world.y)]).team_ids == _ids(
        world.x, world.y
    )
    with world.db.unit_of_work(world.home) as s:
        stored = s.get(SavedView, view_id)
        assert stored is not None
        assert stored.team_id is None
    assert world.update(view_id, visibility="project").team_ids == []


def test_the_downgrade_writes_one_team_back(legacy: tuple[Config, World, uuid.UUID]) -> None:
    cfg, world, view_id = legacy
    shared = world.save(name="Two teams", visibility="team", team_ids=[str(world.x), str(world.y)])
    world.update(view_id, team_ids=[str(world.y)])  # legacy column cleared, Y linked
    command.downgrade(cfg, "0031")
    with world.db.unit_of_work(world.home) as s:
        rows = s.execute(sa.select(_LEGACY.c.id, _LEGACY.c.visibility, _LEGACY.c.team_id))
        stored = {row.id: (row.visibility, row.team_id) for row in rows}
    assert stored[shared] == ("team", min(world.x, world.y, key=str))
    assert stored[view_id] == ("team", world.y)
