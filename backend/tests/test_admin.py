"""Admin module: grants, users, memberships, ``/me/projects`` and the membership-based scope
(T-0014, diff §4.1, edge spec §5/§6 amendments, TD-053, TD-151, TD-265).

Runs on SQLite (the ORM scope mirrors the policies) and, in CI, on PostgreSQL under RLS
against the migrated schema. Raw-SQL RLS checks as ``kerp_app``: ``test_rls_admin.py``.
All data is synthetic.
"""

import importlib.util
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.audit.service import chain_entries
from app.core.auth.access import (
    HOLD_AUTHORITY,
    PERSONAS,
    SYSTEM_ADMIN,
    Capability,
    Grant,
    HoldKind,
    Persona,
    caller_admin_grants,
    project_access,
    resolve_request_scope,
    sod_warning,
)
from app.core.auth.models import PERSONA_CODES, ProjectMembership
from app.core.db import Base, Database, contract_violations
from app.core.module_loader.loader import discover_modules
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import (
    ADMIN_GRANT_POLICY_NAMES,
    SESSION_INFO_KEY,
    SETTINGS,
    RlsPolicy,
    expected_policy_names,
    rls_policy_sql,
    rls_tables,
    setting_values,
    table_policy,
)
from app.core.tenancy.scope import OutOfScopeError
from app.modules.admin import service
from app.modules.admin.models import TABLE, AdminGrant
from app.modules.admin.schemas import GrantIn, MemberAssign, UserCreate, UserUpdate
from app.modules.projects import service as projects
from app.modules.projects.models import Project
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import (
    ALICE,
    BOB,
    PASSWORD,
    T1,
    auth_app,
    make_settings,
    revoke_project_viewers,
    seed_user,
)
from tests.db_support import database_url

ADMIN = "/api/v1/admin"
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)  # provisioning / scripts
CAROL = "carol@example.test"
DAVE = "dave@example.test"
SITE = uuid.UUID("00000000-0000-4000-8000-00000000051e")


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    migrated = migrated_database(tmp_path)
    database = next(migrated)
    try:
        yield database
    finally:
        revoke_project_viewers(database)  # the 0012 downgrade refuses while PER-16 is active
        next(migrated, None)


def _project(db: Database, code: str) -> uuid.UUID:
    with db.unit_of_work(ROOT) as s:
        return projects.create_project(s, ProjectCreate(code=code, name=f"Synthetic {code}")).id


def _grant(db: Database, user: uuid.UUID, project: uuid.UUID, persona: Persona) -> None:
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
        assert service.grant(s, user, SYSTEM_ADMIN, actor_id=None)


# --- contract -------------------------------------------------------------------------------


def test_grant_table_meets_the_record_contract_with_its_own_policy() -> None:
    assert [p for p in contract_violations(Base.metadata) if p.startswith(TABLE)] == []
    assert TABLE in rls_tables(Base.metadata)
    assert expected_policy_names(Base.metadata.tables[TABLE]) == ADMIN_GRANT_POLICY_NAMES
    for table in ("oe_projects_project", "auth_project_membership"):
        assert expected_policy_names(Base.metadata.tables[table])[0] == "membership_read_policy"


def test_admin_and_me_are_platform_modules() -> None:
    modules = discover_modules()
    assert modules.get("admin").toggleable is False
    assert modules.get("me").toggleable is False


def test_bound_settings_come_from_the_context_only() -> None:
    user = uuid.uuid4()
    values = setting_values(TenancyContext(tenant_id=T1, user_id=user, is_system_admin=True))
    assert values["app.current_user_id"] == str(user)
    assert values["app.is_system_admin"] == "true"
    plain = setting_values(TenancyContext(tenant_id=T1))
    assert (plain["app.current_user_id"], plain["app.is_system_admin"]) == ("", "")
    assert setting_values(None) == dict.fromkeys(SETTINGS, "")


# --- separation of duties, no A/S from admin ------------------------------------------------


def test_hold_authority_is_derived_from_the_matrix() -> None:
    assert {p.value for p in HOLD_AUTHORITY} == {"PER-02", "PER-06", "PER-07", "PER-11"}
    for persona in HOLD_AUTHORITY:
        assert sod_warning([Persona.PROJECT_ADMINISTRATOR, persona])
        assert not sod_warning([persona])
    assert not sod_warning([Persona.PROJECT_ADMINISTRATOR, Persona.PROJECT_VIEWER])
    assert not sod_warning([Persona.PROJECT_ADMINISTRATOR, Persona.SITE_SUPERINTENDENT])


def test_project_administrator_confers_no_approval_or_signature() -> None:
    project = uuid.uuid4()
    admin_only = project_access([Grant(project, Persona.PROJECT_ADMINISTRATOR)], project)
    assert admin_only.capabilities.isdisjoint({Capability.APPROVE, Capability.SIGN})
    assert not admin_only.can_release_hold(HoldKind.QUALITY)
    combined = project_access(
        [Grant(project, Persona.PROJECT_ADMINISTRATOR), Grant(project, Persona.HSE_MANAGER)],
        project,
    )
    hse = PERSONAS[Persona.HSE_MANAGER].capabilities
    assert combined.capabilities - {Capability.ADMINISTER} == hse
    assert combined.can_release_hold(HoldKind.SAFETY)  # PER-11's own authority
    assert not combined.can_release_hold(HoldKind.QUALITY)


# --- schemas (TD-265) -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["is_internal", "entra_oid", "entra_tid", "tenant_id", "password"]
)
def test_user_bodies_refuse_derived_and_unknown_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        UserCreate.model_validate({"email": CAROL, field: "x"})
    with pytest.raises(ValidationError):
        UserUpdate.model_validate({"display_name": "Carol", field: "x"})


def test_user_update_needs_a_non_null_field() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        UserUpdate.model_validate({})
    with pytest.raises(ValidationError, match="null"):
        UserUpdate.model_validate({"is_active": None})


def test_member_bodies_take_personas_only_with_site_rules() -> None:
    assert [p.value for p in Persona] == list(PERSONA_CODES)
    for bad in ("system_admin", "create_projects", "PER-17"):
        with pytest.raises(ValidationError):
            GrantIn.model_validate({"persona": bad})
    with pytest.raises(ValidationError, match="site_node_id"):
        GrantIn.model_validate({"persona": "PER-07"})
    with pytest.raises(ValidationError, match="not site-scoped"):
        GrantIn.model_validate({"persona": "PER-06", "site_node_id": str(SITE)})
    with pytest.raises(ValidationError, match="repeat"):
        MemberAssign.model_validate({"grants": [{"persona": "PER-06"}, {"persona": "PER-06"}]})
    with pytest.raises(ValidationError):
        MemberAssign.model_validate({"grants": []})


# --- grants and the membership-based scope (service level, both dialects) ------------------


def test_grants_are_written_by_system_admins_only_and_read_as_own_rows(db: Database) -> None:
    alice, bob = seed_user(db, T1, ALICE), seed_user(db, T1, BOB)
    _make_admin(db, alice)
    with db.unit_of_work(ROOT) as s:
        assert service.grant(s, bob, "create_projects", actor_id=alice)
        assert not service.grant(s, bob, "create_projects", actor_id=alice)  # already held
    as_bob = TenancyContext(tenant_id=T1, user_id=bob)
    with pytest.raises(OutOfScopeError), db.unit_of_work(as_bob) as s:
        service.grant(s, bob, SYSTEM_ADMIN, actor_id=bob)
    with pytest.raises(OutOfScopeError), db.unit_of_work(as_bob) as s:
        service.revoke(s, bob, "create_projects", actor_id=bob)
    with db.unit_of_work(as_bob) as s:
        assert set(s.scalars(select(AdminGrant.kind))) == {"create_projects"}  # own rows only
        assert caller_admin_grants(s, T1, bob) == {"create_projects"}
        assert caller_admin_grants(s, T1, alice) == frozenset()  # another user's: invisible
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as s:  # no user, no flag: nothing
        assert list(s.scalars(select(AdminGrant))) == []
    with db.unit_of_work(ROOT) as s:
        assert len(list(s.scalars(select(AdminGrant)))) == 2
        assert service.revoke(s, bob, "create_projects", actor_id=alice)
        assert not service.revoke(s, bob, "create_projects", actor_id=alice)
        assert service.grant(s, bob, "create_projects", actor_id=alice)  # again after revoke
        actions = [e.payload["action"] for e in chain_entries(s, f"admin-grant:{bob}")]
    assert actions == ["grant", "revoke", "grant"]


def test_the_scope_resolver_binds_the_system_admin_flag_from_the_table(db: Database) -> None:
    alice, bob = seed_user(db, T1, ALICE), seed_user(db, T1, BOB)
    _make_admin(db, alice)
    home = _project(db, "HOME")
    _grant(db, bob, home, Persona.PROJECT_MANAGER)
    admin = resolve_request_scope(db, TenancyContext(tenant_id=T1, user_id=alice))
    assert admin.is_system_admin
    assert admin.member_project_ids == frozenset()
    member = resolve_request_scope(db, TenancyContext(tenant_id=T1, user_id=bob))
    assert not member.is_system_admin
    assert member.member_project_ids == {home}
    assert member.allowed_project_ids == frozenset()  # PER-02 is not a portfolio persona


def test_project_rows_are_visible_by_membership_or_to_system_admins(db: Database) -> None:
    bob = seed_user(db, T1, BOB)
    home, other = _project(db, "HOME"), _project(db, "OTHER")
    _grant(db, bob, home, Persona.CLIENT_REPRESENTATIVE)
    scope = resolve_request_scope(db, TenancyContext(tenant_id=T1, user_id=bob))
    with db.unit_of_work(scope) as s:
        assert [p.code for p in s.scalars(select(Project))] == ["HOME"]
        with pytest.raises(OutOfScopeError):
            projects.get_project(s, other)
        rows = s.scalars(select(ProjectMembership)).all()
        assert {r.member_project_id for r in rows} == {home}
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as s:  # plain tenant session
        assert list(s.scalars(select(Project))) == []
    with db.unit_of_work(ROOT) as s:
        assert {p.code for p in s.scalars(select(Project))} == {"HOME", "OTHER"}


def test_the_write_guard_refuses_changing_a_row_the_session_cannot_see(db: Database) -> None:
    """Mirror of the update/delete policies: exact scope *and* visibility."""
    home = _project(db, "HOME")
    with db.unit_of_work(ROOT) as s:
        project = projects.get_project(s, home)
        # Swap the scope under the loaded object (``bind_tenancy`` itself refuses that).
        s.info[SESSION_INFO_KEY] = TenancyContext(tenant_id=T1)
        project.name = "Hijacked"
        with pytest.raises(OutOfScopeError):
            s.flush()
        s.rollback()


# --- HTTP -----------------------------------------------------------------------------------


class Api:
    """ALICE: System admin. BOB: PER-09 of HOME. CAROL: PER-02 of HOME. DAVE: no project."""

    def __init__(self, client: TestClient, db: Database) -> None:
        self.client, self.db = client, db
        self.users = {e: seed_user(db, T1, e) for e in (ALICE, BOB, CAROL, DAVE)}
        _make_admin(db, self.users[ALICE])
        self.home, self.away = _project(db, "HOME"), _project(db, "AWAY")
        _grant(db, self.users[BOB], self.home, Persona.PROJECT_ADMINISTRATOR)
        _grant(db, self.users[CAROL], self.home, Persona.PROJECT_MANAGER)
        _grant(db, self.users[CAROL], self.away, Persona.PROJECT_MANAGER)
        self._headers: dict[str, dict[str, str]] = {}

    def headers(self, email: str) -> dict[str, str]:
        if email not in self._headers:
            body = {"email": email, "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            token = response.json()["access_token"]
            self._headers[email] = {"Authorization": f"Bearer {token}"}
        return self._headers[email]

    def members(self, project: uuid.UUID, email: str) -> Any:
        return self.client.get(f"{ADMIN}/projects/{project}/members", headers=self.headers(email))

    def put(self, project: uuid.UUID, user: str, email: str, body: dict[str, Any]) -> Any:
        url = f"{ADMIN}/projects/{project}/members/{self.users[user]}"
        return self.client.put(url, json=body, headers=self.headers(email))

    def chain(self, key: str) -> list[dict[str, Any]]:
        with self.db.unit_of_work(ROOT) as s:
            return [e.payload for e in chain_entries(s, key)]


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    settings = make_settings(
        database_url=database_url(tmp_path), internal_email_domains="staff.example.test"
    )
    app, _runtime = auth_app(settings)
    with TestClient(app) as client:
        yield Api(client, db)


def test_every_endpoint_needs_a_token(api: Api) -> None:
    calls = [
        ("GET", f"{ADMIN}/users"),
        ("POST", f"{ADMIN}/users"),
        ("PATCH", f"{ADMIN}/users/{uuid.uuid4()}"),
        ("POST", f"{ADMIN}/users/lookup"),
        ("GET", f"{ADMIN}/projects/{api.home}/members"),
        ("PUT", f"{ADMIN}/projects/{api.home}/members/{uuid.uuid4()}"),
        ("DELETE", f"{ADMIN}/projects/{api.home}/members/{uuid.uuid4()}"),
        ("GET", "/api/v1/me/projects"),
    ]
    assert [api.client.request(m, u, json={}).status_code for m, u in calls] == [401] * 8


def test_a_system_admin_lists_creates_and_deactivates_users(api: Api) -> None:
    root = api.headers(ALICE)
    listed = api.client.get(f"{ADMIN}/users", headers=root).json()
    assert [u["email"] for u in listed] == [ALICE, BOB, CAROL, DAVE]
    assert all("tenant_id" not in u and u["is_internal"] is False for u in listed)
    body = {"email": " Erin@Staff.Example.Test ", "display_name": "Erin"}
    created = api.client.post(f"{ADMIN}/users", json=body, headers=root)
    assert created.status_code == 201, created.text
    erin = created.json()
    assert (erin["email"], erin["is_internal"], erin["is_active"]) == (
        "erin@staff.example.test",
        True,  # derived from KERP_INTERNAL_EMAIL_DOMAINS by core auth, never input
        True,
    )
    assert api.client.post(f"{ADMIN}/users", json=body, headers=root).status_code == 409
    bob_headers = api.headers(BOB)
    off = api.client.patch(
        f"{ADMIN}/users/{api.users[BOB]}", json={"is_active": False}, headers=root
    )
    assert off.status_code == 200
    assert off.json()["is_active"] is False
    assert api.client.get("/api/v1/me/projects", headers=bob_headers).status_code == 401
    assert [e["action"] for e in api.chain(f"admin-user:{erin['id']}")] == ["create"]
    assert api.chain(f"admin-user:{api.users[BOB]}") == [
        {"action": "update", "fields": ["is_active"], "is_active": False}
    ]
    missing = api.client.patch(
        f"{ADMIN}/users/{uuid.uuid4()}", json={"is_active": False}, headers=root
    )
    assert missing.status_code == 404


@pytest.mark.parametrize("field", ["is_internal", "entra_oid", "entra_tid", "tenant_id"])
def test_user_endpoints_refuse_derived_fields_with_422(api: Api, field: str) -> None:
    root = api.headers(ALICE)
    value: Any = True if field == "is_internal" else str(uuid.uuid4())
    create = api.client.post(
        f"{ADMIN}/users", json={"email": "x@example.test", field: value}, headers=root
    )
    update = api.client.patch(
        f"{ADMIN}/users/{api.users[DAVE]}", json={"display_name": "D", field: value}, headers=root
    )
    assert (create.status_code, update.status_code) == (422, 422)


def test_user_administration_is_for_system_admins_only(api: Api) -> None:
    for email in (BOB, CAROL, DAVE):
        headers = api.headers(email)
        assert api.client.get(f"{ADMIN}/users", headers=headers).status_code == 403
        body = {"email": "x@example.test"}
        assert api.client.post(f"{ADMIN}/users", json=body, headers=headers).status_code == 403
        url = f"{ADMIN}/users/{api.users[DAVE]}"
        assert api.client.patch(url, json={"is_active": False}, headers=headers).status_code == 403


def test_the_admin_flag_is_never_taken_from_the_request(api: Api) -> None:
    headers = {**api.headers(DAVE), "X-Is-System-Admin": "true", "app.is_system_admin": "true"}
    url = f"{ADMIN}/users?is_system_admin=true"
    assert api.client.get(url, headers=headers).status_code == 403


def test_a_project_administrator_looks_up_an_account_by_exact_email(api: Api) -> None:
    url, headers = f"{ADMIN}/users/lookup", api.headers(BOB)
    found = api.client.post(url, json={"email": "  CAROL@example.test"}, headers=headers)
    assert found.status_code == 200
    assert found.json() == {
        "id": str(api.users[CAROL]),
        "email": CAROL,
        "display_name": "",
    }
    for partial in ("carol", "carol@example", "%@example.test", "*@example.test"):
        assert api.client.post(url, json={"email": partial}, headers=headers).status_code in (
            404,
            422,
        )
    entries = api.chain(f"admin-user-lookup:{api.users[BOB]}")
    assert entries[0]["user_id"] == str(api.users[CAROL])
    assert all(CAROL not in str(e) for e in entries)  # the e-mail only as a digest
    assert len(entries) >= 2
    assert entries[-1]["user_id"] is None
    assert (
        api.client.post(url, json={"email": CAROL}, headers=api.headers(ALICE)).status_code == 200
    )
    for email in (CAROL, DAVE):  # PER-02 / no project
        response = api.client.post(url, json={"email": ALICE}, headers=api.headers(email))
        assert response.status_code == 403


def test_lookup_hides_inactive_accounts_and_is_rate_limited(api: Api) -> None:
    root = api.headers(ALICE)
    api.client.patch(f"{ADMIN}/users/{api.users[DAVE]}", json={"is_active": False}, headers=root)
    url, headers = f"{ADMIN}/users/lookup", api.headers(BOB)
    assert api.client.post(url, json={"email": DAVE}, headers=headers).status_code == 404
    codes = [
        api.client.post(url, json={"email": CAROL}, headers=headers).status_code for _ in range(21)
    ]
    assert codes[:20] == [200] * 20
    assert codes[20] == 429
    limited = api.client.post(url, json={"email": CAROL}, headers=headers)
    assert int(limited.headers["Retry-After"]) > 0
    # Per caller: the System admin is not affected.
    assert api.client.post(url, json={"email": CAROL}, headers=root).status_code == 200


def test_a_project_administrator_manages_members_of_their_project(api: Api) -> None:
    listed = api.members(api.home, BOB)
    assert listed.status_code == 200
    assert [(m["email"], [g["persona"] for g in m["grants"]]) for m in listed.json()] == [
        (BOB, ["PER-09"]),
        (CAROL, ["PER-02"]),
    ]
    body = {"grants": [{"persona": "PER-07", "site_node_id": str(SITE)}, {"persona": "PER-16"}]}
    assigned = api.put(api.home, DAVE, BOB, body)
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["sod_warning"] is False
    assert assigned.json()["grants"] == [
        {"persona": "PER-07", "site_node_id": str(SITE)},
        {"persona": "PER-16", "site_node_id": None},
    ]
    co_admin = api.put(api.home, DAVE, BOB, {"grants": [{"persona": "PER-09"}]})
    assert co_admin.status_code == 200  # PER-09 for a co-admin; replaces the set
    assert [g["persona"] for g in co_admin.json()["grants"]] == ["PER-09"]
    dave = api.headers(DAVE)
    assert api.members(api.home, DAVE).status_code == 200  # now a co-admin
    assert [p["code"] for p in api.client.get("/api/v1/me/projects", headers=dave).json()] == [
        "HOME"
    ]
    url = f"{ADMIN}/projects/{api.home}/members/{api.users[DAVE]}"
    assert api.client.delete(url, headers=api.headers(BOB)).status_code == 204
    assert api.client.delete(url, headers=api.headers(BOB)).status_code == 404
    again = api.put(api.home, DAVE, BOB, {"grants": [{"persona": "PER-16"}]})
    assert again.status_code == 200  # a revoked grant comes back (revived row)
    actions = [e["action"] for e in api.chain(f"project-members:{api.home}")]
    assert actions == ["assign", "assign", "remove", "assign"]


def test_a_project_administrator_cannot_touch_other_projects(api: Api) -> None:
    body = {"grants": [{"persona": "PER-16"}]}
    assert api.members(api.away, BOB).status_code == 404  # not a member of AWAY
    assert api.put(api.away, DAVE, BOB, body).status_code == 404
    url = f"{ADMIN}/projects/{api.away}/members/{api.users[CAROL]}"
    assert api.client.delete(url, headers=api.headers(BOB)).status_code == 404
    assert api.members(api.home, CAROL).status_code == 403  # member, not PER-09
    assert api.put(api.home, DAVE, CAROL, body).status_code == 403
    assert api.members(uuid.uuid4(), BOB).status_code == 404
    # A tenant-level grant is not a persona: never assignable through memberships.
    grant = api.put(api.home, DAVE, BOB, {"grants": [{"persona": "system_admin"}]})
    assert grant.status_code == 422


def test_a_system_admin_manages_any_project_and_unknowns_are_404(api: Api) -> None:
    assert [m["email"] for m in api.members(api.away, ALICE).json()] == [CAROL]
    recovered = api.put(api.away, BOB, ALICE, {"grants": [{"persona": "PER-09"}]})
    assert recovered.status_code == 200  # e.g. recovering a project whose admins left
    url = f"{ADMIN}/projects/{api.away}/members/{uuid.uuid4()}"
    assert (
        api.client.put(
            url, json={"grants": [{"persona": "PER-16"}]}, headers=api.headers(ALICE)
        ).status_code
        == 404
    )
    assert api.members(uuid.uuid4(), ALICE).status_code == 404
    with api.db.unit_of_work(ROOT) as s:
        projects.delete_project(s, api.away)
    assert api.members(api.away, ALICE).status_code == 404
    assert api.members(api.away, BOB).status_code == 404


def test_project_administrator_plus_hold_authority_needs_confirmation(api: Api) -> None:
    body: dict[str, Any] = {"grants": [{"persona": "PER-09"}, {"persona": "PER-06"}]}
    refused = api.put(api.home, CAROL, BOB, body)
    assert refused.status_code == 409
    assert refused.json()["detail"]["sod_warning"] is True
    assert api.chain(f"project-members:{api.home}") == []
    confirmed = api.put(api.home, CAROL, BOB, {**body, "confirm_sod": True})
    assert confirmed.status_code == 200
    assert confirmed.json()["sod_warning"] is True
    [entry] = api.chain(f"project-members:{api.home}")
    assert entry["sod_warning"] is True
    assert entry["user_id"] == str(api.users[CAROL])
    # The combination adds no capability beyond each persona's own.
    access = project_access(
        [Grant(api.home, Persona.PROJECT_ADMINISTRATOR), Grant(api.home, Persona.QAQC_LEAD)],
        api.home,
    )
    assert Capability.SIGN not in access.capabilities
    viewer = api.put(
        api.home, DAVE, BOB, {"grants": [{"persona": "PER-09"}, {"persona": "PER-16"}]}
    )
    assert viewer.json()["sod_warning"] is False


def test_me_projects_lists_the_callers_live_projects(api: Api) -> None:
    third = _project(api.db, "AAA")
    with api.db.unit_of_work(ROOT) as s:
        s.add(
            ProjectMembership(
                tenant_id=T1,
                user_id=api.users[CAROL],
                member_project_id=third,
                persona=Persona.PROJECT_VIEWER.value,
            )
        )
        projects.delete_project(s, third)
    response = api.client.get("/api/v1/me/projects", headers=api.headers(CAROL))
    assert response.status_code == 200
    assert response.json() == [
        {"id": str(api.away), "code": "AWAY", "name": "Synthetic AWAY", "personas": ["PER-02"]},
        {"id": str(api.home), "code": "HOME", "name": "Synthetic HOME", "personas": ["PER-02"]},
    ]
    # A System admin grant adds no project of its own; no tenant id anywhere.
    assert api.client.get("/api/v1/me/projects", headers=api.headers(ALICE)).json() == []
    assert api.client.get("/api/v1/me/projects", headers=api.headers(DAVE)).json() == []


def test_migration_0014_inlines_exactly_the_live_policy_sql() -> None:
    """The migration's frozen DDL equals ``rls_policy_sql`` for each table at this revision."""
    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    spec = importlib.util.spec_from_file_location("m0014", next(versions.glob("0014_*.py")))
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def creates(statements: list[str]) -> set[str]:
        return {s for s in statements if s.startswith("CREATE")}

    tables = Base.metadata.tables
    for table, column, owner in migration.MEMBERSHIP_TABLES:
        policy = table_policy(tables[table])
        assert policy == RlsPolicy("membership", owner_column=owner, project_column=column)
        frozen = migration._membership_sql(table, column, owner)
        assert creates(rls_policy_sql(table, policy)) == creates(frozen)
    grant_policy = table_policy(tables[TABLE])
    assert grant_policy is not None
    assert creates(rls_policy_sql(TABLE, grant_policy)) == creates(migration._admin_grant_sql())
