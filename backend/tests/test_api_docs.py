"""API docs and schema are hidden in production, available in dev/test (T-0030, TD-022)."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.openapi import openapi_schema

DOCS_PATHS = ("/docs", "/redoc", "/docs/oauth2-redirect", "/openapi.json")


def _status(settings: Settings, path: str) -> int:
    with TestClient(create_app(settings)) as c:
        return c.get(path).status_code


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_docs_return_404_in_production(path: str) -> None:
    assert _status(Settings(environment="production"), path) == 404


@pytest.mark.parametrize("environment", ["development", "test"])
@pytest.mark.parametrize("path", DOCS_PATHS)
def test_docs_are_served_in_dev_and_test(environment: str, path: str) -> None:
    assert _status(Settings(environment=environment), path) == 200  # type: ignore[arg-type]


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_docs_can_be_enabled_in_production(path: str) -> None:
    assert _status(Settings(environment="production", api_docs_enabled=True), path) == 200


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_docs_can_be_disabled_outside_production(path: str) -> None:
    assert _status(Settings(environment="test", api_docs_enabled=False), path) == 404


def test_docs_setting_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERP_ENVIRONMENT", "production")
    monkeypatch.setenv("KERP_API_DOCS_ENABLED", "true")
    assert Settings().api_docs_active is True
    monkeypatch.setenv("KERP_ENVIRONMENT", "development")
    monkeypatch.setenv("KERP_API_DOCS_ENABLED", "false")
    assert Settings().api_docs_active is False


@pytest.mark.parametrize(
    ("environment", "override", "expected"),
    [
        ("production", None, False),
        ("development", None, True),
        ("test", None, True),
        ("production", True, True),
        ("test", False, False),
    ],
)
def test_api_docs_active_property(environment: str, override: bool | None, expected: bool) -> None:
    settings = Settings(environment=environment, api_docs_enabled=override)  # type: ignore[arg-type]
    assert settings.api_docs_active is expected


def test_health_still_served_in_production() -> None:
    assert _status(Settings(environment="production"), "/health") == 200


def test_schema_export_works_whatever_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """``npm run gen:api`` must not depend on the docs routes being mounted."""
    monkeypatch.setenv("KERP_ENVIRONMENT", "production")
    monkeypatch.setenv("KERP_API_DOCS_ENABLED", "false")
    assert "/health" in openapi_schema()["paths"]
