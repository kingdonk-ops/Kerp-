"""Session lifetime and exact password-change cut-off (T-0037; T-0061 from PR #31 review).

Diff §4.2: a login session ends 12 hours after sign-in; rotation issues a new single-use
refresh token but never extends the session. A password change rejects every token issued
before it and accepts every token issued after it, even within the same second.
"""

import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth.field import PIN_TTL_MARGIN
from app.core.auth.models import User
from app.core.auth.runtime import AuthRuntime, install_auth_runtime
from app.core.auth.service import AuthService
from app.core.auth.tokens import TokenService
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from tests.audit_support import migrated_database
from tests.auth_support import (
    ALICE,
    PASSWORD,
    T1,
    auth_app,
    make_settings,
    refresh,
    seed_user,
    token_body,
)
from tests.db_support import database_url

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
SESSION = timedelta(hours=12)


class Clock:
    """The token service's clock, moved by the test. PyJWT keeps checking real time, so a
    test signs in in the past and refreshes at (about) the real now."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def alice(db: Database) -> uuid.UUID:
    return seed_user(db, T1, ALICE)


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime.now(UTC))


@pytest.fixture
def setup(
    tmp_path: Path, db: Database, alice: uuid.UUID, clock: Clock
) -> Iterator[tuple[TestClient, AuthRuntime]]:
    app, runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    tokens = runtime.tokens
    clocked = TokenService(
        tokens.keys,
        issuer=tokens.issuer,
        audience=tokens.audience,
        access_ttl=tokens.ttl["access"],
        refresh_ttl=tokens.ttl["refresh"],
        clock=clock,
    )
    runtime = replace(runtime, tokens=clocked)
    install_auth_runtime(app, runtime)
    with TestClient(app) as client:
        yield client, runtime


def _sign_in(client: TestClient) -> dict[str, str]:
    return token_body(client.post(LOGIN, json={"email": ALICE, "password": PASSWORD}))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- 12 hours from sign-in --------------------------------------------------------------------


def test_default_session_is_12_hours(setup: tuple[TestClient, AuthRuntime]) -> None:
    assert make_settings().refresh_token_hours == 12
    assert setup[1].tokens.ttl["refresh"] == SESSION


def test_rotation_at_11h59_succeeds_and_keeps_the_sign_in_expiry(
    setup: tuple[TestClient, AuthRuntime], clock: Clock
) -> None:
    client, runtime = setup
    now = clock.now
    clock.now = now - SESSION + timedelta(minutes=1)  # signed in 11 h 59 min ago
    first = _sign_in(client)
    end = runtime.tokens.verify(first["refresh_token"], "refresh").expires_at
    assert end == clock.now + SESSION

    clock.now = now
    response = refresh(client, first["refresh_token"])
    rotated = token_body(response)
    claims = runtime.tokens.verify(rotated["refresh_token"], "refresh")
    assert claims.expires_at == end  # rotation never extends the session
    assert runtime.tokens.verify(rotated["access_token"], "access").expires_at == end
    assert rotated["refresh_expires_in"] in (59, 60)
    assert "Max-Age=59" in response.headers["set-cookie"] or "Max-Age=60" in (
        response.headers["set-cookie"]
    )
    assert client.get(ME, headers=_bearer(rotated["access_token"])).status_code == 200


def test_rotations_never_extend_the_session(
    setup: tuple[TestClient, AuthRuntime], clock: Clock
) -> None:
    client, runtime = setup
    now = clock.now
    clock.now = now - timedelta(hours=11)
    token = _sign_in(client)["refresh_token"]
    end = runtime.tokens.verify(token, "refresh").expires_at
    for hours_ago in (8, 4, 1, 0):
        clock.now = now - timedelta(hours=hours_ago)
        token = token_body(refresh(client, token))["refresh_token"]
        assert runtime.tokens.verify(token, "refresh").expires_at == end


def test_refresh_at_12h_fails_and_needs_a_new_sign_in(
    setup: tuple[TestClient, AuthRuntime], clock: Clock
) -> None:
    client, _ = setup
    now = clock.now
    clock.now = now - SESSION  # signed in exactly 12 h ago
    token = _sign_in(client)["refresh_token"]
    clock.now = now
    response = refresh(client, token)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'
    assert _sign_in(client)["access_token"]  # signing in again starts a new 12 h session


def test_reuse_detection_still_revokes_the_session(
    setup: tuple[TestClient, AuthRuntime], clock: Clock
) -> None:
    client, _ = setup
    now = clock.now
    clock.now = now - timedelta(hours=6)
    first = _sign_in(client)
    clock.now = now
    rotated = token_body(refresh(client, first["refresh_token"]))
    assert refresh(client, first["refresh_token"]).status_code == 401  # replayed
    assert client.get(ME, headers=_bearer(rotated["access_token"])).status_code == 401
    assert refresh(client, rotated["refresh_token"]).status_code == 401


def test_pin_failure_window_follows_the_refresh_lifetime(
    setup: tuple[TestClient, AuthRuntime],
) -> None:
    # Until T-0038 moves the PIN to the device, its counter outlives the 12 h session.
    service = AuthService(setup[1])
    assert service.pin_lockout.policy.window == SESSION + PIN_TTL_MARGIN


# --- password change: exact, no fail-closed window (T-0061) ----------------------------------


def _change_password_at(db: Database, moment: datetime) -> None:
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as session:
        user = session.scalars(select(User).where(User.email == ALICE)).one()
        user.password_changed_at = moment


def _same_second(clock: Clock) -> datetime:
    """A whole second a little in the past (PyJWT refuses an ``iat`` in the future)."""
    return clock.now.replace(microsecond=0) - timedelta(seconds=5)


def test_token_issued_before_the_change_in_the_same_second_is_rejected(
    setup: tuple[TestClient, AuthRuntime], clock: Clock, db: Database, alice: uuid.UUID
) -> None:
    client, runtime = setup
    second = _same_second(clock)
    clock.now = second + timedelta(microseconds=100_000)
    token = runtime.tokens.issue("access", user_id=alice, tenant_id=T1, session_id=uuid.uuid4())
    _change_password_at(db, second + timedelta(microseconds=500_000))
    assert client.get(ME, headers=_bearer(token.token)).status_code == 401


def test_token_issued_after_the_change_in_the_same_second_is_accepted(
    setup: tuple[TestClient, AuthRuntime], clock: Clock, db: Database, alice: uuid.UUID
) -> None:
    client, runtime = setup
    second = _same_second(clock)
    _change_password_at(db, second + timedelta(microseconds=100_000))
    clock.now = second + timedelta(microseconds=500_000)
    token = runtime.tokens.issue("access", user_id=alice, tenant_id=T1, session_id=uuid.uuid4())
    assert client.get(ME, headers=_bearer(token.token)).status_code == 200


def test_sign_in_straight_after_a_password_change_is_accepted(
    setup: tuple[TestClient, AuthRuntime], clock: Clock, db: Database
) -> None:
    client, _ = setup
    second = _same_second(clock)
    _change_password_at(db, second + timedelta(microseconds=999_998))
    clock.now = second + timedelta(microseconds=999_999)
    tokens = _sign_in(client)
    assert client.get(ME, headers=_bearer(tokens["access_token"])).status_code == 200
