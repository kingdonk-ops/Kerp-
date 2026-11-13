"""Field-worker auth over HTTP: magic link, PIN-gated sessions, progressive lockout (T-0009).

Diff §4: "magic-link + 6-digit PIN as a *local session unlock only*, with progressive
lockout throttling". The PIN never authenticates on its own: it only unlocks (rotates) a
session that a magic link started on the device, together with that session's refresh
token. Runs on the real migration with a store whose clock the tests control.
"""

import logging
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.auth import service as service_module
from app.core.auth.field import (
    PIN_TTL_MARGIN,
    LoggingMagicLinkSender,
    UndeliverableMagicLinkSender,
    is_weak_pin,
    magic_link_digest,
    sender_from_settings,
)
from app.core.auth.lockout import LOGIN_ACCOUNT_POLICY
from app.core.auth.revocation import InMemoryRevocationStore, RedisRevocationStore
from app.core.auth.runtime import AuthRuntime
from app.core.auth.service import AuthService
from app.core.db import Database
from app.openapi import openapi_schema
from tests.audit_support import migrated_database
from tests.auth_support import (
    ALICE,
    BOB,
    PASSWORD,
    T1,
    T2,
    CapturingSender,
    auth_app,
    fake_store,
    make_settings,
    seed_user,
)
from tests.db_support import database_url
from tests.test_auth_revocation import BrokenRedis, FakeClock

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"
MAGIC = "/api/v1/auth/magic-link"
REDEEM = "/api/v1/auth/magic-link/redeem"
UNLOCK = "/api/v1/auth/pin/unlock"
PIN = "482913"  # synthetic
WRONG = "771204"


class Harness:
    def __init__(
        self, client: TestClient, runtime: AuthRuntime, sender: CapturingSender, clock: FakeClock
    ) -> None:
        self.client = client
        self.runtime = runtime
        self.sender = sender
        self.clock = clock

    def request_link(self, email: str = ALICE, tenant: uuid.UUID = T1) -> str:
        before = len(self.sender.sent)
        response = self.client.post(MAGIC, json={"email": email, "tenant_id": str(tenant)})
        assert response.status_code == 202, response.text
        assert len(self.sender.sent) == before + 1
        return self.sender.sent[-1].token

    def redeem(self, token: str, pin: str = PIN) -> dict[str, str]:
        response = self.client.post(REDEEM, json={"token": token, "pin": pin})
        assert response.status_code == 200, response.text
        tokens: dict[str, str] = response.json()
        return tokens

    def field_session(self) -> dict[str, str]:
        return self.redeem(self.request_link())

    def unlock(self, refresh_token: str, pin: str = PIN) -> httpx.Response:
        return self.client.post(UNLOCK, json={"refresh_token": refresh_token, "pin": pin})


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def users(db: Database) -> dict[str, uuid.UUID]:
    return {
        "alice": seed_user(db, T1, ALICE, display_name="Alice Example"),
        "bob_t2": seed_user(db, T2, BOB),
        "inactive": seed_user(db, T1, "inactive@example.test", is_active=False),
    }


@pytest.fixture
def h(tmp_path: Path, db: Database, users: dict[str, uuid.UUID]) -> Iterator[Harness]:
    clock = FakeClock()
    sender = CapturingSender()
    app, runtime = auth_app(
        make_settings(database_url=database_url(tmp_path)),
        store=InMemoryRevocationStore(clock=clock),
        magic_links=sender,
    )
    with TestClient(app) as client:
        yield Harness(client, runtime, sender, clock)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _wrong_pins(h: Harness, refresh_token: str, count: int) -> list[int]:
    """Send ``count`` wrong PINs, waiting out each lock. Returns the lock delays seen."""
    delays: list[int] = []
    sent = 0
    while sent < count:
        response = h.unlock(refresh_token, WRONG)
        if response.status_code == 429:
            delay = int(response.headers["retry-after"])
            delays.append(delay)
            h.clock.now += delay
            continue
        assert response.status_code == 401
        sent += 1
    return delays


# --- magic link -----------------------------------------------------------------------------


def test_magic_link_signs_in_once_and_starts_a_session(
    h: Harness, users: dict[str, uuid.UUID]
) -> None:
    token = h.request_link()
    link = h.sender.sent[-1]
    assert (link.user_id, link.tenant_id, link.email) == (users["alice"], T1, ALICE)
    tokens = h.redeem(token)
    me = h.client.get(ME, headers=_bearer(tokens["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == ALICE
    # One use only.
    again = h.client.post(REDEEM, json={"token": token, "pin": PIN})
    assert again.status_code == 401
    assert again.json() == {"detail": "Not authenticated"}


def test_only_the_token_digest_is_stored(h: Harness) -> None:
    token = h.request_link()
    store = h.runtime.store
    assert store.take_magic_link(token) is None
    assert store.take_magic_link(magic_link_digest(token)) is not None


@pytest.mark.parametrize(
    "body",
    [
        {"email": "nobody@example.test", "tenant_id": str(T1)},
        {"email": "inactive@example.test", "tenant_id": str(T1)},
        {"email": BOB, "tenant_id": str(T1)},  # Bob lives in T2
        {"email": ALICE},  # no tenant and no default tenant
    ],
)
def test_magic_link_request_is_202_and_silent_for_anyone_else(
    h: Harness, body: dict[str, str]
) -> None:
    response = h.client.post(MAGIC, json=body)
    assert response.status_code == 202
    assert h.sender.sent == []


def test_magic_link_uses_the_default_tenant(
    tmp_path: Path, db: Database, users: dict[str, uuid.UUID]
) -> None:
    sender = CapturingSender()
    app, _ = auth_app(
        make_settings(database_url=database_url(tmp_path), default_tenant_id=T1),
        magic_links=sender,
    )
    with TestClient(app) as client:
        assert client.post(MAGIC, json={"email": ALICE.upper()}).status_code == 202
    assert [link.user_id for link in sender.sent] == [users["alice"]]


def test_magic_link_expires(h: Harness) -> None:
    token = h.request_link()
    h.clock.now += 15 * 60 + 1
    assert h.client.post(REDEEM, json={"token": token, "pin": PIN}).status_code == 401


def test_garbage_magic_link_is_401(h: Harness) -> None:
    response = h.client.post(REDEEM, json={"token": "x" * 43, "pin": PIN})
    assert response.status_code == 401


def test_magic_link_of_a_user_deactivated_meanwhile_is_401(h: Harness, db: Database) -> None:
    token = h.request_link()
    from app.core.auth.models import User
    from app.core.tenancy.context import TenancyContext

    with db.unit_of_work(TenancyContext(tenant_id=T1)) as session:
        for user in session.query(User).filter(User.email == ALICE):
            user.is_active = False
    assert h.client.post(REDEEM, json={"token": token, "pin": PIN}).status_code == 401


@pytest.mark.parametrize(
    "pin",
    [
        "12345",
        "1234567",
        "12a456",
        "",
        "000000",
        "777777",
        "123456",
        "654321",
        "\uff14\uff18\uff12\uff19\uff11\uff13",  # full-width digits are not ASCII digits
    ],
)
def test_malformed_or_weak_pin_is_422_and_keeps_the_link(h: Harness, pin: str) -> None:
    token = h.request_link()
    assert h.client.post(REDEEM, json={"token": token, "pin": pin}).status_code == 422
    h.redeem(token)  # the link was not spent by the rejected request


def test_weak_pin_rule() -> None:
    assert is_weak_pin("111111")
    assert is_weak_pin("345678")
    assert is_weak_pin("876543")
    assert not is_weak_pin("482913")
    assert not is_weak_pin("135790")


def test_magic_link_requests_are_throttled_per_address(h: Harness) -> None:
    for _ in range(4):  # 3 free, the 4th is sent but arms a 1-minute lock
        h.request_link()
    locked = h.client.post(MAGIC, json={"email": ALICE, "tenant_id": str(T1)})
    assert locked.status_code == 429
    assert locked.headers["retry-after"] == "60"
    # An unknown address is throttled the same way (no enumeration through the 429).
    for _ in range(4):
        h.client.post(MAGIC, json={"email": "nobody@example.test", "tenant_id": str(T1)})
    unknown = h.client.post(MAGIC, json={"email": "nobody@example.test", "tenant_id": str(T1)})
    assert unknown.status_code == 429
    h.clock.now += 60
    h.request_link()


# --- the PIN never authenticates alone ------------------------------------------------------


def test_field_session_cannot_be_refreshed_without_the_pin(h: Harness) -> None:
    tokens = h.field_session()
    response = h.client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 401
    # Not consumed: the device can still unlock with it.
    assert h.unlock(tokens["refresh_token"]).status_code == 200


def test_unlock_rotates_the_session_with_refresh_token_and_pin(h: Harness) -> None:
    tokens = h.field_session()
    old = h.runtime.tokens.verify(tokens["refresh_token"], "refresh")
    response = h.unlock(tokens["refresh_token"])
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    new = response.json()
    claims = h.runtime.tokens.verify(new["refresh_token"], "refresh")
    assert claims.session_id == old.session_id
    assert h.client.get(ME, headers=_bearer(new["access_token"])).status_code == 200
    # The next unlock needs the new refresh token; the new one is still PIN-gated.
    assert h.client.post(REFRESH, json={"refresh_token": new["refresh_token"]}).status_code == 401
    assert h.unlock(new["refresh_token"]).status_code == 200


def test_replayed_refresh_token_on_unlock_revokes_the_session(h: Harness) -> None:
    tokens = h.field_session()
    new = h.unlock(tokens["refresh_token"]).json()
    assert h.unlock(tokens["refresh_token"]).status_code == 401
    assert h.unlock(new["refresh_token"]).status_code == 401


def test_pin_without_a_refresh_token_is_rejected(h: Harness) -> None:
    h.field_session()
    assert h.client.post(UNLOCK, json={"pin": PIN}).status_code == 422
    assert h.client.post(UNLOCK, json={"pin": PIN, "refresh_token": ""}).status_code == 422
    assert h.unlock("not.a.token").status_code == 401


def test_pin_cannot_unlock_with_an_access_token(h: Harness) -> None:
    tokens = h.field_session()
    assert h.unlock(tokens["access_token"]).status_code == 401


def test_pin_cannot_unlock_a_password_session(h: Harness) -> None:
    h.field_session()  # Alice has a PIN on another session ...
    login = h.client.post(LOGIN, json={"email": ALICE, "password": PASSWORD, "tenant_id": str(T1)})
    refresh_token = login.json()["refresh_token"]
    # ... but it does not open her password session, which has no PIN gate.
    assert h.unlock(refresh_token).status_code == 401
    assert h.client.post(REFRESH, json={"refresh_token": refresh_token}).status_code == 200


def test_pin_is_bound_to_its_own_session(h: Harness) -> None:
    first = h.field_session()
    second = h.redeem(h.request_link(), pin="905172")
    assert h.unlock(first["refresh_token"], "905172").status_code == 401
    assert h.unlock(second["refresh_token"], "905172").status_code == 200


def test_logged_out_field_session_cannot_be_unlocked(h: Harness) -> None:
    tokens = h.field_session()
    assert h.client.post(LOGOUT, headers=_bearer(tokens["access_token"])).status_code == 204
    assert h.unlock(tokens["refresh_token"]).status_code == 401


def test_no_endpoint_accepts_a_pin_without_a_session_credential() -> None:
    """Contract: every request body with a ``pin`` also requires a token that only a
    magic link (``token``) or an existing session (``refresh_token``) can provide."""
    spec = openapi_schema()
    schemas = spec["components"]["schemas"]
    with_pin = {name: s for name, s in schemas.items() if "pin" in s.get("properties", {})}
    assert set(with_pin) == {"MagicLinkRedeemRequest", "PinUnlockRequest"}
    for schema in with_pin.values():
        required = set(schema["required"])
        assert "pin" in required
        assert required & {"token", "refresh_token"}


# --- progressive lockout on the PIN ---------------------------------------------------------


def test_wrong_pins_escalate_the_lock(h: Harness) -> None:
    tokens = h.field_session()
    refresh_token = tokens["refresh_token"]
    for _ in range(3):  # the free attempts
        assert h.unlock(refresh_token, WRONG).status_code == 401
    assert h.unlock(refresh_token, WRONG).status_code == 401
    locked = h.unlock(refresh_token)  # even the right PIN is not checked while locked
    assert locked.status_code == 429
    assert locked.headers["retry-after"] == "30"
    assert locked.json() == {"detail": "Too many attempts"}
    h.clock.now += 30
    delays = _wrong_pins(h, refresh_token, 3)  # failures 5..7
    assert delays == [60, 120]
    assert h.unlock(refresh_token).status_code == 429
    h.clock.now += 240
    assert h.unlock(refresh_token).status_code == 200


def test_right_pin_resets_the_count(h: Harness) -> None:
    tokens = h.field_session()
    for _ in range(3):
        h.unlock(tokens["refresh_token"], WRONG)
    new = h.unlock(tokens["refresh_token"]).json()
    for _ in range(3):  # three free attempts again
        assert h.unlock(new["refresh_token"], WRONG).status_code == 401
    assert h.unlock(new["refresh_token"]).status_code == 200


def test_tenth_wrong_pin_revokes_the_session(h: Harness) -> None:
    tokens = h.field_session()
    session_id = h.runtime.tokens.verify(tokens["refresh_token"], "refresh").session_id
    _wrong_pins(h, tokens["refresh_token"], 9)
    assert not h.runtime.store.is_revoked("any", session_id)
    _wrong_pins(h, tokens["refresh_token"], 1)
    # Revoked by the 10th wrong PIN itself, before any further request.
    assert h.runtime.store.is_revoked("any", session_id)
    h.clock.now += 3600
    assert h.unlock(tokens["refresh_token"]).status_code == 401
    assert h.client.get(ME, headers=_bearer(tokens["access_token"])).status_code == 401
    # A new magic link starts over.
    h.redeem(h.request_link())


def test_right_pin_after_nine_wrong_ones_still_unlocks(h: Harness) -> None:
    tokens = h.field_session()
    session_id = h.runtime.tokens.verify(tokens["refresh_token"], "refresh").session_id
    _wrong_pins(h, tokens["refresh_token"], 9)
    h.clock.now += 15 * 60  # wait out the lock armed by the 9th failure (the 15-min cap)
    assert h.unlock(tokens["refresh_token"]).status_code == 200
    assert not h.runtime.store.is_revoked("any", session_id)


def test_pin_cap_holds_for_the_session_not_per_day(h: Harness) -> None:
    tokens = h.field_session()
    session_id = h.runtime.tokens.verify(tokens["refresh_token"], "refresh").session_id
    _wrong_pins(h, tokens["refresh_token"], 9)
    h.clock.now += 25 * 3600  # a day later the count must not have reset
    _wrong_pins(h, tokens["refresh_token"], 1)
    assert h.runtime.store.is_revoked("any", session_id)


def test_pin_counter_lives_as_long_as_the_refresh_token(h: Harness) -> None:
    window = h.runtime.tokens.ttl["refresh"] + PIN_TTL_MARGIN
    assert AuthService(h.runtime).pin_lockout.policy.window == window
    tokens = h.field_session()
    _wrong_pins(h, tokens["refresh_token"], 1)
    h.clock.now += h.runtime.tokens.ttl["refresh"].total_seconds()
    # Still counting: failures 2-4 run back to back only if failure 1 was kept; failure 4
    # then arms the first lock.
    assert _wrong_pins(h, tokens["refresh_token"], 3) == []
    assert h.unlock(tokens["refresh_token"], WRONG).status_code == 429


def test_pin_lockout_is_per_session(h: Harness) -> None:
    first = h.field_session()
    second = h.field_session()
    _wrong_pins(h, first["refresh_token"], 4)
    assert h.unlock(first["refresh_token"]).status_code == 429
    assert h.unlock(second["refresh_token"]).status_code == 200


# --- progressive lockout on password login (TD-123) -----------------------------------------


def _login(h: Harness, email: str = ALICE, password: str = PASSWORD) -> int:
    response = h.client.post(
        LOGIN, json={"email": email, "password": password, "tenant_id": str(T1)}
    )
    return response.status_code


def test_password_login_locks_progressively(h: Harness) -> None:
    for _ in range(6):  # 5 free, the 6th arms the lock
        assert _login(h, password="wrong") == 401
    response = h.client.post(
        LOGIN, json={"email": ALICE, "password": PASSWORD, "tenant_id": str(T1)}
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    h.clock.now += 30
    assert _login(h) == 200
    # Success cleared the count.
    for _ in range(5):
        assert _login(h, password="wrong") == 401
    assert _login(h) == 200


def test_login_lock_is_per_address_and_case_insensitive(h: Harness) -> None:
    for _ in range(6):
        _login(h, email=ALICE.upper(), password="wrong")
    assert _login(h) == 429
    assert _login(h, email="nobody@example.test") == 401  # other addresses unaffected


def _login_from(h: Harness, client: str, password: str = PASSWORD) -> int:
    """A login from another client address (as uvicorn reports it in ``request.client``)."""
    other = TestClient(h.client.app, client=(client, 40000))
    return other.post(
        LOGIN, json={"email": ALICE, "password": password, "tenant_id": str(T1)}
    ).status_code


def test_another_clients_failures_do_not_lock_the_user_out(h: Harness) -> None:
    """T-0068: an attacker who knows the address cannot keep its owner out."""
    for _ in range(6):
        assert _login_from(h, "203.0.113.66", password="wrong") == 401
    assert _login_from(h, "203.0.113.66") == 429  # the attacker's client is locked...
    assert _login(h) == 200  # ...the owner's is not
    assert _login_from(h, "203.0.113.66") == 429  # and the owner's success did not unlock it


def test_account_backstop_locks_across_clients(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rotating client addresses does not give unlimited guesses at one account."""
    monkeypatch.setattr(
        service_module, "LOGIN_ACCOUNT_POLICY", replace(LOGIN_ACCOUNT_POLICY, free_attempts=8)
    )
    for n in range(9):  # 8 free, the 9th arms the account lock; each from a new address
        assert _login_from(h, f"198.51.100.{n}", password="wrong") == 401
    assert _login_from(h, "198.51.100.200") == 429
    assert _login(h) == 429  # the backstop holds for every client
    h.clock.now += 30
    assert _login(h) == 200


def test_success_clears_the_client_and_the_account_counters(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        service_module, "LOGIN_ACCOUNT_POLICY", replace(LOGIN_ACCOUNT_POLICY, free_attempts=12)
    )
    for n in range(5):
        assert _login_from(h, f"198.51.100.{n}", password="wrong") == 401
    for _ in range(5):
        assert _login(h, password="wrong") == 401
    assert _login(h) == 200
    # Both counts restarted. Without that, this client would be past its 6 free tries and
    # the account past its 12; with it, 10 more failures (5 from this client) all run.
    for n in range(5):
        assert _login_from(h, f"192.0.2.{n}", password="wrong") == 401
    for _ in range(5):
        assert _login(h, password="wrong") == 401
    assert _login(h) == 200


def test_unknown_address_locks_like_a_real_one(h: Harness) -> None:
    for _ in range(6):
        assert _login(h, email="nobody@example.test", password="wrong") == 401
    assert _login(h, email="nobody@example.test", password="wrong") == 429


# --- Redis store, outage --------------------------------------------------------------------


def test_full_flow_on_redis(tmp_path: Path, db: Database, users: dict[str, uuid.UUID]) -> None:
    sender = CapturingSender()
    app, _ = auth_app(
        make_settings(database_url=database_url(tmp_path)), store=fake_store(), magic_links=sender
    )
    with TestClient(app) as client:
        client.post(MAGIC, json={"email": ALICE, "tenant_id": str(T1)})
        tokens = client.post(REDEEM, json={"token": sender.sent[0].token, "pin": PIN}).json()
        for _ in range(4):
            client.post(UNLOCK, json={"refresh_token": tokens["refresh_token"], "pin": WRONG})
        body = {"refresh_token": tokens["refresh_token"], "pin": PIN}
        assert client.post(UNLOCK, json=body).status_code == 429


def test_store_outage_is_503(tmp_path: Path, db: Database, users: dict[str, uuid.UUID]) -> None:
    app, runtime = auth_app(
        make_settings(database_url=database_url(tmp_path)),
        store=RedisRevocationStore(BrokenRedis()),
        magic_links=CapturingSender(),
    )
    refresh = runtime.tokens.issue(
        "refresh", user_id=users["alice"], tenant_id=T1, session_id=uuid.uuid4()
    )
    with TestClient(app) as client:
        assert client.post(MAGIC, json={"email": ALICE, "tenant_id": str(T1)}).status_code == 503
        assert client.post(REDEEM, json={"token": "x" * 43, "pin": PIN}).status_code == 503
        body = {"refresh_token": refresh.token, "pin": PIN}
        assert client.post(UNLOCK, json=body).status_code == 503


# --- delivery -------------------------------------------------------------------------------


def test_development_logs_the_link_and_production_never_does(
    caplog: pytest.LogCaptureFixture, users: dict[str, uuid.UUID]
) -> None:
    from datetime import UTC, datetime

    from app.core.auth.field import MagicLink

    link = MagicLink(
        token="tok-synthetic",
        email=ALICE,
        user_id=users["alice"],
        tenant_id=T1,
        expires_at=datetime.now(UTC),
    )
    dev = sender_from_settings(make_settings(environment="development"))
    prod = sender_from_settings(make_settings(environment="production"))
    assert isinstance(dev, LoggingMagicLinkSender)
    assert isinstance(prod, UndeliverableMagicLinkSender)
    with caplog.at_level(logging.INFO, logger="kerp.auth.field"):
        dev.send(link)
        assert "tok-synthetic" in caplog.text
        caplog.clear()
        prod.send(link)
    assert "tok-synthetic" not in caplog.text
    assert ALICE not in caplog.text
    assert "not delivered" in caplog.text
