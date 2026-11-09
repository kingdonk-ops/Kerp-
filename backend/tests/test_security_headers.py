"""Security headers on every API response (T-0021)."""

from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security_headers import DOCS_CONTENT_SECURITY_POLICY, HSTS_VALUE
from app.main import create_app

BASELINE = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}
API_CSP = "default-src 'none'; frame-ancestors 'none'"


def _get(settings: Settings, path: str = "/health") -> httpx.Response:
    with TestClient(create_app(settings)) as c:
        return c.get(path)


@pytest.mark.parametrize("path", ["/health", "/does-not-exist", "/openapi.json"])
def test_baseline_headers_on_every_response(client: TestClient, path: str) -> None:
    headers = client.get(path).headers
    for name, value in BASELINE.items():
        assert headers[name] == value
    assert headers["content-security-policy"] == API_CSP


def test_default_csp_setting_is_locked_down() -> None:
    assert Settings().content_security_policy == API_CSP


def test_headers_on_unhandled_500(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/_probe/boom")
    def boom() -> None:
        raise RuntimeError("synthetic")

    with TestClient(app) as c:
        response = c.get("/_probe/boom")
    assert response.status_code == 500
    for name, value in BASELINE.items():
        assert response.headers[name] == value
    assert response.headers["content-security-policy"] == API_CSP


def test_headers_on_method_not_allowed(client: TestClient) -> None:
    response = client.post("/health")
    assert response.status_code == 405
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/docs/oauth2-redirect"])
def test_docs_pages_get_docs_csp(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == DOCS_CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"


def test_docs_csp_still_denies_by_default_and_forbids_framing() -> None:
    assert DOCS_CONTENT_SECURITY_POLICY.startswith("default-src 'none';")
    assert "frame-ancestors 'none'" in DOCS_CONTENT_SECURITY_POLICY


def test_docs_csp_is_not_applied_to_lookalike_paths(client: TestClient) -> None:
    response = client.get("/docsx")
    assert response.headers["content-security-policy"] == API_CSP


def test_csp_is_configurable() -> None:
    settings = Settings(environment="test", content_security_policy="default-src 'self'")
    assert _get(settings).headers["content-security-policy"] == "default-src 'self'"


def test_csp_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERP_CONTENT_SECURITY_POLICY", "default-src 'self'")
    assert Settings().content_security_policy == "default-src 'self'"


# --- HSTS -------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["development", "test"])
def test_no_hsts_outside_production_by_default(environment: str) -> None:
    settings = Settings(environment=environment)  # type: ignore[arg-type]
    assert "strict-transport-security" not in _get(settings).headers


def test_hsts_in_production_by_default() -> None:
    assert HSTS_VALUE == "max-age=31536000; includeSubDomains"
    headers = _get(Settings(environment="production")).headers
    assert headers["strict-transport-security"] == HSTS_VALUE


def test_hsts_can_be_forced_off_in_production() -> None:
    headers = _get(Settings(environment="production", hsts_enabled=False)).headers
    assert "strict-transport-security" not in headers


def test_hsts_can_be_forced_on_outside_production() -> None:
    headers = _get(Settings(environment="development", hsts_enabled=True)).headers
    assert headers["strict-transport-security"] == HSTS_VALUE


@pytest.mark.parametrize(
    ("environment", "override", "expected"),
    [
        ("production", None, True),
        ("development", None, False),
        ("test", None, False),
        ("production", False, False),
        ("test", True, True),
    ],
)
def test_hsts_active_property(environment: str, override: bool | None, expected: bool) -> None:
    settings = Settings(environment=environment, hsts_enabled=override)  # type: ignore[arg-type]
    assert settings.hsts_active is expected


# --- route-set headers win --------------------------------------------------


@pytest.fixture
def override_client(settings: Settings) -> Iterator[TestClient]:
    app: FastAPI = create_app(settings)

    @app.get("/_probe/own-csp")
    def own_csp(response: Response) -> dict[str, str]:
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        return {}

    with TestClient(app) as c:
        yield c


def test_route_set_header_is_not_overwritten_or_duplicated(override_client: TestClient) -> None:
    response = override_client.get("/_probe/own-csp")
    assert response.headers.get_list("content-security-policy") == ["default-src 'self'"]
    assert response.headers["x-frame-options"] == "DENY"
