"""Per-project module enable/disable (T-0011).

A project turns modules on and off; a module that is off for a project answers 404 on all
of its routes for that project, exactly as if the route did not exist. These run on SQLite
and, in CI, on PostgreSQL under project RLS against the migrated schema.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.audit.service import chain_entries
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
    """Switches the tenancy context the app sees (the seam auth fills in T-0008)."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.context: TenancyContext | None = None
        app.dependency_overrides[get_tenancy_context] = lambda: self.context

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


def test_toggle_errors(http: tuple[TestClient, Caller]) -> None:
    client, caller = http
    caller.as_(CTX_A)
    assert client.put(f"{MODULES}/ghost", json={"enabled": False}).status_code == 404
    refused = client.put(f"{MODULES}/projects", json={"enabled": False})
    assert refused.status_code == 409
    assert "cannot be disabled" in refused.json()["detail"]
    assert client.put(f"{MODULES}/assets", json={"enabled": False}).status_code == 409
    assert client.put(f"{MODULES}/quality", json={}).status_code == 422


@pytest.mark.parametrize("ctx", [None, CTX_T1])
def test_the_toggle_api_needs_a_project(
    http: tuple[TestClient, Caller], ctx: TenancyContext | None
) -> None:
    client, caller = http
    caller.as_(ctx)
    assert client.get(MODULES).status_code == 404
    assert client.put(f"{MODULES}/quality", json={"enabled": False}).status_code == 404
