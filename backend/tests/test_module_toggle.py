"""Per-project module enable/disable (T-0011) and its API on the project path (T-0084).

A project turns modules on and off; a module that is off for a project answers 404 on all
of its routes for that project, exactly as if the route did not exist. The toggle API is
``/api/v1/projects/{project_id}/modules[/{name}]`` (projects module): ``V`` to read, the
``admin`` capability (PER-09 or a ``system_admin``, ADR 0016) to change. These run on SQLite
and, in CI, on PostgreSQL under project RLS against the migrated schema. Synthetic data only.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit.service import chain_entries
from app.core.auth.access import SYSTEM_ADMIN, Persona
from app.core.auth.deps import Principal, get_current_user, unauthorized
from app.core.auth.models import ProjectMembership, User
from app.core.auth.tokens import TokenClaims
from app.core.config import Settings
from app.core.db import Base, Database, contract_violations
from app.core.module_loader import service
from app.core.module_loader.loader import API_PREFIX, ModuleRegistry
from app.core.module_loader.manifest import ModuleManifest
from app.core.module_loader.models import TABLE, ProjectModuleSetting
from app.core.module_loader.service import (
    AUDIT_CHAIN,
    ModuleToggleError,
    UnknownModuleError,
    project_states,
    set_enabled,
)
from app.core.tenancy.context import TenancyContext, get_tenancy_context
from app.core.tenancy.rls import bind_tenancy, rls_tables, session_tenancy
from app.main import create_app
from app.modules.admin import service as admin
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, auth_app, make_settings, seed_user
from tests.auth_support import T1 as AUTH_T1
from tests.auth_support import T2 as AUTH_T2
from tests.db_support import database_url
from tests.module_support import FakeModule, ModulePackages
from tests.tenancy_support import CTX_A, CTX_B, CTX_C, CTX_T1, PA, T1

USER = uuid.UUID("00000000-0000-4000-8000-00000000f001")

REGISTRY = ModuleRegistry.from_manifests(
    "synthetic",
    [
        ModuleManifest(name="projects", version="1.0.0", toggleable=False),
        ModuleManifest(name="assets", version="1.0.0", depends_on=("projects",)),
        ModuleManifest(name="quality", version="1.0.0", depends_on=("assets",)),
        ModuleManifest(name="welding", version="1.0.0", default_enabled=False),
        ModuleManifest(name="ndt", version="1.0.0", depends_on=("welding",)),
    ],
)
DEFAULTS = {"projects": True, "assets": True, "quality": True, "welding": False, "ndt": False}


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def _states(db: Database, ctx: TenancyContext) -> dict[str, bool]:
    with db.unit_of_work(ctx) as s:
        return project_states(s, REGISTRY)


def _set(db: Database, ctx: TenancyContext, name: str, enabled: bool) -> dict[str, bool]:
    with db.unit_of_work(ctx) as s:
        return set_enabled(s, REGISTRY, name, enabled, actor_id=USER)


# --- contract --------------------------------------------------------------------------------


def test_setting_table_meets_the_record_contract_and_is_under_rls() -> None:
    assert TABLE in Base.metadata.tables
    assert [p for p in contract_violations(Base.metadata) if p.startswith(TABLE)] == []
    assert TABLE in rls_tables(Base.metadata)
    assert Base.metadata.tables[TABLE].columns["project_id"].nullable is False


# --- service ---------------------------------------------------------------------------------


def test_defaults_come_from_the_manifests(db: Database) -> None:
    assert _states(db, CTX_A) == DEFAULTS


def test_disable_and_enable_a_module_for_one_project(db: Database) -> None:
    after = _set(db, CTX_A, "quality", False)
    assert after == {**DEFAULTS, "quality": False}
    assert _states(db, CTX_A)["quality"] is False
    assert _states(db, CTX_B) == DEFAULTS  # another project of the same tenant
    assert _states(db, CTX_C) == DEFAULTS  # another tenant
    assert _set(db, CTX_A, "quality", True) == DEFAULTS


def test_the_setting_row_is_project_scoped_and_revised(db: Database) -> None:
    _set(db, CTX_A, "quality", False)
    _set(db, CTX_A, "quality", True)
    with db.unit_of_work(CTX_A) as s:
        row = s.scalars(select(ProjectModuleSetting)).one()
        assert (row.tenant_id, row.project_id, row.module_name) == (T1, PA, "quality")
        assert (row.enabled, row.revision, row.created_by, row.updated_by) == (True, 2, USER, USER)
    with db.unit_of_work(CTX_B) as s:
        assert s.scalars(select(ProjectModuleSetting)).all() == []


def test_a_default_off_module_is_enabled_explicitly(db: Database) -> None:
    assert _set(db, CTX_A, "welding", True)["welding"] is True
    assert _set(db, CTX_A, "ndt", True)["ndt"] is True


def test_enabling_needs_the_dependencies_enabled(db: Database) -> None:
    with pytest.raises(ModuleToggleError, match="enable welding first"):
        _set(db, CTX_A, "ndt", True)


def test_disabling_is_refused_while_a_dependant_is_enabled(db: Database) -> None:
    with pytest.raises(ModuleToggleError, match="disable quality first"):
        _set(db, CTX_A, "assets", False)
    _set(db, CTX_A, "quality", False)
    assert _set(db, CTX_A, "assets", False) == {**DEFAULTS, "assets": False, "quality": False}
    # quality's own flag is back on, but it stays off while assets is off.
    with pytest.raises(ModuleToggleError, match="enable assets first"):
        _set(db, CTX_A, "quality", True)


def test_a_module_follows_its_dependency_off(db: Database) -> None:
    # welding off by default: ndt is off even if its own row says on.
    with db.unit_of_work(CTX_A) as s:
        s.add(ProjectModuleSetting(tenant_id=T1, project_id=PA, module_name="ndt", enabled=True))
    assert _states(db, CTX_A)["ndt"] is False


def test_a_module_that_cannot_be_disabled_is_refused(db: Database) -> None:
    with pytest.raises(ModuleToggleError, match="projects cannot be disabled"):
        _set(db, CTX_A, "projects", False)


def test_rows_for_modules_no_longer_shipped_are_ignored(db: Database) -> None:
    with db.unit_of_work(CTX_A) as s:
        s.add(ProjectModuleSetting(tenant_id=T1, project_id=PA, module_name="gone", enabled=False))
    assert _states(db, CTX_A) == DEFAULTS


def test_an_unknown_module_is_not_found(db: Database) -> None:
    with pytest.raises(UnknownModuleError, match="ghost"):
        _set(db, CTX_A, "ghost", False)


@pytest.mark.parametrize("ctx", [None, CTX_T1])
def test_the_service_needs_a_project_session(db: Database, ctx: TenancyContext | None) -> None:
    with db.sessionmaker() as s:
        bind_tenancy(s, ctx)
        with pytest.raises(RuntimeError, match="project"):
            project_states(s, REGISTRY)


def test_each_change_is_audited_and_a_no_op_is_not(db: Database) -> None:
    _set(db, CTX_A, "quality", False)
    _set(db, CTX_A, "quality", False)  # unchanged: no entry
    _set(db, CTX_A, "quality", True)
    with db.unit_of_work(CTX_A) as s:
        entries = chain_entries(s, AUDIT_CHAIN)
        payloads = [e.payload for e in entries]
        assert payloads == [
            {"module": "quality", "enabled": False},
            {"module": "quality", "enabled": True},
        ]
        assert {e.created_by for e in entries} == {USER}


def test_is_enabled(db: Database) -> None:
    _set(db, CTX_A, "quality", False)
    with db.unit_of_work(CTX_A) as s:
        assert service.is_enabled(s, REGISTRY, "quality") is False
        assert service.is_enabled(s, REGISTRY, "assets") is True


# --- HTTP: routes of a disabled module (the gate) --------------------------------------------


@pytest.fixture
def packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModulePackages:
    return ModulePackages(tmp_path / "pkgs", monkeypatch)


# The synthetic package: the real ``projects`` router (it serves the toggle API, T-0084) under
# a manifest without dependencies, plus test-only toggleable modules (never in app/modules/).
REAL_PROJECTS_ROUTER = "from app.modules.projects.router import router  # noqa: F401\n"


@pytest.fixture
def package(packages: ModulePackages) -> str:
    return packages.write(
        {
            "projects": FakeModule(toggleable=False, router=REAL_PROJECTS_ROUTER),
            "assets": FakeModule(depends_on=("projects",)),
            "quality": FakeModule(depends_on=("assets",)),
            "welding": FakeModule(default_enabled=False),
            "ndt": FakeModule(depends_on=("welding",)),
        }
    )


class Caller:
    """Switches the tenancy context and principal the app sees.

    A module route is not bound to a project through a real token (TD-122: only the
    project-scoped routers bind the path's project), so the gate tests override both seams:
    ``get_tenancy_context`` (the session binding) and ``get_current_user`` (the
    authenticated, active user). ``None`` is an anonymous caller.
    """

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.context: TenancyContext | None = None
        app.dependency_overrides[get_tenancy_context] = lambda: self.context
        app.dependency_overrides[get_current_user] = self.principal

    def principal(self) -> Principal:
        if self.context is None:
            raise unauthorized()
        now = datetime.now(UTC)
        claims = TokenClaims(
            type="access",
            user_id=USER,
            tenant_id=self.context.tenant_id,
            session_id=uuid.uuid4(),
            jti=uuid.uuid4().hex,
            issued_at=now,
            expires_at=now + timedelta(minutes=15),
        )
        user = User(id=USER, tenant_id=self.context.tenant_id, email="seam@example.test")
        return Principal(user=user, claims=claims)

    def as_(self, context: TenancyContext | None) -> "Caller":
        self.context = context
        return self


@pytest.fixture
def app(db: Database, package: str) -> FastAPI:
    url = str(db.engine.url.render_as_string(hide_password=False))
    return create_app(Settings(environment="test", database_url=url), modules_package=package)


@pytest.fixture
def http(app: FastAPI) -> Iterator[tuple[TestClient, Caller]]:
    with TestClient(app) as client:
        yield client, Caller(app)


QUALITY = f"{API_PREFIX}/quality/ping"


def test_a_disabled_module_answers_404_for_that_project_only(
    http: tuple[TestClient, Caller], db: Database
) -> None:
    client, caller = http
    caller.as_(CTX_A)
    assert client.get(QUALITY).json() == {"module": "quality"}
    _set(db, CTX_A, "quality", False)
    response = client.get(QUALITY)
    assert (response.status_code, response.json()) == (404, {"detail": "Not Found"})
    caller.as_(CTX_B)
    assert client.get(QUALITY).status_code == 200
    caller.as_(CTX_C)
    assert client.get(QUALITY).status_code == 200
    caller.as_(CTX_A)
    _set(db, CTX_A, "quality", True)
    assert client.get(QUALITY).status_code == 200


def test_requests_without_a_project_are_not_gated(
    http: tuple[TestClient, Caller], db: Database
) -> None:
    client, caller = http
    caller.as_(CTX_A)
    _set(db, CTX_A, "quality", False)
    assert client.get(QUALITY).status_code == 404
    caller.as_(CTX_T1)
    assert client.get(QUALITY).status_code == 200


def test_the_project_less_toggle_routes_are_gone(
    app: FastAPI, http: tuple[TestClient, Caller]
) -> None:
    client, caller = http
    caller.as_(CTX_A)
    paths = set(app.openapi()["paths"])
    assert not {p for p in paths if p.startswith(f"{API_PREFIX}/modules")}
    assert client.get(f"{API_PREFIX}/modules").status_code == 404
    assert client.put(f"{API_PREFIX}/modules/quality", json={"enabled": False}).status_code in {
        404,
        405,
    }


# --- HTTP: the toggle API on the project path (T-0084) with real tokens -----------------------

ROOT = TenancyContext(tenant_id=AUTH_T1, is_system_admin=True)
ROOT_T2 = TenancyContext(tenant_id=AUTH_T2, is_system_admin=True)
CAROL = "carol@example.test"
DAVE = "dave@example.test"

# Dependency order: each level of the graph in turn, ties by name.
STATES = [
    {"name": "projects", "version": "1.0.0", "depends_on": [], "toggleable": False},
    {"name": "welding", "version": "1.0.0", "depends_on": [], "toggleable": True},
    {"name": "assets", "version": "1.0.0", "depends_on": ["projects"], "toggleable": True},
    {"name": "ndt", "version": "1.0.0", "depends_on": ["welding"], "toggleable": True},
    {"name": "quality", "version": "1.0.0", "depends_on": ["assets"], "toggleable": True},
]
QUALITY_STATE = STATES[4]
API_DEFAULTS = {"projects": True, "assets": True, "quality": True, "welding": False, "ndt": False}


def _project(db: Database, code: str, ctx: TenancyContext = ROOT) -> uuid.UUID:
    with db.unit_of_work(ctx) as s:
        return projects.create_project(s, ProjectCreate(code=code, name=f"Synthetic {code}")).id


def _grant(db: Database, user: uuid.UUID, project: uuid.UUID, persona: Persona) -> None:
    with db.unit_of_work(ROOT) as s:
        s.add(
            ProjectMembership(
                tenant_id=AUTH_T1, user_id=user, member_project_id=project, persona=persona.value
            )
        )


class Api:
    """ALICE: System admin, no membership. BOB: PER-09 of HOME. CAROL: PER-02 of HOME (``V``,
    no ``admin``). DAVE: nothing. AWAY: nobody's; GONE: deleted; FOREIGN: tenant T2's."""

    def __init__(self, client: TestClient, db: Database) -> None:
        self.client, self.db = client, db
        self.users = {e: seed_user(db, AUTH_T1, e) for e in (ALICE, BOB, CAROL, DAVE)}
        with db.unit_of_work(ROOT) as s:
            assert admin.grant(s, self.users[ALICE], SYSTEM_ADMIN, actor_id=None)
        self.home, self.away, self.gone = (_project(db, c) for c in ("HOME", "AWAY", "GONE"))
        self.foreign = _project(db, "FOREIGN", ROOT_T2)
        _grant(db, self.users[BOB], self.home, Persona.PROJECT_ADMINISTRATOR)
        _grant(db, self.users[CAROL], self.home, Persona.PROJECT_MANAGER)
        with db.unit_of_work(ROOT) as s:
            projects.delete_project(s, self.gone)
        self._headers: dict[str, dict[str, str]] = {}

    def headers(self, email: str) -> dict[str, str]:
        if email not in self._headers:
            body = {"email": email, "password": PASSWORD}
            response = self.client.post(f"{API_PREFIX}/auth/login", json=body)
            assert response.status_code == 200, response.text
            self._headers[email] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._headers[email]

    def list(self, project: uuid.UUID, email: str) -> Any:
        return self.client.get(_modules(project), headers=self.headers(email))

    def put(self, project: uuid.UUID, name: str, body: object, email: str) -> Any:
        url = f"{_modules(project)}/{name}"
        return self.client.put(url, json=body, headers=self.headers(email))

    def states(self, project: uuid.UUID, email: str = ALICE) -> dict[str, bool]:
        listed = self.list(project, email)
        assert listed.status_code == 200, listed.text
        return {m["name"]: m["enabled"] for m in listed.json()}

    def bound(self, project: uuid.UUID) -> TenancyContext:
        return TenancyContext(tenant_id=AUTH_T1, project_id=project)


def _modules(project: uuid.UUID) -> str:
    return f"{API_PREFIX}/projects/{project}/modules"


@pytest.fixture
def api(tmp_path: Path, db: Database, package: str) -> Iterator[Api]:
    settings = make_settings(database_url=database_url(tmp_path))
    app, _runtime = auth_app(settings, modules_package=package)
    with TestClient(app) as client:
        yield Api(client, db)


def test_a_member_with_view_lists_the_modules_of_the_project(api: Api) -> None:
    listed = api.list(api.home, CAROL)
    assert listed.status_code == 200, listed.text
    # Every loaded module, dependencies first, with its state for that project.
    assert listed.json() == [{**s, "enabled": API_DEFAULTS[s["name"]]} for s in STATES]
    assert api.list(api.home, BOB).status_code == 200


def test_a_system_admin_lists_any_live_project_of_its_tenant(api: Api) -> None:
    assert api.states(api.away, ALICE) == API_DEFAULTS


def test_listing_is_404_for_a_non_member_and_hidden_projects(api: Api) -> None:
    assert api.list(api.home, DAVE).status_code == 404
    assert api.list(api.away, BOB).status_code == 404  # a PER-09 of another project
    for hidden in (api.gone, api.foreign, uuid.uuid4()):
        for email in (ALICE, BOB):
            assert api.list(hidden, email).status_code == 404


def test_a_project_administrator_toggles_a_module_for_its_project(api: Api) -> None:
    changed = api.put(api.home, "quality", {"enabled": False}, BOB)
    assert changed.status_code == 200, changed.text
    assert changed.json() == {**QUALITY_STATE, "enabled": False}
    assert api.states(api.home, CAROL) == {**API_DEFAULTS, "quality": False}
    assert api.states(api.away) == API_DEFAULTS  # another project of the tenant
    back = api.put(api.home, "quality", {"enabled": True}, BOB)
    assert (back.status_code, back.json()["enabled"]) == (200, True)


def test_a_change_is_audited_on_the_project_chain_with_the_caller(api: Api) -> None:
    api.put(api.home, "quality", {"enabled": False}, BOB)
    with api.db.unit_of_work(api.bound(api.home)) as s:
        row = s.scalars(select(ProjectModuleSetting)).one()
        assert (row.project_id, row.module_name, row.enabled) == (api.home, "quality", False)
        assert (row.created_by, row.updated_by) == (api.users[BOB], api.users[BOB])
        (entry,) = chain_entries(s, AUDIT_CHAIN)
        assert entry.payload == {"module": "quality", "enabled": False}
        assert entry.created_by == api.users[BOB]
    with api.db.unit_of_work(api.bound(api.away)) as s:
        assert s.scalars(select(ProjectModuleSetting)).all() == []
        assert chain_entries(s, AUDIT_CHAIN) == []


def test_a_system_admin_toggles_any_live_project_without_a_membership(api: Api) -> None:
    changed = api.put(api.away, "quality", {"enabled": False}, ALICE)
    assert changed.status_code == 200, changed.text
    assert api.states(api.away)["quality"] is False
    with api.db.unit_of_work(api.bound(api.away)) as s:
        (entry,) = chain_entries(s, AUDIT_CHAIN)
        assert entry.created_by == api.users[ALICE]


def test_toggling_needs_the_admin_capability(api: Api) -> None:
    refused = api.put(api.home, "quality", {"enabled": False}, CAROL)  # V/C/E/A, no admin
    assert refused.status_code == 403
    assert api.put(api.home, "quality", {"enabled": False}, DAVE).status_code == 404
    assert api.put(api.away, "quality", {"enabled": False}, BOB).status_code == 404
    for hidden in (api.gone, api.foreign, uuid.uuid4()):
        for email in (ALICE, BOB):
            assert api.put(hidden, "quality", {"enabled": False}, email).status_code == 404
    assert api.states(api.home) == API_DEFAULTS  # nothing changed
    with api.db.unit_of_work(api.bound(api.home)) as s:
        assert chain_entries(s, AUDIT_CHAIN) == []


def test_toggle_errors(api: Api) -> None:
    unknown = api.put(api.home, "ghost", {"enabled": False}, BOB)
    assert (unknown.status_code, unknown.json()) == (404, {"detail": "Not Found"})
    refused = api.put(api.home, "projects", {"enabled": False}, BOB)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "projects cannot be disabled"
    needed = api.put(api.home, "assets", {"enabled": False}, BOB)
    assert needed.status_code == 409
    assert "disable quality first" in needed.json()["detail"]
    missing = api.put(api.home, "ndt", {"enabled": True}, BOB)
    assert missing.status_code == 409
    assert "enable welding first" in missing.json()["detail"]
    assert api.put(api.home, "quality", {}, BOB).status_code == 422
    assert api.put(api.home, "quality", {"enabled": True, "x": 1}, BOB).status_code == 422
    assert api.states(api.home) == API_DEFAULTS


def test_enable_listeners_run_in_the_bound_project(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[uuid.UUID | None, frozenset[str]]] = []

    def listener(session: Session, names: frozenset[str]) -> None:
        context = session_tenancy(session)
        calls.append((context.project_id if context else None, names))

    monkeypatch.setattr(service, "_ENABLE_LISTENERS", [])
    service.add_enable_listener(listener)
    assert api.put(api.home, "welding", {"enabled": True}, BOB).status_code == 200
    # ndt's own setting is on by default: it follows welding on and is reported with it.
    assert calls == [(api.home, frozenset({"welding", "ndt"}))]
    assert api.put(api.home, "ndt", {"enabled": True}, BOB).status_code == 200  # no change
    assert api.put(api.home, "quality", {"enabled": False}, BOB).status_code == 200
    assert calls == [(api.home, frozenset({"welding", "ndt"}))]


# --- HTTP with real tokens (T-0008 auth): 401 before anything else --------------------------


def _calls(api: Api, headers: dict[str, str]) -> list[tuple[int, str | None]]:
    responses = [
        api.client.get(_modules(api.home), headers=headers),
        api.client.put(f"{_modules(api.home)}/quality", json={"enabled": False}, headers=headers),
    ]
    return [(r.status_code, r.headers.get("www-authenticate")) for r in responses]


def test_toggle_api_without_a_token_is_401(api: Api) -> None:
    assert _calls(api, {}) == [(401, "Bearer"), (401, "Bearer")]


def test_toggle_api_with_an_invalid_token_is_401(api: Api) -> None:
    challenge = 'Bearer error="invalid_token"'
    headers = {"Authorization": "Bearer not-a-token"}
    assert _calls(api, headers) == [(401, challenge), (401, challenge)]


def test_toggle_api_for_an_inactive_user_is_401(api: Api) -> None:
    headers = api.headers(BOB)
    with api.db.unit_of_work(TenancyContext(tenant_id=AUTH_T1)) as s:
        s.scalars(select(User).where(User.email == BOB)).one().is_active = False
    assert [status for status, _ in _calls(api, headers)] == [401, 401]
