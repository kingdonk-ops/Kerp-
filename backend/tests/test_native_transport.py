"""Native-app refresh-token body transport and its CORS (T-0043, diff §4.2, TD-203).

Only a request whose ``Origin`` is on ``KERP_NATIVE_APP_ORIGINS`` gets (and may send) the
refresh token in a JSON body; everyone else, the web origin and a request without ``Origin``
included, gets the cookie of T-0037. The device platform never decides it. All synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.auth.device_signal import DEVICE_HEADER
from app.core.auth.router import REFRESH_COOKIE
from app.core.config import Settings, get_settings, normalize_origin
from app.core.db import Database
from app.main import create_app
from tests.audit_support import migrated_database
from tests.auth_support import (
    ALICE,
    PASSWORD,
    T1,
    CapturingSender,
    auth_app,
    cookie_headers,
    make_settings,
    seed_field_worker,
    seed_user,
)
from tests.db_support import database_url

WEB = "https://kerp.example.test"
NATIVE = "https://localhost"  # the Capacitor Android app's origin (androidScheme https)
CAPACITOR = "capacitor://localhost"
FOREIGN = "https://evil.example"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
UNLOCK = "/api/v1/auth/pin/unlock"
PIN = "482913"  # synthetic


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def sender() -> CapturingSender:
    return CapturingSender()


@pytest.fixture
def client(tmp_path: Path, db: Database, sender: CapturingSender) -> Iterator[TestClient]:
    seed_field_worker(db, T1, ALICE)  # PER-14: may sign in by magic link (T-0057)
    settings = make_settings(
        database_url=database_url(tmp_path),
        web_origin=WEB,
        native_app_origins=f"{NATIVE}, {CAPACITOR}",
    )
    app, _runtime = auth_app(settings, magic_links=sender)
    with TestClient(app) as test_client:
        yield test_client


def _login(client: TestClient, origin: str | None, **headers: str) -> httpx.Response:
    sent = dict(headers)
    if origin is not None:
        sent["Origin"] = origin
    response = client.post(LOGIN, json={"email": ALICE, "password": PASSWORD}, headers=sent)
    assert response.status_code == 200, response.text
    return response


def _assert_cookie_transport(response: httpx.Response) -> str:
    assert response.status_code == 200, response.text
    assert "refresh_token" not in response.json()
    return response.cookies[REFRESH_COOKIE]


def _assert_body_transport(response: httpx.Response) -> str:
    assert response.status_code == 200, response.text
    assert REFRESH_COOKIE not in response.cookies
    assert "set-cookie" not in response.headers
    token: str = response.json()["refresh_token"]
    return token


def _native_refresh(client: TestClient, token: str, origin: str = NATIVE) -> httpx.Response:
    return client.post(REFRESH, json={"refresh_token": token}, headers={"Origin": origin})


def _enrol(client: TestClient, login: httpx.Response, platform: str) -> None:
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    body = {"device_id": str(uuid.uuid4()), "platform": platform}
    assert client.post("/api/v1/devices/enrol", json=body, headers=headers).status_code == 201


# --- the transport ---------------------------------------------------------------------------


def test_the_web_origin_never_gets_the_token_in_a_body_even_on_an_android_device(
    client: TestClient,
) -> None:
    login = _login(client, WEB)
    token = _assert_cookie_transport(login)
    _enrol(client, login, "android")  # the session's device claims to be Android
    rotated = client.post(REFRESH, headers=cookie_headers(token, WEB))
    token = _assert_cookie_transport(rotated)
    # A body token from the web origin is no transport: the cookie is still needed.
    body_only = client.post(REFRESH, json={"refresh_token": token}, headers={"Origin": WEB})
    assert body_only.status_code == 401


def test_an_allow_listed_native_origin_gets_and_sends_the_token_in_the_body(
    client: TestClient,
) -> None:
    for origin in (NATIVE, CAPACITOR):
        token = _assert_body_transport(_login(client, origin))
        rotated = _native_refresh(client, token, origin)
        new_token = _assert_body_transport(rotated)
        assert new_token != token
        assert _native_refresh(client, token, origin).status_code == 401  # spent: reuse


def test_the_native_body_transport_ignores_the_platform_and_flags(client: TestClient) -> None:
    login = _login(client, NATIVE)
    _assert_body_transport(login)
    _enrol(client, login, "web")  # a native app reporting "web" still gets the body
    _assert_body_transport(_native_refresh(client, login.json()["refresh_token"]))


@pytest.mark.parametrize("origin", [None, FOREIGN, "http://testserver"])
def test_any_other_origin_falls_back_to_the_cookie(client: TestClient, origin: str | None) -> None:
    _assert_cookie_transport(_login(client, origin))


def test_a_native_refresh_needs_the_body_token_and_ignores_the_cookie(client: TestClient) -> None:
    web_token = _assert_cookie_transport(_login(client, WEB))
    cookie_only = client.post(REFRESH, headers=cookie_headers(web_token, NATIVE))
    assert cookie_only.status_code == 401


def test_a_foreign_origin_cannot_refresh_with_either_transport(client: TestClient) -> None:
    native_token = _assert_body_transport(_login(client, NATIVE))
    assert _native_refresh(client, native_token, FOREIGN).status_code == 403
    web_token = _assert_cookie_transport(_login(client, WEB))
    assert client.post(REFRESH, headers=cookie_headers(web_token, FOREIGN)).status_code == 403


def test_the_api_own_origin_never_gets_the_body_even_if_listed(
    tmp_path: Path, db: Database
) -> None:
    seed_user(db, T1, ALICE)
    own = "http://testserver"  # TestClient's origin: the API serves itself from here
    settings = make_settings(
        database_url=database_url(tmp_path), web_origin=WEB, native_app_origins=own
    )
    app, _runtime = auth_app(settings)
    with TestClient(app) as test_client:
        _assert_cookie_transport(_login(test_client, own))


def test_the_native_field_worker_flow_uses_the_body(
    client: TestClient, sender: CapturingSender
) -> None:
    assert client.post("/api/v1/auth/magic-link", json={"email": ALICE}).status_code == 202
    redeem = {"token": sender.sent[-1].token}
    device = uuid.uuid4()
    headers = {"Origin": NATIVE, DEVICE_HEADER: str(device)}
    redeemed = client.post("/api/v1/auth/magic-link/redeem", json=redeem, headers=headers)
    token = _assert_body_transport(redeemed)
    # The device's PIN is set at enrolment (T-0038); the PIN unlock renews its session.
    enrol = {"device_id": str(device), "platform": "android", "pin": PIN}
    bearer = {**headers, "Authorization": f"Bearer {redeemed.json()['access_token']}"}
    assert client.post("/api/v1/devices/enrol", json=enrol, headers=bearer).status_code == 201
    unlocked = client.post(
        UNLOCK, json={"pin": PIN, "refresh_token": token}, headers={"Origin": NATIVE}
    )
    _assert_body_transport(unlocked)
    # The web origin's PIN unlock never takes a body token (cookie only, as before).
    web = client.post(UNLOCK, json={"pin": PIN, "refresh_token": token}, headers={"Origin": WEB})
    assert web.status_code == 401


# --- CORS ------------------------------------------------------------------------------------


def _preflight(client: TestClient, origin: str) -> httpx.Response:
    return client.options(
        REFRESH,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": f"content-type,{DEVICE_HEADER.lower()}",
        },
    )


def test_cors_allows_exactly_the_native_origins_without_credentials(client: TestClient) -> None:
    allowed = _preflight(client, NATIVE)
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == NATIVE
    assert "access-control-allow-credentials" not in allowed.headers
    assert DEVICE_HEADER.lower() in allowed.headers["access-control-allow-headers"].lower()
    for origin in (WEB, FOREIGN):
        refused = _preflight(client, origin)
        assert "access-control-allow-origin" not in refused.headers
    simple = client.get("/health", headers={"Origin": NATIVE})
    assert simple.headers["access-control-allow-origin"] == NATIVE
    assert (
        "access-control-allow-origin" not in client.get("/health", headers={"Origin": WEB}).headers
    )


def test_no_native_origins_means_no_cors(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings())) as plain:
        response = _preflight(plain, NATIVE)
        assert "access-control-allow-origin" not in response.headers


# --- configuration ---------------------------------------------------------------------------


def test_native_origins_are_parsed_and_normalised() -> None:
    settings = make_settings(
        web_origin=f"{WEB}/", native_app_origins=" HTTPS://LocalHost:443 ,capacitor://localhost,"
    )
    assert settings.native_app_origins == (CAPACITOR, NATIVE)
    assert settings.web_origin == WEB
    assert make_settings().native_app_origins == ()
    assert normalize_origin("http://localhost:8100") == "http://localhost:8100"
    assert normalize_origin("http://localhost:80") == "http://localhost"


@pytest.mark.parametrize(
    "entry",
    [
        "*",
        "https://*.example",
        "localhost",
        "https://localhost/app",
        "https://u@localhost",
        "https://localhost?x=1",
        "https://localhost:99999",
        "null",
        "https://local host",
    ],
)
def test_malformed_native_origins_refuse_startup(entry: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(web_origin=WEB, native_app_origins=entry)


@pytest.mark.parametrize("web", [WEB, f"{WEB}/", "HTTPS://KERP.example.test:443"])
def test_the_web_origin_on_the_native_list_refuses_startup(web: str) -> None:
    with pytest.raises(ValidationError, match="must not contain the web origin"):
        make_settings(web_origin=web, native_app_origins=f"{NATIVE},{WEB}")


def test_native_origins_need_the_web_origin_to_check_against() -> None:
    with pytest.raises(ValidationError, match="needs KERP_WEB_ORIGIN"):
        make_settings(native_app_origins=NATIVE)


def test_the_app_does_not_start_with_the_web_origin_on_the_native_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERP_ENVIRONMENT", "test")
    monkeypatch.setenv("KERP_WEB_ORIGIN", WEB)
    monkeypatch.setenv("KERP_NATIVE_APP_ORIGINS", f"{NATIVE},{WEB}")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        create_app()
    monkeypatch.setenv("KERP_NATIVE_APP_ORIGINS", NATIVE)
    get_settings.cache_clear()
    assert isinstance(create_app().state.settings, Settings)
    assert get_settings().native_app_origins == (NATIVE,)
