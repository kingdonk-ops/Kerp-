"""Magic-link sign-in limited to Field Workers (T-0057, diff §4.2, TD-136).

Only an active account with a PER-14 grant on a live project (direct or team-derived) gets a
link; everyone else gets the same neutral ``202`` as an unknown address, and nothing is
stored or sent. The request does the same work on every path: the lookup, the eligibility
check, the link, its e-mail and the refusal audit run after the response. Redeem re-checks
eligibility. Synthetic users and projects only.
"""

import ast
import logging
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.audit.service import chain_entries
from app.core.auth import service as service_module
from app.core.auth import team_grants
from app.core.auth.field import MagicLink, magic_link_digest
from app.core.auth.magic_link import (
    MAGIC_LINK_AUDIT_CHAIN,
    MAGIC_LINK_NOT_FIELD_WORKER,
    MagicLinkCandidate,
    MagicLinkJob,
    magic_link_eligible,
    register_magic_link_eligibility,
    unregister_magic_link_eligibility,
)
from app.core.auth.models import ProjectMembership, User
from app.core.auth.revocation import InMemoryRevocationStore
from app.core.auth.runtime import AuthRuntime
from app.core.auth.service import AuthService
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import (
    T1,
    T2,
    CapturingSender,
    auth_app,
    grant_persona,
    make_settings,
    seed_field_worker,
    seed_user,
)
from tests.db_support import database_url

MAGIC = "/api/v1/auth/magic-link"
REDEEM = "/api/v1/auth/magic-link/redeem"
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
BACKEND = Path(__file__).resolve().parents[1]

FIELD = "field.worker@example.test"  # PER-14, direct grant
TEAM = "team.worker@example.test"  # PER-14 only through a team link
GONE = "gone.project@example.test"  # PER-14 only on a deleted project
QAQC = "qaqc.lead@example.test"  # PER-06 only
NOBODY = "no.membership@example.test"  # an account without any membership
DEACTIVATED = "deactivated@example.test"  # PER-14, deactivated
UNKNOWN = "unknown@example.test"  # no account
OTHER_TENANT = "other.tenant@example.test"  # PER-14, but in T2
# Every path that must answer exactly like an unknown address, and get nothing.
NEUTRAL = (GONE, QAQC, NOBODY, DEACTIVATED, UNKNOWN, OTHER_TENANT)
EVERY_PATH = (FIELD, TEAM, *NEUTRAL)


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


class SpySender(CapturingSender):
    def __init__(self, spy: Spy) -> None:
        super().__init__()
        self._spy = spy

    def send(self, session: Any, link: MagicLink) -> None:
        self._spy.events.append("mail.send")
        super().send(session, link)


def _recording(app: Any, spy: Spy) -> Callable[..., Any]:
    """The app behind a thin ASGI layer that records when the response goes out."""

    async def wrapped(scope: Any, receive: Any, send: Any) -> None:
        async def recorded(message: Any) -> None:
            if scope["type"] == "http":
                spy.events.append(str(message["type"]))
            await send(message)

        await app(scope, receive, recorded)

    return wrapped


class World:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.users = {
            FIELD: seed_field_worker(db, T1, FIELD),
            TEAM: seed_user(db, T1, TEAM),
            GONE: seed_user(db, T1, GONE),
            QAQC: seed_user(db, T1, QAQC),
            NOBODY: seed_user(db, T1, NOBODY),
            DEACTIVATED: seed_field_worker(db, T1, DEACTIVATED, is_active=False),
            OTHER_TENANT: seed_field_worker(db, T2, OTHER_TENANT),
        }
        grant_persona(db, T1, self.users[QAQC], "PER-06")
        with db.unit_of_work(ROOT) as s:
            gone = projects.create_project(s, ProjectCreate(code="GONE", name="Synthetic GONE"))
            gone_id = gone.id
        grant_persona(db, T1, self.users[GONE], "PER-14", project_id=gone_id)
        with db.unit_of_work(ROOT) as s:
            projects.delete_project(s, gone_id)

    def link_team(self) -> None:
        """TEAM's only PER-14 grant, derived from a team link (T-0073). Made once the app has
        started: until the teams module exists (T-0013) the startup check refuses live
        team-derived grants."""
        with self.db.unit_of_work(ROOT) as s:
            team_grants.sync_team_link_grants(
                s,
                link_id=uuid.uuid4(),
                project_id=uuid.uuid4(),
                persona="PER-14",
                site_node_id=None,
                user_ids=[self.users[TEAM]],
            )

    def unlink_teams(self) -> None:
        """End the team-derived grants again before the app shuts down."""
        with self.db.unit_of_work(ROOT) as s:
            for link_id in team_grants.team_link_grants(s):
                team_grants.revoke_team_link_grants(s, link_id=link_id)

    def revoke_field_worker(self, email: str) -> None:
        with self.db.unit_of_work(ROOT) as s:
            s.execute(
                update(ProjectMembership)
                .where(ProjectMembership.user_id == self.users[email])
                .values(deleted_at=service_module.utcnow())
            )

    def deactivate(self, email: str) -> None:
        with self.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
            s.execute(update(User).where(User.id == self.users[email]).values(is_active=False))

    def refusals(self) -> list[dict[str, Any]]:
        with self.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
            return [dict(e.payload) for e in chain_entries(s, MAGIC_LINK_AUDIT_CHAIN)]


class Harness:
    def __init__(self, client: TestClient, runtime: AuthRuntime, sender: SpySender, spy: Spy):
        self.client, self.runtime, self.sender, self.spy = client, runtime, sender, spy

    def request(self, email: str) -> Any:
        return self.client.post(MAGIC, json={"email": email})


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def world(db: Database) -> World:
    return World(db)


@pytest.fixture
def h(tmp_path: Path, world: World, monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    spy = Spy()
    sender = SpySender(spy)
    store = SpyStore(InMemoryRevocationStore(), spy)
    for name in ("new_magic_link_token", "magic_link_digest"):
        real = getattr(service_module, name)

        def hashed(*args: Any, _real: Any = real, _name: str = name) -> Any:
            spy.events.append(f"hash.{_name}")
            return _real(*args)

        monkeypatch.setattr(service_module, name, hashed)
    app, runtime = auth_app(
        make_settings(database_url=database_url(tmp_path)), store=store, magic_links=sender
    )
    with TestClient(_recording(app, spy)) as client:
        world.link_team()
        yield Harness(client, runtime, sender, spy)
        world.unlink_teams()


# --- who gets a link ----------------------------------------------------------------------


def test_a_direct_field_worker_gets_a_link_and_signs_in(h: Harness, world: World) -> None:
    assert h.request(FIELD).status_code == 202
    [link] = h.sender.sent
    assert (link.user_id, link.tenant_id, link.email) == (world.users[FIELD], T1, FIELD)
    assert h.runtime.store.peek_magic_link(magic_link_digest(link.token)) is not None
    assert h.client.post(REDEEM, json={"token": link.token}).status_code == 200
    assert world.refusals() == []


def test_a_field_worker_only_through_a_team_link_gets_a_link(h: Harness, world: World) -> None:
    assert h.request(TEAM).status_code == 202
    assert [link.user_id for link in h.sender.sent] == [world.users[TEAM]]
    assert h.client.post(REDEEM, json={"token": h.sender.sent[0].token}).status_code == 200


@pytest.mark.parametrize("email", NEUTRAL)
def test_everyone_else_gets_the_neutral_202_and_nothing_is_issued(h: Harness, email: str) -> None:
    mark = h.spy.mark()
    assert h.request(email).status_code == 202
    assert h.sender.sent == []
    assert "store.put_magic_link" not in h.spy.events[mark:]
    assert "hash.new_magic_link_token" not in h.spy.events[mark:]


def test_the_neutral_answer_is_identical_on_every_path(h: Harness) -> None:
    volatile = {"date", "x-request-id"}

    def answer(email: str) -> tuple[int, bytes, dict[str, str]]:
        response = h.request(email)
        headers = {k: v for k, v in response.headers.items() if k.lower() not in volatile}
        return response.status_code, response.content, headers

    reference = answer(FIELD)
    assert reference[0] == 202
    for email in EVERY_PATH[1:]:
        assert answer(email) == reference, email


def test_the_refusal_is_audited_and_logged_without_token_or_address(
    h: Harness, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="kerp.auth.magic_link"):
        h.request(QAQC)
        h.request(NOBODY)
        h.request(UNKNOWN)  # no account: nothing to audit
        h.request(DEACTIVATED)  # deactivated: silent, as before (no link either)
    refusals = world.refusals()
    assert refusals == [
        {
            "action": "magic_link_refused",
            "reason": MAGIC_LINK_NOT_FIELD_WORKER,
            "user_id": str(world.users[email]),
        }
        for email in (QAQC, NOBODY)
    ]
    text = caplog.text
    assert MAGIC_LINK_NOT_FIELD_WORKER in text
    assert str(world.users[QAQC]) in text
    for email in (QAQC, NOBODY, UNKNOWN, DEACTIVATED):
        assert email not in text


# --- redeem re-checks -----------------------------------------------------------------------


def test_redeem_is_refused_once_the_last_per14_grant_is_gone(h: Harness, world: World) -> None:
    h.request(FIELD)
    token = h.sender.sent[-1].token
    world.revoke_field_worker(FIELD)
    refused = h.client.post(REDEEM, json={"token": token})
    assert refused.status_code == 401
    garbage = h.client.post(REDEEM, json={"token": "x" * 43})
    assert (refused.json(), refused.headers["www-authenticate"]) == (
        garbage.json(),
        garbage.headers["www-authenticate"],
    )


def test_redeem_is_refused_for_a_user_deactivated_meanwhile(h: Harness, world: World) -> None:
    h.request(FIELD)
    token = h.sender.sent[-1].token
    world.deactivate(FIELD)
    assert h.client.post(REDEEM, json={"token": token}).status_code == 401


# --- no timing oracle -----------------------------------------------------------------------


def test_every_path_does_the_same_work_before_the_response(h: Harness) -> None:
    seen: dict[str, list[str]] = {}
    for email in EVERY_PATH:
        mark = h.spy.mark()
        assert h.request(email).status_code == 202
        seen[email] = h.spy.before_response(mark)
    # Only the per-client and the per-mailbox lockouts are counted in the request (T-0345):
    # no lookup-dependent store or hash call, and never the mail sender.
    counted = ("store.begin_attempt", "store.begin_attempt")
    assert set(map(tuple, seen.values())) == {counted}, seen


def test_the_link_is_issued_and_sent_only_after_the_response(h: Harness) -> None:
    mark = h.spy.mark()
    h.request(FIELD)
    assert h.spy.after_response(mark) == [
        "hash.new_magic_link_token",
        "hash.magic_link_digest",
        "store.put_magic_link",
        "mail.send",
    ]
    for email in NEUTRAL:
        mark = h.spy.mark()
        h.request(email)
        assert h.spy.after_response(mark) == [], email


def test_the_request_half_returns_the_same_job_for_every_address(tmp_path: Path) -> None:
    spy = Spy()
    sender = SpySender(spy)
    store = SpyStore(InMemoryRevocationStore(), spy)
    _app, runtime = auth_app(
        make_settings(database_url=database_url(tmp_path)), store=store, magic_links=sender
    )
    service = AuthService(runtime)
    for email in EVERY_PATH:
        mark = spy.mark()
        job = service.request_magic_link(email=email.upper())
        assert job == MagicLinkJob(tenant_id=T1, email=email)
        assert email not in repr(job)
        assert spy.events[mark:] == ["store.begin_attempt", "store.begin_attempt"]
    assert sender.sent == []


def test_the_lockout_applies_to_ineligible_addresses_unchanged(h: Harness) -> None:
    for email in (QAQC, FIELD):
        for _ in range(4):
            assert h.request(email).status_code == 202
        locked = h.request(email)
        assert locked.status_code == 429
        assert locked.headers["retry-after"] == "60"


def test_a_failure_after_the_response_is_logged_not_raised(
    h: Harness, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_session: Any, _link: MagicLink) -> None:
        raise RuntimeError("synthetic transport failure")

    monkeypatch.setattr(h.sender, "send", broken)
    with caplog.at_level(logging.ERROR, logger="kerp.auth"):
        assert h.request(FIELD).status_code == 202
    assert "magic-link request not processed" in caplog.text
    assert FIELD not in caplog.text


# --- the eligibility extension point -------------------------------------------------------


def test_a_registered_provider_extends_eligibility(h: Harness, world: World) -> None:
    asked: list[MagicLinkCandidate] = []

    def invited(_session: Session, candidate: MagicLinkCandidate) -> bool:
        asked.append(candidate)
        return candidate.email == NOBODY

    register_magic_link_eligibility(invited)
    register_magic_link_eligibility(invited)  # idempotent
    try:
        h.request(NOBODY)
        h.request(QAQC)
        assert [link.user_id for link in h.sender.sent] == [world.users[NOBODY]]
        assert [c.user_id for c in asked] == [world.users[NOBODY], world.users[QAQC]]
        assert NOBODY not in repr(asked[0])
        token = h.sender.sent[0].token
    finally:
        unregister_magic_link_eligibility(invited)
    unregister_magic_link_eligibility(invited)  # unknown: ignored
    # Redeem asks again: without the provider the account is no longer eligible.
    assert h.client.post(REDEEM, json={"token": token}).status_code == 401


def test_no_provider_is_registered_by_default(world: World) -> None:
    candidate = MagicLinkCandidate(tenant_id=T1, user_id=world.users[NOBODY], email=NOBODY)
    own = TenancyContext(tenant_id=T1, user_id=world.users[NOBODY])
    with world.db.unit_of_work(own) as s:
        assert magic_link_eligible(s, candidate) is False


def _imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def test_core_auth_never_imports_a_module() -> None:
    auth = BACKEND / "app" / "core" / "auth"
    offenders = [
        f"{path.name}: {name}"
        for path in sorted(auth.rglob("*.py"))
        for name in _imports(path)
        if name == "app.modules" or name.startswith("app.modules.")
    ]
    assert offenders == []
