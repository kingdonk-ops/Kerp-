"""Production refuses to start on an unsafe database role or a missing runtime (T-0342).

The role checks themselves need PostgreSQL (``test_startup_safety_pg.py``); here: the rule
(production raises, elsewhere warns, SQLite and no database skip with a logged reason), the
Redis and signing-key refusals of the API ``lifespan``, and that the worker runs the same
function before it touches Redis. All settings are synthetic.
"""

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.core import startup_safety
from app.core.auth.keys import AuthConfigError
from app.core.auth.runtime import STATE_ATTR
from app.core.config import Settings
from app.core.db import Database
from app.core.startup_safety import (
    StartupRefusedError,
    check_database_role,
    database_role_problems,
    require_redis,
)
from app.main import create_app
from app.modules.jobs import worker as worker_module
from app.modules.jobs.registry import JobRegistry
from tests.auth_support import PRODUCTION_MAIL, PRODUCTION_RUNTIME, T1, fake_production_redis

PROBLEMS = [
    "database role 'kerp' is a SUPERUSER (bypasses RLS)",
    "audit_ledger: current role has UPDATE",
]


@pytest.fixture(autouse=True)
def _production_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_production_redis(monkeypatch)  # production apps start with Redis (T-0342)


def _production(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "production",
        "default_tenant_id": T1,
        **PRODUCTION_MAIL,
        **PRODUCTION_RUNTIME,
    }
    values.update(overrides)
    return Settings(**values)


def _postgres_stub() -> Database:
    """Stands in for a PostgreSQL ``Database``; only the dialect name is read before the
    (patched) problem query."""
    return cast(
        Database,
        SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))),
    )


@pytest.fixture
def unsafe_role(monkeypatch: pytest.MonkeyPatch) -> list[Database]:
    """``database_role_problems`` reports ``PROBLEMS``; the databases it was asked about."""
    seen: list[Database] = []

    def problems(database: Database) -> list[str]:
        seen.append(database)
        return list(PROBLEMS)

    monkeypatch.setattr(startup_safety, "database_role_problems", problems)
    return seen


# --- check_database_role -------------------------------------------------------------------


def test_production_refuses_an_unsafe_role_naming_every_problem(
    unsafe_role: list[Database], caplog: pytest.LogCaptureFixture
) -> None:
    database = _postgres_stub()
    with (
        caplog.at_level(logging.ERROR, logger="kerp.startup"),
        pytest.raises(StartupRefusedError) as refused,
    ):
        check_database_role(database, _production())
    assert unsafe_role == [database]
    for problem in PROBLEMS:
        assert problem in str(refused.value)
    assert "deploy/first-run.md" in str(refused.value)  # where to read the refusal
    logged = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert logged == [f"unsafe database role: {p}" for p in PROBLEMS]


@pytest.mark.parametrize("environment", ["development", "test"])
def test_outside_production_an_unsafe_role_is_a_warning(
    environment: str, unsafe_role: list[Database], caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(environment=environment)  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="kerp.startup"):
        assert check_database_role(_postgres_stub(), settings) == PROBLEMS
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warned == [f"unsafe database role (refused in production): {p}" for p in PROBLEMS]


def test_a_safe_role_passes_in_production(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(startup_safety, "database_role_problems", lambda _db: [])
    with caplog.at_level(logging.INFO, logger="kerp.startup"):
        assert check_database_role(_postgres_stub(), _production()) == []
    assert "database role checks passed" in caplog.text


def test_sqlite_skips_the_role_checks_with_a_reason(
    tmp_path: Path, unsafe_role: list[Database], caplog: pytest.LogCaptureFixture
) -> None:
    # A SQLite file on either CI backend: the dialect decides before any query.
    database = Database("sqlite:///" + (tmp_path / "skip.db").as_posix())
    try:
        with caplog.at_level(logging.INFO, logger="kerp.startup"):
            assert check_database_role(database, _production()) == []
        assert unsafe_role == []  # the (patched) problem query was never asked
        assert "database role checks skipped: sqlite has no row-level security" in caplog.text
        # The unpatched checks (imported above) skip SQLite themselves too.
        assert database_role_problems(database) == []
    finally:
        database.dispose()


def test_no_database_skips_the_role_checks_with_a_reason(
    unsafe_role: list[Database], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="kerp.startup"):
        assert check_database_role(None, _production()) == []
    assert unsafe_role == []
    assert "KERP_DATABASE_URL is not set" in caplog.text


# --- require_redis -------------------------------------------------------------------------


def test_production_requires_redis() -> None:
    with pytest.raises(StartupRefusedError, match="KERP_REDIS_URL"):
        require_redis(_production(redis_url=None))
    require_redis(_production())


@pytest.mark.parametrize("environment", ["development", "test"])
def test_redis_is_optional_outside_production(environment: str) -> None:
    require_redis(Settings(environment=environment, redis_url=None))  # type: ignore[arg-type]


# --- the API lifespan ----------------------------------------------------------------------


def test_production_api_refuses_to_start_without_redis() -> None:
    app = create_app(_production(redis_url=None))
    with pytest.raises(StartupRefusedError, match="KERP_REDIS_URL"), TestClient(app):
        pass  # pragma: no cover - startup raises first


def test_production_api_refuses_to_start_without_a_signing_key() -> None:
    app = create_app(_production(jwt_private_key=None))
    with pytest.raises(AuthConfigError, match="KERP_JWT_PRIVATE_KEY"), TestClient(app):
        pass  # pragma: no cover - startup raises first


def test_production_api_refuses_to_start_with_an_invalid_signing_key() -> None:
    app = create_app(_production(jwt_private_key="not a PEM key"))
    with pytest.raises(AuthConfigError), TestClient(app):
        pass  # pragma: no cover - startup raises first


def test_production_api_refuses_to_start_with_an_unsafe_role(
    tmp_path: Path, unsafe_role: list[Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Database | None, Settings]] = []

    def check(database: Database | None, settings: Settings) -> list[str]:
        calls.append((database, settings))
        return check_database_role(_postgres_stub(), settings)

    monkeypatch.setattr("app.main.check_database_role", check)
    settings = _production(database_url="sqlite:///" + (tmp_path / "unused.db").as_posix())
    app = create_app(settings)
    with pytest.raises(StartupRefusedError, match="SUPERUSER"), TestClient(app):
        pass  # pragma: no cover - startup raises first
    assert calls == [(app.state.db, settings)]


def test_production_api_with_redis_and_a_key_starts_and_builds_the_runtime() -> None:
    app = create_app(_production())
    with TestClient(app) as client:
        assert getattr(app.state, STATE_ATTR, None) is not None  # built at startup, not lazily
        assert client.get("/health").status_code == 200  # still a liveness check


def test_outside_production_an_unsafe_role_still_starts(
    tmp_path: Path, unsafe_role: list[Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.main.check_database_role",
        lambda _database, settings: check_database_role(_postgres_stub(), settings),
    )
    app = create_app(Settings(environment="test", redis_url=None, database_url=None))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert getattr(app.state, STATE_ATTR, None) is None  # the runtime stays lazy outside prod


# --- the worker ----------------------------------------------------------------------------


def test_worker_runs_the_same_check_and_stops_before_redis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Database | None, Settings]] = []

    def refuse(database: Database | None, settings: Settings) -> list[str]:
        seen.append((database, settings))
        raise StartupRefusedError("refusing to start: unsafe role")

    def no_broker(_url: str) -> None:  # pragma: no cover - must not be reached
        raise AssertionError("the worker connected to Redis before the role check")

    monkeypatch.setattr(worker_module, "check_database_role", refuse)
    monkeypatch.setattr(worker_module, "SaqBroker", no_broker)
    monkeypatch.setattr(worker_module, "load_registry", JobRegistry)
    url = "sqlite:///" + (tmp_path / "worker.db").as_posix()
    settings = Settings(environment="test", redis_url="redis://127.0.0.1:1/0", database_url=url)
    with pytest.raises(StartupRefusedError, match="unsafe role"):
        asyncio.run(worker_module.serve(settings))
    assert [(type(db).__name__, s) for db, s in seen] == [("Database", settings)]
    assert worker_module.check_database_role is not startup_safety.check_database_role
    # The worker imports the shared function, not a copy of it.
    monkeypatch.undo()
    assert worker_module.check_database_role is startup_safety.check_database_role


# --- python -m app.core.startup_safety -----------------------------------------------------


@pytest.mark.parametrize("url", [None, "sqlite:///unused.db"])
def test_the_preflight_needs_a_postgresql_url(
    url: str | None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if url is None:
        monkeypatch.delenv("KERP_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("KERP_DATABASE_URL", url)
    assert startup_safety.main([]) == 2
    assert "KERP_DATABASE_URL must name a PostgreSQL database" in capsys.readouterr().err
