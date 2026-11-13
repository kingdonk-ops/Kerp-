"""Auth over HTTP: login, me, refresh rotation, logout, JWKS, tenant binding (T-0008).

Runs on the real migration (``auth_user`` under RLS on PostgreSQL) with the revocation
store on ``fakeredis``.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth.deps import CurrentPrincipal
from app.core.auth.keys import KeySet, generate_signing_key
from app.core.auth.models import User
from app.core.auth.revocation import InMemoryRevocationStore, RevocationUnavailableError
from app.core.auth.runtime import AuthRuntime
from app.core.auth.service import set_password
from app.core.db import Database, DbSession
from app.core.tenancy.context import (
    TenancyContext,
    current_tenant_id,
    current_user_id,
)
from app.core.tenancy.rls import session_tenancy
from tests.audit_support import migrated_database
from tests.auth_support import (
    ALICE,
    BOB,
    ORIGIN,
    PASSWORD,
    T1,
    T2,
    auth_app,
    cookie_headers,
    make_settings,
    refresh,
    seed_user,
    token_body,
)
from tests.db_support import database_url
from tests.test_auth_revocation import BrokenRedis

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"
JWKS = "/.well-known/jwks.json"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def users(db: Database) -> dict[str, uuid.UUID]:
    return {
        "alice": seed_user(db, T1, ALICE, display_name="Alice Example"),
        "bob_t2": seed_user(db, T2, BOB),
        "inactive": seed_user(db, T1, "inactive@example.test", is_active=False),
        "sso_only": seed_user(db, T1, "sso@example.test", password=None),
    }


@pytest.fixture
def app_runtime(tmp_path: Path, db: Database) -> tuple[FastAPI, AuthRuntime]:
    app, runtime = auth_app(make_settings(database_url=database_url(tmp_path)))

    @app.get("/_whoami")
    def whoami(principal: CurrentPrincipal, session: DbSession) -> dict[str, str | None]:
        bound = session_tenancy(session)
        return {
            "tenant": str(principal.tenant_id),
            "bound_tenant": str(bound.tenant_id) if bound else None,
            "bound_project": str(bound.project_id) if bound and bound.project_id else None,
            "ctx_tenant": str(current_tenant_id.get()),
            "ctx_user": str(current_user_id.get()),
        }

    @app.get("/_bound")
    def bound(session: DbSession) -> str | None:
        context = session_tenancy(session)
        return str(context.tenant_id) if context else None

    @app.get("/_users")
    def list_users(session: DbSession) -> list[str]:
        # No auth dependency: only the tenancy bound by get_db scopes this query.
        return sorted(session.scalars(select(User.email)))

    return app, runtime


@pytest.fixture
def client(app_runtime: tuple[FastAPI, AuthRuntime], users: dict[str, uuid.UUID]) -> TestClient:
    with TestClient(app_runtime[0]) as test_client:
        yield test_client  # type: ignore[misc]


@pytest.fixture
def runtime(app_runtime: tuple[FastAPI, AuthRuntime]) -> AuthRuntime:
    return app_runtime[1]


def _login(client: TestClient, email: str = ALICE) -> dict[str, str]:
    return token_body(client.post(LOGIN, json={"email": email, "password": PASSWORD}))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- JWKS -----------------------------------------------------------------------------------


def test_jwks_serves_the_public_signing_key(client: TestClient, runtime: AuthRuntime) -> None:
    response = client.get(JWKS)
    assert response.status_code == 200
    assert "max-age" in response.headers["cache-control"]
    body = response.json()
    assert body == runtime.keys.jwks()
    (key,) = body["keys"]
    assert key["kid"] == runtime.keys.signing.kid
    assert key["kty"] == "OKP"
    assert "d" not in key


def test_jwks_needs_no_authentication_or_database(tmp_path: Path) -> None:
    app, runtime = auth_app(make_settings())
    with TestClient(app) as c:
        assert c.get(JWKS).json() == runtime.keys.jwks()


def test_issued_token_verifies_with_the_published_jwks(client: TestClient) -> None:
    # What a third party (e.g. another service) does: verify using only the JWKS.
    token = _login(client)["access_token"]
    jwk_set = jwt.PyJWKSet.from_dict(client.get(JWKS).json())
    key = jwk_set[jwt.get_unverified_header(token)["kid"]]
    payload = jwt.decode(token, key.key, algorithms=["EdDSA"], audience="kerp-api")
    assert payload["tid"] == str(T1)


def test_jwks_includes_retired_keys(tmp_path: Path) -> None:
    retired = generate_signing_key("EdDSA").verification
    app, runtime = auth_app(make_settings(), keys=KeySet(generate_signing_key("EdDSA"), (retired,)))
    with TestClient(app) as c:
        kids = {k["kid"] for k in c.get(JWKS).json()["keys"]}
    assert kids == {runtime.keys.signing.kid, retired.kid}


def test_rs256_deployment_publishes_rsa_key(tmp_path: Path) -> None:
    app, _ = auth_app(make_settings(jwt_algorithm="RS256"))
    with TestClient(app) as c:
        (key,) = c.get(JWKS).json()["keys"]
    assert (key["kty"], key["alg"]) == ("RSA", "RS256")
    assert {"n", "e"} <= set(key)


# --- login --------------------------------------------------------------------------------


def test_login_issues_a_15_minute_access_token_and_a_refresh_token(
    client: TestClient, runtime: AuthRuntime, users: dict[str, uuid.UUID]
) -> None:
    response = client.post(LOGIN, json={"email": ALICE, "password": PASSWORD})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = token_body(response)
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["refresh_expires_in"] == 12 * 3600
    access = runtime.tokens.verify(body["access_token"], "access")
    refresh_claims = runtime.tokens.verify(body["refresh_token"], "refresh")
    assert access.user_id == users["alice"]
    assert access.tenant_id == T1
    assert access.session_id == refresh_claims.session_id


def test_refresh_cookie_is_httponly_secure_strict_and_scoped_to_auth(client: TestClient) -> None:
    response = client.post(LOGIN, json={"email": ALICE, "password": PASSWORD})
    cookie = response.headers["set-cookie"]
    attributes = {part.strip().split("=")[0].lower() for part in cookie.split(";")}
    assert cookie.startswith("kerp_refresh=")
    assert {"httponly", "secure", "path", "max-age", "samesite"} <= attributes
    assert "Path=/api/v1/auth" in cookie
    assert "SameSite=strict" in cookie
    assert "Max-Age=43200" in cookie


def test_login_email_is_case_insensitive(client: TestClient) -> None:
    _login(client, email="  ALICE@Example.Test ")


@pytest.mark.parametrize(
    "body",
    [
        {"email": ALICE, "password": "wrong"},
        {"email": "nobody@example.test", "password": PASSWORD},
        {"email": "inactive@example.test", "password": PASSWORD},
        {"email": "sso@example.test", "password": PASSWORD},
        # Bob exists, but in T2: a login to T1 cannot find him.
        {"email": BOB, "password": PASSWORD},
    ],
)
def test_login_failures_are_indistinguishable_401s(
    client: TestClient, body: dict[str, str]
) -> None:
    response = client.post(LOGIN, json=body)
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_login_signs_in_to_the_deployments_tenant(
    tmp_path: Path, db: Database, users: dict[str, uuid.UUID]
) -> None:
    # Another company is another deployment (diff §4.1): Bob's deployment is T2's.
    app, _ = auth_app(make_settings(database_url=database_url(tmp_path), default_tenant_id=T2))
    with TestClient(app) as c:
        tokens = _login(c, email=BOB)
        assert c.get(ME, headers=_bearer(tokens["access_token"])).json()["id"] == str(
            users["bob_t2"]
        )
        assert c.post(LOGIN, json={"email": ALICE, "password": PASSWORD}).status_code == 401


def test_login_without_a_deployment_tenant_is_401(
    tmp_path: Path, db: Database, users: dict[str, uuid.UUID]
) -> None:
    app, _ = auth_app(make_settings(database_url=database_url(tmp_path), default_tenant_id=None))
    with TestClient(app) as c:
        response = c.post(LOGIN, json={"email": ALICE, "password": PASSWORD})
    assert response.status_code == 401


@pytest.mark.parametrize("tenant", [T1, T2])
def test_login_with_a_tenant_field_is_422_and_never_names_the_tenant(
    client: TestClient, tenant: uuid.UUID
) -> None:
    body = {"email": ALICE, "password": PASSWORD, "tenant_id": str(tenant)}
    response = client.post(LOGIN, json=body)
    assert response.status_code == 422
    # The error echoes the caller's own input at most, never the deployment's tenant.
    other = T2 if tenant == T1 else T1
    assert str(other) not in response.text
    assert "set-cookie" not in response.headers
    ok = client.post(LOGIN, json={"email": ALICE, "password": PASSWORD})
    assert str(T1) not in ok.text
    assert str(T1) not in client.post(LOGIN, json={"email": ALICE, "password": "no"}).text


def test_login_rejects_unknown_fields_and_oversized_passwords(client: TestClient) -> None:
    extra = {"email": ALICE, "password": PASSWORD, "role": "admin"}
    assert client.post(LOGIN, json=extra).status_code == 422
    huge = {"email": ALICE, "password": "x" * 1025}
    assert client.post(LOGIN, json=huge).status_code == 422


def test_login_ignores_a_stale_bearer_header(client: TestClient) -> None:
    response = client.post(
        LOGIN,
        json={"email": ALICE, "password": PASSWORD},
        headers=_bearer("stale.invalid.token"),
    )
    assert response.status_code == 200


# --- authenticated requests ---------------------------------------------------------------


def test_me_returns_the_token_user(client: TestClient, users: dict[str, uuid.UUID]) -> None:
    tokens = _login(client)
    response = client.get(ME, headers=_bearer(tokens["access_token"]))
    assert response.status_code == 200
    assert response.json() == {
        "id": str(users["alice"]),
        "tenant_id": str(T1),
        "email": ALICE,
        "display_name": "Alice Example",
    }


@pytest.mark.parametrize(
    ("headers", "challenge"),
    [
        ({}, "Bearer"),
        ({"Authorization": "Bearer not-a-jwt"}, 'Bearer error="invalid_token"'),
        ({"Authorization": "Basic YWxpY2U6cHc="}, "Bearer"),
    ],
)
def test_me_requires_a_valid_bearer(
    client: TestClient, headers: dict[str, str], challenge: str
) -> None:
    response = client.get(ME, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == challenge


def test_refresh_token_is_not_an_access_token(client: TestClient) -> None:
    tokens = _login(client)
    assert client.get(ME, headers=_bearer(tokens["refresh_token"])).status_code == 401


def test_token_from_another_deployment_key_is_rejected(
    client: TestClient, users: dict[str, uuid.UUID]
) -> None:
    _other_app, other = auth_app(make_settings())
    forged = other.tokens.issue(
        "access", user_id=users["alice"], tenant_id=T1, session_id=uuid.uuid4()
    )
    assert client.get(ME, headers=_bearer(forged.token)).status_code == 401


def test_request_session_is_bound_to_the_token_tenant(
    client: TestClient, users: dict[str, uuid.UUID]
) -> None:
    tokens = _login(client)
    body = client.get("/_whoami", headers=_bearer(tokens["access_token"])).json()
    assert body == {
        "tenant": str(T1),
        "bound_tenant": str(T1),
        "bound_project": None,
        "ctx_tenant": str(T1),
        "ctx_user": str(users["alice"]),
    }


def test_tenant_comes_from_the_token_only(
    client: TestClient, runtime: AuthRuntime, users: dict[str, uuid.UUID]
) -> None:
    # T1's users are visible to a T1 token; T2's are not, and no parameter changes that.
    t1 = _login(client)["access_token"]
    t2 = runtime.tokens.issue(
        "access", user_id=users["bob_t2"], tenant_id=T2, session_id=uuid.uuid4()
    ).token
    t1_users = client.get("/_users", headers=_bearer(t1)).json()
    assert ALICE in t1_users
    assert BOB not in t1_users
    assert client.get("/_users", headers=_bearer(t2)).json() == [BOB]
    assert ALICE not in client.get(f"/_users?tenant_id={T1}", headers=_bearer(t2)).json()


def test_deactivated_user_is_locked_out_immediately(client: TestClient, db: Database) -> None:
    tokens = _login(client)
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        s.scalars(select(User).where(User.email == ALICE)).one().is_active = False
    assert client.get(ME, headers=_bearer(tokens["access_token"])).status_code == 401
    assert refresh(client, tokens["refresh_token"]).status_code == 401


def test_password_change_invalidates_earlier_tokens(
    client: TestClient, db: Database, runtime: AuthRuntime, users: dict[str, uuid.UUID]
) -> None:
    old = runtime.tokens.issue(
        "access", user_id=users["alice"], tenant_id=T1, session_id=uuid.uuid4()
    )
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        user = s.scalars(select(User).where(User.email == ALICE)).one()
        set_password(user, PASSWORD)
        user.password_changed_at = datetime.now(UTC) + timedelta(seconds=2)
    assert client.get(ME, headers=_bearer(old.token)).status_code == 401


def test_token_issued_after_password_change_works(client: TestClient, db: Database) -> None:
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        set_password(s.scalars(select(User).where(User.email == ALICE)).one(), PASSWORD)
    tokens = _login(client)
    assert client.get(ME, headers=_bearer(tokens["access_token"])).status_code == 200


# --- revocation (acceptance: a revoked jti is blocked) -------------------------------------


def test_revoked_jti_blocks_the_token(client: TestClient, runtime: AuthRuntime) -> None:
    tokens = _login(client)
    headers = _bearer(tokens["access_token"])
    assert client.get(ME, headers=headers).status_code == 200
    claims = runtime.tokens.verify(tokens["access_token"], "access")
    runtime.store.revoke_token(claims.jti, claims.remaining())
    response = client.get(ME, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'
    # A different token of another login is unaffected.
    assert client.get(ME, headers=_bearer(_login(client)["access_token"])).status_code == 200


def test_invalid_or_revoked_token_leaves_the_session_unbound(
    client: TestClient, runtime: AuthRuntime
) -> None:
    token = _login(client)["access_token"]
    assert client.get("/_bound", headers=_bearer(token)).json() == str(T1)
    claims = runtime.tokens.verify(token, "access")
    runtime.store.revoke_token(claims.jti, claims.remaining())
    assert client.get("/_bound", headers=_bearer(token)).json() is None
    assert client.get("/_bound", headers=_bearer("garbage")).json() is None
    assert client.get("/_bound").json() is None


def test_logout_revokes_access_and_refresh_tokens(client: TestClient) -> None:
    tokens = _login(client)
    headers = _bearer(tokens["access_token"])
    assert client.post(LOGOUT, headers=headers).status_code == 204
    assert client.get(ME, headers=headers).status_code == 401
    assert refresh(client, tokens["refresh_token"]).status_code == 401
    assert client.post(LOGOUT, headers=headers).status_code == 401


def test_logout_requires_authentication(client: TestClient) -> None:
    assert client.post(LOGOUT).status_code == 401


def test_logout_leaves_other_sessions_alive(client: TestClient) -> None:
    first, second = _login(client), _login(client)
    client.post(LOGOUT, headers=_bearer(first["access_token"]))
    assert client.get(ME, headers=_bearer(second["access_token"])).status_code == 200


# --- refresh rotation --------------------------------------------------------------------


def test_refresh_rotates_both_tokens_in_the_same_session(
    client: TestClient, runtime: AuthRuntime
) -> None:
    tokens = _login(client)
    response = client.post(
        REFRESH,
        # Clients often send the stale access token along.
        headers={**cookie_headers(tokens["refresh_token"]), **_bearer("expired-or-garbage")},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    rotated = token_body(response)
    assert rotated["refresh_token"] != tokens["refresh_token"]
    assert rotated["access_token"] != tokens["access_token"]
    old = runtime.tokens.verify(tokens["refresh_token"], "refresh")
    new = runtime.tokens.verify(rotated["refresh_token"], "refresh")
    assert new.session_id == old.session_id
    assert client.get(ME, headers=_bearer(rotated["access_token"])).status_code == 200
    # The rotated token can be used again (once).
    assert refresh(client, rotated["refresh_token"]).status_code == 200


def test_refresh_token_reuse_revokes_the_whole_session(client: TestClient) -> None:
    tokens = _login(client)
    rotated = token_body(refresh(client, tokens["refresh_token"]))
    # The old refresh token is replayed (stolen copy): rejected, and the session is killed.
    replay = refresh(client, tokens["refresh_token"])
    assert replay.status_code == 401
    assert client.get(ME, headers=_bearer(rotated["access_token"])).status_code == 401
    assert refresh(client, rotated["refresh_token"]).status_code == 401


def test_access_token_cannot_be_used_to_refresh(client: TestClient) -> None:
    tokens = _login(client)
    response = refresh(client, tokens["access_token"])
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'


def test_refresh_with_garbage_is_401(client: TestClient) -> None:
    assert refresh(client, "garbage").status_code == 401
    assert refresh(client, "x" * 4097).status_code == 401


def test_refresh_without_the_cookie_is_401(client: TestClient) -> None:
    assert client.post(REFRESH, headers={"Origin": ORIGIN}).status_code == 401
    # A body is no transport for the refresh token (cookie only, diff §4.2).
    token = _login(client)["refresh_token"]
    body = client.post(REFRESH, headers={"Origin": ORIGIN}, json={"refresh_token": token})
    assert body.status_code == 401


@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://testserver.evil"])
def test_refresh_rejects_a_cross_origin_request(client: TestClient, origin: str | None) -> None:
    tokens = _login(client)
    response = refresh(client, tokens["refresh_token"], origin=origin)
    assert response.status_code == 403
    assert "set-cookie" not in response.headers
    # Refused before the token was looked at: it still rotates from the real origin.
    assert refresh(client, tokens["refresh_token"]).status_code == 200


def test_refresh_accepts_the_configured_web_origin(
    tmp_path: Path, db: Database, users: dict[str, uuid.UUID]
) -> None:
    web = "https://kerp.example.test"
    app, _ = auth_app(make_settings(database_url=database_url(tmp_path), web_origin=web))
    with TestClient(app) as c:
        token = _login(c)["refresh_token"]
        assert refresh(c, token, origin=ORIGIN).status_code == 403
        assert refresh(c, token, origin=web).status_code == 200


def test_logout_clears_the_refresh_cookie(client: TestClient) -> None:
    tokens = _login(client)
    response = client.post(LOGOUT, headers=_bearer(tokens["access_token"]))
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert cookie.startswith('kerp_refresh="";') or cookie.startswith("kerp_refresh=;")
    assert "Max-Age=0" in cookie
    assert "Path=/api/v1/auth" in cookie


def test_unregistered_refresh_token_is_rejected(
    client: TestClient, runtime: AuthRuntime, users: dict[str, uuid.UUID]
) -> None:
    # Correctly signed but never issued through login (e.g. minted by a leaked key).
    session = uuid.uuid4()
    minted = runtime.tokens.issue(
        "refresh", user_id=users["alice"], tenant_id=T1, session_id=session
    )
    assert refresh(client, minted.token).status_code == 401
    assert runtime.store.is_revoked("anything", session)


# --- store outage fails closed ------------------------------------------------------------


def test_revocation_store_outage_is_503_not_access(
    tmp_path: Path, db: Database, users: dict[str, uuid.UUID]
) -> None:
    from app.core.auth.revocation import RedisRevocationStore

    app, runtime = auth_app(
        make_settings(database_url=database_url(tmp_path)),
        store=RedisRevocationStore(BrokenRedis()),
    )
    token = runtime.tokens.issue(
        "access", user_id=users["alice"], tenant_id=T1, session_id=uuid.uuid4()
    )
    minted = runtime.tokens.issue(
        "refresh", user_id=users["alice"], tenant_id=T1, session_id=uuid.uuid4()
    )
    with TestClient(app) as c:
        assert c.get(ME, headers=_bearer(token.token)).status_code == 503
        login = c.post(LOGIN, json={"email": ALICE, "password": PASSWORD})
        assert login.status_code == 503
        assert refresh(c, minted.token).status_code == 503


class RevokeFails(InMemoryRevocationStore):
    """Checks work, writes fail: the outage hits in the middle of a logout."""

    def revoke_token(self, jti: str, ttl: timedelta) -> None:
        raise RevocationUnavailableError("down")


def test_logout_during_store_outage_is_503(
    tmp_path: Path, db: Database, users: dict[str, uuid.UUID]
) -> None:
    app, runtime = auth_app(make_settings(database_url=database_url(tmp_path)), store=RevokeFails())
    token = runtime.tokens.issue(
        "access", user_id=users["alice"], tenant_id=T1, session_id=uuid.uuid4()
    )
    with TestClient(app) as c:
        assert c.post(LOGOUT, headers=_bearer(token.token)).status_code == 503
