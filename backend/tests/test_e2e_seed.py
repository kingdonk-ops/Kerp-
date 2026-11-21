"""The dev-only e2e seed script ``e2e/seed/seed.py`` (T-0027).

The Playwright ``globalSetup`` runs it after ``first_run --demo``. Contract checked here: it
refuses outside ``KERP_ENVIRONMENT=development`` (exit 2, nothing written), sets the synthetic
passwords from the environment, creates this run's QA user (member of nothing) and the
tenant-layer ``check`` set ``e2e_smoke`` (owned by no module, so a new project copies it,
ADR 0004 §2), all through the modules' services, and is idempotent per run. Runs on SQLite
and, in CI, on PostgreSQL as the runtime role. Synthetic data only.
"""

import importlib.util
import io
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic import command
from alembic.config import Config

from app.core.auth import service as users
from app.core.auth.passwords import verify_password
from app.core.config import Settings
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.modules.admin import service as admin
from app.modules.onboarding import service as onboarding
from app.modules.onboarding.schemas import FirstRunRequest
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.validation import service as rule_store
from tests.audit_support import ALEMBIC_INI, migrated_sqlite_file
from tests.db_support import ON_POSTGRES, database_url

SEED = Path(__file__).resolve().parents[2] / "e2e" / "seed" / "seed.py"
TENANT = uuid.UUID("00000000-0000-4000-8000-0000000027e1")
ADMIN = "e2e-admin@example.test"
ADMIN_PASSWORD = "dev-only-admin-Quartz-41"  # synthetic
QA_PASSWORD = "dev-only-qa-Lantern-73"  # synthetic
ENV = {"KERP_E2E_ADMIN_PASSWORD": ADMIN_PASSWORD, "KERP_E2E_QA_PASSWORD": QA_PASSWORD}


def _load_seed() -> ModuleType:
    spec = importlib.util.spec_from_file_location("kerp_e2e_seed", SEED)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed = _load_seed()


@pytest.fixture
def url(tmp_path: Path) -> Iterator[str]:
    """A database at head with an empty tenant directory (as test_onboarding)."""
    if not ON_POSTGRES:
        yield migrated_sqlite_file(tmp_path, seeded=False)
        return
    db_url = database_url(tmp_path)
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=db_url, configure_logger=False)
    command.upgrade(cfg, "head")
    try:
        yield db_url
    finally:
        command.downgrade(cfg, "base")


@pytest.fixture
def db(url: str) -> Iterator[Database]:
    database = Database(url)
    try:
        yield database
    finally:
        database.dispose()


def _settings(url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "development",
        "database_url": url,
        "default_tenant_id": TENANT,
        "redis_url": None,
    }
    values.update(overrides)
    return Settings(**values)


def _first_run(db: Database, url: str) -> uuid.UUID:
    request = FirstRunRequest.model_validate({"admin_email": ADMIN, "demo": True})
    result = onboarding.first_run(db, request, settings=_settings(url))
    assert result.demo_project_id is not None
    return result.demo_project_id


def _seed(url: str, run: str = "r1", **kwargs: Any) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    settings = kwargs.pop("settings", _settings(url))
    environ = kwargs.pop("environ", ENV)
    argv = ["--run", run, "--admin-email", ADMIN, *kwargs.pop("argv", [])]
    code = seed.main(argv, settings=settings, environ=environ, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _ctx(project_id: uuid.UUID | None = None) -> TenancyContext:
    return TenancyContext(tenant_id=TENANT, project_id=project_id, is_system_admin=True)


def _tenant_sets(db: Database) -> list[tuple[str, str, str | None]]:
    with db.unit_of_work(_ctx()) as s:
        return [(r.key, r.kind, r.module) for r in rule_store.list_rule_sets(s, "tenant")]


# --- refusals -------------------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["production", "test", "staging"])
def test_the_seed_refuses_outside_development(db: Database, url: str, environment: str) -> None:
    _first_run(db, url)
    code, out, err = _seed(url, environ={**ENV, "KERP_ENVIRONMENT": environment}, settings=None)
    assert code == 2
    assert out == ""
    assert "development" in err
    with db.unit_of_work(_ctx()) as s:
        assert users.find_user_by_email(s, "e2e-qa-r1@example.test") is None
    assert [key for key, _, _ in _tenant_sets(db)] == []


def test_the_seed_refuses_settings_of_another_environment(url: str) -> None:
    code, _, err = _seed(url, settings=_settings(url, environment="test"))
    assert code == 2
    assert "development" in err


@pytest.mark.parametrize("missing", sorted(ENV))
def test_the_seed_needs_both_passwords_from_the_environment(
    db: Database, url: str, missing: str
) -> None:
    _first_run(db, url)
    environ = {k: v for k, v in ENV.items() if k != missing}
    code, _, err = _seed(url, environ=environ)
    assert code == 2
    assert missing in err


@pytest.mark.parametrize("run", ["", "UPPER", "a b", "x" * 25, "r-1"])
def test_the_run_id_is_short_lowercase_alphanumeric(url: str, run: str) -> None:
    code, _, err = _seed(url, run=run)
    assert code == 2
    assert "--run" in err


def test_the_seed_needs_the_first_run_admin(url: str, db: Database) -> None:
    code, _, err = _seed(url)
    assert code == 1
    assert "first_run" in err


def test_a_password_the_policy_refuses_is_a_usage_error(db: Database, url: str) -> None:
    _first_run(db, url)
    code, _, err = _seed(url, environ={**ENV, "KERP_E2E_QA_PASSWORD": "short"})
    assert code == 2
    assert "KERP_E2E_QA_PASSWORD" in err


# --- what it creates ------------------------------------------------------------------------


def test_the_seed_creates_the_run_data_through_the_services(db: Database, url: str) -> None:
    demo = _first_run(db, url)
    code, out, err = _seed(url, run="r1")
    assert code == 0, err
    state = json.loads(out)
    assert state["tenant_id"] == str(TENANT)
    assert state["qa_email"] == "e2e-qa-r1@example.test"
    assert state["demo_project_id"] == str(demo)
    assert state["rule_set_key"] == "e2e_smoke"
    with db.unit_of_work(_ctx()) as s:
        admin_user = users.find_user_by_email(s, ADMIN)
        qa = users.find_user_by_email(s, "e2e-qa-r1@example.test")
        assert admin_user is not None
        assert qa is not None
        assert state["admin_user_id"] == str(admin_user.id)
        assert state["qa_user_id"] == str(qa.id)
        assert verify_password(ADMIN_PASSWORD, admin_user.password_hash)
        assert verify_password(QA_PASSWORD, qa.password_hash)
        assert state["qa_name"] == qa.display_name
        assert qa.display_name.endswith("r1")
        assert qa.is_active
        # This run's QA user is a member of nothing.
        assert all(
            m.user.id != qa.id
            for pid in projects.live_project_ids(s)
            for m in admin.project_members(s, pid)
        )
    assert _tenant_sets(db) == [("e2e_smoke", "check", None)]


def test_a_new_project_copies_the_seeded_set(db: Database, url: str) -> None:
    demo = _first_run(db, url)
    assert _seed(url)[0] == 0
    with db.unit_of_work(_ctx()) as s:
        admin_user = users.find_user_by_email(s, ADMIN)
        assert admin_user is not None
        project = projects.create_project(
            s,
            ProjectCreate(code="E2E-r1", name="Synthetic e2e project"),
            actor_id=admin_user.id,
            creator_id=admin_user.id,
        )
        project_id = project.id
    with db.unit_of_work(_ctx(project_id)) as s:
        copied = [(r.key, r.layer) for r in rule_store.list_rule_sets(s, "project")]
    assert ("e2e_smoke", "project") in copied
    # The demo project was created before the seed: it never gets the set.
    with db.unit_of_work(_ctx(demo)) as s:
        assert "e2e_smoke" not in {r.key for r in rule_store.list_rule_sets(s, "project")}


def test_re_seeding_a_run_changes_nothing_but_the_passwords(db: Database, url: str) -> None:
    _first_run(db, url)
    first = json.loads(_seed(url, run="r1")[1])
    code, out, _ = _seed(url, run="r1")
    assert code == 0
    assert json.loads(out) == first
    assert len(_tenant_sets(db)) == 1


def test_each_run_gets_its_own_qa_user(db: Database, url: str) -> None:
    _first_run(db, url)
    one = json.loads(_seed(url, run="r1")[1])
    two = json.loads(_seed(url, run="r2")[1])
    assert one["qa_user_id"] != two["qa_user_id"]
    assert two["qa_email"] == "e2e-qa-r2@example.test"
    assert one["admin_user_id"] == two["admin_user_id"]
    assert len(_tenant_sets(db)) == 1
