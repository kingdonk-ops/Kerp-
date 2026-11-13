"""App factory and /health endpoint (T-0001)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_create_app_returns_configured_fastapi_app(settings: Settings) -> None:
    app = create_app(settings)
    assert isinstance(app, FastAPI)
    assert app.title == settings.app_name
    assert app.version == settings.version
    assert app.state.settings is settings


def test_create_app_returns_independent_instances(settings: Settings) -> None:
    assert create_app(settings) is not create_app(settings)


def test_create_app_without_args_uses_env_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERP_APP_NAME", "KERP Env")
    monkeypatch.setenv("KERP_VERSION", "7.7.7")
    monkeypatch.setenv("KERP_DEBUG", "true")
    app = create_app()
    assert app.title == "KERP Env"
    assert app.version == "7.7.7"
    assert app.debug is True


def test_app_boots(client: TestClient) -> None:
    # Entering the TestClient context runs startup/lifespan; any failure raises here.
    assert client.get("/openapi.json").status_code == 200


def test_health_returns_status_and_version(client: TestClient, settings: Settings) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "ok", "version": settings.version}


def test_health_version_follows_settings() -> None:
    app = create_app(Settings(environment="test", version="2.0.0-synthetic"))
    with TestClient(app) as c:
        assert c.get("/health").json()["version"] == "2.0.0-synthetic"


def test_health_is_documented_in_openapi(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths["/health"]


def test_unknown_route_is_404(client: TestClient) -> None:
    assert client.get("/does-not-exist").status_code == 404
