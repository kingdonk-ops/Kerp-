"""Self-service password reset by e-mail (T-0077, diff §4.2).

Neutral ``202`` with equal work and no synchronous send on any path; a 256-bit, hash-only,
single-use, expiring token that a newer request supersedes; confirm enforces the policy,
revokes every session, invalidates magic links and other reset links, signs nobody in and
sends the notice; rate limits; audit without token, password or address. Synthetic users only.
"""

import ast
import base64
import logging
import threading
from collections.abc import Callable, Iterator, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.audit.service import chain_entries, verify_chain
from app.core.auth import password_reset as reset_module
from app.core.auth import post_auth, providers
from app.core.auth import service as auth_service
from app.core.auth.field import MagicLink, magic_link_digest
from app.core.auth.lockout import LockedOutError
from app.core.auth.models import Device, User
from app.core.auth.password_reset import (
    PASSWORD_RESET_AUDIT_CHAIN,
    PasswordResetJob,
    PasswordResetService,
    reset_digest,
)
from app.core.auth.passwords import hash_password, verify_password
from app.core.auth.post_auth import PostAuthOutcome
from app.core.auth.revocation import InMemoryRevocationStore
from app.core.auth.router import REFRESH_COOKIE
from app.core.auth.runtime import AuthRuntime
from app.core.db import Database
from app.core.mail import service as mail_service
from app.core.mail.models import MailSend
from app.core.mail.queue import QueuedMail, set_mail_enqueuer
from app.core.tenancy.context import TenancyContext
from tests.audit_support import migrated_database
from tests.auth_support import (
    PASSWORD,
    T1,
    T2,
    CapturingSender,
    auth_app,
    make_settings,
    refresh,
    seed_field_worker,
    seed_user,
    token_body,
)
from tests.db_support import database_url

WEB = "https://kerp.example.test"  # KERP_WEB_ORIGIN of the harness: mail links, cookie Origin
REQUEST = "/api/v1/auth/password-reset/request"
CONFIRM = "/api/v1/auth/password-reset/confirm"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
BACKEND = Path(__file__).resolve().parents[1]
SCOPE = TenancyContext(tenant_id=T1)

ACTIVE = "active.user@example.test"  # has a password
INVITEE = "new.invitee@example.test"  # active, no password yet
WORKER = "field.worker@example.test"  # PER-14, has a password
DEACTIVATED = "deactivated@example.test"
UNKNOWN = "unknown@example.test"  # no account
OTHER_TENANT = "other.tenant@example.test"  # an account, but in T2
GETS_A_LINK = (ACTIVE, INVITEE, WORKER)
NEUTRAL = (DEACTIVATED, UNKNOWN, OTHER_TENANT)
EVERY_PATH = (*GETS_A_LINK, *NEUTRAL)
NEW_PASSWORD = "plum-Radio 41 kettle"  # synthetic
OTHER_PASSWORD = "walnut 73 Lantern-sky"  # synthetic


class Spy:
    """Records the calls of interest in order, together with the response messages."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def mark(self) -> int:
        return len(self.events)

    def before_response(self, start: int) -> list[str]:
        window = self.events[start:]
        return window[: window.index("http.response.start")]

    def after_response(self, start: int) -> list[str]:
        window = self.events[start:]
        return window[window.index("http.response.body") + 1 :]


class SpyStore:
    """The revocation store, every call recorded."""

    def __init__(self, inner: InMemoryRevocationStore, spy: Spy) -> None:
        self._inner, self._spy = inner, spy

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            self._spy.events.append(f"store.{name}")
            return attr(*args, **kwargs)

        return call


class Outbox:
    """The mail enqueuer: what core hands to the queue after a commit."""

    def __init__(self, spy: Spy) -> None:
        self.mails: list[QueuedMail] = []
        self._spy = spy

    def __call__(self, mails: Sequence[QueuedMail]) -> None:
        self._spy.events.append("mail.enqueue")
        self.mails.extend(mails)

    def of(self, template: str) -> list[QueuedMail]:
        return [mail for mail in self.mails if mail.template_key == template]

    def token(self, index: int = -1) -> str:
        return str(self.of("auth.password_reset")[index].context["token"])


def _recording(app: Any, spy: Spy) -> Callable[..., Any]:
    """The app behind a thin ASGI layer that records when the response goes out."""

    async def wrapped(scope: Any, receive: Any, send: Any) -> None:
        async def recorded(message: Any) -> None:
            if scope["type"] == "http":
                spy.events.append(str(message["type"]))
            await send(message)

        await app(scope, receive, recorded)

    return wrapped


class Harness:
    def __init__(
        self,
        client: TestClient,
        runtime: AuthRuntime,
        outbox: Outbox,
        spy: Spy,
        db: Database,
        users: dict[str, Any],
        magic: CapturingSender,
        clock: list[float],
    ) -> None:
        self.client, self.runtime, self.outbox, self.spy = client, runtime, outbox, spy
        self.db, self.users, self.magic, self.clock = db, users, magic, clock

    def request(self, email: str) -> Any:
        return self.client.post(REQUEST, json={"email": email})

    def confirm(self, token: str, password: str = NEW_PASSWORD) -> Any:
        return self.client.post(CONFIRM, json={"token": token, "new_password": password})

    def link(self, email: str = ACTIVE) -> str:
        """Request a reset for ``email`` and return the token of the e-mailed link."""
        before = len(self.outbox.of("auth.password_reset"))
        assert self.request(email).status_code == 202
        assert len(self.outbox.of("auth.password_reset")) == before + 1
        return self.outbox.token()

    def login(self, email: str, password: str) -> Any:
        return self.client.post(LOGIN, json={"email": email, "password": password})

    def user(self, email: str) -> User:
        with self.db.unit_of_work(SCOPE) as session:
            return session.scalars(select(User).where(User.id == self.users[email])).one()

    def audit(self) -> list[dict[str, Any]]:
        with self.db.unit_of_work(SCOPE) as session:
            assert verify_chain(session, PASSWORD_RESET_AUDIT_CHAIN).ok
            return [dict(e.payload) for e in chain_entries(session, PASSWORD_RESET_AUDIT_CHAIN)]

    def advance(self, delta: timedelta) -> None:
        self.clock[0] += delta.total_seconds()


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def h(tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    users = {
        ACTIVE: seed_user(db, T1, ACTIVE),
        INVITEE: seed_user(db, T1, INVITEE, password=None),
        WORKER: seed_field_worker(db, T1, WORKER),
        DEACTIVATED: seed_user(db, T1, DEACTIVATED, is_active=False),
        OTHER_TENANT: seed_user(db, T2, OTHER_TENANT),
    }
    spy = Spy()
    clock = [1000.0]
    store = SpyStore(InMemoryRevocationStore(clock=lambda: clock[0]), spy)
    for name in ("new_reset_token", "reset_digest"):
        real = getattr(reset_module, name)

        def hashed(*args: Any, _real: Any = real, _name: str = name) -> Any:
            spy.events.append(f"hash.{_name}")
            return _real(*args)

        monkeypatch.setattr(reset_module, name, hashed)
    real_send_mail = mail_service.send_mail

    def staged(*args: Any, **kwargs: Any) -> Any:
        spy.events.append("mail.send_mail")
        return real_send_mail(*args, **kwargs)

    monkeypatch.setattr(mail_service, "send_mail", staged)
    outbox = Outbox(spy)
    set_mail_enqueuer(outbox)
    magic = CapturingSender()
    settings = make_settings(
        database_url=database_url(tmp_path), web_origin=WEB
    )
    app, runtime = auth_app(settings, store=store, magic_links=magic)
    try:
        with TestClient(_recording(app, spy)) as client:
            yield Harness(client, runtime, outbox, spy, db, users, magic, clock)
    finally:
        set_mail_enqueuer(None)


# --- the neutral request --------------------------------------------------------------------


def test_the_answer_is_identical_for_unknown_deactivated_and_active_accounts(h: Harness) -> None:
    volatile = {"date", "x-request-id"}

    def answer(email: str) -> tuple[int, bytes, dict[str, str]]:
        response = h.request(email)
        headers = {k: v for k, v in response.headers.items() if k.lower() not in volatile}
        return response.status_code, response.content, headers

    reference = answer(ACTIVE)
    assert reference[0] == 202
    for email in EVERY_PATH[1:]:
        assert answer(email) == reference, email
    assert "set-cookie" not in reference[2]


def test_every_path_does_the_same_work_before_the_response_and_never_sends(h: Harness) -> None:
    seen: dict[str, list[str]] = {}
    for email in EVERY_PATH:
        mark = h.spy.mark()
        assert h.request(email).status_code == 202
        seen[email] = h.spy.before_response(mark)
    # Only the two rate-limit counters (client, mailbox): no lookup-dependent store or hash
    # call, nothing staged or handed to the mail queue.
    assert set(map(tuple, seen.values())) == {("store.begin_attempt", "store.begin_attempt")}, seen


def test_the_link_is_issued_and_queued_only_after_the_response(h: Harness) -> None:
    for email in GETS_A_LINK:
        mark = h.spy.mark()
        h.request(email)
        assert h.spy.after_response(mark) == [
            "hash.new_reset_token",
            "hash.reset_digest",
            "store.peek_value",
            "store.put_value",
            "store.put_value",
            "mail.send_mail",
            "mail.enqueue",  # after the deferred unit of work committed
        ], email
    for email in NEUTRAL:
        mark = h.spy.mark()
        h.request(email)
        assert h.spy.after_response(mark) == [], email
    assert len(h.outbox.mails) == len(GETS_A_LINK)


def test_the_request_half_returns_the_same_job_for_every_address(h: Harness) -> None:
    service = PasswordResetService(h.runtime)
    for email in EVERY_PATH:
        mark = h.spy.mark()
        job = service.request(email=f"  {email.upper()} ", client="203.0.113.9")
        assert job == PasswordResetJob(tenant_id=T1, email=email)
        assert email not in repr(job)
        assert h.spy.events[mark:] == ["store.begin_attempt", "store.begin_attempt"]
    assert h.outbox.mails == []


def test_an_active_account_is_mailed_the_link_with_the_token_in_the_fragment(h: Harness) -> None:
    token = h.link(ACTIVE)
    [mail] = h.outbox.mails
    assert (mail.template_key, mail.to, mail.tenant_id) == ("auth.password_reset", ACTIVE, T1)
    assert mail.context == {"token": token, "expires_minutes": 60}
    # >= 256 random bits, URL-safe.
    assert len(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))) >= 32
    assert token != h.link(INVITEE)  # an account without a password gets a link too
    from app.core.mail.templates import TEMPLATES, render

    rendered = render(
        TEMPLATES.get("auth.password_reset"),
        dict(mail.context),
        locale=None,
        app_name="KERP",
        web_origin=WEB,
    )
    link = f"{WEB}/reset-password#{token}"
    assert link in rendered.text
    assert link in rendered.html
    assert rendered.text.count(token) == 1
    assert token not in rendered.subject
    assert "60 minutes" in rendered.text


def test_a_failure_after_the_response_is_logged_not_raised(
    h: Harness, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("synthetic mail failure")

    monkeypatch.setattr(mail_service, "send_mail", broken)
    with caplog.at_level(logging.ERROR, logger="kerp.auth.password_reset"):
        assert h.request(ACTIVE).status_code == 202
    assert "password-reset request not processed" in caplog.text
    assert ACTIVE not in caplog.text
    assert h.outbox.mails == []


def test_an_address_the_mail_service_refuses_is_skipped(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    odd = "odd name@example.test"  # a stored address that is not one plain mailbox
    with h.db.unit_of_work(SCOPE) as session:
        session.execute(update(User).where(User.id == h.users[ACTIVE]).values(email=odd))
    with caplog.at_level(logging.WARNING, logger="kerp.auth.password_reset"):
        assert h.request(odd).status_code == 202
    assert h.outbox.mails == []
    assert "not queued: invalid address" in caplog.text
    assert odd not in caplog.text


# --- the token --------------------------------------------------------------------------------


def test_only_a_hash_of_the_token_is_stored_anywhere(h: Harness) -> None:
    token = h.link(ACTIVE)
    inner = h.runtime.store._inner
    stored = {k: value for k, (_expiry, value) in inner._entries.items() if "auth:pwreset:" in k}
    assert len(stored) == 2
    digest = reset_digest(token)
    assert any(digest in key for key in stored)
    for key, value in stored.items():
        assert token not in key
        assert token.encode() not in value
    with h.db.unit_of_work(SCOPE) as session:
        for row in session.scalars(select(MailSend)):
            values = [str(getattr(row, c.key)) for c in MailSend.__table__.columns]
            assert not any(token in value or digest in value for value in values)
    assert token not in str(h.audit())
    assert digest not in str(h.audit())


def test_the_token_is_single_use(h: Harness) -> None:
    token = h.link(ACTIVE)
    assert h.confirm(token).status_code == 200
    again = h.confirm(token, OTHER_PASSWORD)
    assert again.status_code == 410
    assert h.login(ACTIVE, OTHER_PASSWORD).status_code == 401
    assert h.login(ACTIVE, NEW_PASSWORD).status_code == 200


def test_two_concurrent_confirms_one_wins_one_gets_410(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = h.link(ACTIVE)
    # Both requests have found the live token before either spends it.
    barrier = threading.Barrier(2, timeout=20)
    real_policy = reset_module.policy_from_settings

    def gated(*args: Any, **kwargs: Any) -> Any:
        barrier.wait()
        return real_policy(*args, **kwargs)

    monkeypatch.setattr(reset_module, "policy_from_settings", gated)
    passwords = (NEW_PASSWORD, OTHER_PASSWORD)
    results: dict[str, int] = {}

    def run(password: str) -> None:
        results[password] = h.confirm(token, password).status_code

    threads = [threading.Thread(target=run, args=(password,)) for password in passwords]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert sorted(results.values()) == [200, 410], results
    winner = next(password for password, status in results.items() if status == 200)
    loser = next(password for password in passwords if password != winner)
    assert verify_password(winner, h.user(ACTIVE).password_hash)
    assert h.login(ACTIVE, loser).status_code == 401
    assert len(h.outbox.of("auth.password_changed")) == 1
    actions = [entry["action"] for entry in h.audit()]
    assert actions.count("password_reset_confirmed") == 1


def test_the_token_expires_after_the_configured_lifetime(h: Harness) -> None:
    assert h.runtime.settings.password_reset_ttl_minutes == 60
    token = h.link(ACTIVE)
    h.advance(timedelta(minutes=59, seconds=59))
    assert h.runtime.store.peek_value(f"pwreset:token:{reset_digest(token)}") is not None
    h.advance(timedelta(seconds=2))
    expired = h.confirm(token)
    assert expired.status_code == 410
    assert h.login(ACTIVE, PASSWORD).status_code == 200  # nothing changed


def test_a_shorter_lifetime_from_the_settings_is_honoured(tmp_path: Path, db: Database) -> None:
    clock = [0.0]
    store = InMemoryRevocationStore(clock=lambda: clock[0])
    settings = make_settings(database_url=database_url(tmp_path), password_reset_ttl_minutes=5)
    _app, runtime = auth_app(settings, store=store)
    seed_user(db, T1, ACTIVE)
    service = PasswordResetService(runtime)
    assert service.ttl == timedelta(minutes=5)
    job = service.request(email=ACTIVE)
    assert job is not None
    tokens: list[str] = []
    service._queue_mail = lambda _s, _t, _to, context, _u: tokens.append(  # type: ignore[method-assign]
        str(context["token"])
    )
    service.issue(db, job)
    key = f"pwreset:token:{reset_digest(tokens[0])}"
    clock[0] += 299
    assert store.peek_value(key) is not None
    clock[0] += 2
    assert store.peek_value(key) is None


def test_a_new_request_invalidates_the_earlier_token(h: Harness) -> None:
    first = h.link(ACTIVE)
    second = h.link(ACTIVE)
    assert first != second
    assert h.confirm(first).status_code == 410
    assert h.login(ACTIVE, PASSWORD).status_code == 200  # still the old password
    assert h.confirm(second).status_code == 200


def test_a_token_whose_pointer_names_another_is_dead_even_if_its_entry_survives(
    h: Harness,
) -> None:
    # Two requests racing can leave the loser's entry behind; the pointer decides.
    first = h.link(ACTIVE)
    inner = h.runtime.store._inner
    saved = inner.peek_value(f"pwreset:token:{reset_digest(first)}")
    second = h.link(ACTIVE)
    inner.put_value(f"pwreset:token:{reset_digest(first)}", saved, timedelta(minutes=5))
    assert h.confirm(first).status_code == 410
    assert inner.peek_value(f"pwreset:token:{reset_digest(first)}") is None  # cleaned up
    assert h.confirm(second).status_code == 200


def test_every_dead_link_gets_the_same_410(h: Harness) -> None:
    def answer(response: Any) -> tuple[int, bytes, dict[str, str]]:
        volatile = {"date", "x-request-id"}
        headers = {k: v for k, v in response.headers.items() if k.lower() not in volatile}
        return response.status_code, response.content, headers

    used = h.link(ACTIVE)
    assert h.confirm(used).status_code == 200
    superseded = h.link(INVITEE)
    h.link(INVITEE)
    expired = h.link(WORKER)
    h.advance(timedelta(minutes=61))
    garbage = answer(h.confirm("x"))
    assert garbage[0] == 410
    assert garbage[1] == (
        b'{"detail":"This reset link is invalid or has expired","code":"RESET_LINK_INVALID"}'
    )
    assert garbage[2]["cache-control"] == "no-store"
    for token in (used, superseded, expired, "A" * 43, "z" * 512):
        assert answer(h.confirm(token)) == garbage
    # ... and whatever the password looks like: a dead link never reveals the policy.
    assert answer(h.confirm(used, "short")) == garbage


def test_a_corrupt_store_entry_is_a_dead_link(h: Harness) -> None:
    token = "t" * 43
    h.runtime.store._inner.put_value(
        f"pwreset:token:{reset_digest(token)}", b"not json", timedelta(minutes=5)
    )
    assert h.confirm(token).status_code == 410


def test_the_link_of_an_account_deactivated_meanwhile_is_dead(h: Harness) -> None:
    token = h.link(ACTIVE)
    with h.db.unit_of_work(SCOPE) as session:
        session.execute(update(User).where(User.id == h.users[ACTIVE]).values(is_active=False))
    assert h.confirm(token).status_code == 410
    assert h.runtime.store.peek_value(f"pwreset:token:{reset_digest(token)}") is None
    assert h.audit()[-1] == {
        "action": "password_reset_refused",
        "stage": "confirm",
        "reason": "account_inactive",
        "user_id": str(h.users[ACTIVE]),
    }


# --- confirm ----------------------------------------------------------------------------------


def test_confirm_sets_the_scrypt_hash_and_does_not_sign_in(h: Harness) -> None:
    token = h.link(ACTIVE)
    before = h.user(ACTIVE)
    response = h.confirm(token)
    assert response.status_code == 200
    assert response.json() == {"status": "password_changed"}
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert "access_token" not in response.text
    assert "refresh_token" not in response.text
    after = h.user(ACTIVE)
    assert after.password_hash is not None
    assert after.password_hash.startswith("scrypt$")
    assert after.password_hash != before.password_hash
    assert verify_password(NEW_PASSWORD, after.password_hash)
    assert after.password_changed_at is not None
    assert h.login(ACTIVE, PASSWORD).status_code == 401
    assert h.login(ACTIVE, NEW_PASSWORD).status_code == 200


def test_an_account_without_a_password_sets_its_first_password_through_reset(h: Harness) -> None:
    assert h.user(INVITEE).password_hash is None
    assert h.login(INVITEE, NEW_PASSWORD).status_code == 401
    assert h.confirm(h.link(INVITEE)).status_code == 200
    assert h.login(INVITEE, NEW_PASSWORD).status_code == 200
    assert h.audit()[-1] == {
        "action": "password_reset_confirmed",
        "user_id": str(h.users[INVITEE]),
        "first_password": True,
    }


def test_confirm_revokes_every_session_and_refresh_token_of_the_user(h: Harness) -> None:
    sessions = [token_body(h.login(ACTIVE, PASSWORD)) for _ in range(2)]  # two devices
    bystander = token_body(h.login(WORKER, PASSWORD))
    for tokens in sessions:
        bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
        assert h.client.get(ME, headers=bearer).status_code == 200
    assert h.confirm(h.link(ACTIVE)).status_code == 200
    for tokens in sessions:
        bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
        assert h.client.get(ME, headers=bearer).status_code == 401
        assert refresh(h.client, tokens["refresh_token"], WEB).status_code == 401  # old one refused
    # Another user's session is untouched.
    assert refresh(h.client, bystander["refresh_token"], WEB).status_code == 200
    # A sign-in after the reset works at once, even within the same second (T-0037 / T-0061).
    fresh = token_body(h.login(ACTIVE, NEW_PASSWORD))
    bearer = {"Authorization": f"Bearer {fresh['access_token']}"}
    assert h.client.get(ME, headers=bearer).status_code == 200
    assert refresh(h.client, fresh["refresh_token"], WEB).status_code == 200


def test_device_enrolment_and_pin_survive_and_pin_unlock_needs_a_new_sign_in(h: Harness) -> None:
    import uuid

    device_id = str(uuid.uuid4())
    tokens = token_body(h.login(WORKER, PASSWORD))
    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
    enrolled = h.client.post(
        "/api/v1/devices/enrol",
        headers=bearer,
        json={
            "device_id": device_id,
            "platform": "android",
            "os_version": "14",
            "app_version": "1.0.0",
            "pin": "285714",
        },
    )
    assert enrolled.status_code == 201, enrolled.text

    def device() -> tuple[str, str | None]:
        with h.db.unit_of_work(TenancyContext(tenant_id=T1, user_id=h.users[WORKER])) as s:
            row = s.scalars(select(Device).where(Device.device_id == uuid.UUID(device_id))).one()
            return row.status, row.pin_hash

    before = device()
    assert before[0] == "active"
    assert before[1] is not None
    assert h.confirm(h.link(WORKER)).status_code == 200
    assert device() == before  # enrolment and PIN kept
    # At its next server contact the old session is refused, PIN or not: sign in again.
    headers = {"Cookie": f"{REFRESH_COOKIE}={tokens['refresh_token']}", "Origin": WEB}
    unlock = h.client.post("/api/v1/auth/pin/unlock", headers=headers, json={"pin": "285714"})
    assert unlock.status_code == 401
    assert "code" not in unlock.json()  # not a device revoke: nothing is wiped
    again = h.client.post(
        LOGIN,
        json={"email": WORKER, "password": NEW_PASSWORD},
        headers={"X-KERP-Device-Id": device_id},
    )
    assert again.status_code == 200
    assert device() == before


def test_confirm_invalidates_the_users_outstanding_magic_links(h: Harness) -> None:
    redeem = "/api/v1/auth/magic-link/redeem"
    assert h.client.post("/api/v1/auth/magic-link", json={"email": WORKER}).status_code == 202
    [old] = h.magic.sent
    assert h.confirm(h.link(WORKER)).status_code == 200
    refused = h.client.post(redeem, json={"token": old.token})
    garbage = h.client.post(redeem, json={"token": "x" * 43})
    assert refused.status_code == 401
    assert (refused.json(), refused.headers["www-authenticate"]) == (
        garbage.json(),
        garbage.headers["www-authenticate"],
    )
    # A link asked for after the reset signs in.
    assert h.client.post("/api/v1/auth/magic-link", json={"email": WORKER}).status_code == 202
    new: MagicLink = h.magic.sent[-1]
    assert h.client.post(redeem, json={"token": new.token}).status_code == 200


def test_a_magic_link_stored_before_this_release_is_judged_by_its_latest_possible_age(
    h: Harness,
) -> None:
    import json

    redeem = "/api/v1/auth/magic-link/redeem"
    payload = json.dumps({"uid": str(h.users[WORKER]), "tid": str(T1)}).encode()  # no "iat"
    store = h.runtime.store
    first, second = "legacy-link-1-" + "a" * 30, "legacy-link-2-" + "a" * 30
    store.put_magic_link(magic_link_digest(first), payload, timedelta(minutes=15))
    # No password change at all: the link works as before.
    with h.db.unit_of_work(SCOPE) as session:
        session.execute(
            update(User).where(User.id == h.users[WORKER]).values(password_changed_at=None)
        )
    ok = h.client.post(redeem, json={"token": first})
    assert ok.status_code == 200
    store.put_magic_link(magic_link_digest(second), payload, timedelta(minutes=15))
    assert h.confirm(h.link(WORKER)).status_code == 200
    refused = h.client.post(redeem, json={"token": second})
    assert refused.status_code == 401


def test_confirm_sends_the_password_changed_notice_off_the_request_path(h: Harness) -> None:
    token = h.link(ACTIVE)
    mark = h.spy.mark()
    assert h.confirm(token).status_code == 200
    [notice] = h.outbox.of("auth.password_changed")
    assert (notice.to, notice.context) == (ACTIVE, {})
    # Staged in the request's unit of work, handed to the queue at its commit: the worker
    # sends it. Nothing reaches a transport from the request.
    window = h.spy.events[mark:]
    assert window.count("mail.send_mail") == 1
    assert window.count("mail.enqueue") == 1
    assert window.index("mail.send_mail") < window.index("mail.enqueue")
    with h.db.unit_of_work(SCOPE) as session:
        rows = session.scalars(select(MailSend).order_by(MailSend.created_at)).all()
        assert [(r.template_key, r.status) for r in rows] == [
            ("auth.password_reset", "queued"),
            ("auth.password_changed", "queued"),
        ]


def test_a_refused_password_keeps_the_link_and_says_why(h: Harness) -> None:
    token = h.link(ACTIVE)
    short = h.confirm(token, "xk7#Qp2")
    assert short.status_code == 422
    assert short.json() == {
        "detail": "Password is too short: use at least 8 characters",
        "code": "PASSWORD_TOO_SHORT",
        "min_length": 8,
        "max_length": 256,
    }
    assert short.headers["cache-control"] == "no-store"
    for bad in ("password123", ACTIVE):
        common = h.confirm(token, bad)
        assert (common.status_code, common.json()["code"]) == (422, "PASSWORD_TOO_COMMON"), bad
    long = h.confirm(token, "k7 Q" * 64 + "!")
    assert (long.status_code, long.json()["code"]) == (422, "PASSWORD_TOO_LONG")
    assert h.login(ACTIVE, PASSWORD).status_code == 200  # nothing changed, nobody signed out
    assert h.outbox.of("auth.password_changed") == []
    assert h.confirm(token, "xk7#Qp2m").status_code == 200  # 8 characters: accepted


def test_a_raised_minimum_length_is_enforced_by_confirm(tmp_path: Path, db: Database) -> None:
    outbox = Outbox(Spy())
    set_mail_enqueuer(outbox)
    try:
        seed_user(db, T1, ACTIVE)
        settings = make_settings(database_url=database_url(tmp_path), password_min_length=12)
        app, _ = auth_app(settings)
        with TestClient(app) as client:
            assert client.post(REQUEST, json={"email": ACTIVE}).status_code == 202
            token = outbox.token()
            refused = client.post(CONFIRM, json={"token": token, "new_password": "xk7#Qp2m-Lw"})
            assert refused.status_code == 422
            assert (refused.json()["code"], refused.json()["min_length"]) == (
                "PASSWORD_TOO_SHORT",
                12,
            )
            accepted = client.post(CONFIRM, json={"token": token, "new_password": "xk7#Qp2m-Lw9"})
            assert accepted.status_code == 200
    finally:
        set_mail_enqueuer(None)


def test_the_new_password_is_stored_nfkc_and_signs_in_in_either_form(h: Harness) -> None:
    import unicodedata

    composed = "café-Zürich 9 ok"
    decomposed = unicodedata.normalize("NFD", composed)
    assert decomposed != composed
    assert h.confirm(h.link(ACTIVE), decomposed).status_code == 200
    assert verify_password(composed, h.user(ACTIVE).password_hash)
    assert h.login(ACTIVE, composed).status_code == 200
    assert h.login(ACTIVE, decomposed).status_code == 200
    fullwidth = "".join(chr(ord(ch) + 0xFEE0) for ch in "caf") + "é-Zürich 9 ok"
    assert h.login(ACTIVE, fullwidth).status_code == 200  # typed in full-width forms


def test_a_password_stored_before_the_policy_still_signs_in_as_typed(h: Harness) -> None:
    import unicodedata

    legacy = unicodedata.normalize("NFD", "légacy-pässword 1")  # hashed as typed, pre-T-0077
    with h.db.unit_of_work(SCOPE) as session:
        session.execute(
            update(User)
            .where(User.id == h.users[ACTIVE])
            .values(password_hash=hash_password(legacy))
        )
    assert h.login(ACTIVE, legacy).status_code == 200
    assert h.login(ACTIVE, "wrong " + legacy).status_code == 401
    assert h.login(UNKNOWN, legacy).status_code == 401


def test_the_breach_lookup_is_used_only_when_switched_on(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.auth import password_policy

    looked_up: list[str] = []

    def lookup(password: str) -> bool:
        looked_up.append(password)
        return password == NEW_PASSWORD

    monkeypatch.setattr(password_policy, "breached_by_range_lookup", lookup)
    outbox = Outbox(Spy())
    set_mail_enqueuer(outbox)
    try:
        seed_user(db, T1, ACTIVE)
        settings = make_settings(database_url=database_url(tmp_path), password_breach_check=True)
        app, _ = auth_app(settings)
        with TestClient(app) as client:
            client.post(REQUEST, json={"email": ACTIVE})
            token = outbox.token()
            breached = client.post(CONFIRM, json={"token": token, "new_password": NEW_PASSWORD})
            assert (breached.status_code, breached.json()["code"]) == (422, "PASSWORD_TOO_COMMON")
            fine = client.post(CONFIRM, json={"token": token, "new_password": OTHER_PASSWORD})
            assert fine.status_code == 200
    finally:
        set_mail_enqueuer(None)
    assert looked_up == [NEW_PASSWORD, OTHER_PASSWORD]



# --- confirm racing a sign-in or a refresh (PR #125 review) ---------------------------------


def _in_thread(call: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    out: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: out.update(response=call()))
    thread.start()
    return thread, out


def _gate_once(
    monkeypatch: pytest.MonkeyPatch, module: Any, name: str
) -> tuple[threading.Event, threading.Event]:
    """The first call of ``module.name`` runs, then waits: ``reached`` is set once it has
    returned its value to nobody yet, ``go`` lets it return."""
    reached, go = threading.Event(), threading.Event()
    real = getattr(module, name)
    first = [True]

    def gated(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        if first[0]:
            first[0] = False
            reached.set()
            assert go.wait(30)
        return result

    monkeypatch.setattr(module, name, gated)
    return reached, go


def test_an_old_password_login_in_flight_while_the_reset_commits_is_refused(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = h.link(ACTIVE)
    # The login has read the row and verified the OLD password; it has no tokens yet.
    verified, go = _gate_once(monkeypatch, providers, "verify_password")
    thread, out = _in_thread(lambda: h.login(ACTIVE, PASSWORD))
    assert verified.wait(30)
    assert h.confirm(token).status_code == 200  # the reset commits meanwhile
    go.set()
    thread.join(60)
    assert out["response"].status_code == 401
    assert "set-cookie" not in out["response"].headers
    assert h.login(ACTIVE, PASSWORD).status_code == 401
    assert h.login(ACTIVE, NEW_PASSWORD).status_code == 200


def test_a_magic_link_redeemed_while_the_reset_commits_is_refused(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert h.client.post("/api/v1/auth/magic-link", json={"email": WORKER}).status_code == 202
    [link] = h.magic.sent
    token = h.link(WORKER)
    # The redeem has spent the link and read the row; it has no tokens yet.
    checked, go = _gate_once(monkeypatch, auth_service, "magic_link_eligible")
    redeem = "/api/v1/auth/magic-link/redeem"
    thread, out = _in_thread(lambda: h.client.post(redeem, json={"token": link.token}))
    assert checked.wait(30)
    assert h.confirm(token).status_code == 200
    go.set()
    thread.join(60)
    assert out["response"].status_code == 401
    assert "set-cookie" not in out["response"].headers


def _announce_lock(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """Set once ``lock_user`` is called from now on (the caller then waits for the row)."""
    locking = threading.Event()
    real_lock = auth_service.lock_user

    def announced(*args: Any, **kwargs: Any) -> Any:
        locking.set()
        return real_lock(*args, **kwargs)

    monkeypatch.setattr(auth_service, "lock_user", announced)
    return locking


def test_a_refresh_racing_the_reset_waits_for_it_and_is_refused(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = token_body(h.login(ACTIVE, PASSWORD))
    token = h.link(ACTIVE)
    # The confirm has stamped the change under the row lock and has not committed yet.
    stamped, commit = threading.Event(), threading.Event()
    real_queue = PasswordResetService._queue_mail

    def held(self: Any, session: Any, template: str, *args: Any) -> None:
        real_queue(self, session, template, *args)
        if template == "auth.password_changed":
            stamped.set()
            assert commit.wait(30)

    monkeypatch.setattr(PasswordResetService, "_queue_mail", held)
    confirming, confirmed = _in_thread(lambda: h.confirm(token))
    threads = [confirming]
    try:
        assert stamped.wait(30)
        locking = _announce_lock(monkeypatch)
        refreshing, refreshed = _in_thread(
            lambda: refresh(h.client, tokens["refresh_token"], WEB)
        )
        threads.append(refreshing)
        assert locking.wait(30)
        refreshing.join(0.5)
        assert refreshing.is_alive()  # it waits for the row; it has not issued anything
    finally:  # a regression fails, it does not hang
        commit.set()
        for thread in threads:
            thread.join(60)
    assert confirmed["response"].status_code == 200
    assert refreshed["response"].status_code == 401
    assert "access_token" not in refreshed["response"].text
    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert h.client.get(ME, headers=bearer).status_code == 401


def test_a_sign_in_that_issued_its_tokens_first_makes_the_reset_wait_and_is_revoked(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = h.link(ACTIVE)
    # The login holds the row lock and has issued its tokens; it has not committed yet.
    issued, go = _gate_once(monkeypatch, auth_service.AuthService, "_issue_pair")
    logging_in, logged_in = _in_thread(lambda: h.login(ACTIVE, PASSWORD))
    threads = [logging_in]
    try:
        assert issued.wait(30)
        locking = _announce_lock(monkeypatch)
        confirming, confirmed = _in_thread(lambda: h.confirm(token))
        threads.append(confirming)
        assert locking.wait(30)  # the confirm has reached the row lock ...
        confirming.join(0.5)
        assert confirming.is_alive()  # ... and its stamp waits for the sign-in
    finally:  # a regression fails, it does not hang
        go.set()
        for thread in threads:
            thread.join(60)
    assert confirmed["response"].status_code == 200
    tokens = token_body(logged_in["response"])  # issued before the stamp ...
    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert h.client.get(ME, headers=bearer).status_code == 401  # ... so revoked by it
    assert refresh(h.client, tokens["refresh_token"], WEB).status_code == 401


class _RecordingInviteHook:
    """A post-authentication hook (T-0052) that records what it is asked to accept."""

    def __init__(self) -> None:
        self.accepted: list[tuple[Any, str]] = []

    def reference(self, invite_token: str) -> str:
        return f"ref:{invite_token}"

    def after_authentication(self, session: Any, user: User, reference: str) -> PostAuthOutcome:
        self.accepted.append((user.id, reference))
        return PostAuthOutcome()


@pytest.fixture
def invite_hook(monkeypatch: pytest.MonkeyPatch) -> _RecordingInviteHook:
    """Installed for one test; the hook registered before (the admin module's) comes back."""
    hook = _RecordingInviteHook()
    monkeypatch.setattr(post_auth, "_hooks", [hook])
    return hook


def test_an_invite_is_not_accepted_off_a_sign_in_the_reset_refused(
    h: Harness, monkeypatch: pytest.MonkeyPatch, invite_hook: _RecordingInviteHook
) -> None:
    token = h.link(ACTIVE)
    verified, go = _gate_once(monkeypatch, providers, "verify_password")
    body = {"email": ACTIVE, "password": PASSWORD, "invite_token": "synthetic-invite"}
    thread, out = _in_thread(lambda: h.client.post(LOGIN, json=body))
    try:
        assert verified.wait(30)
        assert h.confirm(token).status_code == 200
    finally:
        go.set()
        thread.join(60)
    assert out["response"].status_code == 401
    assert invite_hook.accepted == []  # the sign-in failed before the hook ran
    # The same invite with the new password: accepted, by a session the reset did not revoke.
    fresh = h.client.post(LOGIN, json={**body, "password": NEW_PASSWORD})
    assert invite_hook.accepted == [(h.users[ACTIVE], "ref:synthetic-invite")]
    bearer = {"Authorization": f"Bearer {token_body(fresh)['access_token']}"}
    assert h.client.get(ME, headers=bearer).status_code == 200


def test_the_invite_hook_runs_only_after_the_tokens_exist_and_a_reset_then_revokes_them(
    h: Harness, monkeypatch: pytest.MonkeyPatch, invite_hook: _RecordingInviteHook
) -> None:
    """The hook commits mid-request, which releases the row lock: by then the tokens must
    have been issued, so a reset that was waiting for the row stamps after their ``iat``."""
    token = h.link(ACTIVE)
    order: list[str] = []
    real_issue = auth_service.AuthService._issue_pair
    real_run = auth_service.run_post_authentication
    in_hook, go = threading.Event(), threading.Event()

    def issuing(self: Any, *args: Any, **kwargs: Any) -> Any:
        order.append("issue")
        return real_issue(self, *args, **kwargs)

    def running(*args: Any, **kwargs: Any) -> Any:
        order.append("hook")
        result = real_run(*args, **kwargs)  # commits: the row lock is released here
        in_hook.set()
        assert go.wait(30)
        return result

    monkeypatch.setattr(auth_service.AuthService, "_issue_pair", issuing)
    monkeypatch.setattr(auth_service, "run_post_authentication", running)
    body = {"email": ACTIVE, "password": PASSWORD, "invite_token": "synthetic-invite"}
    thread, out = _in_thread(lambda: h.client.post(LOGIN, json=body))
    try:
        assert in_hook.wait(30)
        assert order == ["issue", "hook"]
        assert h.confirm(token).status_code == 200  # not blocked: the hook's commit freed the row
    finally:
        go.set()
        thread.join(60)
    tokens = token_body(out["response"])
    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert h.client.get(ME, headers=bearer).status_code == 401
    assert refresh(h.client, tokens["refresh_token"], WEB).status_code == 401


def test_an_account_deactivated_while_confirm_runs_keeps_its_password(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = h.link(ACTIVE)
    before = h.user(ACTIVE).password_hash
    real_hash = auth_service.hash_password

    def deactivating(password: str) -> str:
        with h.db.unit_of_work(SCOPE) as session:
            session.execute(update(User).where(User.id == h.users[ACTIVE]).values(is_active=False))
        return real_hash(password)

    monkeypatch.setattr(auth_service, "hash_password", deactivating)
    assert h.confirm(token).status_code == 410
    assert h.user(ACTIVE).password_hash == before
    assert h.audit()[-1]["reason"] == "account_inactive"
    assert h.outbox.of("auth.password_changed") == []

# --- rate limits --------------------------------------------------------------------------------


def test_requests_are_capped_per_mailbox_like_magic_links(h: Harness) -> None:
    for email in (ACTIVE, UNKNOWN):  # the cap never depends on the account
        for _ in range(4):
            assert h.request(email).status_code == 202
        locked = h.request(email.upper())
        assert locked.status_code == 429
        assert locked.headers["retry-after"] == "60"
    assert len(h.outbox.of("auth.password_reset")) == 4
    # Its own counter: the mailbox's magic-link requests are not used up by resets.
    assert h.client.post("/api/v1/auth/magic-link", json={"email": ACTIVE}).status_code == 202


def test_requests_are_capped_per_client_whatever_the_address(h: Harness) -> None:
    for index in range(20):
        assert h.request(f"someone{index}@example.test").status_code == 202
    mark = h.spy.mark()
    locked = h.request("someone-else@example.test")
    assert (locked.status_code, locked.headers["retry-after"]) == (429, "30")
    # Refused before the mailbox counter and before anything is scheduled.
    assert [e for e in h.spy.events[mark:] if not e.startswith("http.")] == ["store.begin_attempt"]
    # Another client is not affected.
    other = PasswordResetService(h.runtime).request(email=ACTIVE, client="198.51.100.7")
    assert other is not None
    with pytest.raises(LockedOutError):
        PasswordResetService(h.runtime).request(email=ACTIVE, client="testclient")


def test_confirm_attempts_per_token_are_limited(h: Harness) -> None:
    token = h.link(ACTIVE)
    for _ in range(5):  # 5 refused passwords back to back
        assert h.confirm(token, "short").status_code == 422
    locked = h.confirm(token, NEW_PASSWORD)  # even the right kind of password waits now
    assert (locked.status_code, locked.headers["retry-after"]) == (429, "30")
    assert h.login(ACTIVE, PASSWORD).status_code == 200
    # Attempts 6 to 10, each after its lock; the 10th refused attempt kills the link.
    for wait in (30, 60, 120, 240, 480):
        h.advance(timedelta(seconds=wait + 1))
        assert h.confirm(token, "short").status_code == 422
    h.advance(timedelta(minutes=16))
    assert h.confirm(token, NEW_PASSWORD).status_code == 410
    assert h.login(ACTIVE, PASSWORD).status_code == 200


def test_an_attempt_past_the_cap_is_refused_as_a_dead_link(h: Harness) -> None:
    token = h.link(ACTIVE)
    digest = reset_digest(token)
    service = PasswordResetService(h.runtime)
    for _ in range(10):  # ten attempts counted elsewhere, the token entry still there
        service.token_lockout.store.begin_attempt(
            service.token_lockout._key(digest), timedelta(hours=1), lambda _n: timedelta(0)
        )
    assert h.confirm(token).status_code == 410
    assert h.audit()[-1]["reason"] == "attempts_exhausted"
    assert h.runtime.store.peek_value(f"pwreset:token:{digest}") is None


def test_confirm_is_rate_limited_per_client(h: Harness) -> None:
    for _ in range(11):
        assert h.confirm("x" * 43).status_code == 410
    mark = len(h.audit())
    locked = h.confirm("x" * 43)
    assert (locked.status_code, locked.headers["retry-after"]) == (429, "30")
    assert len(h.audit()) == mark  # throttled before any lookup or audit entry


def test_a_store_outage_fails_closed(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.auth.revocation import RevocationUnavailableError

    token = h.link(ACTIVE)

    def down(*_args: Any, **_kwargs: Any) -> Any:
        raise RevocationUnavailableError("synthetic outage")

    monkeypatch.setattr(h.runtime.store._inner, "begin_attempt", down)
    assert h.request(ACTIVE).status_code == 503
    assert h.confirm(token).status_code == 503
    assert h.login(ACTIVE, NEW_PASSWORD).status_code == 503


def test_without_a_deployment_tenant_nothing_is_issued_or_accepted(
    tmp_path: Path, db: Database
) -> None:
    outbox = Outbox(Spy())
    set_mail_enqueuer(outbox)
    try:
        seed_user(db, T1, ACTIVE)
        settings = make_settings(database_url=database_url(tmp_path), default_tenant_id=None)
        app, _ = auth_app(settings)
        with TestClient(app) as client:
            assert client.post(REQUEST, json={"email": ACTIVE}).status_code == 202
            gone = client.post(CONFIRM, json={"token": "x" * 43, "new_password": NEW_PASSWORD})
            assert gone.status_code == 410
    finally:
        set_mail_enqueuer(None)
    assert outbox.mails == []


# --- audit and logs -----------------------------------------------------------------------------


def test_request_confirm_and_refusals_are_audited_without_token_password_or_address(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        token = h.link(ACTIVE)
        h.request(UNKNOWN)
        h.request(DEACTIVATED)
        h.request(OTHER_TENANT)
        h.confirm("x" * 43)
        h.confirm(token, "password123")
        h.confirm(token)
    active, inactive = str(h.users[ACTIVE]), str(h.users[DEACTIVATED])
    refused = {"action": "password_reset_refused"}
    assert h.audit() == [
        {"action": "password_reset_requested", "user_id": active},
        {**refused, "stage": "request", "reason": "unknown_account"},
        {**refused, "stage": "request", "reason": "account_inactive", "user_id": inactive},
        {**refused, "stage": "request", "reason": "unknown_account"},  # another tenant's
        {**refused, "stage": "confirm", "reason": "invalid_token"},
        {**refused, "stage": "confirm", "reason": "PASSWORD_TOO_COMMON", "user_id": active},
        {"action": "password_reset_confirmed", "user_id": active, "first_password": False},
    ]
    text = caplog.text
    assert "password reset link issued" in text
    assert "password reset confirmed" in text
    assert active in text
    for secret in (token, reset_digest(token), NEW_PASSWORD, "password123"):
        assert secret not in text
    for email in (ACTIVE, UNKNOWN, DEACTIVATED, OTHER_TENANT):
        assert email not in text
    # The other tenant's ledger and account are untouched.
    with h.db.unit_of_work(TenancyContext(tenant_id=T2)) as session:
        assert chain_entries(session, PASSWORD_RESET_AUDIT_CHAIN) == []
        other = session.scalars(select(User).where(User.id == h.users[OTHER_TENANT])).one()
        assert other.password_changed_at is None


def test_a_malformed_body_is_a_422_that_does_not_echo_into_the_logs(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        extra = h.client.post(
            CONFIRM,
            json={"token": "x" * 43, "new_password": "leaky-Secret 91", "tenant_id": str(T2)},
        )
        missing = h.client.post(CONFIRM, json={"token": "x" * 43})
        tenant = h.client.post(REQUEST, json={"email": ACTIVE, "tenant_id": str(T2)})
    assert (extra.status_code, missing.status_code, tenant.status_code) == (422, 422, 422)
    assert "leaky-Secret 91" not in caplog.text


# --- structure ----------------------------------------------------------------------------------


def test_core_password_code_never_imports_a_module() -> None:
    for name in ("password_policy.py", "password_reset.py", "password_reset_router.py"):
        tree = ast.parse((BACKEND / "app" / "core" / "auth" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            assert not any(n.startswith("app.modules") for n in names), (name, names)


def test_the_endpoints_are_documented_in_the_openapi_schema() -> None:
    from app.openapi import openapi_schema

    paths = openapi_schema()["paths"]
    request = paths[REQUEST]["post"]
    confirm = paths[CONFIRM]["post"]
    assert set(request["responses"]) >= {"202", "429"}
    assert set(confirm["responses"]) >= {"200", "410", "422", "429"}
    assert "security" not in request  # no bearer, no session: only the two counters
    assert "RESET_LINK_INVALID" in confirm["responses"]["410"]["description"]
