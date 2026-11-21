"""system_admin capabilities on every project (ADR 0016 §4, T-0275; diff §4.1, edge spec §5.2).

A ``system_admin`` has ``V``, ``C``, ``E`` and ``admin`` (PER-09 equivalent) on every live
project of its tenant without a membership, never ``A`` or ``S``: those come only from its own
qualified personas. Lists show every live project with ``access``; a hold-authority persona
meeting ``system_admin`` warns (SoD) and is audited. Runs on SQLite and, in CI, PostgreSQL
under RLS. All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.audit.service import chain_entries
from app.core.auth.access import (
    HOLD_AUTHORITY,
    SYSTEM_ADMIN,
    Capability,
    Grant,
    HoldKind,
    Persona,
    project_access,
    resolve_scope,
    sod_warning,
)
from app.core.auth.models import ProjectMembership
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import OutOfScopeError
from app.modules.admin import service as admin
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
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

P = Persona
V, C, E, A, S, ADM = (
    Capability.VIEW,
    Capability.CREATE,
    Capability.EDIT,
    Capability.APPROVE,
    Capability.SIGN,
    Capability.ADMINISTER,
)
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
ROOT_T2 = TenancyContext(tenant_id=T2, is_system_admin=True)
CAROL = "carol@example.test"
DAVE = "dave@example.test"
SITE = uuid.UUID("00000000-0000-4000-8000-0000000051e1")
PROJECT = uuid.UUID("00000000-0000-4000-8000-0000000a0001")


# --- ProjectAccess (pure) ---------------------------------------------------------------------


def test_a_non_member_system_admin_gets_v_c_e_admin_and_never_a_or_s() -> None:
    access = project_access([], PROJECT, system_admin=True)
    assert access.capabilities == {V, C, E, ADM}
    for capability in (A, S):
        assert not access.has(capability)
    for kind in HoldKind:
        assert not access.can_release_hold(kind)
    assert access.personas == frozenset()
    assert access.acting_personas == {P.PROJECT_ADMINISTRATOR}
    assert access.site_node_ids is None


def test_a_system_admin_gets_a_only_from_its_own_qualified_persona() -> None:
    qa = project_access([Grant(PROJECT, P.QAQC_LEAD)], PROJECT, system_admin=True)
    assert qa.capabilities == {V, C, E, ADM, A}
    assert qa.can_release_hold(HoldKind.QUALITY)
    assert not qa.can_release_hold(HoldKind.SAFETY)
    assert not qa.has(S)
    for persona in (P.PROJECT_MANAGER, P.HSE_MANAGER):
        assert project_access([Grant(PROJECT, persona)], PROJECT, system_admin=True).has(A)
    # A persona on another project gives nothing here.
    other = project_access([Grant(uuid.uuid4(), P.QAQC_LEAD)], PROJECT, system_admin=True)
    assert other.capabilities == {V, C, E, ADM}
    assert not other.can_release_hold(HoldKind.QUALITY)


def test_a_system_admin_gets_s_only_as_client_representative() -> None:
    signer = project_access([Grant(PROJECT, P.CLIENT_REPRESENTATIVE)], PROJECT, system_admin=True)
    assert signer.capabilities == {V, C, E, ADM, S}
    assert not signer.has(A)
    viewer = project_access([Grant(PROJECT, P.PROJECT_VIEWER)], PROJECT, system_admin=True)
    assert viewer.capabilities == {V, C, E, ADM}


def test_a_site_inspector_admin_releases_only_its_sites_holds_but_reads_every_site() -> None:
    access = project_access([Grant(PROJECT, P.SITE_INSPECTOR, SITE)], PROJECT, system_admin=True)
    assert access.site_node_ids is None  # an admin is never site-filtered (scope.site_filter)
    assert access.can_release_hold(HoldKind.QUALITY, site_node_id=SITE)
    assert not access.can_release_hold(HoldKind.QUALITY, site_node_id=uuid.uuid4())


def test_non_admins_are_unchanged() -> None:
    with pytest.raises(OutOfScopeError):
        project_access([], PROJECT)
    with pytest.raises(OutOfScopeError):
        project_access([Grant(uuid.uuid4(), P.PROJECT_ADMINISTRATOR)], PROJECT)
    pm = project_access([Grant(PROJECT, P.PROJECT_MANAGER)], PROJECT)
    assert pm.capabilities == {V, C, E, A}
    assert pm.acting_personas == {P.PROJECT_MANAGER}
    inspector = project_access([Grant(PROJECT, P.SITE_INSPECTOR, SITE)], PROJECT)
    assert inspector.site_node_ids == {SITE}


def test_the_scope_of_a_non_member_admin_binds_the_project_without_a_site_filter() -> None:
    user = uuid.uuid4()
    base = TenancyContext(tenant_id=T1, user_id=user, is_system_admin=True)
    scope = resolve_scope([], base, PROJECT)
    assert (scope.project_id, scope.site_node_ids, scope.is_system_admin) == (PROJECT, None, True)
    assert scope.member_project_ids == frozenset()
    with pytest.raises(OutOfScopeError):
        resolve_scope([], TenancyContext(tenant_id=T1, user_id=user), PROJECT)


@pytest.mark.parametrize("persona", sorted(HOLD_AUTHORITY))
def test_a_hold_persona_on_a_system_admin_warns(persona: Persona) -> None:
    assert sod_warning([persona], system_admin=True)
    assert not sod_warning([persona])  # alone, for a non-admin: no warning (unchanged)


@pytest.mark.parametrize("persona", [P.CLIENT_REPRESENTATIVE, P.PROJECT_VIEWER, P.FIELD_WORKER])
def test_a_persona_without_hold_authority_never_warns(persona: Persona) -> None:
    assert not sod_warning([persona], system_admin=True)


# --- HTTP -------------------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    migrated = migrated_database(tmp_path)
    database = next(migrated)
    try:
        yield database
    finally:
        revoke_project_viewers(database)
        next(migrated, None)


def _project(db: Database, code: str, ctx: TenancyContext = ROOT) -> uuid.UUID:
    with db.unit_of_work(ctx) as s:
        return projects.create_project(s, ProjectCreate(code=code, name=f"Synthetic {code}")).id


def _grant(db: Database, user: uuid.UUID, project: uuid.UUID, persona: Persona) -> None:
    with db.unit_of_work(ROOT) as s:
        s.add(
            ProjectMembership(
                tenant_id=T1, user_id=user, member_project_id=project, persona=persona.value
            )
        )


class Api:
    """ALICE: System admin, no membership. BOB: PER-09 of HOME. CAROL: PER-02 of HOME and
    AWAY. DAVE: nothing. GONE: a deleted project; FOREIGN: a project of tenant T2."""

    def __init__(self, client: TestClient, db: Database) -> None:
        self.client, self.db = client, db
        self.users = {e: seed_user(db, T1, e) for e in (ALICE, BOB, CAROL, DAVE)}
        with db.unit_of_work(ROOT) as s:
            assert admin.grant(s, self.users[ALICE], SYSTEM_ADMIN, actor_id=None)
        self.home, self.away, self.gone = (_project(db, c) for c in ("HOME", "AWAY", "GONE"))
        self.foreign = _project(db, "FOREIGN", ROOT_T2)
        _grant(db, self.users[BOB], self.home, P.PROJECT_ADMINISTRATOR)
        _grant(db, self.users[CAROL], self.home, P.PROJECT_MANAGER)
        _grant(db, self.users[CAROL], self.away, P.PROJECT_MANAGER)
        with db.unit_of_work(ROOT) as s:
            projects.delete_project(s, self.gone)
        self._headers: dict[str, dict[str, str]] = {}

    def headers(self, email: str) -> dict[str, str]:
        if email not in self._headers:
            body = {"email": email, "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            self._headers[email] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._headers[email]

    def get(self, url: str, email: str) -> Any:
        return self.client.get(url, headers=self.headers(email))

    def chain(self, key: str) -> list[dict[str, Any]]:
        with self.db.unit_of_work(ROOT) as s:
            return [e.payload for e in chain_entries(s, key)]


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        yield Api(client, db)


def _access(listed: list[dict[str, Any]]) -> dict[str, str]:
    return {p["code"]: p["access"] for p in listed}


def test_a_system_admin_lists_every_live_project_of_its_tenant(api: Api) -> None:
    listed = api.get("/api/v1/projects", ALICE)
    assert listed.status_code == 200
    assert _access(listed.json()) == {"AWAY": "system_admin", "HOME": "system_admin"}
    _grant(api.db, api.users[ALICE], api.home, P.PROJECT_VIEWER)
    assert _access(api.get("/api/v1/projects", ALICE).json()) == {
        "AWAY": "system_admin",
        "HOME": "member",
    }
    # Non-admins are unchanged: their own live projects, as members.
    assert _access(api.get("/api/v1/projects", BOB).json()) == {"HOME": "member"}
    assert api.get("/api/v1/projects", DAVE).json() == []


def test_me_projects_lists_every_live_project_for_a_system_admin(api: Api) -> None:
    _grant(api.db, api.users[ALICE], api.home, P.QAQC_LEAD)
    mine = api.get("/api/v1/me/projects", ALICE)
    assert mine.status_code == 200
    assert mine.json() == [
        {
            "id": str(api.away),
            "code": "AWAY",
            "name": "Synthetic AWAY",
            "personas": [],
            "access": "system_admin",
        },
        {
            "id": str(api.home),
            "code": "HOME",
            "name": "Synthetic HOME",
            "personas": ["PER-06"],
            "access": "member",
        },
    ]
    assert [(p["code"], p["access"]) for p in api.get("/api/v1/me/projects", CAROL).json()] == [
        ("AWAY", "member"),
        ("HOME", "member"),
    ]
    assert api.get("/api/v1/me/projects", DAVE).json() == []


def test_a_non_member_admin_reads_and_administers_any_live_project(api: Api) -> None:
    url = f"/api/v1/projects/{api.away}"
    read = api.get(url, ALICE)
    assert read.status_code == 200
    assert (read.json()["code"], read.json()["access"]) == ("AWAY", "system_admin")
    renamed = api.client.patch(url, json={"name": "Renamed"}, headers=api.headers(ALICE))
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Renamed"
    lobby = api.client.put(
        f"{url}/lobby", json={"visible_in_lobby": True}, headers=api.headers(ALICE)
    )
    assert lobby.status_code == 200
    # A deleted project, another tenant's and an unknown id stay 404 on the project endpoints.
    for hidden in (api.gone, api.foreign, uuid.uuid4()):
        hidden_url = f"/api/v1/projects/{hidden}"
        assert api.get(hidden_url, ALICE).status_code == 404
        patch = api.client.patch(hidden_url, json={"name": "X"}, headers=api.headers(ALICE))
        assert patch.status_code == 404
    # Non-admins: a non-member still gets 404, a member without admin 403.
    assert api.get(url, BOB).status_code == 404
    patch = api.client.patch(url, json={"name": "X"}, headers=api.headers(CAROL))
    assert patch.status_code == 403


def test_project_scoped_endpoints_answer_a_non_member_admin_like_a_per09(api: Api) -> None:
    alice = api.headers(ALICE)
    notes = f"/api/v1/notifications/projects/{api.away}/notifications"
    body = {"recipient_id": str(api.users[CAROL]), "kind": "shift.briefing"}
    sent = api.client.post(notes, json=body, headers=alice)  # needs admin
    assert sent.status_code == 201, sent.text
    assert api.client.get(notes, headers=alice).status_code == 200  # needs V
    teams = api.get(f"/api/v1/teams/projects/{api.away}/teams", ALICE)
    assert teams.status_code == 200
    rule_sets = api.get(f"/api/v1/validation/projects/{api.away}/rule-sets", ALICE)
    assert rule_sets.status_code == 200
    for hidden in (api.gone, api.foreign, uuid.uuid4()):
        url = f"/api/v1/notifications/projects/{hidden}/notifications"
        assert api.client.post(url, json=body, headers=alice).status_code == 404
        assert api.client.get(url, headers=alice).status_code == 404
    # A non-admin non-member is unchanged.
    assert api.get(notes, DAVE).status_code == 404


def _principal(user: uuid.UUID) -> Any:
    return SimpleNamespace(tenant_id=T1, user_id=user)


def test_bind_project_gives_a_non_member_admin_admin_but_never_approval(api: Api) -> None:
    alice = api.users[ALICE]
    base = TenancyContext(tenant_id=T1, user_id=alice, is_system_admin=True)
    with api.db.unit_of_work(base) as s:
        access = projects.bind_project(s, _principal(alice), [], api.away, require=ADM)
        assert access.system_admin
        bound = session_tenancy(s)
        assert bound is not None
        assert (bound.project_id, bound.site_node_ids) == (api.away, None)
    for capability in (A, S):
        with api.db.unit_of_work(base) as s, pytest.raises(HTTPException) as refused:
            projects.bind_project(s, _principal(alice), [], api.away, require=capability)
        assert refused.value.status_code == 403
        # Unknown, deleted and other-tenant projects are 404 before the capability.
        for hidden in (api.gone, api.foreign, uuid.uuid4()):
            with api.db.unit_of_work(base) as s, pytest.raises(OutOfScopeError):
                projects.bind_project(s, _principal(alice), [], hidden, require=capability)
    # The flag counts only when the session was bound with it for this very caller.
    other = TenancyContext(tenant_id=T1, user_id=api.users[DAVE], is_system_admin=True)
    with api.db.unit_of_work(other) as s, pytest.raises(OutOfScopeError):
        projects.bind_project(s, _principal(alice), [], api.away)


# --- separation of duties (diff §4.1, ADR 0016) -----------------------------------------------


def _put(api: Api, project: uuid.UUID, user: str, caller: str, body: dict[str, Any]) -> Any:
    url = f"/api/v1/admin/projects/{project}/members/{api.users[user]}"
    return api.client.put(url, json=body, headers=api.headers(caller))


@pytest.mark.parametrize("caller", [BOB, ALICE], ids=["per09", "system-admin"])
def test_a_hold_persona_for_a_system_admin_needs_confirmation(api: Api, caller: str) -> None:
    body: dict[str, Any] = {"grants": [{"persona": "PER-06"}]}
    refused = _put(api, api.home, ALICE, caller, body)
    assert refused.status_code == 409
    assert refused.json()["detail"]["sod_warning"] is True
    assert api.chain(f"project-members:{api.home}") == []
    confirmed = _put(api, api.home, ALICE, caller, {**body, "confirm_sod": True})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["sod_warning"] is True
    [entry] = api.chain(f"project-members:{api.home}")
    assert (entry["sod_warning"], entry["user_id"]) == (True, str(api.users[ALICE]))


def test_a_persona_without_hold_authority_for_an_admin_and_a_hold_one_for_others_pass(
    api: Api,
) -> None:
    viewer = _put(api, api.home, ALICE, BOB, {"grants": [{"persona": "PER-16"}]})
    assert (viewer.status_code, viewer.json()["sod_warning"]) == (200, False)
    qa = _put(api, api.home, DAVE, BOB, {"grants": [{"persona": "PER-06"}]})
    assert (qa.status_code, qa.json()["sod_warning"]) == (200, False)


def test_granting_system_admin_to_a_hold_persona_holder_needs_confirmation(api: Api) -> None:
    root, carol = api.headers(ALICE), str(api.users[CAROL])
    refused = api.client.post(
        "/api/v1/admin/grants", json={"user_id": carol, "grant": SYSTEM_ADMIN}, headers=root
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "sod_confirmation_required"
    assert refused.json()["detail"]["sod_warning"] is True
    assert api.chain(f"admin-grant:{carol}") == []
    body = {"user_id": carol, "grant": SYSTEM_ADMIN, "confirm_sod": True}
    granted = api.client.post("/api/v1/admin/grants", json=body, headers=root)
    assert granted.status_code == 201, granted.text
    assert granted.json()["sod_warning"] is True
    [entry] = api.chain(f"admin-grant:{carol}")
    assert entry == {
        "action": "grant",
        "grant": SYSTEM_ADMIN,
        "sod_warning": True,
        "sod_project_ids": sorted([str(api.home), str(api.away)]),
    }
    # Already held: nothing new, nothing to warn about.
    again = api.client.post("/api/v1/admin/grants", json=body, headers=root)
    assert (again.status_code, again.json()["sod_warning"]) == (200, False)


def test_other_grants_and_users_without_hold_personas_do_not_warn(api: Api) -> None:
    root = api.headers(ALICE)
    for user, kind in ((CAROL, "create_projects"), (DAVE, SYSTEM_ADMIN), (BOB, SYSTEM_ADMIN)):
        body = {"user_id": str(api.users[user]), "grant": kind}
        granted = api.client.post("/api/v1/admin/grants", json=body, headers=root)
        assert (granted.status_code, granted.json()["sod_warning"]) == (201, False)


def test_a_team_link_giving_a_hold_persona_to_an_admin_needs_confirmation(api: Api) -> None:
    root = api.headers(ALICE)
    team = api.client.post("/api/v1/teams", json={"name": "Synthetic QA"}, headers=root)
    assert team.status_code == 201, team.text
    team_url = f"/api/v1/teams/{team.json()['id']}"
    added = api.client.put(f"{team_url}/members/{api.users[ALICE]}", headers=root)
    assert added.status_code == 204
    link = f"{team_url}/projects/{api.away}"
    refused = api.client.put(link, json={"persona": "PER-06"}, headers=root)
    assert refused.status_code == 409
    sod = refused.json()["detail"]["sod_warning"]
    assert sod["user_ids"] == [str(api.users[ALICE])]
    confirmed = api.client.put(link, json={"persona": "PER-06", "confirm_sod": True}, headers=root)
    assert confirmed.status_code == 200, confirmed.text
    # Adding a System admin to a team that already carries a hold persona warns too.
    with api.db.unit_of_work(ROOT) as s:
        assert admin.grant(s, api.users[DAVE], SYSTEM_ADMIN, actor_id=None)
    member = api.client.put(f"{team_url}/members/{api.users[DAVE]}", headers=root)
    assert member.status_code == 409
    confirmed_member = api.client.put(
        f"{team_url}/members/{api.users[DAVE]}", json={"confirm_sod": True}, headers=root
    )
    assert confirmed_member.status_code == 204
