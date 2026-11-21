"""Shared fixtures for the backend test suite."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from tests import pg_databases
from tests.skip_guard import pytest_sessionfinish  # noqa: F401  (KERP_FAIL_ON_SKIP hook)


@pytest.fixture(scope="session", autouse=True)
def _postgres_templates() -> Iterator[None]:
    """Drop this worker's PostgreSQL template databases when its session ends (T-0082)."""
    yield
    if pg_databases.PER_TEST:
        pg_databases.drop_templates()


@pytest.fixture(autouse=True)
def _postgres_test_database() -> Iterator[None]:
    """On PostgreSQL each test gets its own database, created on first use and dropped here.

    Autouse fixtures are set up first, so this teardown runs after every fixture that used
    the database has disposed of its engines (T-0082).
    """
    if not pg_databases.PER_TEST:
        yield
        return
    pg_databases.begin_test()
    try:
        yield
    finally:
        pg_databases.end_test()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Each test sees a fresh settings object built from its own environment."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="test", version="9.9.9-test")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
