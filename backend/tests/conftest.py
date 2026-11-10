"""Shared fixtures for the backend test suite."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from tests.skip_guard import pytest_sessionfinish  # noqa: F401  (KERP_FAIL_ON_SKIP hook)


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
