"""Per-project module enable/disable (T-0011).

A project turns modules on and off; a module that is off for a project answers 404 on all
of its routes for that project, exactly as if the route did not exist. These run on SQLite
and, in CI, on PostgreSQL under project RLS against the migrated schema.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.audit.service import chain_entries
from app.core.auth.deps import Principal, get_current_user, unauthorized
from app.core.auth.models import User
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
from app.core.tenancy.rls import bind_tenancy, rls_tables
from app.main import create_app
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, PASSWORD, auth_app, make_settings, seed_user
from tests.auth_support import T1 as AUTH_T1
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


# --- HTTP: routes of a disabled module, toggle API ------------------------------------------


@pytest.fixture
def packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModulePackages:
    return ModulePackages(tmp_path / "pkgs", monkeypatch)


class Caller:
    """Switches the tenancy context and principal the app sees.

    A project-bound request is not possible through a real token until TD-122, so these
    tests override both seams: ``get_tenancy_context`` (the session binding) and
    ``get_current_user`` (the authenticated, active user). ``None`` is an anonymous caller.
    Real-token behaviour (401s, tenant-level 404) is tested further below.
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
def app(db: Database, packages: ModulePackages) -> FastAPI:
    package = packages.write(
        {
            "projects": FakeModule(toggleable=False),
            "assets": FakeModule(depends_on=("projects",)),
            "quality": FakeModule(depends_on=("assets",)),
        }
    )
    url = str(db.engine.url.render_as_string(hide_password=False))
    return create_app(Settings(environment="test", database_url=url), modules_package=package)


@pytest.fixture
def http(app: FastAPI) -> Iterator[tuple[TestClient, Caller]]:
    with TestClient(app) as client:
        yield client, Caller(app)


QUALITY = f"{API_PREFIX}/quality/ping"
MODULES = f"{API_PREFIX}/modules"


def test_a_disabled_module_answers_404_for_that_project_only(
    http: tuple[TestClient, Caller],
) -> None:
    client, caller = http
    caller.as_(CTX_A)
    assert client.get(QUALITY).json() == {"module": "quality"}
    assert client.put(f"{MODULES}/quality", json={"enabled": False}).status_code == 200
    response = client.get(QUALITY)
    assert (response.status_code, response.json()) == (404, {"detail": "Not Found"})
    caller.as_(CTX_B)
    assert client.get(QUALITY).status_code == 200
    caller.as_(CTX_C)
    assert client.get(QUALITY).status_code == 200
    caller.as_(CTX_A)
    assert client.put(f"{MODULES}/quality", json={"enabled": True}).status_code == 200
    assert client.get(QUALITY).status_code == 200


def test_requests_without_a_project_are_not_gated(http: tuple[TestClient, Caller]) -> None:
    client, caller = http
    caller.as_(CTX_A)
    client.put(f"{MODULES}/quality", json={"enabled": False})
    assert client.get(QUALITY).status_code == 404
    caller.as_(CTX_T1)
    assert client.get(QUALITY).status_code == 200


def test_list_modules_for_the_project(http: tuple[TestClient, Caller]) -> None:
    client, caller = http
    caller.as_(CTX_A)
    client.put(f"{MODULES}/quality", json={"enabled": False})
    body = client.get(MODULES).json()
    assert body == [
        {
            "name": "projects",
            "version": "1.0.0",
            "depends_on": [],
            "toggleable": False,
            "enabled": True,
        },
        {
            "name": "assets",
            "version": "1.0.0",
            "depends_on": ["projects"],
            "toggleable": True,
            "enabled": True,
        },
        {
            "name": "quality",
            "version": "1.0.0",
            "depends_on": ["assets"],
            "toggleable": True,
            "enabled": False,
        },
    ]


def test_toggle_returns_the_module_state(http: tuple[TestClient, Caller]) -> None:
    client, caller = http
    caller.as_(CTX_A)
    response = client.put(f"{MODULES}/quality", json={"enabled": False})
    assert response.json() == {
        "name": "quality",
        "version": "1.0.0",
        "depends_on": ["assets"],
        "toggleable": True,
        "enabled": False,
    }


def test_toggle_records_the_principal_as_actor(
    http: tuple[TestClient, Caller], db: Database
) -> None:
    client, caller = http
    caller.as_(CTX_A)
    client.put(f"{MODULES}/quality", json={"enabled": False})
    with db.unit_of_work(CTX_A) as s:
        row = s.scalars(select(ProjectModuleSetting)).one()
        assert (row.created_by, row.updated_by) == (USER, USER)
        (entry,) = chain_entries(s, AUDIT_CHAIN)
        assert entry.created_by == USER


def test_toggle_errors(http: tuple[TestClient, Caller]) -> None:
    client, caller = http
    caller.as_(CTX_A)
    assert client.put(f"{MODULES}/ghost", json={"enabled": False}).status_code == 404
    refused = client.put(f"{MODULES}/projects", json={"enabled": False})
    assert refused.status_code == 409
    assert "cannot be disabled" in refused.json()["detail"]
    assert client.put(f"{MODULES}/assets", json={"enabled": False}).status_code == 409
    assert client.put(f"{MODULES}/quality", json={}).status_code == 422


def test_the_toggle_api_needs_a_project(http: tuple[TestClient, Caller]) -> None:
    client, caller = http
    caller.as_(CTX_T1)
    assert client.get(MODULES).status_code == 404
    assert client.put(f"{MODULES}/quality", json={"enabled": False}).status_code == 404


# --- HTTP with real tokens (T-0008 auth): 401 before anything else --------------------------


@pytest.fixture
def auth_client(tmp_path: Path, db: Database) -> Iterator[TestClient]:
    seed_user(db, AUTH_T1, ALICE)
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        yield client


def _token(client: TestClient) -> dict[str, str]:
    response = client.post(
        f"{API_PREFIX}/auth/login",
        json={"email": ALICE, "password": PASSWORD, "tenant_id": str(AUTH_T1)},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _calls(client: TestClient, headers: dict[str, str]) -> list[tuple[int, str | None]]:
    responses = [
        client.get(MODULES, headers=headers),
        client.put(f"{MODULES}/quality", json={"enabled": False}, headers=headers),
    ]
    return [(r.status_code, r.headers.get("www-authenticate")) for r in responses]


def test_toggle_api_without_a_token_is_401(auth_client: TestClient) -> None:
    assert _calls(auth_client, {}) == [(401, "Bearer"), (401, "Bearer")]


def test_toggle_api_with_an_invalid_token_is_401(auth_client: TestClient) -> None:
    challenge = 'Bearer error="invalid_token"'
    headers = {"Authorization": "Bearer not-a-token"}
    assert _calls(auth_client, headers) == [(401, challenge), (401, challenge)]


def test_toggle_api_for_an_inactive_user_is_401(auth_client: TestClient, db: Database) -> None:
    headers = _token(auth_client)
    with db.unit_of_work(TenancyContext(tenant_id=AUTH_T1)) as s:
        s.scalars(select(User).where(User.email == ALICE)).one().is_active = False
    assert [status for status, _ in _calls(auth_client, headers)] == [401, 401]


def test_toggle_api_with_a_tenant_level_token_is_404(auth_client: TestClient) -> None:
    # Authenticated, but the token binds no project until TD-122.
    assert [status for status, _ in _calls(auth_client, _token(auth_client))] == [404, 404]
