"""Teams module (T-0013, diff §4.1 Teams, ADR 0008): tenant-level teams, members and
team-project links, the derived project grants they keep in step (T-0073), separation of
duties across sources, the audit chain and the read contract other modules build on.

Runs on SQLite (the ORM scope mirrors the ``team`` policy) and, in CI's PostgreSQL step,
against the migrated schema as ``kerp_app`` under RLS. Raw-SQL RLS checks:
``test_rls_teams.py``. All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.audit.service import chain_entries
from app.core.auth.access import (
    HOLD_AUTHORITY,
    Capability,
    Persona,
    load_grants,
    project_access,
    resolve_request_scope,
)
from app.core.auth.models import SOURCE_TEAM_LINK, ProjectMembership
from app.core.auth.team_grants import LINK_PERSONAS
from app.core.db import GUID, Base, Database, contract_violations
from app.core.module_loader.loader import discover_modules
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import (
    TEAM_POLICY_NAMES,
    expected_policy_names,
    rls_policy_sql,
    rls_tables,
    table_policy,
)
from app.core.tenancy.scope import INCLUDE_DELETED, OutOfScopeError
from app.modules.admin import service as admin
from app.modules.admin.schemas import UserUpdate
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.teams import service as teams
from app.modules.teams.models import (
    LINK_PERSONA_CODES,
    TABLES,
    Team,
    TeamMember,
    TeamProjectLink,
    normalize_name,
)
from app.modules.teams.schemas import TeamCreate
from tests.audit_support import migrated_database
from tests.auth_support import (
    ALICE,
    BOB,
    PASSWORD,
    T1,
    T2,
    auth_app,
    make_settings,
    revoke_project_viewers,
    seed_user,
)
from tests.db_support import database_url

TEAMS = "/api/v1/teams"
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
CAROL = "carol@example.test"
DAVE = "dave@example.test"
ERIN = "erin@example.test"
FRANK = "frank@example.test"
SITE = uuid.UUID("00000000-0000-4000-8000-00000000051e")
INVALID_MEMBER = {"detail": "This user cannot be added to a team"}


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    migrated = migrated_database(tmp_path)
    database = next(migrated)
    try:
        yield database
    finally:
        revoke_project_viewers(database)  # the 0012 downgrade refuses while PER-16 is active
        next(migrated, None)


def _project(db: Database, code: str, tenant: uuid.UUID = T1) -> uuid.UUID:
    ctx = TenancyContext(tenant_id=tenant, is_system_admin=True)
    with db.unit_of_work(ctx) as s:
        return projects.create_project(s, ProjectCreate(code=code, name=f"Synthetic {code}")).id


def _direct(db: Database, user: uuid.UUID, project: uuid.UUID, persona: Persona) -> None:
    with db.unit_of_work(ROOT) as s:
        site = SITE if persona is Persona.SITE_INSPECTOR else None
        s.add(
            ProjectMembership(
                tenant_id=T1,
                user_id=user,
                member_project_id=project,
                persona=persona.value,
                site_node_id=site,
            )
        )


def _make_admin(db: Database, user: uuid.UUID) -> None:
    with db.unit_of_work(ROOT) as s:
        assert admin.grant(s, user, "system_admin", actor_id=None)


def _grants(db: Database, user: uuid.UUID) -> set[tuple[uuid.UUID, Persona]]:
    """The user's effective grants, as ``load_grants`` (the request path) reads them."""
    with db.unit_of_work(TenancyContext(tenant_id=T1, user_id=user)) as s:
        return {(g.project_id, g.persona) for g in load_grants(s, T1, user)}


def _set_active(db: Database, user: uuid.UUID, active: bool) -> None:
    with db.unit_of_work(ROOT) as s:
        admin.update_user(s, user, UserUpdate(is_active=active), actor_id=uuid.uuid4())


class Api:
    """ALICE: System admin. BOB: individual PER-09 of HOME. CAROL: PER-04 of HOME.
    DAVE: no project. ERIN, FRANK: plain accounts for teams."""

    def __init__(self, client: TestClient, db: Database) -> None:
        self.client, self.db = client, db
        self.users = {e: seed_user(db, T1, e) for e in (ALICE, BOB, CAROL, DAVE, ERIN, FRANK)}
        _make_admin(db, self.users[ALICE])
        self.home, self.away, self.third = (_project(db, c) for c in ("HOME", "AWAY", "THIRD"))
        _direct(db, self.users[BOB], self.home, Persona.PROJECT_ADMINISTRATOR)
        _direct(db, self.users[CAROL], self.home, Persona.TRADE_SUPERVISOR)
        self._headers: dict[str, dict[str, str]] = {}

    def headers(self, email: str) -> dict[str, str]:
        if email not in self._headers:
            body = {"email": email, "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            self._headers[email] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._headers[email]

    def call(self, method: str, path: str, email: str = ALICE, **kwargs: Any) -> Any:
        return self.client.request(method, f"{TEAMS}{path}", headers=self.headers(email), **kwargs)

    def team(self, name: str = "Insulation crew A") -> uuid.UUID:
        response = self.call("POST", "", json={"name": name})
        assert response.status_code == 201, response.text
        return uuid.UUID(response.json()["id"])

    def add(self, team: uuid.UUID, user: str, **body: Any) -> Any:
        return self.call("PUT", f"/{team}/members/{self.users[user]}", json=body or None)

    def link(self, team: uuid.UUID, project: uuid.UUID, persona: str, **body: Any) -> Any:
        payload = {"persona": persona, **body}
        if persona == "PER-07" and "site_node_id" not in body:
            payload["site_node_id"] = str(SITE)
        return self.call("PUT", f"/{team}/projects/{project}", json=payload)

    def chain(self, team: uuid.UUID) -> list[dict[str, Any]]:
        with self.db.unit_of_work(ROOT) as s:
            return [e.payload for e in chain_entries(s, f"team:{team}")]

    def actors(self, team: uuid.UUID) -> set[uuid.UUID | None]:
        with self.db.unit_of_work(ROOT) as s:
            return {e.created_by for e in chain_entries(s, f"team:{team}")}


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        yield Api(client, db)


# --- contract -------------------------------------------------------------------------------


def test_tables_meet_the_record_contract_with_the_team_policy() -> None:
    for table in TABLES:
        assert [p for p in contract_violations(Base.metadata) if p.startswith(table)] == []
        assert table in rls_tables(Base.metadata)
        assert expected_policy_names(Base.metadata.tables[table]) == TEAM_POLICY_NAMES
        policy = table_policy(Base.metadata.tables[table])
        assert policy is not None
        assert policy.kind == "team"
        ddl = "\n".join(rls_policy_sql(table, policy))
        assert "team_read_policy" in ddl
        assert "team_write_policy" in ddl
        assert "kerp_visible_team_ids" in ddl
        assert "SECURITY DEFINER" not in ddl
        # Tenant-level only, so a project session can never create one.
        checks = {c.name for c in Base.metadata.tables[table].constraints}
        assert f"ck_{table}_tenant_level" in checks


def test_link_personas_are_the_allowed_set() -> None:
    expected = {f"PER-{n:02d}" for n in range(1, 17)} - {"PER-09", "PER-15"}
    assert set(LINK_PERSONA_CODES) == expected == {p.value for p in LINK_PERSONAS}


def test_teams_is_a_platform_module_on_projects() -> None:
    modules = discover_modules()
    manifest = modules.get("teams")
    assert manifest.toggleable is False
    assert manifest.depends_on == ("projects",)
    assert manifest.route_prefix == "/teams"
    assert "teams" in modules.get("admin").depends_on  # it names teams in the member list


def test_names_compare_case_insensitively() -> None:
    assert normalize_name("  Insulation   Crew A ") == normalize_name("insulation crew a")


def test_every_endpoint_needs_a_token(api: Api) -> None:
    team, user, project = uuid.uuid4(), uuid.uuid4(), api.home
    calls = [
        ("GET", ""),
        ("POST", ""),
        ("GET", f"/{team}"),
        ("PATCH", f"/{team}"),
        ("DELETE", f"/{team}"),
        ("PUT", f"/{team}/members/{user}"),
        ("DELETE", f"/{team}/members/{user}"),
        ("PUT", f"/{team}/projects/{project}"),
        ("DELETE", f"/{team}/projects/{project}"),
        ("GET", f"/projects/{project}/teams"),
        ("GET", f"/projects/{project}/teams/mine"),
    ]
    codes = [api.client.request(m, f"{TEAMS}{p}", json={}).status_code for m, p in calls]
    assert codes == [401] * len(calls)


# --- API: role matrix -----------------------------------------------------------------------


def test_system_admin_manages_and_others_read_or_are_refused(api: Api) -> None:
    team = api.team()
    assert api.add(team, ERIN).status_code == 204
    assert api.link(team, api.home, "PER-04").status_code == 200
    # BOB (PER-09 of HOME) reads the project list and the team, but cannot write (403).
    listed = api.call("GET", f"/projects/{api.home}/teams", BOB)
    assert listed.status_code == 200
    assert [(t["name"], t["persona"], t["member_count"]) for t in listed.json()] == [
        ("Insulation crew A", "PER-04", 1)
    ]
    assert api.call("GET", f"/{team}", BOB).status_code == 200
    writes = [
        ("PATCH", f"/{team}", {"name": "Renamed"}),
        ("DELETE", f"/{team}", None),
        ("PUT", f"/{team}/members/{api.users[FRANK]}", None),
        ("DELETE", f"/{team}/members/{api.users[ERIN]}", None),
        ("PUT", f"/{team}/projects/{api.away}", {"persona": "PER-04"}),
        ("DELETE", f"/{team}/projects/{api.home}", None),
    ]
    for method, path, body in writes:
        assert api.call(method, path, BOB, json=body).status_code == 403, (method, path)
    assert api.call("POST", "", BOB, json={"name": "Mine"}).status_code == 403
    # DAVE has no access to HOME: 404 on its list; the team is invisible to him (404 writes).
    assert api.call("GET", f"/projects/{api.home}/teams", DAVE).status_code == 404
    assert api.call("GET", f"/{team}", DAVE).status_code == 404
    assert api.call("GET", "", DAVE).json() == []
    for method, path, body in writes:
        assert api.call(method, path, DAVE, json=body).status_code == 404, (method, path)
    assert api.call("POST", "", DAVE, json={"name": "Mine"}).status_code == 403
    # Nothing changed.
    detail = api.call("GET", f"/{team}").json()
    assert [m["user_id"] for m in detail["members"]] == [str(api.users[ERIN])]
    assert [link["project_id"] for link in detail["links"]] == [str(api.home)]


def test_a_member_reads_their_team_and_a_team_derived_member_reads_the_project_list(
    api: Api,
) -> None:
    team = api.team()
    api.add(team, ERIN)
    # Not linked yet: ERIN sees her team as a member, but has no project.
    assert [t["id"] for t in api.call("GET", "", ERIN).json()] == [str(team)]
    assert api.call("GET", f"/projects/{api.away}/teams", ERIN).status_code == 404
    api.link(team, api.away, "PER-05")
    # Team-derived access to AWAY: the project list and her teams there.
    listed = api.call("GET", f"/projects/{api.away}/teams", ERIN)
    assert listed.status_code == 200
    assert [t["team_id"] for t in listed.json()] == [str(team)]
    mine = api.call("GET", f"/projects/{api.away}/teams/mine", ERIN).json()
    assert [t["team_id"] for t in mine] == [str(team)]
    # CAROL (HOME only) does not see the team linked to AWAY.
    assert api.call("GET", f"/{team}", CAROL).status_code == 404


def test_mine_lists_only_the_callers_teams_on_the_project(api: Api) -> None:
    a, b = api.team("Team A"), api.team("Team B")
    api.add(a, ERIN)
    api.add(b, FRANK)
    api.link(a, api.home, "PER-04")
    api.link(b, api.home, "PER-05")
    everyone = api.call("GET", f"/projects/{api.home}/teams", CAROL).json()
    assert sorted(t["name"] for t in everyone) == ["Team A", "Team B"]
    assert api.call("GET", f"/projects/{api.home}/teams/mine", CAROL).json() == []
    mine = api.call("GET", f"/projects/{api.home}/teams/mine", ERIN).json()
    assert [t["name"] for t in mine] == ["Team A"]


def test_duplicate_names_are_409_case_insensitively(api: Api) -> None:
    team = api.team("Insulation crew A")
    assert api.call("POST", "", json={"name": " insulation  CREW a"}).status_code == 409
    other = api.team("Scaffold crew")
    assert api.call("PATCH", f"/{other}", json={"name": "INSULATION crew a"}).status_code == 409
    renamed = api.call("PATCH", f"/{team}", json={"name": "Insulation Crew A", "description": "x"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Insulation Crew A"
    # A deleted team frees its name.
    assert api.call("DELETE", f"/{team}").status_code == 204
    assert api.call("POST", "", json={"name": "insulation crew a"}).status_code == 201


def test_bodies_are_validated_and_take_no_tenant(api: Api) -> None:
    assert api.call("POST", "", json={"name": ""}).status_code == 422
    assert api.call("POST", "", json={"name": "X", "tenant_id": str(T1)}).status_code == 422
    team = api.team()
    assert api.call("PATCH", f"/{team}", json={}).status_code == 422
    assert api.call("PATCH", f"/{team}", json={"name": None}).status_code == 422
    for persona in ("PER-09", "PER-15", "PER-99"):
        assert api.link(team, api.home, persona).status_code == 422, persona
    assert api.link(team, api.home, "PER-07", site_node_id=None).status_code == 422
    assert api.link(team, api.home, "PER-04", site_node_id=str(SITE)).status_code == 422
    assert api.call("PUT", f"/{team}/projects/{api.home}", json={}).status_code == 422
    assert "tenant" not in str(api.call("GET", f"/{team}").json())


def test_unknown_deactivated_or_foreign_users_get_the_same_422(api: Api) -> None:
    team = api.team()
    foreign = seed_user(api.db, T2, "zed@example.test")
    _set_active(api.db, api.users[FRANK], False)
    for user in (uuid.uuid4(), foreign, api.users[FRANK]):
        response = api.call("PUT", f"/{team}/members/{user}")
        assert (response.status_code, response.json()) == (422, INVALID_MEMBER)
    assert api.call("GET", f"/{team}").json()["members"] == []


def test_deleted_or_foreign_projects_are_404(api: Api) -> None:
    team = api.team()
    foreign = _project(api.db, "ELSEWHERE", T2)
    assert api.link(team, foreign, "PER-04").status_code == 404
    assert api.call("DELETE", f"/{team}/projects/{foreign}").status_code == 404
    assert api.link(team, uuid.uuid4(), "PER-04").status_code == 404
    with api.db.unit_of_work(ROOT) as s:
        projects.delete_project(s, api.third, actor_id=None)
    assert api.link(team, api.third, "PER-04").status_code == 404
    assert api.call("DELETE", f"/{team}/projects/{api.third}").status_code == 404
    # Another tenant's team is 404 too.
    with api.db.unit_of_work(TenancyContext(tenant_id=T2, is_system_admin=True)) as s:
        theirs = teams.create_team(s, TeamCreate(name="Theirs"), actor_id=None).id
    assert api.call("GET", f"/{theirs}").status_code == 404
    assert api.call("DELETE", f"/{theirs}").status_code == 404


def test_idempotent_writes_are_no_ops_and_write_no_audit(api: Api) -> None:
    team = api.team()
    assert api.add(team, ERIN).status_code == 204
    assert api.link(team, api.home, "PER-04").status_code == 200
    before = len(api.chain(team))
    assert api.add(team, ERIN).status_code == 204
    again = api.link(team, api.home, "PER-04")
    assert again.status_code == 200
    assert api.call("DELETE", f"/{team}/members/{api.users[FRANK]}").status_code == 204
    assert api.call("DELETE", f"/{team}/projects/{api.away}").status_code == 204
    assert api.call("PATCH", f"/{team}", json={"name": "Insulation crew A"}).status_code == 200
    assert len(api.chain(team)) == before
    with api.db.unit_of_work(ROOT) as s:
        rows = s.scalars(select(TeamMember).execution_options(**{INCLUDE_DELETED: True})).all()
        assert len(rows) == 1


def test_every_change_is_audited_on_the_team_chain_with_the_actor(api: Api) -> None:
    team = api.team()
    api.call("PATCH", f"/{team}", json={"description": "Night shift insulation"})
    api.add(team, ERIN)
    api.link(team, api.home, "PER-04")
    api.link(team, api.home, "PER-05")
    api.call("DELETE", f"/{team}/members/{api.users[ERIN]}")
    api.call("DELETE", f"/{team}/projects/{api.home}")
    api.call("DELETE", f"/{team}")
    actions = [e["action"] for e in api.chain(team)]
    assert actions == [
        "create",
        "update",
        "member_add",
        "link_create",
        "link_rerole",
        "member_remove",
        "link_remove",
        "delete",
    ]
    assert api.actors(team) == {api.users[ALICE]}
    rerole = api.chain(team)[4]
    assert (rerole["previous_persona"], rerole["persona"]) == ("PER-04", "PER-05")


# --- derived access -------------------------------------------------------------------------


def test_a_member_of_a_linked_team_gets_the_links_persona(api: Api) -> None:
    erin = api.users[ERIN]
    team = api.team()
    api.link(team, api.home, "PER-04")
    assert _grants(api.db, erin) == set()
    api.add(team, ERIN)
    assert _grants(api.db, erin) == {(api.home, Persona.TRADE_SUPERVISOR)}
    access = project_access(list(_load(api.db, erin)), api.home)
    assert access.personas == {Persona.TRADE_SUPERVISOR}
    # Re-role: the persona follows.
    api.link(team, api.home, "PER-05")
    assert _grants(api.db, erin) == {(api.home, Persona.AREA_PLANNER)}
    # Removing the member takes the access away.
    api.call("DELETE", f"/{team}/members/{erin}")
    assert _grants(api.db, erin) == set()


def _load(db: Database, user: uuid.UUID) -> list[Any]:
    with db.unit_of_work(TenancyContext(tenant_id=T1, user_id=user)) as s:
        return load_grants(s, T1, user)


def test_a_portfolio_link_puts_the_project_in_the_allowed_list(api: Api) -> None:
    erin = api.users[ERIN]
    team = api.team()
    api.add(team, ERIN)
    api.link(team, api.away, "PER-01")  # Operations Management: portfolio scope
    scope = resolve_request_scope(api.db, TenancyContext(tenant_id=T1, user_id=erin))
    assert api.away in scope.allowed_project_ids
    api.call("DELETE", f"/{team}/projects/{api.away}")
    scope = resolve_request_scope(api.db, TenancyContext(tenant_id=T1, user_id=erin))
    assert scope.allowed_project_ids == frozenset()
    assert scope.member_project_ids == frozenset()


def test_a_project_viewer_link_gives_read_only_access(api: Api) -> None:
    team = api.team()
    api.add(team, ERIN)
    assert api.link(team, api.home, "PER-16").status_code == 200
    access = project_access(_load(api.db, api.users[ERIN]), api.home)
    assert access.capabilities == {Capability.VIEW}


def test_unlinking_or_deleting_the_team_takes_access_away_but_never_individual_rows(
    api: Api,
) -> None:
    carol, erin = api.users[CAROL], api.users[ERIN]
    team = api.team()
    api.add(team, CAROL)
    api.add(team, ERIN)
    api.link(team, api.home, "PER-05")
    api.link(team, api.away, "PER-05")
    assert _grants(api.db, carol) == {
        (api.home, Persona.TRADE_SUPERVISOR),  # individual
        (api.home, Persona.AREA_PLANNER),
        (api.away, Persona.AREA_PLANNER),
    }
    api.call("DELETE", f"/{team}/projects/{api.away}")
    assert (api.away, Persona.AREA_PLANNER) not in _grants(api.db, erin)
    assert api.call("DELETE", f"/{team}").status_code == 204
    assert _grants(api.db, erin) == set()
    assert _grants(api.db, carol) == {(api.home, Persona.TRADE_SUPERVISOR)}
    with api.db.unit_of_work(ROOT) as s:
        live = s.scalars(
            select(ProjectMembership).where(ProjectMembership.source_type == SOURCE_TEAM_LINK)
        ).all()
        assert live == []


def _team_link_rows(db: Database, project: uuid.UUID) -> list[ProjectMembership]:
    with db.unit_of_work(ROOT) as s:
        return list(
            s.scalars(
                select(ProjectMembership).where(
                    ProjectMembership.source_type == SOURCE_TEAM_LINK,
                    ProjectMembership.member_project_id == project,
                )
            )
        )


def test_a_link_to_a_deleted_project_confers_nothing_and_can_still_be_removed(
    api: Api,
) -> None:
    """Review round 1: an empty team's link does not stop a project delete; later member
    adds/removes must not create rows on the deleted project, and the link stays removable."""
    erin, frank = api.users[ERIN], api.users[FRANK]
    team = api.team()
    assert api.link(team, api.third, "PER-04").status_code == 200
    assert api.link(team, api.home, "PER-05").status_code == 200
    with api.db.unit_of_work(ROOT) as s:
        projects.delete_project(s, api.third, actor_id=None)
    assert api.add(team, ERIN).status_code == 204
    assert _team_link_rows(api.db, api.third) == []
    assert _grants(api.db, erin) == {(api.home, Persona.AREA_PLANNER)}  # the live link only
    assert api.add(team, FRANK).status_code == 204
    assert api.call("DELETE", f"/{team}/members/{frank}").status_code == 204
    assert _team_link_rows(api.db, api.third) == []
    assert not any(project == api.third for project, _ in _grants(api.db, frank))
    # The dangling link is still removable (204), audited, and then gone.
    assert api.call("DELETE", f"/{team}/projects/{api.third}").status_code == 204
    removed = [e for e in api.chain(team) if e["action"] == "link_remove"]
    assert [e["project_id"] for e in removed] == [str(api.third)]
    assert [link["project_id"] for link in api.call("GET", f"/{team}").json()["links"]] == [
        str(api.home)
    ]
    # Unlinked now: the deleted project is 404 again, like one that was never linked.
    assert api.call("DELETE", f"/{team}/projects/{api.third}").status_code == 404
    assert _grants(api.db, erin) == {(api.home, Persona.AREA_PLANNER)}


@pytest.mark.parametrize("via", ["member_add", "unlink"])
def test_rows_left_on_a_deleted_project_are_revoked(api: Api, via: str) -> None:
    """Rows a link still holds on a deleted project (written before this fix) are revoked by
    the next sync of the team, never refreshed, or by unlinking it."""
    erin = api.users[ERIN]
    team = api.team()
    api.add(team, ERIN)
    api.link(team, api.third, "PER-04")
    with api.db.unit_of_work(ROOT) as s:
        (row,) = (r for r in s.scalars(select(ProjectMembership)) if r.user_id == erin)
        row.soft_delete()  # let the project delete through, then bring the row back
        s.flush()
        projects.delete_project(s, api.third, actor_id=None)
        row.deleted_at = None
    assert len(_team_link_rows(api.db, api.third)) == 1
    if via == "member_add":
        assert api.add(team, FRANK).status_code == 204
    else:
        assert api.call("DELETE", f"/{team}/projects/{api.third}").status_code == 204
        assert [e["action"] for e in api.chain(team)][-1] == "link_remove"
    assert _team_link_rows(api.db, api.third) == []
    assert _grants(api.db, erin) == set()


def test_a_deactivated_member_keeps_their_rows_and_regains_access_on_reactivation(
    api: Api,
) -> None:
    erin, frank = api.users[ERIN], api.users[FRANK]
    team = api.team()
    api.add(team, ERIN)
    api.link(team, api.home, "PER-04")
    with api.db.unit_of_work(ROOT) as s:
        row_id = s.scalars(
            select(ProjectMembership.id).where(ProjectMembership.user_id == erin)
        ).one()
    _set_active(api.db, erin, False)
    assert api.add(team, FRANK).status_code == 204  # re-syncs the link
    _set_active(api.db, erin, True)
    assert project_access(_load(api.db, erin), api.home).personas == {Persona.TRADE_SUPERVISOR}
    assert project_access(_load(api.db, frank), api.home).personas == {Persona.TRADE_SUPERVISOR}
    with api.db.unit_of_work(ROOT) as s:
        rows = s.scalars(
            select(ProjectMembership)
            .where(ProjectMembership.user_id == erin)
            .execution_options(**{INCLUDE_DELETED: True})
        ).all()
        assert [(r.id, r.deleted_at) for r in rows] == [(row_id, None)]  # never soft-deleted
    # While deactivated, the read contract does not count her; the detail still lists her.
    _set_active(api.db, erin, False)
    with api.db.unit_of_work(ROOT) as s:
        assert teams.team_member_ids(s, team) == {frank}
        assert not teams.is_member(s, team, erin)
    detail = api.call("GET", f"/{team}").json()
    assert {(m["user_id"], m["is_active"]) for m in detail["members"]} == {
        (str(erin), False),
        (str(frank), True),
    }
    # A deactivated member can still be removed, which ends her rows.
    assert api.call("DELETE", f"/{team}/members/{erin}").status_code == 204
    _set_active(api.db, erin, True)
    assert _grants(api.db, erin) == set()


# --- separation of duties -------------------------------------------------------------------


@pytest.mark.parametrize("persona", sorted(p.value for p in HOLD_AUTHORITY))
def test_a_hold_persona_link_over_an_individual_per09_needs_confirmation(
    api: Api, persona: str
) -> None:
    bob = api.users[BOB]
    team = api.team()
    api.add(team, BOB)
    api.add(team, ERIN)
    refused = api.link(team, api.home, persona)
    assert refused.status_code == 409
    body = refused.json()["detail"]
    assert body["code"] == "sod_confirmation_required"
    assert body["sod_warning"] == {"user_ids": [str(bob)], "project_ids": [str(api.home)]}
    detail = api.call("GET", f"/{team}").json()
    assert detail["links"] == []  # nothing written
    assert _grants(api.db, api.users[ERIN]) == set()
    confirmed = api.link(team, api.home, persona, confirm_sod=True)
    assert confirmed.status_code == 200
    assert (api.home, Persona(persona)) in _grants(api.db, bob)
    entry = api.chain(team)[-1]
    assert entry["action"] == "link_create"
    assert entry["sod_warning"] is True
    assert entry["sod_user_ids"] == [str(bob)]


def test_a_non_hold_link_over_an_individual_per09_needs_no_confirmation(api: Api) -> None:
    team = api.team()
    api.add(team, BOB)
    response = api.link(team, api.home, "PER-04")
    assert response.status_code == 200
    assert api.chain(team)[-1]["sod_warning"] is False


def test_re_roling_to_a_hold_persona_needs_confirmation(api: Api) -> None:
    team = api.team()
    api.add(team, BOB)
    api.link(team, api.home, "PER-04")
    assert api.link(team, api.home, "PER-06").status_code == 409
    assert api.call("GET", f"/{team}").json()["links"][0]["persona"] == "PER-04"
    assert api.link(team, api.home, "PER-06", confirm_sod=True).status_code == 200


def test_a_member_add_checks_every_linked_project(api: Api) -> None:
    bob = api.users[BOB]  # individual PER-09 of HOME only
    team = api.team()
    api.link(team, api.home, "PER-02")
    api.link(team, api.away, "PER-06")
    refused = api.add(team, BOB)
    assert refused.status_code == 409
    assert refused.json()["detail"]["sod_warning"] == {
        "user_ids": [str(bob)],
        "project_ids": [str(api.home)],  # AWAY does not trigger: no PER-09 there
    }
    assert api.call("GET", f"/{team}").json()["members"] == []
    assert _grants(api.db, bob) == {(api.home, Persona.PROJECT_ADMINISTRATOR)}
    assert api.add(team, BOB, confirm_sod=True).status_code == 204
    assert _grants(api.db, bob) == {
        (api.home, Persona.PROJECT_ADMINISTRATOR),
        (api.home, Persona.PROJECT_MANAGER),
        (api.away, Persona.QAQC_LEAD),
    }
    entry = api.chain(team)[-1]
    assert entry["action"] == "member_add"
    assert entry["sod_warning"] is True
    assert entry["sod_project_ids"] == [str(api.home)]


# --- the T-0014 member list -----------------------------------------------------------------


def test_the_project_member_list_names_the_team_of_derived_grants(api: Api) -> None:
    team = api.team("Insulation crew A")
    api.add(team, ERIN)
    api.link(team, api.home, "PER-04")
    for caller in (ALICE, BOB):
        url = f"/api/v1/admin/projects/{api.home}/members"
        members = api.client.get(url, headers=api.headers(caller)).json()
        erin = next(m for m in members if m["user_id"] == str(api.users[ERIN]))
        assert [(g["source"], g["team_name"]) for g in erin["grants"]] == [
            ("team", "Insulation crew A")
        ], caller
        carol = next(m for m in members if m["user_id"] == str(api.users[CAROL]))
        assert [g["team_name"] for g in carol["grants"]] == [None]


# --- read contract --------------------------------------------------------------------------


def _bound(project: uuid.UUID, user: uuid.UUID, db: Database) -> TenancyContext:
    return resolve_request_scope(db, TenancyContext(tenant_id=T1, user_id=user, project_id=project))


def test_user_team_ids_follow_the_bound_project(api: Api) -> None:
    erin = api.users[ERIN]
    a = api.team("Team A")
    api.add(a, ERIN)
    api.link(a, api.home, "PER-04")
    api.link(a, api.away, "PER-04")
    _direct(api.db, erin, api.third, Persona.FIELD_WORKER)  # R: a member, but A is not linked
    for project, expected in ((api.home, {a}), (api.away, {a}), (api.third, set())):
        with api.db.unit_of_work(_bound(project, erin, api.db)) as s:
            assert teams.user_team_ids(s, erin) == expected, project
            assert teams.project_team_ids(s, project) == expected
    with api.db.unit_of_work(TenancyContext(tenant_id=T1, user_id=erin)) as s:
        assert teams.user_team_ids(s, erin) == frozenset()  # no bound project


def test_contract_functions_ignore_deleted_teams(api: Api) -> None:
    erin = api.users[ERIN]
    team = api.team()
    api.add(team, ERIN)
    api.link(team, api.home, "PER-04")
    with api.db.unit_of_work(ROOT) as s:
        assert teams.team_exists(s, team)
        assert teams.is_member(s, team, erin)
        assert teams.team_member_ids(s, team) == {erin}
        assert teams.project_team_ids(s, api.home) == {team}
    with api.db.unit_of_work(_bound(api.home, erin, api.db)) as s:
        assert teams.user_team_ids(s, erin) == {team}
    api.call("DELETE", f"/{team}")
    with api.db.unit_of_work(ROOT) as s:
        assert not teams.team_exists(s, team)
        assert not teams.is_member(s, team, erin)
        assert teams.team_member_ids(s, team) == frozenset()
        assert teams.project_team_ids(s, api.home) == frozenset()
        assert teams.link_team_names(s, {uuid.uuid4()}) == {}
        with pytest.raises(OutOfScopeError):
            teams.get_team(s, team)
    assert api.call("GET", "").json() == []
    assert api.call("GET", f"/{team}").status_code == 404
    assert api.call("GET", f"/projects/{api.home}/teams", BOB).json() == []
    assert not teams_unknown_is_member(api.db, erin)


def teams_unknown_is_member(db: Database, user: uuid.UUID) -> bool:
    with db.unit_of_work(ROOT) as s:
        return teams.is_member(s, uuid.uuid4(), user)


def test_the_orm_scope_hides_invisible_team_rows(api: Api) -> None:
    team = api.team()
    api.add(team, ERIN)
    api.link(team, api.away, "PER-04")
    carol = api.users[CAROL]  # HOME only
    ctx = resolve_request_scope(api.db, TenancyContext(tenant_id=T1, user_id=carol))
    with api.db.unit_of_work(ctx) as s:
        for model in (Team, TeamMember, TeamProjectLink):
            assert s.scalars(select(model)).all() == [], model
        assert not teams.team_exists(s, team)
        with pytest.raises(OutOfScopeError):
            teams.get_team(s, team)
    # A non-admin session cannot write team rows, even for a visible team (ORM guard).
    erin = api.users[ERIN]
    ctx = resolve_request_scope(api.db, TenancyContext(tenant_id=T1, user_id=erin))
    with api.db.unit_of_work(ctx) as s:
        visible = teams.get_team(s, team)  # ERIN is a member: she reads it
        visible.name = "Hijacked"
        with pytest.raises(OutOfScopeError):
            s.flush()
        s.rollback()
    with api.db.unit_of_work(ctx) as s:
        s.add(Team(tenant_id=T1, name="Mine", name_key="mine", description=""))
        with pytest.raises(OutOfScopeError):
            s.flush()
        s.rollback()


# --- a test-only consumer of the contract (what T-0074 builds on) ---------------------------


class ConsumerBase(DeclarativeBase):
    """Own metadata: the consumer table never joins ``Base`` or ``ScratchBase``."""


class SharedNote(ConsumerBase):
    """A project record shared with one team, standing in for a consumer module."""

    __tablename__ = "test_teams_shared_note"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    body: Mapped[str] = mapped_column(String(100), nullable=False)


def _visible_notes(db: Database, project: uuid.UUID, user: uuid.UUID) -> list[str]:
    """What the consumer shows ``user``: notes shared with one of their teams on the project,
    asked through the contract only (the consumer never reads the teams tables)."""
    with db.unit_of_work(_bound(project, user, db)) as s:
        mine = teams.user_team_ids(s, user)
        query = select(SharedNote.body).where(
            SharedNote.project_id == project, SharedNote.team_id.in_(sorted(mine))
        )
        return list(s.scalars(query))


def test_a_record_shared_to_a_team_is_visible_to_its_members_only(api: Api) -> None:
    table = SharedNote.__table__
    table.create(api.db.engine)
    try:
        x, y = api.team("Team X"), api.team("Team Y")
        api.add(x, ERIN)
        api.add(y, FRANK)
        api.link(x, api.home, "PER-04")
        api.link(y, api.home, "PER-04")
        with api.db.unit_of_work(TenancyContext(tenant_id=T1, project_id=api.home)) as s:
            s.add(SharedNote(project_id=api.home, team_id=x, body="for X"))
        assert _visible_notes(api.db, api.home, api.users[ERIN]) == ["for X"]
        # Project members outside X: FRANK (through team Y) and CAROL (individually).
        assert _visible_notes(api.db, api.home, api.users[FRANK]) == []
        assert _visible_notes(api.db, api.home, api.users[CAROL]) == []
    finally:
        table.drop(api.db.engine)
