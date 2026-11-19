"""First-run provisioning: the hidden tenant, deployment settings, first System admin (T-0023).

Diff §4.1 (one hidden tenant per deployment, System admins, Project Admins), §4.4 (deployment
settings). Runs on SQLite and, in CI, on PostgreSQL 16 as the ``NOSUPERUSER NOBYPASSRLS`` app
role against the migrated schema (the census function of migration ``0029``). The directory
starts empty here: unlike ``migrated_database`` no fixture tenants are seeded. Synthetic data
only.
"""

import io
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.audit.service import chain_entries
from app.core.auth import service as users
from app.core.auth.access import CREATE_PROJECTS, SYSTEM_ADMIN, Persona
from app.core.config import Settings
from app.core.db import TENANTS_TABLE, Database
from app.core.module_loader.loader import discover_modules
from app.core.tenancy import provision, rls
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.models import Tenant
from app.modules.admin import service as admin
from app.modules.onboarding import first_run as cli
from app.modules.onboarding import service
from app.modules.onboarding.schemas import (
    DEMO_PROJECT_CODE,
    FirstRunRequest,
    FirstRunResult,
)
from app.modules.projects import service as projects
from app.modules.settings import service as deployment_settings
from app.modules.settings.models import SHIPPED_DEFAULTS
from tests.audit_support import ALEMBIC_INI
from tests.auth_support import PASSWORD, auth_app, make_settings
from tests.db_support import ON_POSTGRES, database_url

TENANT = uuid.UUID("00000000-0000-4000-8000-0000000023a1")
OTHER = uuid.UUID("00000000-0000-4000-8000-0000000023a2")
ADMIN = "first.admin@company.example"
SECOND = "second.admin@company.example"
ON_PG = pytest.mark.skipif(
    not ON_POSTGRES,
    reason="needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)",
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    """A database at head with an **empty** tenant directory."""
    url = database_url(tmp_path)
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=url, configure_logger=False)
    command.upgrade(cfg, "head")
    database = Database(url)
    try:
        yield database
    finally:
        database.dispose()
        command.downgrade(cfg, "base")


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"environment": "test", "default_tenant_id": None}
    values.update(overrides)
    return Settings(**values)


def _request(**fields: Any) -> FirstRunRequest:
    values: dict[str, Any] = {"admin_email": ADMIN}
    values.update(fields)
    return FirstRunRequest.model_validate(values)


def _run(db: Database, settings: Settings | None = None, **fields: Any) -> FirstRunResult:
    settings = settings or _settings(default_tenant_id=TENANT)
    return service.first_run(db, _request(**fields), settings=settings)


def _ctx(tenant_id: uuid.UUID = TENANT) -> TenancyContext:
    return TenancyContext(tenant_id=tenant_id, is_system_admin=True)


def _grants(db: Database, tenant_id: uuid.UUID = TENANT) -> list[tuple[str, str]]:
    with db.unit_of_work(_ctx(tenant_id)) as s:
        return [(user.email, kind) for user, kind in admin.list_grants(s)]


# --- module contract ------------------------------------------------------------------------


def test_onboarding_is_a_platform_module_without_endpoints() -> None:
    manifest = discover_modules().get("onboarding")
    assert manifest is not None
    assert manifest.toggleable is False
    assert set(manifest.depends_on) == {"admin", "projects", "settings"}
    from app.modules.onboarding.router import router

    assert router.routes == []


def test_the_request_has_no_password_field() -> None:
    assert not any("password" in name for name in FirstRunRequest.model_fields)
    with pytest.raises(ValueError, match="password"):
        FirstRunRequest.model_validate({"admin_email": ADMIN, "password": "x"})


def test_the_census_names_agree() -> None:
    assert rls.TENANTS_TABLE == TENANTS_TABLE
    tenants = Tenant.__table__
    assert rls.expected_policy_names(tenants) == (
        *rls.POLICY_NAMES,
        rls.TENANT_CENSUS_POLICY_NAME,
    )


# --- fresh deployment -----------------------------------------------------------------------


def test_fresh_deploy_yields_tenant_settings_and_a_system_admin(db: Database) -> None:
    result = _run(db)
    assert (result.tenant_id, result.tenant_created, result.settings_created) == (
        TENANT,
        True,
        True,
    )
    assert (result.admin_created, result.admin_granted) == (True, True)
    assert provision.tenant_directory_count(db) == 1
    with db.unit_of_work(_ctx()) as s:
        record = deployment_settings.stored_record(s)
        assert record is not None
        assert record.values() == SHIPPED_DEFAULTS
        user = users.get_user(s, result.admin_user_id)
        assert user.email == ADMIN
        assert user.password_hash is None  # no password: SSO or a one-time link sets it
        assert user.is_active
        assert projects.live_project_ids(s) == []  # no default project outside --demo
        trail = chain_entries(s, f"{service.CHAIN_PREFIX}{TENANT}")
        assert [e.payload["action"] for e in trail] == ["first_run"]
        assert trail[0].payload["admin_user_id"] == str(user.id)
        provisioned = chain_entries(s, f"{deployment_settings.CHAIN_PREFIX}{TENANT}")
        assert [e.payload["action"] for e in provisioned] == ["provision"]
    assert _grants(db) == [(ADMIN, SYSTEM_ADMIN)]


def test_without_a_configured_tenant_a_new_id_is_generated(db: Database) -> None:
    result = _run(db, _settings())
    assert result.tenant_created
    assert result.tenant_id not in (TENANT, OTHER)
    assert provision.tenant_exists(db, result.tenant_id)


def test_the_requested_tenant_id_is_used(db: Database) -> None:
    assert _run(db, _settings(), tenant_id=OTHER).tenant_id == OTHER


def test_the_tenant_gets_the_given_internal_name(db: Database) -> None:
    _run(db, tenant_display_name="Synthetic deployment")
    with db.unit_of_work(TenancyContext(tenant_id=TENANT)) as s:
        row = s.get(Tenant, TENANT)
        assert row is not None
        assert row.display_name == "Synthetic deployment"


def test_an_internal_domain_makes_the_admin_internal(db: Database) -> None:
    settings = _settings(default_tenant_id=TENANT, internal_email_domains="company.example")
    assert _run(db, settings).admin_is_internal is True
    with db.unit_of_work(_ctx()) as s:
        assert users.is_internal_user(s, _grants_user(s))


def _grants_user(s: Any) -> uuid.UUID:
    return next(user.id for user, _kind in admin.list_grants(s))


def test_an_external_admin_is_not_internal(db: Database) -> None:
    assert _run(db).admin_is_internal is False


# --- idempotence ----------------------------------------------------------------------------


def test_re_running_changes_nothing(db: Database) -> None:
    first = _run(db)
    again = _run(db)
    assert again.tenant_id == first.tenant_id
    assert again.admin_user_id == first.admin_user_id
    assert not again.changed
    assert provision.tenant_directory_count(db) == 1
    assert _grants(db) == [(ADMIN, SYSTEM_ADMIN)]
    with db.unit_of_work(_ctx()) as s:
        assert [u.email for u in users.list_users(s)] == [ADMIN]
        trail = chain_entries(s, f"{service.CHAIN_PREFIX}{TENANT}")
        assert len(trail) == 1  # a run that changes nothing is not audited


def test_a_tenant_made_by_migration_or_start_up_is_reused(db: Database) -> None:
    provision.ensure_tenant(db, TENANT)  # migration 0026 / the API start-up did this
    result = _run(db)
    assert (result.tenant_id, result.tenant_created, result.admin_created) == (
        TENANT,
        False,
        True,
    )


def test_a_partial_earlier_run_is_completed(db: Database) -> None:
    provision.ensure_tenant(db, TENANT)
    with db.unit_of_work(_ctx()) as s:
        deployment_settings.ensure_record(s, actor_id=None)
        users.create_user(s, tenant_id=TENANT, email=ADMIN, password=None)
    result = _run(db)
    assert (result.settings_created, result.admin_created, result.admin_granted) == (
        False,
        False,
        True,
    )
    assert _grants(db) == [(ADMIN, SYSTEM_ADMIN)]


def test_an_existing_settings_record_is_kept(db: Database) -> None:
    provision.ensure_tenant(db, TENANT)
    with db.unit_of_work(_ctx()) as s:
        from app.modules.settings.schemas import DeploymentSettingsUpdate

        update = DeploymentSettingsUpdate.model_validate({"offline_window_days": 3})
        deployment_settings.update_settings(s, update, actor_id=None)
    assert _run(db).settings_created is False
    with db.unit_of_work(_ctx()) as s:
        assert deployment_settings.get_settings(s).offline_window_days == 3


# --- refusals: one tenant, one first admin --------------------------------------------------


def test_a_second_tenant_is_refused(db: Database) -> None:
    _run(db)
    with pytest.raises(service.AnotherTenantError):
        _run(db, _settings(default_tenant_id=OTHER), admin_email=SECOND)
    assert provision.tenant_directory_count(db) == 1
    assert not provision.tenant_exists(db, OTHER)


def test_another_tenant_made_elsewhere_is_refused(db: Database) -> None:
    provision.ensure_tenant(db, OTHER)
    with pytest.raises(service.AnotherTenantError):
        _run(db)
    assert not provision.tenant_exists(db, TENANT)


def test_a_soft_deleted_tenant_still_counts(db: Database) -> None:
    provision.ensure_tenant(db, OTHER)
    with db.unit_of_work(TenancyContext(tenant_id=OTHER)) as s:
        row = s.get(Tenant, OTHER)
        assert row is not None
        row.soft_delete()
    with pytest.raises(service.AnotherTenantError):
        _run(db)


def test_an_existing_tenant_must_be_named(db: Database) -> None:
    _run(db)
    with pytest.raises(service.TenantNotConfiguredError, match="KERP_DEFAULT_TENANT_ID"):
        _run(db, _settings())


def test_a_tenant_id_that_contradicts_the_configuration_is_refused(db: Database) -> None:
    with pytest.raises(service.TenantMismatchError):
        _run(db, tenant_id=OTHER)
    assert provision.tenant_directory_count(db) == 0


def test_a_second_first_admin_is_refused(db: Database) -> None:
    _run(db)
    with pytest.raises(service.SystemAdminExistsError):
        _run(db, admin_email=SECOND)
    assert _grants(db) == [(ADMIN, SYSTEM_ADMIN)]
    with db.unit_of_work(_ctx()) as s:
        assert users.find_user_by_email(s, SECOND) is None  # rolled back


def test_a_deactivated_admin_does_not_block_a_new_one(db: Database) -> None:
    first = _run(db)
    with db.unit_of_work(_ctx()) as s:
        users.get_user(s, first.admin_user_id).is_active = False
    assert _run(db, admin_email=SECOND).admin_created
    with db.unit_of_work(_ctx()) as s:
        assert {u.email for u, kind in admin.list_grants(s) if kind == SYSTEM_ADMIN} == {
            ADMIN,
            SECOND,
        }


def test_a_deactivated_account_is_not_reused(db: Database) -> None:
    provision.ensure_tenant(db, TENANT)
    with db.unit_of_work(_ctx()) as s:
        user = users.create_user(s, tenant_id=TENANT, email=ADMIN, password=None)
        user.is_active = False
    with pytest.raises(service.InactiveAccountError):
        _run(db)
    assert _grants(db) == []


# --- --demo ---------------------------------------------------------------------------------


def test_demo_adds_create_projects_and_a_demo_project_with_the_admin_as_per09(
    db: Database,
) -> None:
    result = _run(db, demo=True)
    assert result.demo_project_created
    assert sorted(_grants(db)) == [(ADMIN, CREATE_PROJECTS), (ADMIN, SYSTEM_ADMIN)]
    with db.unit_of_work(_ctx()) as s:
        assert result.demo_project_id is not None
        project = projects.get_project(s, result.demo_project_id)
        assert project.code == DEMO_PROJECT_CODE
        assert project.currency_code == SHIPPED_DEFAULTS.default_currency_code
        members = admin.project_members(s, project.id)
        assert [m.user.email for m in members] == [ADMIN]
        assert {g.grant.persona for g in members[0].grants} == {Persona.PROJECT_ADMINISTRATOR}
    again = _run(db, demo=True)
    assert (again.demo_project_id, again.demo_project_created) == (result.demo_project_id, False)
    with db.unit_of_work(_ctx()) as s:
        assert len(projects.live_project_ids(s)) == 1


def test_demo_is_refused_in_production(db: Database) -> None:
    production = _settings(environment="production", default_tenant_id=TENANT)
    with pytest.raises(service.DemoNotAllowedError):
        service.first_run(db, _request(demo=True), settings=production)
    assert provision.tenant_directory_count(db) == 0


# --- sign-in afterwards (T-0037) -------------------------------------------------------------


def test_login_needs_no_tenant_after_first_run(db: Database, tmp_path: Path) -> None:
    result = _run(db, _settings())
    with db.unit_of_work(_ctx(result.tenant_id)) as s:
        # Stand-in for the person's own first credential (SSO or one-time link).
        users.set_password(users.get_user(s, result.admin_user_id), PASSWORD)
    settings = make_settings(
        database_url=database_url(tmp_path), default_tenant_id=result.tenant_id
    )
    app, _runtime = auth_app(settings)
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/login", json={"email": ADMIN, "password": PASSWORD})
        assert response.status_code == 200, response.text
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        patch = {"offline_window_days": 5}
        changed = client.patch("/api/v1/settings/deployment", json=patch, headers=headers)
        assert changed.status_code == 200, changed.text  # the System admin is effective


# --- PostgreSQL: the census -----------------------------------------------------------------


@ON_PG
def test_the_census_counts_every_tenant_but_shows_none(db: Database) -> None:
    for tenant in (TENANT, OTHER):
        provision.ensure_tenant(db, tenant)
    assert provision.tenant_directory_count(db) == 2
    with db.unit_of_work(TenancyContext(tenant_id=TENANT)) as s:
        s.execute(text("SELECT set_config('app.tenant_census', 'false', true)"))
        assert s.execute(text(f"SELECT {rls.TENANT_CENSUS_FUNCTION}()")).scalar_one() == 2
        # the caller's value is restored and the rows stay hidden
        census = text("SELECT current_setting('app.tenant_census', true)")
        assert s.execute(census).scalar_one() == "false"
        visible = s.execute(text(f"SELECT id FROM {TENANTS_TABLE}")).scalars().all()
        assert [uuid.UUID(str(v)) for v in visible] == [TENANT]
    with contextmanager(db.session)(None) as s:  # unbound: nothing visible, the count still works
        assert s.execute(text(f"SELECT id FROM {TENANTS_TABLE}")).all() == []
        assert s.scalars(select(Tenant.id)).all() == []


@ON_PG
def test_the_census_function_is_not_public(db: Database) -> None:
    with db.engine.connect() as conn:
        granted = conn.execute(
            text("SELECT has_function_privilege('public', :fn, 'EXECUTE')"),
            {"fn": f"{rls.TENANT_CENSUS_FUNCTION}()"},
        ).scalar_one()
    assert granted is False


# --- command line ---------------------------------------------------------------------------


def _main(*argv: str, settings: Settings) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(list(argv), settings=settings, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_the_command_prints_the_tenant_id(db: Database, tmp_path: Path) -> None:
    settings = _settings(database_url=database_url(tmp_path))
    code, out, err = _main("--admin-email", ADMIN, "--admin-name", "First Admin", settings=settings)
    assert (code, err) == (0, "")
    line = next(x for x in out.splitlines() if x.startswith("KERP_DEFAULT_TENANT_ID="))
    tenant_id = uuid.UUID(line.split("=", 1)[1])
    assert provision.tenant_exists(db, tenant_id)
    assert "tenant: created" in out
    rerun = _settings(database_url=database_url(tmp_path), default_tenant_id=tenant_id)
    code, out, _err = _main("--admin-email", ADMIN, settings=rerun)
    assert code == 0
    assert "tenant: present" in out
    assert "system_admin grant: present" in out


def test_the_command_reads_the_email_from_the_environment(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli.EMAIL_ENV, ADMIN)
    settings = _settings(database_url=database_url(tmp_path), default_tenant_id=TENANT)
    code, out, _err = _main(settings=settings)
    assert code == 0
    assert f"KERP_DEFAULT_TENANT_ID={TENANT}" in out
    assert _grants(db) == [(ADMIN, SYSTEM_ADMIN)]


def test_the_command_demo_option(db: Database, tmp_path: Path) -> None:
    settings = _settings(database_url=database_url(tmp_path), default_tenant_id=TENANT)
    code, out, _err = _main("--admin-email", ADMIN, "--demo", settings=settings)
    assert code == 0
    assert f"demo project {DEMO_PROJECT_CODE}: created" in out


def test_the_command_refuses_a_second_tenant(db: Database, tmp_path: Path) -> None:
    provision.ensure_tenant(db, OTHER)
    settings = _settings(database_url=database_url(tmp_path), default_tenant_id=TENANT)
    code, out, err = _main("--admin-email", ADMIN, settings=settings)
    assert (code, out) == (cli.EXIT_REFUSED, "")
    assert err.startswith("refused: ")


@pytest.mark.parametrize(
    ("argv", "overrides", "message"),
    [
        ((), {}, "--admin-email"),
        (("--admin-email", "not-an-email"), {}, "invalid input: admin_email"),
        (("--admin-email", ADMIN), {"database_url": None}, "KERP_DATABASE_URL"),
        (("--admin-email", ADMIN, "--demo"), {"environment": "production"}, "development"),
    ],
)
def test_the_command_usage_errors(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
    overrides: dict[str, Any],
    message: str,
) -> None:
    monkeypatch.delenv(cli.EMAIL_ENV, raising=False)
    values: dict[str, Any] = {"database_url": database_url(tmp_path), "default_tenant_id": TENANT}
    values.update(overrides)
    code, out, err = _main(*argv, settings=_settings(**values))
    assert (code, out) == (cli.EXIT_USAGE, "")
    assert message in err
    assert provision.tenant_directory_count(db) == 0


def test_the_command_has_no_password_option() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--admin-email", ADMIN, "--password", "x"], err=io.StringIO())
    assert exc.value.code == 2
