"""Entra ID SSO, identity only (T-0054, diff §4.5, §4.2, §4.1).

Against a fake OIDC provider (``tests/sso_support.py``): local RSA keys and JWKS, signed ID
tokens, codes bound to the PKCE challenge; no network. All identities are synthetic.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.audit import service as audit
from app.core.auth import entra, sso
from app.core.auth.access import Persona, load_grants
from app.core.auth.device_signal import DEVICE_HEADER
from app.core.auth.keys import AuthConfigError
from app.core.auth.models import DEVICE_ACTIVE, DEVICE_REVOKED, Device, ProjectMembership, User
from app.core.auth.revocation import InMemoryRevocationStore, RedisRevocationStore
from app.core.auth.router import REFRESH_COOKIE
from app.core.auth.runtime import AuthRuntime, check_startup_config
from app.core.auth.sso_router import PREAUTH_COOKIE
from app.core.config import Settings
from app.core.db import Database, utcnow
from app.core.tenancy.context import TenancyContext
from app.modules.admin.models import AdminGrant
from tests.audit_support import migrated_database
from tests.auth_support import PASSWORD, T1, auth_app, cookie_headers, make_settings, seed_user
from tests.db_support import database_url
from tests.sso_support import (
    ENTRA_CLIENT,
    ENTRA_TENANT,
    ISSUER,
    OTHER_TENANT,
    FakeEntra,
    new_rsa_key,
)

DEPLOYMENT = "https://kerp.example.test"
NATIVE_ORIGIN = "https://localhost"  # the Capacitor Android app (androidScheme https)
REDIRECT_URI = f"{DEPLOYMENT}/api/v1/auth/sso/entra/callback"
CONFIG = "/api/v1/auth/config"
START = "/api/v1/auth/sso/entra/start"
CALLBACK = "/api/v1/auth/sso/entra/callback"
EXCHANGE = "/api/v1/auth/sso/entra/exchange"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
STAFF = "staff@company.example"
OID = "a7e3c1d0-0000-4000-8000-000000000001"
OTHER_OID = "a7e3c1d0-0000-4000-8000-000000000002"
VERIFIER = "v" * 43 + "-synthetic-app-verifier"  # the app's own PKCE verifier
CTX = TenancyContext(tenant_id=T1)


def sso_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": database_url(tmp_path),
        "web_origin": DEPLOYMENT,
        "native_app_origins": NATIVE_ORIGIN,
        "entra_tenant_id": ENTRA_TENANT,
        "entra_client_id": ENTRA_CLIENT,
        "entra_client_secret": "synthetic-client-secret",
        "entra_redirect_uri": REDIRECT_URI,
    }
    values.update(overrides)
    return make_settings(**values)


@dataclass
class Harness:
    client: TestClient
    db: Database
    fake: FakeEntra
    runtime: AuthRuntime
    redis: fakeredis.FakeRedis | None
    staff: uuid.UUID


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def _harness(tmp_path: Path, db: Database, *, store: Any = None, **settings: Any) -> Harness:
    staff = seed_user(db, T1, STAFF)
    fake = FakeEntra()
    redis = None
    if store is None:
        redis = fakeredis.FakeRedis()
        store = RedisRevocationStore(redis)
    app, runtime = auth_app(sso_settings(tmp_path, **settings), store=store, entra_transport=fake)
    return Harness(TestClient(app), db, fake, runtime, redis, staff)


@pytest.fixture
def h(tmp_path: Path, db: Database) -> Iterator[Harness]:
    harness = _harness(tmp_path, db)
    with harness.client:
        yield harness


# --- helpers --------------------------------------------------------------------------------


def start(h: Harness, **params: Any) -> tuple[str, str]:
    response = h.client.get(START, params=params, follow_redirects=False)
    assert response.status_code == 302, response.text
    return response.headers["location"], response.cookies[PREAUTH_COOKIE]


def callback(
    h: Harness, code: str | None, state: str | None, cookie: str | None, **params: str
) -> httpx.Response:
    h.client.cookies.clear()
    query = {k: v for k, v in {"code": code, "state": state, **params}.items() if v is not None}
    headers = {"Cookie": f"{PREAUTH_COOKIE}={cookie}"} if cookie else {}
    return h.client.get(CALLBACK, params=query, headers=headers, follow_redirects=False)


def sign_in(
    h: Harness, start_params: dict[str, Any] | None = None, **claims: Any
) -> httpx.Response:
    location, cookie = start(h, **(start_params or {}))
    code, state = h.fake.authorize(location, **claims)
    return callback(h, code, state, cookie)


def landing(response: httpx.Response) -> tuple[str, dict[str, str]]:
    assert response.status_code == 303, response.text
    parts = urlsplit(response.headers["location"])
    params = {k: v[0] for k, v in parse_qs(parts.query).items()}
    return f"{parts.scheme}://{parts.netloc}{parts.path}", params


def refused_with(response: httpx.Response) -> str:
    _url, params = landing(response)
    assert REFRESH_COOKIE not in response.cookies
    assert "status" not in params
    return params["error"]


def signed_in(response: httpx.Response) -> str:
    url, params = landing(response)
    assert url == f"{DEPLOYMENT}/login/sso-return"
    assert params == {"status": "signed_in"}
    return response.cookies[REFRESH_COOKIE]


def user_row(h: Harness, user_id: uuid.UUID) -> User:
    with h.db.unit_of_work(CTX) as session:
        user = session.get(User, user_id)
        assert user is not None
        session.expunge(user)
        return user


def sso_audit(h: Harness) -> list[dict[str, Any]]:
    with h.db.unit_of_work(CTX) as session:
        return [dict(e.payload) for e in audit.chain_entries(session, sso.AUDIT_CHAIN)]


def add_device(h: Harness, user_id: uuid.UUID, *, revoked: bool = False) -> uuid.UUID:
    device_id = uuid.uuid4()
    with h.db.unit_of_work(CTX) as session:
        now = utcnow()
        session.add(
            Device(
                tenant_id=T1,
                project_id=None,
                user_id=user_id,
                device_id=device_id,
                platform="android",
                enrolled_at=now,
                last_seen_at=now,
                status=DEVICE_REVOKED if revoked else DEVICE_ACTIVE,
                revoked_at=now if revoked else None,
                created_by=user_id,
            )
        )
    return device_id


def link(h: Harness, user_id: uuid.UUID, oid: str) -> None:
    with h.db.unit_of_work(CTX) as session:
        user = session.get(User, user_id)
        assert user is not None
        user.entra_oid, user.entra_tid = oid, ENTRA_TENANT


def deactivate(h: Harness, user_id: uuid.UUID) -> None:
    with h.db.unit_of_work(CTX) as session:
        user = session.get(User, user_id)
        assert user is not None
        user.is_active = False


# --- configuration --------------------------------------------------------------------------


@pytest.mark.parametrize("tenant", ["common", "organizations", "consumers", "company.example"])
def test_the_tenant_must_be_one_tenant_guid(tenant: str) -> None:
    with pytest.raises(ValidationError, match="entra_tenant_id"):
        Settings(environment="test", entra_tenant_id=tenant, entra_client_id=ENTRA_CLIENT)


def test_the_client_id_must_be_a_guid() -> None:
    with pytest.raises(ValidationError, match="entra_client_id"):
        Settings(environment="test", entra_tenant_id=ENTRA_TENANT, entra_client_id="kerp")


@pytest.mark.parametrize(
    ("tenant", "client"), [(None, None), (ENTRA_TENANT, None), (None, ENTRA_CLIENT), ("", "")]
)
def test_sso_is_enabled_only_with_tenant_and_client(tenant: Any, client: Any) -> None:
    settings = Settings(environment="test", entra_tenant_id=tenant, entra_client_id=client)
    assert settings.entra_enabled is False


def test_guids_are_lower_cased_and_the_secret_is_never_in_the_repr() -> None:
    settings = Settings(
        environment="test",
        entra_tenant_id=ENTRA_TENANT.upper(),
        entra_client_id=ENTRA_CLIENT,
        entra_client_secret="synthetic-client-secret",
    )
    assert settings.entra_tenant_id == ENTRA_TENANT
    assert settings.entra_enabled
    assert "synthetic-client-secret" not in repr(settings)


@pytest.mark.parametrize(
    "uri",
    [
        "https://kerp.example.test/elsewhere",
        "ftp://kerp.example.test/api/v1/auth/sso/entra/callback",
        "https://kerp.example.test/api/v1/auth/sso/entra/callback?x=1",
    ],
)
def test_a_redirect_uri_other_than_the_callback_is_refused(uri: str) -> None:
    with pytest.raises(ValidationError, match="entra_redirect_uri"):
        Settings(environment="test", entra_redirect_uri=uri)


def test_production_needs_the_client_secret_and_the_redirect_uri() -> None:
    base: dict[str, Any] = {
        "environment": "production",
        "default_tenant_id": T1,
        "entra_tenant_id": ENTRA_TENANT,
        "entra_client_id": ENTRA_CLIENT,
    }
    with pytest.raises(AuthConfigError, match="KERP_ENTRA_CLIENT_SECRET"):
        check_startup_config(Settings(**base, entra_redirect_uri=REDIRECT_URI))
    with pytest.raises(AuthConfigError, match="KERP_ENTRA_REDIRECT_URI"):
        check_startup_config(Settings(**base, entra_client_secret="synthetic"))
    check_startup_config(
        Settings(**base, entra_client_secret="synthetic", entra_redirect_uri=REDIRECT_URI)
    )


def test_startup_warns_about_the_required_optional_claims(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="kerp.auth.sso"):
        check_startup_config(Settings(environment="test"))
        assert "optional ID-token claims" not in caplog.text
        check_startup_config(
            Settings(environment="test", entra_tenant_id=ENTRA_TENANT, entra_client_id=ENTRA_CLIENT)
        )
    assert "email, acct, xms_edov" in caplog.text
    assert "every SSO sign-in is refused" in caplog.text


def test_android_fingerprints_are_validated() -> None:
    fingerprint = ":".join(["ab"] * 32)
    settings = Settings(environment="test", android_app_cert_fingerprints=f"{fingerprint},,")
    assert settings.android_app_cert_fingerprints == (fingerprint.upper(),)
    with pytest.raises(ValidationError, match="android_app_cert_fingerprints"):
        Settings(environment="test", android_app_cert_fingerprints="AB:CD")
    with pytest.raises(ValidationError, match="android_app_cert_fingerprints"):
        Settings(environment="test", android_app_cert_fingerprints=[5])


# --- the public auth configuration ---------------------------------------------------------


def test_auth_config_offers_sso_as_preferred_and_password_as_available(h: Harness) -> None:
    body = h.client.get(CONFIG).json()
    assert body == {
        "password": {"available": True},
        "sso": {"provider": "entra", "preferred": True, "start_url": START},
    }


def test_auth_config_without_sso_offers_password_only(tmp_path: Path) -> None:
    app, _ = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        assert client.get(CONFIG).json() == {"password": {"available": True}, "sso": None}
        assert client.get(START, follow_redirects=False).status_code == 404
        assert client.get(CALLBACK, follow_redirects=False).status_code == 404
        body = {"code": "x" * 20, "verifier": VERIFIER, "device_id": str(uuid.uuid4())}
        assert client.post(EXCHANGE, json=body).status_code == 404


# --- /start --------------------------------------------------------------------------------


def test_start_redirects_to_the_tenant_pinned_authority_with_pkce(h: Harness) -> None:
    response = h.client.get(START, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["cache-control"] == "no-store"
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        f"https://login.microsoftonline.com/{ENTRA_TENANT}/oauth2/v2.0/authorize"
    )
    assert "common" not in response.headers["location"]
    assert "organizations" not in response.headers["location"]
    params = {k: v[0] for k, v in parse_qs(location.query).items()}
    assert params["scope"] == "openid profile email"
    assert params["client_id"] == ENTRA_CLIENT
    assert params["response_type"] == "code"
    assert params["redirect_uri"] == REDIRECT_URI
    assert params["code_challenge_method"] == "S256"
    assert len(params["code_challenge"]) == 43
    assert params["state"] != params["nonce"]
    cookie = response.headers["set-cookie"]
    assert f"{PREAUTH_COOKIE}=" in cookie
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/api/v1/auth/sso", "Max-Age=600"):
        assert attribute.lower() in cookie.lower(), cookie


def test_the_state_entry_is_server_side_short_lived_and_holds_only_a_cookie_hash(
    h: Harness,
) -> None:
    device = uuid.uuid4()
    challenge = entra.pkce_challenge(VERIFIER)
    location, cookie = start(
        h, invite_token="synthetic-invite", device_id=str(device), app_challenge=challenge
    )
    state = parse_qs(urlsplit(location).query)["state"][0]
    assert h.redis is not None
    keys = [k.decode() for k in h.redis.keys("*sso:entra:state:*")]
    assert len(keys) == 1
    assert state not in keys[0]  # stored under its hash
    assert 0 < h.redis.ttl(keys[0]) <= 600
    raw = h.redis.get(keys[0])
    assert raw is not None
    assert cookie.encode() not in raw
    assert sso.digest(cookie).encode() in raw
    assert b"synthetic-invite" in raw
    assert str(device).encode() in raw
    assert challenge.encode() in raw


def test_an_app_challenge_needs_a_device_id(h: Harness) -> None:
    response = h.client.get(
        START, params={"app_challenge": entra.pkce_challenge(VERIFIER)}, follow_redirects=False
    )
    assert response.status_code == 422
    malformed = h.client.get(START, params={"app_challenge": "short"}, follow_redirects=False)
    assert malformed.status_code == 422


# --- the happy path ---------------------------------------------------------------------


def test_first_sign_in_links_by_verified_email_and_issues_the_kerp_session(h: Harness) -> None:
    response = sign_in(h, email="Staff@Company.Example")
    refresh_token = signed_in(response)
    assert "access_token" not in response.headers["location"]
    set_cookies = response.headers.get_list("set-cookie")
    preauth = next(c for c in set_cookies if c.startswith(f"{PREAUTH_COOKIE}="))
    assert "Max-Age=0" in preauth or 'kerp_sso_preauth=""' in preauth  # cleared
    refresh_cookie = next(c for c in set_cookies if c.startswith(f"{REFRESH_COOKIE}="))
    assert "SameSite=strict" in refresh_cookie
    assert "HttpOnly" in refresh_cookie

    user = user_row(h, h.staff)
    assert (user.entra_oid, user.entra_tid) == (OID, ENTRA_TENANT)
    assert user.is_internal is True

    # The same KERP session as a password sign-in: the SPA rotates the cookie on /refresh.
    rotated = h.client.post(
        "/api/v1/auth/refresh", headers=cookie_headers(refresh_token, DEPLOYMENT)
    )
    assert rotated.status_code == 200, rotated.text
    body = rotated.json()
    assert body["expires_in"] == 15 * 60
    assert 12 * 3600 - 60 <= body["refresh_expires_in"] <= 12 * 3600
    assert "session_expires_at" in body
    me = h.client.get(ME, headers={"Authorization": f"Bearer {body['access_token']}"}).json()
    assert me["email"] == STAFF
    assert me["is_internal"] is True

    actions = [(e["action"], e.get("user_id")) for e in sso_audit(h)]
    assert actions == [("sso_linked", str(h.staff)), ("sso_signed_in", str(h.staff))]


def test_later_sign_ins_match_by_oid_only(h: Harness) -> None:
    signed_in(sign_in(h))
    # The e-mail claim is ignored once linked, even unverified or changed.
    signed_in(sign_in(h, email="renamed@elsewhere.example", xms_edov=False))
    signed_in(sign_in(h, email=None))
    assert [e["action"] for e in sso_audit(h)].count("sso_linked") == 1


def test_internal_domain_counts_as_verified(tmp_path: Path, db: Database) -> None:
    harness = _harness(tmp_path, db, internal_email_domains="company.example")
    with harness.client:
        signed_in(sign_in(harness, xms_edov=False))


def test_the_web_landing_follows_the_web_origin(tmp_path: Path, db: Database) -> None:
    harness = _harness(tmp_path, db, web_origin="https://app.example.test", native_app_origins="")
    with harness.client:
        url, _ = landing(sign_in(harness))
        assert url == "https://app.example.test/login/sso-return"


def test_without_a_configured_redirect_uri_it_follows_the_request(
    tmp_path: Path, db: Database
) -> None:
    harness = _harness(tmp_path, db, entra_redirect_uri=None)
    with harness.client:
        location, _ = start(harness)
        params = parse_qs(urlsplit(location).query)
        assert params["redirect_uri"] == ["http://testserver/api/v1/auth/sso/entra/callback"]
        signed_in(sign_in(harness))


def test_the_provider_seam_authenticates_like_the_callback(h: Harness) -> None:
    assert h.runtime.entra is not None
    provider = h.runtime.entra.provider
    token = h.fake.sign(h.fake.claims(nonce="synthetic-nonce"))
    with h.db.unit_of_work(CTX) as session:
        user = provider.authenticate(
            session, T1, entra.EntraCredentials(id_token=token, nonce="synthetic-nonce")
        )
        assert user.id == h.staff
    assert provider.name == "entra"


# --- ID token verification ---------------------------------------------------------------


def _assert_nothing_happened(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    user = user_row(h, h.staff)
    assert user.entra_oid is None
    assert "eyJ" not in caplog.text  # no token (or token fragment) is ever logged


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"iss": f"https://login.microsoftonline.com/{OTHER_TENANT}/v2.0"}, entra.WRONG_ISSUER),
        ({"iss": "https://login.microsoftonline.com/common/v2.0"}, entra.WRONG_ISSUER),
        ({"tid": OTHER_TENANT}, entra.WRONG_TENANT),
        ({"aud": "00000000-0000-4000-8000-00000000abcd"}, entra.WRONG_AUDIENCE),
        ({"aud": [ENTRA_CLIENT, "another-audience"]}, entra.WRONG_AUDIENCE),
        ({"exp": 1_000_000, "iat": 999_000, "nbf": 999_000}, entra.EXPIRED),
        ({"nbf": 4_000_000_000}, entra.NOT_YET_VALID),
        ({"iat": 4_000_000_000}, entra.NOT_YET_VALID),
        ({"nonce": "another-nonce"}, entra.NONCE_MISMATCH),
        ({"exp": None}, entra.CLAIM_MISSING),
        ({"oid": None}, entra.CLAIM_MISSING),
    ],
)
def test_an_invalid_id_token_is_refused_and_creates_nothing(
    h: Harness, caplog: pytest.LogCaptureFixture, claims: dict[str, Any], reason: str
) -> None:
    with caplog.at_level(logging.INFO):
        assert refused_with(sign_in(h, **claims)) == entra.SSO_FAILED
    assert f"refused: {reason}" in caplog.text
    _assert_nothing_happened(h, caplog)
    assert sso_audit(h) == []  # no verified identity: security log only


def test_the_clock_leeway_is_two_minutes(h: Harness) -> None:
    import time

    now = int(time.time())
    signed_in(sign_in(h, exp=now - 90, iat=now - 3600, nbf=now - 3600))
    assert refused_with(sign_in(h, exp=now - 180, iat=now - 3600, nbf=now - 3600)) == "SSO_FAILED"


def test_a_bad_signature_is_refused(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    location, cookie = start(h)
    code, state = h.fake.authorize(location)
    pending = h.fake.codes[code]
    forged = h.fake.sign(pending.claims, kid="kid-1", key=new_rsa_key())  # same kid, other key
    h.fake.sign = lambda *_a, **_k: forged  # type: ignore[method-assign]
    with caplog.at_level(logging.INFO):
        assert refused_with(callback(h, code, state, cookie)) == entra.SSO_FAILED
    assert f"refused: {entra.BAD_SIGNATURE}" in caplog.text
    _assert_nothing_happened(h, caplog)


def test_a_token_that_is_not_rs256_or_has_no_kid_is_refused(h: Harness) -> None:
    assert h.runtime.entra is not None
    provider = h.runtime.entra.provider
    import jwt as pyjwt

    hs = pyjwt.encode(h.fake.claims(nonce="n"), "synthetic-shared-secret-of-32-bytes!", "HS256")
    for token in (hs, "not-a-jwt"):
        with pytest.raises(entra.SsoRefusedError) as refused:
            provider.verify(token, nonce="n")
        assert refused.value.reason == entra.ID_TOKEN_MALFORMED
        assert refused.value.code == entra.SSO_FAILED


def test_pkce_mismatch_is_refused(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    location, cookie = start(h)
    code, state = h.fake.authorize(location)
    h.fake.codes[code].challenge = entra.pkce_challenge("x" * 43)  # not our verifier's
    with caplog.at_level(logging.INFO):
        assert refused_with(callback(h, code, state, cookie)) == entra.SSO_FAILED
    assert f"refused: {entra.TOKEN_EXCHANGE_FAILED}" in caplog.text
    _assert_nothing_happened(h, caplog)


def test_an_entra_error_is_refused(h: Harness) -> None:
    location, cookie = start(h)
    _code, state = h.fake.authorize(location)
    assert refused_with(callback(h, None, state, cookie, error="access_denied")) == "SSO_FAILED"


# --- state and the pre-auth cookie ------------------------------------------------------


def test_a_replayed_state_is_refused(h: Harness) -> None:
    location, cookie = start(h)
    code, state = h.fake.authorize(location)
    signed_in(callback(h, code, state, cookie))
    code2, _ = h.fake.authorize(location)
    assert refused_with(callback(h, code2, state, cookie)) == entra.SSO_FAILED


def test_an_unknown_or_missing_state_is_refused(h: Harness) -> None:
    location, cookie = start(h)
    code, _state = h.fake.authorize(location)
    assert refused_with(callback(h, code, "unknown-state", cookie)) == entra.SSO_FAILED
    assert refused_with(callback(h, code, None, cookie)) == entra.SSO_FAILED


@pytest.mark.parametrize("cookie", [None, "foreign-cookie-value"])
def test_a_missing_or_foreign_preauth_cookie_is_refused_and_consumes_the_state(
    h: Harness, cookie: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    location, right_cookie = start(h)
    code, state = h.fake.authorize(location)
    with caplog.at_level(logging.INFO):
        response = callback(h, code, state, cookie)
        assert refused_with(response) == entra.SSO_FAILED
    assert f"refused: {entra.PREAUTH_COOKIE_MISMATCH}" in caplog.text
    assert any(c.startswith(f"{PREAUTH_COOKIE}=") for c in response.headers.get_list("set-cookie"))
    assert h.fake.redeemed == []  # the code was never redeemed
    # The state is spent: the victim's own browser cannot use it afterwards either.
    assert refused_with(callback(h, code, state, right_cookie)) == entra.SSO_FAILED
    _assert_nothing_happened(h, caplog)


def test_oversized_parameters_are_refusals_not_validation_errors(h: Harness) -> None:
    location, cookie = start(h)
    code, state = h.fake.authorize(location)
    # An oversized cookie is a mismatch: the state is consumed like for any foreign cookie.
    assert refused_with(callback(h, code, state, "c" * 5000)) == entra.SSO_FAILED
    assert refused_with(callback(h, code, state, cookie)) == entra.SSO_FAILED
    assert refused_with(callback(h, code, "s" * 500, cookie)) == entra.SSO_FAILED
    location, cookie = start(h)
    _code, state = h.fake.authorize(location)
    assert refused_with(callback(h, "x" * 5000, state, cookie)) == entra.SSO_FAILED


def test_an_expired_state_is_refused(tmp_path: Path, db: Database) -> None:
    clock = [1000.0]
    harness = _harness(tmp_path, db, store=InMemoryRevocationStore(clock=lambda: clock[0]))
    with harness.client:
        location, cookie = start(harness)
        code, state = harness.fake.authorize(location)
        clock[0] += 601
        assert refused_with(callback(harness, code, state, cookie)) == entra.SSO_FAILED


# --- members only ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"acct": 1}, entra.GUEST),
        ({"acct": True}, entra.GUEST),
        ({"acct": None}, entra.ACCT_CLAIM_MISSING),
        (
            {"idp": "https://sts.windows.net/72f988bf-0000-4000-8000-000000000000/"},
            entra.FOREIGN_IDP,
        ),
        ({"idp": "live.com"}, entra.FOREIGN_IDP),
    ],
)
def test_a_guest_is_refused_and_never_linked(
    h: Harness, caplog: pytest.LogCaptureFixture, claims: dict[str, Any], reason: str
) -> None:
    with caplog.at_level(logging.INFO):
        assert refused_with(sign_in(h, **claims)) == entra.SSO_GUEST
    assert f"refused: {reason}" in caplog.text
    _assert_nothing_happened(h, caplog)
    assert user_row(h, h.staff).is_internal is False
    assert sso_audit(h) == [
        {"action": "sso_refused", "provider": "entra", "flow": "web", "reason": reason}
    ]


def test_an_idp_equal_to_the_pinned_issuer_is_a_member(h: Harness) -> None:
    signed_in(sign_in(h, idp=ISSUER))
    signed_in(sign_in(h, acct="0"))


def test_acct_claim_missing_raises_a_health_warning(h: Harness) -> None:
    assert h.client.get("/health").json().get("warnings") is None
    refused_with(sign_in(h, acct=None))
    assert h.client.get("/health").json()["warnings"] == ["sso_acct_claim_missing"]
    assert h.redis is not None
    key = next(iter(h.redis.keys("*acct-claim-missing")))
    assert 23 * 3600 < h.redis.ttl(key) <= 24 * 3600


def test_other_refusals_raise_no_health_warning(h: Harness) -> None:
    refused_with(sign_in(h, acct=1))
    assert "warnings" not in h.client.get("/health").json()


def test_health_stays_ok_when_the_auth_store_is_unreachable(tmp_path: Path) -> None:
    server = fakeredis.FakeServer()
    server.connected = False
    store = RedisRevocationStore(fakeredis.FakeRedis(server=server))
    app, _ = auth_app(sso_settings(tmp_path), store=store, entra_transport=FakeEntra())
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert "warnings" not in response.json()


# --- matching by verified e-mail ----------------------------------------------------------


@pytest.mark.parametrize(
    ("claims", "reason", "code"),
    [
        ({"xms_edov": False}, entra.EMAIL_UNVERIFIED, entra.SSO_EMAIL_UNVERIFIED),
        ({"xms_edov": None}, entra.EMAIL_UNVERIFIED, entra.SSO_EMAIL_UNVERIFIED),
        ({"xms_edov": "false"}, entra.EMAIL_UNVERIFIED, entra.SSO_EMAIL_UNVERIFIED),
        ({"email": None}, entra.EMAIL_MISSING, entra.SSO_EMAIL_UNVERIFIED),
        ({"email": "not-an-address"}, entra.EMAIL_MISSING, entra.SSO_EMAIL_UNVERIFIED),
        ({"email": "nobody@company.example"}, entra.NO_ACCOUNT, entra.SSO_NO_ACCOUNT),
    ],
)
def test_unverified_missing_or_unknown_email_is_refused(
    h: Harness, caplog: pytest.LogCaptureFixture, claims: dict[str, Any], reason: str, code: str
) -> None:
    with caplog.at_level(logging.INFO):
        assert refused_with(sign_in(h, **claims)) == code
    assert f"refused: {reason}" in caplog.text
    _assert_nothing_happened(h, caplog)
    with h.db.unit_of_work(CTX) as session:  # nothing created
        assert session.scalar(select(func.count()).select_from(User)) == 1
    assert [e["reason"] for e in sso_audit(h)] == [reason]


def test_a_preferred_username_only_token_is_refused(h: Harness) -> None:
    response = sign_in(h, email=None, preferred_username=STAFF)
    assert refused_with(response) == entra.SSO_EMAIL_UNVERIFIED
    assert user_row(h, h.staff).entra_oid is None


def test_a_deactivated_account_is_refused(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    deactivate(h, h.staff)
    assert refused_with(sign_in(h)) == entra.SSO_ACCOUNT_DEACTIVATED
    _assert_nothing_happened(h, caplog)
    assert sso_audit(h) == [
        {
            "action": "sso_refused",
            "provider": "entra",
            "flow": "web",
            "reason": entra.ACCOUNT_DEACTIVATED,
            "user_id": str(h.staff),
        }
    ]


def test_a_deactivated_linked_account_is_refused(h: Harness) -> None:
    signed_in(sign_in(h))
    deactivate(h, h.staff)
    assert refused_with(sign_in(h)) == entra.SSO_ACCOUNT_DEACTIVATED


def test_an_account_linked_to_another_oid_is_refused(h: Harness) -> None:
    link(h, h.staff, OTHER_OID)
    assert refused_with(sign_in(h)) == entra.SSO_ACCOUNT_LINKED_ELSEWHERE
    assert user_row(h, h.staff).entra_oid == OTHER_OID
    assert sso_audit(h)[-1]["reason"] == entra.OID_MISMATCH


# --- no group-to-role mapping ----------------------------------------------------------------


def test_group_and_role_claims_change_no_persona_grant_or_team(h: Harness) -> None:
    project = uuid.uuid4()
    with h.db.unit_of_work(CTX) as session:
        session.add(
            ProjectMembership(
                tenant_id=T1,
                project_id=None,
                user_id=h.staff,
                member_project_id=project,
                persona=Persona.FIELD_WORKER.value,
            )
        )

    def snapshot() -> tuple[list[Any], int, int]:
        context = TenancyContext(tenant_id=T1, user_id=h.staff, is_system_admin=True)
        with h.db.unit_of_work(context) as session:
            grants = load_grants(session, T1, h.staff)
            admin = session.scalar(select(func.count()).select_from(AdminGrant))
            members = session.scalar(select(func.count()).select_from(ProjectMembership))
            return grants, int(admin or 0), int(members or 0)

    before = snapshot()
    signed_in(
        sign_in(
            h,
            groups=["11111111-0000-4000-8000-000000000001"],
            roles=["KERP.SystemAdmin", "PER-09"],
            wids=["62e90394-69f5-4237-9190-012177145e10"],
        )
    )
    assert snapshot() == before
    user = user_row(h, h.staff)
    assert user.is_internal is True
    assert user.is_active is True


# --- SSO promoted, password kept ----------------------------------------------------------


def test_password_login_keeps_working_for_a_linked_internal_user(
    tmp_path: Path, db: Database
) -> None:
    harness = _harness(tmp_path, db, internal_email_domains="company.example")
    with harness.client:
        signed_in(sign_in(harness))
        assert user_row(harness, harness.staff).entra_oid == OID
        response = harness.client.post(LOGIN, json={"email": STAFF, "password": PASSWORD})
        assert response.status_code == 200, response.text
        assert user_row(harness, harness.staff).is_internal is True


# --- web flow with a device ------------------------------------------------------------------


def test_a_web_sign_in_on_a_revoked_device_is_refused_only_after_the_credentials(
    h: Harness,
) -> None:
    revoked = add_device(h, h.staff, revoked=True)
    # Bad credentials: the same answer as for any device.
    assert refused_with(sign_in(h, {"device_id": str(revoked)}, tid=OTHER_TENANT)) == "SSO_FAILED"
    # Valid credentials: the device is told to wipe.
    assert refused_with(sign_in(h, {"device_id": str(revoked)})) == "DEVICE_REVOKED"
    assert sso_audit(h)[-1]["reason"] == "device_revoked"


def test_a_web_sign_in_on_an_enrolled_device_binds_the_session(h: Harness) -> None:
    device = add_device(h, h.staff)
    refresh_token = signed_in(sign_in(h, {"device_id": str(device)}))
    assert h.runtime.tokens.verify(refresh_token, "refresh").device_id == device


# --- native handoff --------------------------------------------------------------------------


def native_sign_in(h: Harness, device: uuid.UUID, **claims: Any) -> str:
    params = {"device_id": str(device), "app_challenge": entra.pkce_challenge(VERIFIER)}
    response = sign_in(h, params, **claims)
    url, query = landing(response)
    assert url == f"{DEPLOYMENT}/app/sso-return"
    assert set(query) == {"code"}
    assert REFRESH_COOKIE not in response.cookies  # no token in a redirect, no cookie
    assert "token" not in response.headers["location"]
    return query["code"]


def exchange(
    h: Harness,
    code: str,
    device: uuid.UUID,
    verifier: str = VERIFIER,
    origin: str | None = NATIVE_ORIGIN,
) -> httpx.Response:
    headers = {"Origin": origin} if origin else {}
    body = {"code": code, "verifier": verifier, "device_id": str(device)}
    return h.client.post(EXCHANGE, json=body, headers=headers)


def test_native_handoff_issues_the_native_body_transport(h: Harness) -> None:
    device = uuid.uuid4()
    code = native_sign_in(h, device)
    response = exchange(h, code, device)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["refresh_token"]
    assert body["access_token"]
    assert "session_expires_at" in body
    assert REFRESH_COOKIE not in response.cookies
    assert response.headers["cache-control"] == "no-store"
    assert user_row(h, h.staff).entra_oid == OID
    assert [e["action"] for e in sso_audit(h)] == ["sso_linked", "sso_verified", "sso_signed_in"]


def test_the_exchange_code_is_stored_hashed_with_a_60_second_lifetime(h: Harness) -> None:
    code = native_sign_in(h, uuid.uuid4())
    assert h.redis is not None
    keys = [k.decode() for k in h.redis.keys("*sso:entra:exchange:*")]
    assert len(keys) == 1
    assert code not in keys[0]
    assert 0 < h.redis.ttl(keys[0]) <= 60


def test_an_exchange_code_redeemed_twice_is_refused_and_audited(h: Harness) -> None:
    device = uuid.uuid4()
    code = native_sign_in(h, device)
    assert exchange(h, code, device).status_code == 200
    again = exchange(h, code, device)
    assert again.status_code == 401
    assert again.json() == {"detail": "Not authenticated"}
    assert sso_audit(h)[-1] == {
        "action": "sso_refused",
        "provider": "entra",
        "flow": "native",
        "reason": "exchange_code_reused",
        "user_id": str(h.staff),
    }


def test_an_exchange_code_after_60_seconds_is_refused(tmp_path: Path, db: Database) -> None:
    clock = [1000.0]
    harness = _harness(tmp_path, db, store=InMemoryRevocationStore(clock=lambda: clock[0]))
    with harness.client:
        device = uuid.uuid4()
        code = native_sign_in(harness, device)
        clock[0] += 61
        assert exchange(harness, code, device).status_code == 401


def test_an_exchange_with_a_wrong_verifier_or_another_device_is_refused(h: Harness) -> None:
    device = uuid.uuid4()
    code = native_sign_in(h, device)
    assert exchange(h, code, device, verifier="w" * 43).status_code == 401
    assert exchange(h, code, device).status_code == 401  # spent by the failed attempt
    code = native_sign_in(h, device)
    assert exchange(h, code, uuid.uuid4()).status_code == 401
    reasons = [e.get("reason") for e in sso_audit(h)]
    assert "exchange_verifier_mismatch" in reasons
    assert "exchange_device_mismatch" in reasons


def test_the_exchange_is_for_allow_listed_native_origins_only(h: Harness) -> None:
    device = uuid.uuid4()
    code = native_sign_in(h, device)
    for origin in (DEPLOYMENT, "https://evil.example", None):
        assert exchange(h, code, device, origin=origin).status_code == 403
    assert exchange(h, code, device).status_code == 200  # not spent by the refusals


def test_a_deactivated_user_cannot_redeem_an_exchange_code(h: Harness) -> None:
    device = uuid.uuid4()
    code = native_sign_in(h, device)
    deactivate(h, h.staff)
    assert exchange(h, code, device).status_code == 401


def test_a_native_refusal_lands_on_the_app_link(h: Harness) -> None:
    params = {"device_id": str(uuid.uuid4()), "app_challenge": entra.pkce_challenge(VERIFIER)}
    url, query = landing(sign_in(h, params, acct=1))
    assert url == f"{DEPLOYMENT}/app/sso-return"
    assert query == {"error": entra.SSO_GUEST}


def test_a_native_exchange_on_an_enrolled_device_binds_the_session(h: Harness) -> None:
    device = add_device(h, h.staff)
    code = native_sign_in(h, device)
    response = exchange(h, code, device)
    assert response.status_code == 200
    claims = h.runtime.tokens.verify(response.json()["refresh_token"], "refresh")
    assert claims.device_id == device


# --- device check after credentials (T-0080, TD-291) --------------------------------------


@pytest.mark.parametrize("bad", ["unknown-code", "wrong-verifier", "reused", "foreign-device"])
def test_bad_exchange_credentials_answer_the_same_for_every_device_state(
    h: Harness, bad: str
) -> None:
    answers = []
    for state in ("unknown", "active", "revoked"):
        device = (
            uuid.uuid4()
            if state == "unknown"
            else add_device(h, h.staff, revoked=state == "revoked")
        )
        code = native_sign_in(h, device)
        verifier, presented = VERIFIER, device
        if bad == "unknown-code":
            code = "u" * 43
        elif bad == "wrong-verifier":
            verifier = "w" * 43
        elif bad == "reused":
            exchange(h, code, device)
        else:
            code = native_sign_in(h, uuid.uuid4())  # a code bound to another device
        response = exchange(h, code, presented, verifier)
        answers.append(
            (response.status_code, response.json(), response.headers.get("www-authenticate"))
        )
    assert answers[0] == answers[1] == answers[2]
    assert answers[0][0] == 401
    assert "code" not in answers[0][1]


def test_valid_exchange_credentials_on_a_revoked_device_answer_device_revoked(
    h: Harness,
) -> None:
    device = add_device(h, h.staff, revoked=True)
    code = native_sign_in(h, device)  # the callback itself never looks at the device
    response = exchange(h, code, device, origin=NATIVE_ORIGIN)
    assert response.status_code == 401
    assert response.json()["code"] == "DEVICE_REVOKED"
    assert sso_audit(h)[-1]["reason"] == "device_revoked"


def test_the_device_header_does_not_change_the_exchange(h: Harness) -> None:
    device = uuid.uuid4()
    code = native_sign_in(h, device)
    body = {"code": code, "verifier": VERIFIER, "device_id": str(device)}
    headers = {"Origin": NATIVE_ORIGIN, DEVICE_HEADER: str(device)}
    assert h.client.post(EXCHANGE, json=body, headers=headers).status_code == 200


# --- the JWKS cache --------------------------------------------------------------------------


def test_jwks_is_cached(h: Harness) -> None:
    signed_in(sign_in(h))
    signed_in(sign_in(h))
    assert h.fake.jwks_fetches == 1


def test_key_rotation_is_picked_up_by_one_refetch(h: Harness) -> None:
    signed_in(sign_in(h))
    h.fake.rotate("kid-2", drop_old=True)
    signed_in(sign_in(h))
    assert h.fake.jwks_fetches == 2
    signed_in(sign_in(h))
    assert h.fake.jwks_fetches == 2


def test_unknown_kid_refetches_at_most_once_a_minute_deployment_wide() -> None:
    fake = FakeEntra()
    store = RedisRevocationStore(fakeredis.FakeRedis())
    clock = [0.0]
    config = entra.EntraConfig(ENTRA_TENANT, ENTRA_CLIENT, None)
    # Two workers of one deployment: their own caches, one shared store.
    workers = [
        entra.EntraJwks(lambda: fake.fetch_jwks(config), store, clock=lambda: clock[0])
        for _ in range(2)
    ]
    for worker in workers:
        assert worker.key_for("kid-1") is not None
    assert fake.jwks_fetches == 2  # each worker's initial load
    for kid in ("forged-1", "forged-2", "forged-3"):
        for worker in workers:
            assert worker.key_for(kid) is None
    assert fake.jwks_fetches == 3  # one refetch for the whole deployment
    store_redis = store._client  # the limit is a 60-second key
    key = next(iter(store_redis.keys("*jwks-refetch")))
    assert 0 < store_redis.ttl(key) <= 60


def test_unknown_kid_refetch_with_an_unreachable_endpoint_refuses(h: Harness) -> None:
    signed_in(sign_in(h))

    def broken(_config: entra.EntraConfig) -> Any:
        raise entra.EntraTransportError("unreachable")

    h.fake.fetch_jwks = broken  # type: ignore[method-assign]
    h.fake.rotate("kid-2")
    assert refused_with(sign_in(h)) == entra.SSO_FAILED


def test_jwks_reload_after_a_day_keeps_old_keys_when_it_fails() -> None:
    fake = FakeEntra()
    store = InMemoryRevocationStore()
    clock = [0.0]
    calls = {"fail": False}
    config = entra.EntraConfig(ENTRA_TENANT, ENTRA_CLIENT, None)

    def fetch() -> Any:
        if calls["fail"]:
            raise entra.EntraTransportError("down")
        return fake.fetch_jwks(config)

    jwks = entra.EntraJwks(fetch, store, clock=lambda: clock[0])
    assert jwks.key_for("kid-1") is not None
    calls["fail"] = True
    clock[0] += entra.JWKS_CACHE_TTL.total_seconds() + 1
    assert jwks.key_for("kid-1") is not None  # old keys kept
    empty = entra.EntraJwks(fetch, store)
    with pytest.raises(entra.EntraTransportError):
        empty.key_for("kid-1")


def test_jwks_documents_with_unusable_keys_are_filtered() -> None:
    good = FakeEntra()
    config = entra.EntraConfig(ENTRA_TENANT, ENTRA_CLIENT, None)
    document = dict(good.fetch_jwks(config))
    keys = list(document["keys"])
    keys += [
        "not-an-object",
        {"kty": "EC", "kid": "ec"},
        {**keys[0], "kid": "enc", "use": "enc"},
        {"kty": "RSA", "kid": "broken", "n": "!!", "e": "AQAB"},
    ]
    jwks = entra.EntraJwks(lambda: {"keys": keys}, InMemoryRevocationStore())
    assert jwks.key_for("kid-1") is not None
    for kid in ("ec", "enc", "broken"):
        assert jwks.key_for(kid) is None
    with pytest.raises(entra.EntraTransportError):
        entra.EntraJwks(lambda: {"nope": 1}, InMemoryRevocationStore()).key_for("kid-1")


def test_jwks_unavailable_refuses_the_sign_in(tmp_path: Path, db: Database) -> None:
    harness = _harness(tmp_path, db)

    def broken(_config: entra.EntraConfig) -> Any:
        raise entra.EntraTransportError("unreachable")

    harness.fake.fetch_jwks = broken  # type: ignore[method-assign]
    with harness.client:
        assert refused_with(sign_in(harness)) == entra.SSO_FAILED


# --- assetlinks.json -------------------------------------------------------------------------


def test_assetlinks_is_served_from_configuration(tmp_path: Path) -> None:
    fingerprint = ":".join(["0A"] * 32)
    settings = make_settings(
        android_app_package="example.kerp.field", android_app_cert_fingerprints=fingerprint
    )
    app, _ = auth_app(settings)
    with TestClient(app) as client:
        response = client.get("/.well-known/assetlinks.json")
        assert response.status_code == 200
        assert response.json() == [
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": "example.kerp.field",
                    "sha256_cert_fingerprints": [fingerprint],
                },
            }
        ]
    app, _ = auth_app(make_settings())
    with TestClient(app) as client:
        assert client.get("/.well-known/assetlinks.json").status_code == 404


# --- the store and the HTTP transport --------------------------------------------------------


def test_store_values_are_single_use_and_claims_are_exclusive() -> None:
    for store in (InMemoryRevocationStore(), RedisRevocationStore(fakeredis.FakeRedis())):
        store.put_value("sso:test", b"v", timedelta(seconds=30))
        assert store.peek_value("sso:test") == b"v"
        assert store.take_value("sso:test") == b"v"
        assert store.take_value("sso:test") is None
        assert store.claim("sso:claim", timedelta(seconds=30)) is True
        assert store.claim("sso:claim", timedelta(seconds=30)) is False


def test_corrupt_state_and_exchange_entries_are_refused() -> None:
    store = InMemoryRevocationStore()
    store.put_value(f"sso:entra:state:{sso.digest('s')}", b"{}", timedelta(seconds=30))
    assert sso.take_state(store, "s") is None
    store.put_value(f"sso:entra:exchange:{sso.digest('c')}", b"[]", timedelta(seconds=30))
    assert sso.take_exchange_code(store, "c") is None


class _FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self, _size: int) -> bytes:
        return self.body

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def test_http_transport_redeems_the_code_with_the_secret_and_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    seen: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> _FakeHttpResponse:
        seen.append(request)
        if request.full_url.endswith("/token"):
            return _FakeHttpResponse(b'{"id_token": "synthetic.id.token"}')
        return _FakeHttpResponse(b'{"keys": []}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    transport = entra.HttpEntraTransport()
    config = entra.EntraConfig(ENTRA_TENANT, ENTRA_CLIENT, "synthetic-secret")
    token = transport.redeem_code(config, code="c", verifier=VERIFIER, redirect_uri=REDIRECT_URI)
    assert token == "synthetic.id.token"
    form = parse_qs((seen[0].data or b"").decode())  # type: ignore[union-attr]
    assert seen[0].full_url == config.token_url
    assert form["code_verifier"] == [VERIFIER]
    assert form["client_secret"] == ["synthetic-secret"]
    assert form["grant_type"] == ["authorization_code"]
    assert form["scope"] == ["openid profile email"]
    assert transport.fetch_jwks(config) == {"keys": []}
    assert (
        seen[1].full_url == f"https://login.microsoftonline.com/{ENTRA_TENANT}/discovery/v2.0/keys"
    )


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[1]", b'{"token_type": "Bearer"}', b"x" * (entra.MAX_RESPONSE_BYTES + 2)],
)
def test_http_transport_refuses_bad_answers(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _FakeHttpResponse(body))
    config = entra.EntraConfig(ENTRA_TENANT, ENTRA_CLIENT, "synthetic-secret")
    with pytest.raises(entra.EntraTransportError):
        entra.HttpEntraTransport().redeem_code(
            config, code="c", verifier=VERIFIER, redirect_uri=REDIRECT_URI
        )


def test_http_transport_errors_carry_no_body(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import urllib.error
    import urllib.request

    def http_error(*_a: Any, **_k: Any) -> Any:
        raise urllib.error.HTTPError(
            "https://login.example",
            400,
            "Bad Request",
            {},
            io.BytesIO(b"secret-echo"),  # type: ignore[arg-type]
        )

    def unreachable(*_a: Any, **_k: Any) -> Any:
        raise urllib.error.URLError("down")

    config = entra.EntraConfig(ENTRA_TENANT, ENTRA_CLIENT, "synthetic-secret")
    monkeypatch.setattr(urllib.request, "urlopen", http_error)
    with pytest.raises(entra.EntraTransportError, match="HTTP 400") as raised:
        entra.HttpEntraTransport().fetch_jwks(config)
    assert "secret-echo" not in str(raised.value)
    monkeypatch.setattr(urllib.request, "urlopen", unreachable)
    with pytest.raises(entra.EntraTransportError, match="unreachable"):
        entra.HttpEntraTransport().fetch_jwks(config)
    no_secret = entra.EntraConfig(ENTRA_TENANT, ENTRA_CLIENT, None)
    with pytest.raises(entra.EntraTransportError, match="SECRET"):
        entra.HttpEntraTransport().redeem_code(
            no_secret, code="c", verifier=VERIFIER, redirect_uri=REDIRECT_URI
        )
