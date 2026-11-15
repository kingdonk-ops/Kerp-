"""Field-worker auth over HTTP: magic link, the device PIN (hybrid), progressive lockout
(T-0009; reworked by T-0038).

Diff §4.2 *Field PIN, hybrid* (TD-135, decision 8a). The PIN belongs to the enrolled device:
it is set once at enrolment (its scrypt hash on the device row), kept by later sign-ins and
changed only from a session of the device. ``/auth/refresh`` rotates a session of such a
device normally while it was idle less than ``KERP_PIN_IDLE_MINUTES`` (default 30); after a
longer idle it answers ``PIN_REQUIRED`` and only ``/auth/pin/unlock`` (refresh token + PIN)
renews it. The PIN never authenticates on its own. Wrong PINs are counted per device (offline
failures reported by the device included); the 10th ends PIN unlock until a sign-in on the
device, without revoking anything. Runs on the real migration with a store clock and a token
clock the tests control.
"""

import logging
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.audit.service import chain_entries
from app.core.auth import devices
from app.core.auth import service as service_module
from app.core.auth.device_signal import DEVICE_HEADER, DEVICE_REVOKED_CODE
from app.core.auth.field import (
    PIN_EXHAUSTED,
    PIN_INVALID,
    PIN_REQUIRED,
    PIN_TTL_MARGIN,
    LoggingMagicLinkSender,
    UndeliverableMagicLinkSender,
    is_weak_pin,
    magic_link_digest,
    sender_from_settings,
)
from app.core.auth.lockout import LOGIN_ACCOUNT_POLICY, PIN_POLICY
from app.core.auth.models import DEVICE_ACTIVE
from app.core.auth.passwords import verify_password
from app.core.auth.revocation import (
    KEY_PREFIX,
    InMemoryRevocationStore,
    RedisRevocationStore,
    RevocationStore,
)
from app.core.auth.router import REFRESH_COOKIE
from app.core.auth.runtime import AuthRuntime, install_auth_runtime
from app.core.auth.service import AuthService
from app.core.auth.tokens import TokenService
from app.core.config import Settings
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.openapi import openapi_schema
from tests.audit_support import migrated_database
from tests.auth_support import (
    ALICE,
    BOB,
    ORIGIN,
    PASSWORD,
    T1,
    T2,
    CapturingSender,
    auth_app,
    cookie_headers,
    fake_store,
    make_settings,
    refresh,
    seed_user,
    token_body,
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
ENROL = "/api/v1/devices/enrol"
PIN = "482913"  # synthetic
WRONG = "771204"
OTHER_PIN = "905172"
CAROL = "carol@example.test"
SESSION = timedelta(hours=12)
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)


class TokenClock:
    """The token service's clock. PyJWT keeps checking real time, so a test signs in in the
    past (``Harness.sign_in_on(..., minutes_ago=...)``) and renews at the real now."""

    def __init__(self) -> None:
        self.real = datetime.now(UTC)
        self.now = self.real

    def __call__(self) -> datetime:
        return self.now


class Harness:
    def __init__(
        self,
        client: TestClient,
        runtime: AuthRuntime,
        sender: CapturingSender,
        clock: FakeClock,
        tokens_clock: TokenClock,
        db: Database,
    ) -> None:
        self.client = client
        self.runtime = runtime
        self.sender = sender
        self.clock = clock  # the store's: lockout delays and windows
        self.tokens_clock = tokens_clock  # the token service's: idle time, session end
        self.db = db

    def request_link(self, email: str = ALICE) -> str:
        before = len(self.sender.sent)
        response = self.client.post(MAGIC, json={"email": email})
        if response.status_code == 429:  # the per-address cap: wait it out
            self.clock.now += int(response.headers["retry-after"])
            response = self.client.post(MAGIC, json={"email": email})
        assert response.status_code == 202, response.text
        assert len(self.sender.sent) == before + 1
        return self.sender.sent[-1].token

    def redeem(self, token: str, device: uuid.UUID | None = None) -> dict[str, str]:
        headers = {DEVICE_HEADER: str(device)} if device is not None else {}
        return token_body(self.client.post(REDEEM, json={"token": token}, headers=headers))

    def bearer(self, tokens: dict[str, str]) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens['access_token']}"}

    def enrol(
        self, tokens: dict[str, str], device: uuid.UUID, pin: str | None = PIN
    ) -> httpx.Response:
        body: dict[str, Any] = {"device_id": str(device), "platform": "android"}
        if pin is not None:
            body["pin"] = pin
        return self.client.post(ENROL, json=body, headers=self.bearer(tokens))

    def field_device(
        self, pin: str | None = PIN, email: str = ALICE
    ) -> tuple[dict[str, str], uuid.UUID]:
        """First sign-in on a new device, then its enrolment (setting ``pin``): the session is
        bound by the enrolment. Returns that session's tokens and the device."""
        device = uuid.uuid4()
        tokens = self.redeem(self.request_link(email), device)
        assert self.enrol(tokens, device, pin).status_code == 201
        return tokens, device

    def sign_in_on(
        self, device: uuid.UUID, minutes_ago: float = 0, email: str = ALICE
    ) -> dict[str, str]:
        """A magic-link sign-in on an enrolled device, ``minutes_ago`` minutes ago (the
        session was idle since)."""
        token = self.request_link(email)
        self.tokens_clock.now = self.tokens_clock.real - timedelta(minutes=minutes_ago)
        try:
            return self.redeem(token, device)
        finally:
            self.tokens_clock.now = self.tokens_clock.real

    def unlock(
        self,
        refresh_token: str,
        pin: str = PIN,
        origin: str | None = ORIGIN,
        failed_offline: int | None = None,
    ) -> httpx.Response:
        headers = cookie_headers(refresh_token, origin)
        body: dict[str, Any] = {"pin": pin}
        if failed_offline is not None:
            body["failed_offline"] = failed_offline
        return self.client.post(UNLOCK, json=body, headers=headers)

    def session_id(self, refresh_token: str) -> uuid.UUID:
        return self.runtime.tokens.verify(refresh_token, "refresh").session_id

    def row_pin(self, device: uuid.UUID) -> tuple[str | None, datetime | None, str]:
        with self.db.unit_of_work(ROOT) as s:
            row = devices.find_device(s, device)
            assert row is not None
            return row.pin_hash, row.pin_set_at, row.status


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def users(db: Database) -> dict[str, uuid.UUID]:
    return {
        "alice": seed_user(db, T1, ALICE, display_name="Alice Example"),
        "carol": seed_user(db, T1, CAROL),
        "bob_t2": seed_user(db, T2, BOB),
        "inactive": seed_user(db, T1, "inactive@example.test", is_active=False),
    }


def _harness(
    tmp_path: Path, db: Database, store: RevocationStore | None = None, **settings: Any
) -> Iterator[Harness]:
    clock = FakeClock()
    sender = CapturingSender()
    config: Settings = make_settings(database_url=database_url(tmp_path), **settings)
    app, runtime = auth_app(
        config, store=store or InMemoryRevocationStore(clock=clock), magic_links=sender
    )
    tokens_clock = TokenClock()
    tokens = runtime.tokens
    clocked = TokenService(
        tokens.keys,
        issuer=tokens.issuer,
        audience=tokens.audience,
        access_ttl=tokens.ttl["access"],
        refresh_ttl=tokens.ttl["refresh"],
        clock=tokens_clock,
    )
    runtime = replace(runtime, tokens=clocked)
    install_auth_runtime(app, runtime)
    with TestClient(app) as client:
        yield Harness(client, runtime, sender, clock, tokens_clock, db)


@pytest.fixture
def h(tmp_path: Path, db: Database, users: dict[str, uuid.UUID]) -> Iterator[Harness]:
    yield from _harness(tmp_path, db)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _code(response: httpx.Response) -> str | None:
    assert response.status_code == 401, response.text
    code: str | None = response.json().get("code")
    return code


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
    again = h.client.post(REDEEM, json={"token": token})
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
        {"email": "nobody@example.test"},
        {"email": "inactive@example.test"},
        {"email": BOB},  # Bob lives in T2
    ],
)
def test_magic_link_request_is_202_and_silent_for_anyone_else(
    h: Harness, body: dict[str, str]
) -> None:
    response = h.client.post(MAGIC, json=body)
    assert response.status_code == 202
    assert h.sender.sent == []


def test_magic_link_uses_the_deployments_tenant(h: Harness, users: dict[str, uuid.UUID]) -> None:
    assert h.client.post(MAGIC, json={"email": ALICE.upper()}).status_code == 202
    assert [link.user_id for link in h.sender.sent] == [users["alice"]]
    # No tenant field (diff §4.1): sending one is a 422 and sends nothing.
    named = h.client.post(MAGIC, json={"email": ALICE, "tenant_id": str(T1)})
    assert named.status_code == 422
    assert len(h.sender.sent) == 1


def test_magic_link_without_a_deployment_tenant_sends_nothing(
    tmp_path: Path, db: Database, users: dict[str, uuid.UUID]
) -> None:
    sender = CapturingSender()
    app, _ = auth_app(
        make_settings(database_url=database_url(tmp_path), default_tenant_id=None),
        magic_links=sender,
    )
    with TestClient(app) as client:
        assert client.post(MAGIC, json={"email": ALICE}).status_code == 202
    assert sender.sent == []


def test_magic_link_expires(h: Harness) -> None:
    token = h.request_link()
    h.clock.now += 15 * 60 + 1
    assert h.client.post(REDEEM, json={"token": token}).status_code == 401


def test_garbage_magic_link_is_401(h: Harness) -> None:
    response = h.client.post(REDEEM, json={"token": "x" * 43})
    assert response.status_code == 401


def test_magic_link_of_a_user_deactivated_meanwhile_is_401(h: Harness, db: Database) -> None:
    token = h.request_link()
    from app.core.auth.models import User
    from app.core.tenancy.context import TenancyContext

    with db.unit_of_work(TenancyContext(tenant_id=T1)) as session:
        for user in session.query(User).filter(User.email == ALICE):
            user.is_active = False
    assert h.client.post(REDEEM, json={"token": token}).status_code == 401


def test_magic_link_requests_are_throttled_per_address(h: Harness) -> None:
    for _ in range(4):  # 3 free, the 4th is sent but arms a 1-minute lock
        h.request_link()
    locked = h.client.post(MAGIC, json={"email": ALICE})
    assert locked.status_code == 429
    assert locked.headers["retry-after"] == "60"
    # An unknown address is throttled the same way (no enumeration through the 429).
    for _ in range(4):
        h.client.post(MAGIC, json={"email": "nobody@example.test"})
    unknown = h.client.post(MAGIC, json={"email": "nobody@example.test"})
    assert unknown.status_code == 429
    h.clock.now += 60
    h.request_link()


# --- the PIN belongs to the device (T-0038) -------------------------------------------------


def test_pin_is_set_at_enrolment_on_the_device_record(h: Harness) -> None:
    _tokens, device = h.field_device()
    pin_hash, pin_set_at, status = h.row_pin(device)
    assert pin_hash is not None
    assert PIN not in pin_hash
    assert verify_password(PIN, pin_hash)  # scrypt, as passwords are
    assert pin_set_at is not None
    assert status == DEVICE_ACTIVE
    listed = h.client.get("/api/v1/devices", headers=h.bearer(_tokens)).json()
    assert listed[0]["pin_set_at"] is not None
    assert "pin_hash" not in listed[0]


def test_nothing_about_the_pin_is_kept_per_session_in_the_store(
    tmp_path: Path, db: Database, users: dict[str, uuid.UUID]
) -> None:
    redis = fakeredis.FakeRedis()
    for harness in _harness(tmp_path, db, store=RedisRevocationStore(redis)):
        harness.field_device()
        keys = [k.decode() for k in redis.keys(f"{KEY_PREFIX}*")]
        assert keys  # the session's refresh token and binding are there ...
        assert not [k for k in keys if k.startswith(f"{KEY_PREFIX}pin:")]  # ... no PIN


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
def test_malformed_or_weak_pin_is_refused_at_enrolment(h: Harness, pin: str) -> None:
    tokens = h.redeem(h.request_link())
    device = uuid.uuid4()
    assert h.enrol(tokens, device, pin).status_code == 422
    assert h.client.get("/api/v1/devices", headers=h.bearer(tokens)).json() == []
    assert h.enrol(tokens, device).status_code == 201


def test_weak_pin_rule() -> None:
    assert is_weak_pin("111111")
    assert is_weak_pin("345678")
    assert is_weak_pin("876543")
    assert not is_weak_pin("482913")
    assert not is_weak_pin("135790")


def test_magic_link_redeem_takes_no_pin(h: Harness) -> None:
    token = h.request_link()
    assert h.client.post(REDEEM, json={"token": token, "pin": PIN}).status_code == 422
    h.redeem(token)  # the link was not spent by the rejected request


@pytest.mark.parametrize("sign_in", ["magic_link", "password"])
def test_a_sign_in_on_an_enrolled_device_keeps_its_pin(h: Harness, sign_in: str) -> None:
    _first, device = h.field_device()
    before = h.row_pin(device)
    if sign_in == "magic_link":
        tokens = h.sign_in_on(device)
    else:
        body = {"email": ALICE, "password": PASSWORD}
        tokens = token_body(h.client.post(LOGIN, json=body, headers={DEVICE_HEADER: str(device)}))
    assert h.row_pin(device) == before
    # The new session is bound to the device at once (store and the ``did`` claim) ...
    claims = h.runtime.tokens.verify(tokens["refresh_token"], "refresh")
    assert claims.device_id == device
    assert h.runtime.store.session_device(claims.session_id) == device
    # ... and the device's PIN renews it.
    assert h.unlock(tokens["refresh_token"]).status_code == 200


def test_a_sign_in_naming_an_unknown_or_foreign_device_binds_nothing(
    h: Harness, users: dict[str, uuid.UUID]
) -> None:
    _tokens, carols = h.field_device(email=CAROL)
    for device in (uuid.uuid4(), carols):
        tokens = h.sign_in_on(device)
        claims = h.runtime.tokens.verify(tokens["refresh_token"], "refresh")
        assert claims.device_id is None
        assert h.runtime.store.session_device(claims.session_id) is None
        assert _code(h.unlock(tokens["refresh_token"])) is None  # not a PIN session


def test_enrolment_never_replaces_an_existing_pin(h: Harness) -> None:
    tokens, device = h.field_device()
    before = h.row_pin(device)
    again = h.sign_in_on(device)
    conflict = h.enrol(again, device, OTHER_PIN)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "PIN_ALREADY_SET"
    assert h.enrol(again, device, pin=None).status_code == 200
    assert h.row_pin(device) == before
    assert h.unlock(tokens["refresh_token"]).status_code == 200


def test_enrolment_can_set_the_pin_of_a_device_that_has_none(h: Harness) -> None:
    tokens, device = h.field_device(pin=None)
    assert h.row_pin(device)[0] is None
    assert h.enrol(tokens, device, PIN).status_code == 200
    assert verify_password(PIN, h.row_pin(device)[0] or "")


def test_the_pin_is_changed_from_a_session_of_the_device(
    h: Harness, users: dict[str, uuid.UUID]
) -> None:
    tokens, device = h.field_device()
    url = f"/api/v1/devices/{device}/pin"
    # Not from another session of the same user (not bound to the device) ...
    elsewhere = h.redeem(h.request_link())
    assert (
        h.client.put(url, json={"pin": OTHER_PIN}, headers=h.bearer(elsewhere)).status_code == 403
    )
    # ... not for another user's device, an unknown device or with a weak PIN.
    carol, _ = h.field_device(email=CAROL)
    assert h.client.put(url, json={"pin": OTHER_PIN}, headers=h.bearer(carol)).status_code == 404
    unknown = f"/api/v1/devices/{uuid.uuid4()}/pin"
    assert (
        h.client.put(unknown, json={"pin": OTHER_PIN}, headers=h.bearer(tokens)).status_code == 404
    )
    assert h.client.put(url, json={"pin": "111111"}, headers=h.bearer(tokens)).status_code == 422
    # After a sign-in on the device, the old PIN is not needed.
    signed_in = h.sign_in_on(device)
    assert (
        h.client.put(url, json={"pin": OTHER_PIN}, headers=h.bearer(signed_in)).status_code == 204
    )
    assert _code(h.unlock(signed_in["refresh_token"], PIN)) == PIN_INVALID
    assert h.unlock(signed_in["refresh_token"], OTHER_PIN).status_code == 200
    with h.db.unit_of_work(ROOT) as s:
        entries = [e.payload for e in chain_entries(s, f"device:{device}")]
    assert [e["action"] for e in entries] == ["enrol", "pin_set", "pin_changed"]
    for entry in entries:
        assert PIN not in str(entry)
        assert OTHER_PIN not in str(entry)


# --- renewal: plain refresh inside the idle limit, PIN after it -----------------------------


def test_default_idle_limit_is_30_minutes() -> None:
    assert make_settings().pin_idle_minutes == 30


@pytest.mark.parametrize("value", [0, 24 * 60 + 1])
def test_idle_limit_is_bounded(value: int) -> None:
    with pytest.raises(ValueError, match="pin_idle_minutes"):
        make_settings(pin_idle_minutes=value)


def test_refresh_inside_the_idle_limit_rotates_without_the_pin(h: Harness) -> None:
    _tokens, device = h.field_device()
    tokens = h.sign_in_on(device, minutes_ago=29)
    rotated = token_body(refresh(h.client, tokens["refresh_token"]))
    assert h.session_id(rotated["refresh_token"]) == h.session_id(tokens["refresh_token"])
    # The rotated token keeps the device (``did``) and rotates again at once.
    assert h.runtime.tokens.verify(rotated["refresh_token"], "refresh").device_id == device
    assert refresh(h.client, rotated["refresh_token"]).status_code == 200


def test_refresh_after_the_idle_limit_needs_the_pin(h: Harness) -> None:
    _tokens, device = h.field_device()
    tokens = h.sign_in_on(device, minutes_ago=31)
    response = refresh(h.client, tokens["refresh_token"])
    assert response.status_code == 401
    assert response.json() == {"detail": "PIN required", "code": PIN_REQUIRED}
    # The token was not spent: the device unlocks with it and the PIN ...
    unlocked = token_body(h.unlock(tokens["refresh_token"]))
    assert h.session_id(unlocked["refresh_token"]) == h.session_id(tokens["refresh_token"])
    assert h.client.get(ME, headers=_bearer(unlocked["access_token"])).status_code == 200
    # ... and the renewed session rotates normally again.
    assert refresh(h.client, unlocked["refresh_token"]).status_code == 200


def test_idle_is_counted_from_the_last_renewal(h: Harness) -> None:
    _tokens, device = h.field_device()
    first = h.sign_in_on(device, minutes_ago=50)
    h.tokens_clock.now = h.tokens_clock.real - timedelta(minutes=25)
    try:
        renewed = token_body(h.unlock(first["refresh_token"]))  # renewed 25 minutes ago
    finally:
        h.tokens_clock.now = h.tokens_clock.real
    assert refresh(h.client, renewed["refresh_token"]).status_code == 200


def test_a_device_without_a_pin_never_gates_its_sessions(h: Harness) -> None:
    _tokens, device = h.field_device(pin=None)
    tokens = h.sign_in_on(device, minutes_ago=120)
    assert refresh(h.client, tokens["refresh_token"]).status_code == 200


def test_a_session_without_a_device_rotates_after_any_idle(h: Harness) -> None:
    h.field_device()  # Alice has a device with a PIN ...
    token = h.request_link()
    h.tokens_clock.now = h.tokens_clock.real - timedelta(hours=3)
    tokens = h.redeem(token)  # ... but this sign-in is not on it
    h.tokens_clock.now = h.tokens_clock.real
    assert refresh(h.client, tokens["refresh_token"]).status_code == 200


def _access_for(h: Harness, refresh_token: str) -> dict[str, str]:
    """A live access token of the refresh token's session, issued now (the session's own
    access token from a sign-in in the past has expired by PyJWT's real clock)."""
    claims = h.runtime.tokens.verify(refresh_token, "refresh")
    access = h.runtime.tokens.issue(
        "access",
        user_id=claims.user_id,
        tenant_id=claims.tenant_id,
        session_id=claims.session_id,
        device_id=claims.device_id,
    )
    return {"access_token": access.token}


def test_idle_limit_must_cover_an_access_token() -> None:
    with pytest.raises(ValueError, match="pin_idle_minutes"):
        make_settings(access_token_minutes=15, pin_idle_minutes=10)
    assert make_settings(access_token_minutes=10, pin_idle_minutes=10).pin_idle_minutes == 10


def test_idle_limit_follows_the_setting(
    tmp_path: Path, db: Database, users: dict[str, uuid.UUID]
) -> None:
    for harness in _harness(tmp_path, db, access_token_minutes=5, pin_idle_minutes=5):
        _tokens, device = harness.field_device()
        assert (
            refresh(harness.client, harness.sign_in_on(device, 4)["refresh_token"]).status_code
            == 200
        )
        assert _code(refresh(harness.client, harness.sign_in_on(device, 6)["refresh_token"])) == (
            PIN_REQUIRED
        )


def test_a_session_bound_by_enrolment_is_gated_through_the_store_binding(h: Harness) -> None:
    token = h.request_link()
    h.tokens_clock.now = h.tokens_clock.real - timedelta(minutes=31)
    tokens = h.redeem(token)  # a first sign-in, before the device exists
    h.tokens_clock.now = h.tokens_clock.real
    assert h.enrol(_access_for(h, tokens["refresh_token"]), uuid.uuid4()).status_code == 201
    claims = h.runtime.tokens.verify(tokens["refresh_token"], "refresh")
    assert claims.device_id is None  # bound after the token was issued: the store binding
    assert _code(refresh(h.client, tokens["refresh_token"])) == PIN_REQUIRED
    assert h.unlock(tokens["refresh_token"]).status_code == 200


def test_pin_gate_does_not_depend_on_the_stores_binding(
    tmp_path: Path, db: Database, users: dict[str, uuid.UUID]
) -> None:
    """TD-142: losing the store's session binding (eviction) never opens the PIN gate."""
    redis = fakeredis.FakeRedis()
    for harness in _harness(tmp_path, db, store=RedisRevocationStore(redis)):
        _tokens, device = harness.field_device()
        tokens = harness.sign_in_on(device, minutes_ago=31)
        session_id = harness.session_id(tokens["refresh_token"])
        redis.delete(f"{KEY_PREFIX}device:sid:{session_id}")
        assert harness.runtime.store.session_device(session_id) is None
        assert _code(refresh(harness.client, tokens["refresh_token"])) == PIN_REQUIRED
        assert harness.unlock(tokens["refresh_token"]).status_code == 200


# --- the PIN never authenticates alone ------------------------------------------------------


def test_pin_without_a_refresh_token_is_rejected(h: Harness) -> None:
    tokens, _device = h.field_device()
    assert h.client.post(UNLOCK, json={"pin": PIN}, headers={"Origin": ORIGIN}).status_code == 401
    # The refresh token travels only in the cookie, never in a body.
    body = {"pin": PIN, "refresh_token": tokens["refresh_token"]}
    assert h.client.post(UNLOCK, json=body, headers={"Origin": ORIGIN}).status_code == 401
    with_cookie = cookie_headers(tokens["refresh_token"])
    assert h.client.post(UNLOCK, json=body, headers=with_cookie).status_code == 422
    assert h.unlock("not.a.token").status_code == 401


@pytest.mark.parametrize("origin", [None, "https://evil.example"])
def test_unlock_rejects_a_cross_origin_request(h: Harness, origin: str | None) -> None:
    tokens, _device = h.field_device()
    assert h.unlock(tokens["refresh_token"], origin=origin).status_code == 403
    assert h.unlock(tokens["refresh_token"]).status_code == 200


def test_pin_cannot_unlock_with_an_access_token(h: Harness) -> None:
    tokens, _device = h.field_device()
    assert h.unlock(tokens["access_token"]).status_code == 401


def test_pin_cannot_unlock_a_session_without_a_device(h: Harness) -> None:
    h.field_device()  # Alice has a device PIN ...
    login = h.client.post(LOGIN, json={"email": ALICE, "password": PASSWORD})
    refresh_token = token_body(login)["refresh_token"]
    # ... but it does not open a session that is not on that device.
    response = h.unlock(refresh_token)
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert refresh(h.client, refresh_token).status_code == 200


def test_pin_belongs_to_its_own_device(h: Harness) -> None:
    first, _ = h.field_device()
    second, _ = h.field_device(pin=OTHER_PIN)
    assert _code(h.unlock(first["refresh_token"], OTHER_PIN)) == PIN_INVALID
    assert h.unlock(second["refresh_token"], OTHER_PIN).status_code == 200


def test_replayed_refresh_token_on_unlock_revokes_the_session(h: Harness) -> None:
    tokens, _device = h.field_device()
    new = token_body(h.unlock(tokens["refresh_token"]))
    assert h.unlock(tokens["refresh_token"]).status_code == 401
    assert h.unlock(new["refresh_token"]).status_code == 401


def test_logged_out_field_session_cannot_be_unlocked(h: Harness) -> None:
    tokens, _device = h.field_device()
    assert h.client.post(LOGOUT, headers=_bearer(tokens["access_token"])).status_code == 204
    assert h.unlock(tokens["refresh_token"]).status_code == 401


def test_no_endpoint_accepts_a_pin_without_a_session_credential() -> None:
    """Contract (T-0009, kept by T-0038): every request with a ``pin`` also carries a
    credential only an existing session has: the refresh token (cookie, or the native app's
    body) for the unlock, a bearer token for enrolment and PIN change. No sign-in takes one."""
    spec = openapi_schema()
    schemas = spec["components"]["schemas"]
    with_pin = {name for name, s in schemas.items() if "pin" in s.get("properties", {})}
    assert with_pin == {"PinUnlockRequest", "DeviceEnrolRequest", "DevicePinRequest"}
    assert schemas["PinUnlockRequest"]["required"] == ["pin"]
    unlock = spec["paths"][UNLOCK]["post"]
    assert {"in": "cookie", "name": REFRESH_COOKIE} in [
        {"in": p["in"], "name": p["name"]} for p in unlock["parameters"]
    ]
    for path, method in ((ENROL, "post"), ("/api/v1/devices/{device_id}/pin", "put")):
        assert spec["paths"][path][method].get("security"), path
    for sign_in in (LOGIN, REDEEM, MAGIC):
        ref = spec["paths"][sign_in]["post"]["requestBody"]["content"]["application/json"]
        name = ref["schema"]["$ref"].rsplit("/", 1)[-1]
        assert "pin" not in schemas[name]["properties"], sign_in


# --- progressive lockout per device ---------------------------------------------------------


def test_wrong_pins_escalate_the_lock(h: Harness) -> None:
    tokens, _device = h.field_device()
    refresh_token = tokens["refresh_token"]
    for _ in range(3):  # the free attempts
        assert _code(h.unlock(refresh_token, WRONG)) == PIN_INVALID
    assert h.unlock(refresh_token, WRONG).json() == {"detail": "Wrong PIN", "code": PIN_INVALID}
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
    tokens, _device = h.field_device()
    for _ in range(3):
        h.unlock(tokens["refresh_token"], WRONG)
    new = token_body(h.unlock(tokens["refresh_token"]))
    for _ in range(3):  # three free attempts again
        assert h.unlock(new["refresh_token"], WRONG).status_code == 401
    assert h.unlock(new["refresh_token"]).status_code == 200


def test_tenth_wrong_pin_ends_pin_unlock_until_a_sign_in_on_the_device(h: Harness) -> None:
    tokens, device = h.field_device()
    same_device = h.sign_in_on(device)  # a second session on the device, signed in earlier
    session_id = h.session_id(tokens["refresh_token"])
    _wrong_pins(h, tokens["refresh_token"], 9)
    h.clock.now += 15 * 60
    tenth = h.unlock(tokens["refresh_token"], WRONG)
    assert tenth.json() == {"detail": "PIN unlock ended: sign in again", "code": PIN_EXHAUSTED}
    # Even the right PIN, once any lock has passed: PIN unlock has ended on this device.
    h.clock.now += 3600
    assert _code(h.unlock(tokens["refresh_token"])) == PIN_EXHAUSTED
    # Nothing was revoked: not the session, not the device; nothing says "wipe".
    assert not h.runtime.store.is_revoked("any", session_id)
    assert not h.runtime.store.revoked_by_device(session_id)
    assert h.client.get(ME, headers=_bearer(tokens["access_token"])).status_code == 200
    assert h.row_pin(device)[2] == DEVICE_ACTIVE
    # The device's other session is stopped too (the counter is the device's) ...
    assert _code(h.unlock(same_device["refresh_token"])) == PIN_EXHAUSTED
    # ... until a new sign-in on the device, which keeps the PIN and clears the count.
    signed_in = h.sign_in_on(device)
    assert h.unlock(signed_in["refresh_token"]).status_code == 200
    for _ in range(3):
        assert _code(h.unlock(tokens["refresh_token"], WRONG)) == PIN_INVALID


def _exhaust(h: Harness, refresh_token: str) -> None:
    """Ten wrong PINs on the session's device (the 10th answers ``PIN_EXHAUSTED``)."""
    _wrong_pins(h, refresh_token, 9)
    h.clock.now += 15 * 60
    assert _code(h.unlock(refresh_token, WRONG)) == PIN_EXHAUSTED


def _evict_binding(h: Harness, refresh_token: str) -> None:
    """Drop the session's ``device:sid`` binding as an evicting Redis would. The in-memory
    store, because its clock (unlike fakeredis's) lets the lockout delays pass."""
    store = h.runtime.store
    assert isinstance(store, InMemoryRevocationStore)
    session_id = h.session_id(refresh_token)
    store._entries.pop(f"{KEY_PREFIX}device:sid:{session_id}")
    assert store.session_device(session_id) is None


@pytest.mark.parametrize("evicted", [False, True])
def test_re_enrolling_from_a_session_bound_at_sign_in_does_not_clear_the_stop(
    h: Harness, evicted: bool
) -> None:
    _first, device = h.field_device()
    tokens = h.sign_in_on(device)  # bound at sign-in: its tokens carry ``did``
    _exhaust(h, tokens["refresh_token"])
    if evicted:
        _evict_binding(h, tokens["refresh_token"])
    assert h.enrol(tokens, device, pin=None).status_code == 200  # not a new binding
    assert _code(h.unlock(tokens["refresh_token"])) == PIN_EXHAUSTED


@pytest.mark.parametrize("evicted", [False, True])
def test_re_enrolling_from_a_session_bound_by_enrolment_does_not_clear_the_stop(
    h: Harness, evicted: bool
) -> None:
    tokens, device = h.field_device()  # bound by its enrolment (no ``did`` yet)
    if evicted:
        # After a rotation the session's tokens carry ``did``: an evicted binding then
        # changes nothing. (Before its first rotation such a session relies on the store
        # binding alone: the residual risk tracked in TD-303.)
        tokens = token_body(refresh(h.client, tokens["refresh_token"]))
    _exhaust(h, tokens["refresh_token"])
    if evicted:
        _evict_binding(h, tokens["refresh_token"])
    assert h.enrol(tokens, device, pin=None).status_code == 200
    assert _code(h.unlock(tokens["refresh_token"])) == PIN_EXHAUSTED


def test_a_new_sessions_first_binding_by_enrolment_clears_the_stop(h: Harness) -> None:
    first, device = h.field_device()
    _exhaust(h, first["refresh_token"])
    fresh = h.redeem(h.request_link())  # signed in without naming the device: unbound
    assert _code(h.unlock(fresh["refresh_token"])) is None  # not a session of the device yet
    assert h.enrol(fresh, device, pin=None).status_code == 200  # its first binding
    assert h.unlock(fresh["refresh_token"]).status_code == 200


def test_a_revoked_device_is_refused_through_the_did_claim_without_the_binding(
    h: Harness, users: dict[str, uuid.UUID]
) -> None:
    """TD-142: the ``did`` claim alone still answers ``DEVICE_REVOKED`` (the revoke committed,
    but neither the store binding nor the session kill reached Redis)."""
    _first, device = h.field_device()
    tokens = h.sign_in_on(device)
    _evict_binding(h, tokens["refresh_token"])
    with h.db.unit_of_work(ROOT) as s:
        row = devices.find_device(s, device)
        assert row is not None
        assert devices.mark_revoked(s, row, actor_id=users["alice"])
    for response in (
        refresh(h.client, tokens["refresh_token"]),
        h.unlock(tokens["refresh_token"]),
    ):
        assert _code(response) == DEVICE_REVOKED_CODE


def test_right_pin_after_nine_wrong_ones_still_unlocks(h: Harness) -> None:
    tokens, _device = h.field_device()
    _wrong_pins(h, tokens["refresh_token"], 9)
    h.clock.now += 15 * 60  # wait out the lock armed by the 9th failure (the 15-min cap)
    assert h.unlock(tokens["refresh_token"]).status_code == 200


def test_pin_lockout_is_per_device_not_per_session(h: Harness) -> None:
    first, device = h.field_device()
    same_device = h.sign_in_on(device)
    other_device, _ = h.field_device()
    _wrong_pins(h, first["refresh_token"], 3)
    # The failures of one session count for every session of the device ...
    _wrong_pins(h, same_device["refresh_token"], 1)  # failure 4 arms the lock
    assert h.unlock(first["refresh_token"]).status_code == 429
    assert h.unlock(same_device["refresh_token"]).status_code == 429
    # ... and another device is unaffected.
    assert h.unlock(other_device["refresh_token"]).status_code == 200


def test_pin_counter_lives_longer_than_a_session(h: Harness) -> None:
    window = h.runtime.tokens.ttl["refresh"] + PIN_TTL_MARGIN
    assert AuthService(h.runtime).pin_lockout.policy.window == window
    _first, device = h.field_device()
    tokens = h.sign_in_on(device)  # bound by the ``did`` claim, whatever the store keeps
    _wrong_pins(h, tokens["refresh_token"], 1)
    h.clock.now += h.runtime.tokens.ttl["refresh"].total_seconds()
    # Still counting: failures 2-4 run back to back only if failure 1 was kept; failure 4
    # then arms the first lock.
    assert _wrong_pins(h, tokens["refresh_token"], 3) == []
    assert h.unlock(tokens["refresh_token"], WRONG).status_code == 429


def test_pin_numbers_are_unchanged() -> None:
    assert PIN_POLICY.free_attempts == 3  # 4 tries back to back
    assert PIN_POLICY.base_delay == timedelta(seconds=30)
    assert PIN_POLICY.max_delay == timedelta(minutes=15)
    assert PIN_POLICY.max_attempts == 10


# --- offline failures reported by the device ------------------------------------------------


def test_offline_failures_count_before_the_attempt(h: Harness) -> None:
    tokens, _device = h.field_device()
    # 3 offline + this wrong one = failure 4: it arms the first 30 s lock.
    assert _code(h.unlock(tokens["refresh_token"], WRONG, failed_offline=3)) == PIN_INVALID
    locked = h.unlock(tokens["refresh_token"])
    assert locked.status_code == 429
    assert locked.headers["retry-after"] == "30"


def test_right_pin_after_offline_failures_unlocks_and_clears(h: Harness) -> None:
    tokens, _device = h.field_device()
    new = token_body(h.unlock(tokens["refresh_token"], failed_offline=9))
    for _ in range(3):  # cleared: three free attempts again
        assert _code(h.unlock(new["refresh_token"], WRONG)) == PIN_INVALID


@pytest.mark.parametrize(("offline", "pin"), [(9, WRONG), (10, PIN), (400, PIN)])
def test_offline_failures_reach_the_tenth(h: Harness, offline: int, pin: str) -> None:
    tokens, device = h.field_device()
    assert _code(h.unlock(tokens["refresh_token"], pin, failed_offline=offline)) == PIN_EXHAUSTED
    assert h.row_pin(device)[2] == DEVICE_ACTIVE


def test_offline_failures_add_to_online_ones(h: Harness) -> None:
    tokens, _device = h.field_device()
    _wrong_pins(h, tokens["refresh_token"], 5)
    h.clock.now += 60
    # 5 online + 4 offline + this one = the 10th.
    response = h.unlock(tokens["refresh_token"], WRONG, failed_offline=4)
    assert _code(response) == PIN_EXHAUSTED


def test_offline_report_is_not_applied_while_locked(h: Harness) -> None:
    tokens, _device = h.field_device()
    _wrong_pins(h, tokens["refresh_token"], 4)  # failure 4 armed 30 s
    assert h.unlock(tokens["refresh_token"], failed_offline=5).status_code == 429
    h.clock.now += 30
    # Had the 5 been counted, this would be failure 10; it is failure 5 (lock 60 s).
    assert _code(h.unlock(tokens["refresh_token"], WRONG)) == PIN_INVALID
    assert h.unlock(tokens["refresh_token"]).headers["retry-after"] == "60"


@pytest.mark.parametrize("offline", [-1, 1001, "many"])
def test_malformed_offline_report_is_422(h: Harness, offline: object) -> None:
    tokens, _device = h.field_device()
    headers = cookie_headers(tokens["refresh_token"])
    body = {"pin": PIN, "failed_offline": offline}
    assert h.client.post(UNLOCK, json=body, headers=headers).status_code == 422


def test_pin_failures_never_revoke_or_wipe(h: Harness) -> None:
    tokens, device = h.field_device()
    answers = [h.unlock(tokens["refresh_token"], WRONG, failed_offline=8)]
    h.clock.now += 15 * 60
    answers += [h.unlock(tokens["refresh_token"], WRONG) for _ in range(3)]
    for answer in answers:
        assert answer.status_code in (401, 429)
        assert DEVICE_REVOKED_CODE not in answer.text
    assert h.row_pin(device)[2] == DEVICE_ACTIVE
    session_id = h.session_id(tokens["refresh_token"])
    assert not h.runtime.store.revoked_by_device(session_id)
    assert not h.runtime.store.is_revoked("any", session_id)


# --- session_expires_at on every token response ---------------------------------------------


def _expires(response: httpx.Response) -> datetime:
    assert response.status_code == 200, response.text
    return datetime.fromisoformat(response.json()["session_expires_at"])


def test_every_token_response_carries_the_session_end(h: Harness) -> None:
    _tokens, device = h.field_device()
    redeemed = h.client.post(
        REDEEM, json={"token": h.request_link()}, headers={DEVICE_HEADER: str(device)}
    )
    end = _expires(redeemed)
    assert end == h.tokens_clock.real + SESSION
    refresh_token = redeemed.cookies[REFRESH_COOKIE]
    assert end == h.runtime.tokens.verify(refresh_token, "refresh").expires_at
    # Constant across rotations and PIN unlocks.
    for renew in ("refresh", "unlock", "refresh", "unlock"):
        response = (
            refresh(h.client, refresh_token) if renew == "refresh" else h.unlock(refresh_token)
        )
        assert _expires(response) == end
        refresh_token = response.cookies[REFRESH_COOKIE]
    login = h.client.post(LOGIN, json={"email": ALICE, "password": PASSWORD})
    assert _expires(login) == h.tokens_clock.real + SESSION


def test_session_end_stays_the_sign_in_end_after_hours_of_rotation(h: Harness) -> None:
    _tokens, device = h.field_device()
    token = h.request_link()
    h.tokens_clock.now = h.tokens_clock.real - timedelta(hours=11)
    redeemed = h.client.post(REDEEM, json={"token": token}, headers={DEVICE_HEADER: str(device)})
    h.tokens_clock.now = h.tokens_clock.real
    end = _expires(redeemed)
    assert end == h.tokens_clock.real - timedelta(hours=11) + SESSION
    # Idle for 11 hours: the PIN renews the session, which still ends at sign-in + 12 h.
    unlocked = h.unlock(redeemed.cookies[REFRESH_COOKIE])
    assert _expires(unlocked) == end
    assert unlocked.json()["refresh_expires_in"] in (3599, 3600)


# --- progressive lockout on password login (TD-123) -----------------------------------------


def _login(h: Harness, email: str = ALICE, password: str = PASSWORD) -> int:
    response = h.client.post(LOGIN, json={"email": email, "password": password})
    return response.status_code


def test_password_login_locks_progressively(h: Harness) -> None:
    for _ in range(6):  # 5 free, the 6th arms the lock
        assert _login(h, password="wrong") == 401
    response = h.client.post(LOGIN, json={"email": ALICE, "password": PASSWORD})
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
    return other.post(LOGIN, json={"email": ALICE, "password": password}).status_code


def test_another_clients_failures_do_not_lock_the_user_out(h: Harness) -> None:
    """T-0068: an attacker who knows the address cannot keep its owner out."""
    for _ in range(6):
        assert _login_from(h, "203.0.113.66", password="wrong") == 401
    assert _login_from(h, "203.0.113.66") == 429  # the attacker's client is locked...
    assert _login(h) == 200  # ...the owner's is not
    assert _login_from(h, "203.0.113.66") == 429  # and the owner's success did not unlock it


def test_account_backstop_locks_across_clients(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
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
        client.post(MAGIC, json={"email": ALICE})
        tokens = token_body(client.post(REDEEM, json={"token": sender.sent[0].token}))
        enrol = {"device_id": str(uuid.uuid4()), "platform": "android", "pin": PIN}
        bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
        assert client.post(ENROL, json=enrol, headers=bearer).status_code == 201
        headers = cookie_headers(tokens["refresh_token"])
        for _ in range(4):
            client.post(UNLOCK, json={"pin": WRONG}, headers=headers)
        assert client.post(UNLOCK, json={"pin": PIN}, headers=headers).status_code == 429


def test_store_outage_is_503(tmp_path: Path, db: Database, users: dict[str, uuid.UUID]) -> None:
    app, runtime = auth_app(
        make_settings(database_url=database_url(tmp_path)),
        store=RedisRevocationStore(BrokenRedis()),
        magic_links=CapturingSender(),
    )
    minted = runtime.tokens.issue(
        "refresh", user_id=users["alice"], tenant_id=T1, session_id=uuid.uuid4()
    )
    with TestClient(app) as client:
        assert client.post(MAGIC, json={"email": ALICE}).status_code == 503
        assert client.post(REDEEM, json={"token": "x" * 43}).status_code == 503
        unlock = client.post(UNLOCK, json={"pin": PIN}, headers=cookie_headers(minted.token))
        assert unlock.status_code == 503


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
