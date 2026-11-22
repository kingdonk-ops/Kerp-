"""Broadcast e-mail (T-0228, diff §4.7 Broadcast e-mail, §4.1, TD-399).

Server-side audience resolution (System admin tenant-wide, PER-09 only its own projects),
the count-only preview, batch dispatch through ``send_mail`` with the broadcast id, the rate
limit, the one-click opt-out (opaque hashed token, no oracle), per-broadcast delivery counts,
the template and its ``List-Unsubscribe`` headers, the audit entry and migration ``0043``.
Runs on SQLite and, in CI's PostgreSQL step, under RLS. Synthetic data only.
"""

from __future__ import annotations

import asyncio
import importlib.util
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, select

from app.core.audit import service as audit
from app.core.auth.access import SYSTEM_ADMIN, Persona
from app.core.auth.models import ProjectMembership, User
from app.core.db import Base, Database, contract_violations
from app.core.digest import sha256_hex
from app.core.mail import service as mail_service
from app.core.mail.models import MailSend
from app.core.mail.queue import QueuedMail, set_mail_enqueuer
from app.core.mail.service import Delivery, deliver, send_mail
from app.core.mail.templates import (
    TEMPLATES,
    MailTemplate,
    TemplateError,
    render,
    sample_context,
)
from app.core.mail.transport import FakeTransport, OutboundMessage, SesTransport
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import RLS_EXEMPT, rls_tables
from app.core.tenancy.scope import OutOfScopeError
from app.modules.admin import service as admin_service
from app.modules.jobs.models import Job
from app.modules.jobs.registry import JOB_REGISTRY, JobContext
from app.modules.jobs.service import JobError
from app.modules.notifications import broadcasts
from app.modules.notifications import jobs as broadcast_jobs
from app.modules.notifications.broadcasts import Audience, Sender
from app.modules.notifications.models import (
    BROADCAST_TABLE,
    OPTOUT_TABLE,
    RECIPIENT_TABLE,
    Broadcast,
    BroadcastOptOut,
    BroadcastRecipient,
)
from app.modules.notifications.schemas import (
    BroadcastAudienceWrite,
    BroadcastWrite,
    UnsubscribeWrite,
)
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import ALEMBIC_INI, migrated_database
from tests.auth_support import PASSWORD, T1, T2, auth_app, make_settings, seed_user
from tests.db_support import database_url

ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
TENANT = TenancyContext(tenant_id=T1)
ORIGIN = "https://kerp.example.test"
API = "/api/v1/notifications/broadcasts"
UNSUBSCRIBE = f"{API}/unsubscribe"
ONE_CLICK = f"{UNSUBSCRIBE}/one-click"
ADMIN, PM, ALICE, BOB, CAROL, DAVE, ERIN, FRANK = (
    f"{name}@example.test"
    for name in ("admin", "pm", "alice", "bob", "carol", "dave", "erin", "frank")
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


class Outbox:
    def __init__(self) -> None:
        self.mails: list[QueuedMail] = []

    def __call__(self, mails: Sequence[QueuedMail]) -> None:
        self.mails.extend(mails)

    def of(self, template: str = broadcasts.TEMPLATE_KEY) -> list[QueuedMail]:
        return [m for m in self.mails if m.template_key == template]


@pytest.fixture
def outbox() -> Iterator[Outbox]:
    hook = Outbox()
    set_mail_enqueuer(hook)
    yield hook
    set_mail_enqueuer(None)


def _project(db: Database, code: str, ctx: TenancyContext = ROOT) -> uuid.UUID:
    with db.unit_of_work(ctx) as s:
        return projects.create_project(
            s, ProjectCreate.model_validate({"code": code, "name": code})
        ).id


def _grant(
    db: Database, user: uuid.UUID, project: uuid.UUID, persona: Persona, tenant: uuid.UUID = T1
) -> None:
    with db.unit_of_work(TenancyContext(tenant_id=tenant, is_system_admin=True)) as s:
        s.add(
            ProjectMembership(
                tenant_id=tenant, user_id=user, member_project_id=project, persona=persona.value
            )
        )


@dataclass
class World:
    """Tenant T1: projects A and B. ``admin`` holds ``system_admin`` (internal, no project);
    ``pm`` is PER-09 on A; ``alice`` (internal) PER-14 on A; ``bob`` (external) PER-15 on A;
    ``carol`` PER-02 on B; ``dave`` PER-14 on A but deactivated; ``erin`` internal without a
    project. Tenant T2: ``frank`` on its own project."""

    db: Database
    users: dict[str, uuid.UUID]
    a: uuid.UUID
    b: uuid.UUID
    foreign: uuid.UUID
    logins: dict[str, dict[str, str]] = field(default_factory=dict)

    def scope(self, email: str) -> TenancyContext:
        """The request scope of ``email`` (what ``resolve_request_scope`` binds)."""
        if email == ADMIN:
            return TenancyContext(tenant_id=T1, user_id=self.users[ADMIN], is_system_admin=True)
        own = {PM: {self.a}, ALICE: {self.a}, BOB: {self.a}, CAROL: {self.b}}.get(email, set())
        return TenancyContext(
            tenant_id=T1,
            user_id=self.users[email],
            member_project_ids=frozenset(own),
            admin_project_ids=frozenset({self.a}) if email == PM else frozenset(),
        )

    def sender(self, email: str) -> Sender:
        if email == ADMIN:
            return Sender(self.users[ADMIN], True, frozenset())
        return Sender(self.users[email], False, frozenset({self.a}) if email == PM else frozenset())

    def preview(self, email: str, audience: Audience) -> int:
        with self.db.unit_of_work(self.scope(email)) as s:
            return broadcasts.preview(s, self.sender(email), audience)

    def create(self, email: str, audience: Audience, subject: str = "Shutdown") -> uuid.UUID:
        with self.db.unit_of_work(self.scope(email)) as s:
            return broadcasts.create(
                s, self.sender(email), audience, subject=subject, body="Line one\nLine two"
            ).id

    def broadcast(self, broadcast_id: uuid.UUID) -> Broadcast:
        with self.db.unit_of_work(TENANT) as s:
            row = s.get(Broadcast, broadcast_id)
            assert row is not None
            s.expunge(row)
            return row

    def recipients(self, broadcast_id: uuid.UUID) -> dict[uuid.UUID, BroadcastRecipient]:
        with self.db.unit_of_work(TENANT) as s:
            rows = s.scalars(
                select(BroadcastRecipient).where(BroadcastRecipient.broadcast_id == broadcast_id)
            ).all()
            for row in rows:
                s.expunge(row)
            return {row.user_id: row for row in rows}

    def opted_out(self) -> set[uuid.UUID]:
        with self.db.unit_of_work(TENANT) as s:
            return set(s.scalars(select(BroadcastOptOut.user_id)))

    def opt_out(self, email: str) -> None:
        with self.db.unit_of_work(TENANT) as s:
            s.add(BroadcastOptOut(tenant_id=T1, user_id=self.users[email]))


@pytest.fixture
def world(db: Database) -> World:
    users = {
        ADMIN: seed_user(db, T1, ADMIN, is_internal=True),
        PM: seed_user(db, T1, PM),
        ALICE: seed_user(db, T1, ALICE, is_internal=True),
        BOB: seed_user(db, T1, BOB),
        CAROL: seed_user(db, T1, CAROL),
        DAVE: seed_user(db, T1, DAVE, is_active=False),
        ERIN: seed_user(db, T1, ERIN, is_internal=True),
        FRANK: seed_user(db, T2, FRANK),
    }
    a, b = _project(db, "AAA"), _project(db, "BBB")
    foreign = _project(db, "FOREIGN", TenancyContext(tenant_id=T2, is_system_admin=True))
    _grant(db, users[PM], a, Persona.PROJECT_ADMINISTRATOR)
    _grant(db, users[ALICE], a, Persona.FIELD_WORKER)
    _grant(db, users[BOB], a, Persona.CLIENT_REPRESENTATIVE)
    _grant(db, users[CAROL], b, Persona.PROJECT_MANAGER)
    _grant(db, users[DAVE], a, Persona.FIELD_WORKER)
    _grant(db, users[FRANK], foreign, Persona.FIELD_WORKER, tenant=T2)
    with db.unit_of_work(ROOT) as s:
        admin_service.grant(s, users[ADMIN], SYSTEM_ADMIN, actor_id=None)
    return World(db, users, a, b, foreign)


def _ids(world: World, *emails: str) -> set[uuid.UUID]:
    return {world.users[e] for e in emails}


TENANT_WIDE = Audience("tenant")


# --- contract -------------------------------------------------------------------------------


@pytest.mark.parametrize("table", [BROADCAST_TABLE, RECIPIENT_TABLE, OPTOUT_TABLE])
def test_broadcast_tables_meet_the_record_contract_and_are_under_rls(table: str) -> None:
    assert table in Base.metadata.tables
    assert [p for p in contract_violations(Base.metadata) if p.startswith(table)] == []
    assert table in rls_tables(Base.metadata)
    assert table not in RLS_EXEMPT


def test_the_dispatch_job_runs_on_the_batch_queue() -> None:
    kind = JOB_REGISTRY.get(broadcasts.DISPATCH_KIND)
    assert (kind.queue, kind.api, kind.max_attempts) == ("batch", False, 3)


# --- the template ---------------------------------------------------------------------------


def test_the_broadcast_template_is_registered_for_the_editor() -> None:
    template = TEMPLATES.get(broadcasts.TEMPLATE_KEY)
    assert (template.audience, template.required, template.has_action) == (
        "deployment_wide",
        False,
        True,
    )
    assert template.placeholders() == ("subject", "message", "app_name")
    assert "unsubscribe_token" not in template.placeholders()
    assert template.list_unsubscribe_path == ONE_CLICK == broadcasts.ONE_CLICK_PATH


def test_the_body_is_escaped_plain_text_with_one_click_headers() -> None:
    token = "tok-SYNTHETIC-broadcast-0123456789abcdef"
    rendered = render(
        TEMPLATES.get(broadcasts.TEMPLATE_KEY),
        {
            "subject": "Site <b>closed</b>",
            "message": "Hello <script>x</script>\nBye",
            "unsubscribe_token": token,
        },
        locale=None,
        app_name="KERP",
        web_origin=ORIGIN,
    )
    assert rendered.subject == "Site <b>closed</b>"  # the subject header is plain text
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert "Hello &lt;script&gt;x&lt;/script&gt;<br>\nBye" in rendered.html
    assert "Hello <script>x</script>\nBye" in rendered.text
    assert f"{ORIGIN}/unsubscribe#{token}" in rendered.text
    assert dict(rendered.headers) == {
        "List-Unsubscribe": f"<{ORIGIN}{ONE_CLICK}?token={token}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    # Nothing in the URL names the user or the address.
    assert "user_id" not in rendered.headers[0][1]
    assert "@" not in rendered.headers[0][1]


def test_other_templates_carry_no_extra_headers() -> None:
    rendered = render(
        TEMPLATES.get("auth.password_changed"), {}, locale=None, app_name="K", web_origin=ORIGIN
    )
    assert rendered.headers == ()


def test_the_multiline_value_is_checked() -> None:
    template = TEMPLATES.get(broadcasts.TEMPLATE_KEY)
    good = {"subject": "S", "message": "a\n\tb", "unsubscribe_token": "x" * 43}
    assert template.check_context(good)["message"] == "a\n\tb"
    for bad in (
        {"message": "a\x00b"},
        {"message": "a\rb"},
        {"message": "x" * 10_001},
        {"subject": "a\nb"},
    ):
        with pytest.raises(TemplateError):
            template.check_context({**good, **bad})


def test_template_declarations_are_validated() -> None:
    with pytest.raises(ValueError, match="List-Unsubscribe"):
        MailTemplate("area.one", list_unsubscribe_path="/api/v1/x")  # no secret
    with pytest.raises(ValueError, match="List-Unsubscribe"):
        MailTemplate("area.two", link_path="/x", secret="t", list_unsubscribe_path="/x")
    with pytest.raises(ValueError, match="multi-line"):
        MailTemplate("area.three", params=("a",), multiline_param="b")


def test_ses_sends_the_extra_headers() -> None:
    class Client:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        def send_email(self, **request: Any) -> dict[str, str]:
            self.requests.append(request)
            return {"MessageId": "m-1"}

    client = Client()
    transport = SesTransport(
        region="ap-southeast-2", from_address="no-reply@example.test", client=client
    )
    template = TEMPLATES.get(broadcasts.TEMPLATE_KEY)
    rendered = render(
        template, sample_context(template), locale=None, app_name="K", web_origin=ORIGIN
    )
    transport.send(OutboundMessage("someone@example.test", rendered, template.key))
    headers = client.requests[0]["Content"]["Simple"]["Headers"]
    assert [h["Name"] for h in headers] == ["List-Unsubscribe", "List-Unsubscribe-Post"]
    plain = render(
        TEMPLATES.get("auth.password_changed"), {}, locale=None, app_name="K", web_origin=ORIGIN
    )
    transport.send(OutboundMessage("someone@example.test", plain, "auth.password_changed"))
    assert "Headers" not in client.requests[1]["Content"]["Simple"]


# --- request validation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"scope": "tenant", "project_ids": [str(uuid.uuid4())]},
        {"scope": "projects"},
        {"scope": "projects", "project_ids": [str(uuid.UUID(int=1))] * 2},
        {"scope": "tenant", "personas": ["PER-17"]},
        {"scope": "tenant", "personas": ["PER-01", "PER-01"]},
        {"scope": "tenant", "category": "vip"},
        {"scope": "everyone"},
        {"scope": "tenant", "user_ids": [str(uuid.uuid4())]},
    ],
)
def test_bad_audiences_are_refused(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BroadcastAudienceWrite.model_validate(body)


def test_subject_and_body_are_plain_text() -> None:
    base = {"scope": "tenant", "subject": " Hello ", "body": "a\r\nb\rc "}
    parsed = BroadcastWrite.model_validate(base)
    assert (parsed.subject, parsed.body) == ("Hello", "a\nb\nc")
    for bad in ({"subject": "a\nb"}, {"subject": "  "}, {"body": "a\x07b"}, {"body": "x" * 10_001}):
        with pytest.raises(ValidationError):
            BroadcastWrite.model_validate({**base, **bad})


def test_an_over_long_unsubscribe_token_is_dropped_not_refused() -> None:
    limit = UnsubscribeWrite.MAX_TOKEN
    assert UnsubscribeWrite.model_validate({"token": "x" * limit}).token == "x" * limit
    assert UnsubscribeWrite.model_validate({"token": "x" * (limit + 1)}).token is None
    assert UnsubscribeWrite.model_validate({}).token is None


# --- audience -------------------------------------------------------------------------------


def test_a_system_admin_reaches_every_active_user_of_its_tenant(world: World) -> None:
    with world.db.unit_of_work(world.scope(ADMIN)) as s:
        users = broadcasts.resolve(s, TENANT_WIDE)
        assert {u.id for u in users} == _ids(world, ADMIN, PM, ALICE, BOB, CAROL, ERIN)
    assert world.preview(ADMIN, TENANT_WIDE) == 6  # dave is deactivated, frank another tenant's


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("internal", (ADMIN, ALICE, ERIN)),
        ("external", (PM, BOB, CAROL)),
        ("client", (BOB,)),
    ],
)
def test_category_filters(world: World, category: str, expected: tuple[str, ...]) -> None:
    with world.db.unit_of_work(world.scope(ADMIN)) as s:
        found = {u.id for u in broadcasts.resolve(s, Audience("tenant", category=category))}
    assert found == _ids(world, *expected)


def test_persona_and_project_filters(world: World) -> None:
    cases = [
        (Audience("tenant", personas=frozenset({Persona.PROJECT_MANAGER})), (CAROL,)),
        (
            Audience("tenant", personas=frozenset({Persona.FIELD_WORKER, Persona.PROJECT_MANAGER})),
            (ALICE, CAROL),
        ),
        (Audience("projects", frozenset({world.a})), (PM, ALICE, BOB)),
        (Audience("projects", frozenset({world.a, world.b})), (PM, ALICE, BOB, CAROL)),
        (
            Audience("projects", frozenset({world.a}), personas=frozenset({Persona.FIELD_WORKER})),
            (ALICE,),
        ),
        (Audience("projects", frozenset({world.a}), category="internal"), (ALICE,)),
        (
            Audience(
                "projects",
                frozenset({world.a}),
                category="client",
                personas=frozenset({Persona.FIELD_WORKER}),
            ),
            (),
        ),
    ]
    with world.db.unit_of_work(world.scope(ADMIN)) as s:
        for audience, expected in cases:
            assert {u.id for u in broadcasts.resolve(s, audience)} == _ids(world, *expected)


def test_a_project_administrator_reaches_only_its_own_projects(world: World) -> None:
    own = Audience("projects", frozenset({world.a}))
    assert world.preview(PM, own) == 3
    assert world.preview(PM, Audience("projects", frozenset({world.a}), category="client")) == 1
    for projects_ in ({world.b}, {world.a, world.b}, {world.foreign}, {uuid.uuid4()}):
        with pytest.raises(OutOfScopeError):
            world.preview(PM, Audience("projects", frozenset(projects_)))
        with pytest.raises(OutOfScopeError):
            world.create(PM, Audience("projects", frozenset(projects_)))
    with pytest.raises(broadcasts.BroadcastForbiddenError):
        world.preview(PM, TENANT_WIDE)
    with pytest.raises(broadcasts.BroadcastForbiddenError):
        world.preview(ALICE, own)  # a member without PER-09
    # A System admin may target any project of its tenant, never another tenant's.
    assert world.preview(ADMIN, Audience("projects", frozenset({world.b}))) == 1
    with pytest.raises(OutOfScopeError):
        world.preview(ADMIN, Audience("projects", frozenset({world.foreign})))


def test_a_project_administrator_cannot_reach_a_project_it_only_works_on(world: World) -> None:
    """pm is PER-09 on A and also PER-02 on B: B's project row and memberships are visible
    to pm's session, yet B is not one of pm's PER-09 projects, so it stays a 404."""
    _grant(world.db, world.users[PM], world.b, Persona.PROJECT_MANAGER)
    to_b = world.create(ADMIN, Audience("projects", frozenset({world.b})))
    scope = TenancyContext(
        tenant_id=T1,
        user_id=world.users[PM],
        member_project_ids=frozenset({world.a, world.b}),
        admin_project_ids=frozenset({world.a}),
    )
    sender = world.sender(PM)
    for projects_ in ({world.b}, {world.a, world.b}):
        audience = Audience("projects", frozenset(projects_))
        with world.db.unit_of_work(scope) as s:
            assert {p.id for p in projects.list_projects(s, projects_)} == projects_  # visible
            with pytest.raises(OutOfScopeError):
                broadcasts.preview(s, sender, audience)
        with world.db.unit_of_work(scope) as s, pytest.raises(OutOfScopeError):
            broadcasts.create(s, sender, audience, subject="S", body="B")
    with world.db.unit_of_work(scope) as s:
        with pytest.raises(OutOfScopeError):
            broadcasts.get_broadcast(s, sender, to_b)
        assert broadcasts.list_broadcasts(s, sender) == []


def test_the_api_hides_a_project_the_administrator_only_works_on(http: Http) -> None:
    world = http.world
    _grant(world.db, world.users[PM], world.b, Persona.PROJECT_MANAGER)
    created = http.post("", ADMIN, _body(world, project_ids=[str(world.b)]))
    assert created.status_code == 202, created.text
    to_b = created.json()["id"]
    for ids in ([str(world.b)], [str(world.a), str(world.b)]):
        audience = {"scope": "projects", "project_ids": ids}
        assert http.post("/preview", PM, audience).status_code == 404
        assert http.post("", PM, _body(world, project_ids=ids)).status_code == 404
    assert http.get(f"/{to_b}", PM).status_code == 404
    listed = http.get("", PM)
    assert listed.status_code == 200
    assert listed.json() == []
    with world.db.unit_of_work(TENANT) as s:  # pm queued nothing
        senders = set(s.scalars(select(Broadcast.sender_id)))
    assert senders == {world.users[ADMIN]}


def test_opted_out_and_suppressed_users_are_excluded(world: World) -> None:
    world.opt_out(ALICE)
    with world.db.unit_of_work(TENANT) as s:
        mail_service.suppress(s, BOB.upper(), "bounce")
    assert world.preview(PM, Audience("projects", frozenset({world.a}))) == 1  # pm only
    with world.db.unit_of_work(TENANT) as s:
        assert mail_service.suppressed_addresses(s, [BOB, ALICE, "not an address"]) == {BOB}


# --- queueing and dispatch ------------------------------------------------------------------


def test_queueing_stores_ids_audits_and_adds_the_batch_job(world: World) -> None:
    broadcast_id = world.create(PM, Audience("projects", frozenset({world.a})))
    row = world.broadcast(broadcast_id)
    assert (row.status, row.scope, row.project_ids, row.recipient_count) == (
        "queued",
        "projects",
        [str(world.a)],
        3,
    )
    assert row.audience == {"category": None, "personas": []}
    assert row.body == "Line one\nLine two"
    assert set(world.recipients(broadcast_id)) == _ids(world, PM, ALICE, BOB)
    with world.db.unit_of_work(TENANT) as s:
        job = s.get(Job, row.job_id)
        assert job is not None
        assert (job.kind, job.queue, job.payload) == (
            broadcasts.DISPATCH_KIND,
            "batch",
            {"broadcast_id": str(broadcast_id)},
        )
        entries = audit.chain_entries(s, broadcasts.chain_key(broadcast_id))
    assert [e.payload["event"] for e in entries] == ["broadcast_queued"]
    payload = entries[0].payload
    assert payload["sender_id"] == str(world.users[PM])
    assert payload["recipient_count"] == 3
    assert payload["project_ids"] == [str(world.a)]
    text = str(payload)
    assert "@" not in text
    assert "Shutdown" not in text
    assert "Line one" not in text


def test_nobody_to_send_to_is_refused(world: World) -> None:
    with pytest.raises(broadcasts.NoRecipientsError):
        world.create(ADMIN, Audience("projects", frozenset({world.b}), category="client"))


def test_five_broadcasts_per_sender_per_day(world: World) -> None:
    own = Audience("projects", frozenset({world.a}))
    for n in range(broadcasts.RATE_LIMIT):
        world.create(PM, own, subject=f"N{n}")
    with pytest.raises(broadcasts.BroadcastRateLimitedError) as caught:
        world.create(PM, own)
    assert 0 < caught.value.retry_after <= 24 * 3600 + 1
    world.create(ADMIN, own)  # another sender is not limited by pm's


def _dispatch(world: World, broadcast_id: uuid.UUID) -> dict[str, Any]:
    return broadcasts.dispatch(world.db, TENANT, broadcast_id)


def test_dispatch_sends_one_mail_per_recipient_with_a_hashed_token(
    world: World, outbox: Outbox
) -> None:
    broadcast_id = world.create(ADMIN, Audience("projects", frozenset({world.a, world.b})))
    # Between queueing and dispatch: carol is deactivated, bob opts out.
    with world.db.unit_of_work(ROOT) as s:
        user = s.get(User, world.users[CAROL])
        assert user is not None
        user.is_active = False
    world.opt_out(BOB)
    assert _dispatch(world, broadcast_id) == {"queued": 2, "skipped": 2}
    row = world.broadcast(broadcast_id)
    assert (row.status, row.queued_count, row.skipped_count) == ("sent", 2, 2)
    assert row.finished_at is not None
    recipients = world.recipients(broadcast_id)
    assert recipients[world.users[CAROL]].skip_reason == "inactive"
    assert recipients[world.users[BOB]].skip_reason == "opted_out"
    mails = outbox.of()
    assert {m.to for m in mails} == {PM, ALICE}
    for mail in mails:
        token = str(mail.context["unsubscribe_token"])
        assert mail.context["subject"] == "Shutdown"
        assert mail.context["message"] == "Line one\nLine two"
        match = [r for r in recipients.values() if r.send_id == mail.send_id]
        assert len(match) == 1
        assert match[0].unsubscribe_hash == sha256_hex(token)
        assert match[0].status == "queued"
    with world.db.unit_of_work(TENANT) as s:
        sends = s.scalars(select(MailSend).where(MailSend.broadcast_id == broadcast_id)).all()
        assert {r.recipient_user_id for r in sends} == _ids(world, PM, ALICE)
        assert all(r.template_key == broadcasts.TEMPLATE_KEY for r in sends)
        events = [e.payload for e in audit.chain_entries(s, broadcasts.chain_key(broadcast_id))]
    assert events[-1] == {"event": "broadcast_dispatched", "queued": 2, "skipped": 2}
    # A rerun (a retried job) sends nothing twice.
    assert _dispatch(world, broadcast_id) == {"queued": 2, "skipped": 2}
    assert len(outbox.of()) == 2


def test_the_worker_sends_the_list_unsubscribe_headers(world: World, outbox: Outbox) -> None:
    broadcast_id = world.create(PM, Audience("projects", frozenset({world.a})))
    _dispatch(world, broadcast_id)
    transport = FakeTransport()
    delivery = Delivery(world.db, transport, make_settings(web_origin=ORIGIN))
    for mail in outbox.of():
        assert deliver(delivery, mail, final_attempt=True) == "sent"
    for message in transport.sent:
        headers = dict(message.rendered.headers)
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert headers["List-Unsubscribe"].startswith(f"<{ORIGIN}{ONE_CLICK}?token=")


def test_a_failing_dispatch_marks_the_broadcast_failed(
    world: World, outbox: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    broadcast_id = world.create(PM, Audience("projects", frozenset({world.a})))

    def broken(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("synthetic outage")

    monkeypatch.setattr(broadcasts, "_send_chunk", broken)
    with pytest.raises(RuntimeError):
        _dispatch(world, broadcast_id)
    assert world.broadcast(broadcast_id).status == "failed"
    monkeypatch.undo()
    assert _dispatch(world, broadcast_id)["queued"] == 3  # a retry resumes
    assert world.broadcast(broadcast_id).status == "sent"


def test_the_job_handler_checks_its_payload(world: World) -> None:
    broadcast_id = world.create(PM, Audience("projects", frozenset({world.a})))

    async def update(_progress: int, _message: str | None = None) -> None:
        return None

    def context(payload: dict[str, Any], tenancy: TenancyContext = TENANT) -> JobContext:
        return JobContext(
            uuid.uuid4(), broadcasts.DISPATCH_KIND, tenancy, payload, None, update, world.db
        )

    result = asyncio.run(
        broadcast_jobs.dispatch_broadcast(context({"broadcast_id": str(broadcast_id)}))
    )
    assert result == {"queued": 3, "skipped": 0}
    for bad in ({}, {"broadcast_id": "nope"}):
        with pytest.raises(JobError):
            asyncio.run(broadcast_jobs.dispatch_broadcast(context(bad)))
    with pytest.raises(JobError):
        asyncio.run(broadcast_jobs.dispatch_broadcast(context({"broadcast_id": str(uuid.uuid4())})))
    projected = TenancyContext(tenant_id=T1, project_id=world.a)
    with pytest.raises(JobError):
        asyncio.run(
            broadcast_jobs.dispatch_broadcast(
                context({"broadcast_id": str(broadcast_id)}, projected)
            )
        )


# --- reads ----------------------------------------------------------------------------------


def test_a_project_administrator_reads_only_broadcasts_to_its_projects(world: World) -> None:
    own = world.create(ADMIN, Audience("projects", frozenset({world.a})))
    mixed = world.create(ADMIN, Audience("projects", frozenset({world.a, world.b})))
    wide = world.create(ADMIN, TENANT_WIDE)
    with world.db.unit_of_work(world.scope(PM)) as s:
        assert broadcasts.get_broadcast(s, world.sender(PM), own).id == own
        for hidden in (mixed, wide, uuid.uuid4()):
            with pytest.raises(OutOfScopeError):
                broadcasts.get_broadcast(s, world.sender(PM), hidden)
        assert [b.id for b in broadcasts.list_broadcasts(s, world.sender(PM))] == [own]
    with world.db.unit_of_work(world.scope(ADMIN)) as s:
        assert {b.id for b in broadcasts.list_broadcasts(s, world.sender(ADMIN))} == {
            own,
            mixed,
            wide,
        }
    with world.db.unit_of_work(world.scope(ALICE)) as s:
        with pytest.raises(OutOfScopeError):
            broadcasts.get_broadcast(s, world.sender(ALICE), own)
        with pytest.raises(broadcasts.BroadcastForbiddenError):
            broadcasts.list_broadcasts(s, world.sender(ALICE))


def test_another_tenant_never_sees_a_broadcast(world: World) -> None:
    broadcast_id = world.create(ADMIN, TENANT_WIDE)
    other = TenancyContext(tenant_id=T2, is_system_admin=True)
    with world.db.unit_of_work(other) as s:
        with pytest.raises(OutOfScopeError):
            broadcasts.get_broadcast(s, Sender(uuid.uuid4(), True, frozenset()), broadcast_id)
        assert s.scalars(select(BroadcastRecipient)).all() == []


# --- one-click opt-out ----------------------------------------------------------------------


def test_unsubscribe_records_the_opt_out_once(world: World, outbox: Outbox) -> None:
    broadcast_id = world.create(PM, Audience("projects", frozenset({world.a})))
    _dispatch(world, broadcast_id)
    mail = next(m for m in outbox.of() if m.to == ALICE)
    token = str(mail.context["unsubscribe_token"])
    with world.db.unit_of_work(TENANT) as s:
        assert broadcasts.unsubscribe(s, token) is True
    with world.db.unit_of_work(TENANT) as s:
        assert broadcasts.unsubscribe(s, token) is False  # used
        assert broadcasts.unsubscribe(s, token[:-1] + ("A" if token[-1] != "A" else "B")) is False
        assert broadcasts.unsubscribe(s, None) is False
    assert world.opted_out() == {world.users[ALICE]}
    assert world.recipients(broadcast_id)[world.users[ALICE]].unsubscribed_at is not None
    # Alice is no longer in any broadcast audience.
    assert world.preview(PM, Audience("projects", frozenset({world.a}))) == 2


def test_an_opt_out_never_touches_transactional_mail(world: World, outbox: Outbox) -> None:
    world.opt_out(ALICE)
    with world.db.unit_of_work(TENANT) as s:
        send_mail(s, "auth.password_changed", ALICE, {}, recipient_user_id=world.users[ALICE])
    transport = FakeTransport()
    delivery = Delivery(world.db, transport, make_settings(web_origin=ORIGIN))
    assert deliver(delivery, outbox.of("auth.password_changed")[0], final_attempt=True) == "sent"


# --- HTTP -----------------------------------------------------------------------------------


@dataclass
class Http:
    client: TestClient
    world: World
    headers_cache: dict[str, dict[str, str]] = field(default_factory=dict)

    def headers(self, email: str) -> dict[str, str]:
        if email not in self.headers_cache:
            body = {"email": email, "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            self.headers_cache[email] = {
                "Authorization": f"Bearer {response.json()['access_token']}"
            }
        return self.headers_cache[email]

    def post(self, path: str, email: str, body: dict[str, Any]) -> Any:
        return self.client.post(API + path, json=body, headers=self.headers(email))

    def get(self, path: str, email: str) -> Any:
        return self.client.get(API + path, headers=self.headers(email))


@pytest.fixture
def http(tmp_path: Path, world: World, outbox: Outbox) -> Iterator[Http]:
    settings = make_settings(database_url=database_url(tmp_path), web_origin=ORIGIN)
    app, _ = auth_app(settings)
    with TestClient(app) as client:
        yield Http(client, world)


def _body(world: World, **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "scope": "projects",
        "project_ids": [str(world.a)],
        "subject": "Site shutdown",
        "body": "The site closes on Friday.\nPlan ahead.",
    }
    body.update(fields)
    return body


def test_the_api_previews_counts_only_and_hides_foreign_projects(http: Http) -> None:
    world = http.world
    preview = http.post("/preview", PM, {"scope": "projects", "project_ids": [str(world.a)]})
    assert preview.status_code == 200, preview.text
    assert preview.json() == {"recipient_count": 3}
    foreign = http.post("/preview", PM, {"scope": "projects", "project_ids": [str(world.b)]})
    unknown = http.post("/preview", PM, {"scope": "projects", "project_ids": [str(uuid.uuid4())]})
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()
    assert http.post("/preview", PM, {"scope": "tenant"}).status_code == 403
    assert (
        http.post(
            "/preview", ALICE, {"scope": "projects", "project_ids": [str(world.a)]}
        ).status_code
        == 403
    )
    wide = http.post("/preview", ADMIN, {"scope": "tenant", "category": "internal"})
    # Sign-in recomputes is_internal (no internal domains here): the same count as the service.
    internal = world.preview(ADMIN, Audience("tenant", category="internal"))
    assert internal >= 1  # erin never signs in here
    assert wide.json() == {"recipient_count": internal}
    assert http.client.post(API + "/preview", json={"scope": "tenant"}).status_code == 401


def test_the_api_queues_reads_and_counts_deliveries(http: Http, outbox: Outbox) -> None:
    world = http.world
    created = http.post("", PM, _body(world))
    assert created.status_code == 202, created.text
    data = created.json()
    assert (data["status"], data["recipient_count"], data["delivery"]) == ("queued", 3, None)
    assert data["project_ids"] == [str(world.a)]
    assert data["personas"] == []
    assert http.post("", PM, _body(world, project_ids=[str(world.b)])).status_code == 404
    assert http.post("", PM, _body(world, scope="tenant", project_ids=[])).status_code == 403
    nobody = http.post("", ADMIN, _body(world, project_ids=[str(world.b)], category="client"))
    assert nobody.status_code == 422

    broadcast_id = uuid.UUID(data["id"])
    _dispatch(world, broadcast_id)
    transport = FakeTransport()
    delivery = Delivery(world.db, transport, make_settings(web_origin=ORIGIN))
    for mail in outbox.of():
        deliver(delivery, mail, final_attempt=True)
    read = http.get(f"/{broadcast_id}", PM)
    assert read.status_code == 200, read.text
    body = read.json()
    assert (body["status"], body["queued_count"], body["skipped_count"]) == ("sent", 3, 0)
    assert body["delivery"]["sent"] == 3
    assert body["delivery"]["total"] == 3
    assert http.get(f"/{broadcast_id}", ALICE).status_code == 404
    assert [b["id"] for b in http.get("", PM).json()] == [str(broadcast_id)]
    assert http.get("", ALICE).status_code == 403


def test_the_api_rate_limits_each_sender(http: Http) -> None:
    for n in range(broadcasts.RATE_LIMIT):
        assert http.post("", PM, _body(http.world, subject=f"N{n}")).status_code == 202
    limited = http.post("", PM, _body(http.world))
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0


def test_no_broadcast_while_impersonating(http: Http) -> None:
    world = http.world
    started = http.client.post(
        f"/api/v1/admin/users/{world.users[PM]}/impersonate",
        json={"reason": "Ticket 1: synthetic support case"},
        headers=http.headers(ADMIN),
    )
    assert started.status_code == 200, started.text
    as_pm = {"Authorization": f"Bearer {started.json()['access_token']}"}
    refused = http.client.post(API, json=_body(world), headers=as_pm)
    assert refused.status_code == 403
    assert refused.json()["code"] == "IMPERSONATION_ACTION_FORBIDDEN"
    with world.db.unit_of_work(TENANT) as s:
        assert s.scalars(select(Broadcast)).all() == []


def test_one_click_unsubscribe_gives_no_oracle(http: Http, outbox: Outbox) -> None:
    world = http.world
    assert http.post("", PM, _body(world)).status_code == 202
    with world.db.unit_of_work(TENANT) as s:
        broadcast_id = s.scalars(select(Broadcast.id)).one()
    _dispatch(world, broadcast_id)
    token = str(next(m for m in outbox.of() if m.to == ALICE).context["unsubscribe_token"])
    one_click = {"List-Unsubscribe": "One-Click"}  # RFC 8058 form body, ignored

    def call(query: str, **kwargs: Any) -> tuple[int, Any, str]:
        response = http.client.post(f"{ONE_CLICK}{query}", **kwargs)
        return response.status_code, response.json(), response.headers["content-type"]

    first = call(f"?token={token}", data=one_click)
    assert first == (200, {"status": "ok"}, "application/json")
    assert world.opted_out() == {world.users[ALICE]}
    answers = [
        call(f"?token={token}", data=one_click),  # used
        call(f"?token={'A' * 43}"),  # unknown
        call("?token=short"),  # malformed
        call("?token=%00%0a<script>"),
        call(f"?token={'x' * 5000}"),
        call(""),  # no token
        call(f"?user_id={world.users[BOB]}"),  # never a user id
        call(f"?token={sha256_hex(token)}"),  # the stored hash is not a token
    ]
    assert all(answer == first for answer in answers)
    assert world.opted_out() == {world.users[ALICE]}  # nothing else changed
    # A GET (a link scanner) never unsubscribes.
    assert http.client.get(f"{ONE_CLICK}?token={token}").status_code == 405


def test_the_spa_unsubscribe_takes_the_token_in_the_body_only(http: Http, outbox: Outbox) -> None:
    world = http.world
    assert http.post("", PM, _body(world)).status_code == 202
    with world.db.unit_of_work(TENANT) as s:
        broadcast_id = s.scalars(select(Broadcast.id)).one()
    _dispatch(world, broadcast_id)
    tokens = {m.to: str(m.context["unsubscribe_token"]) for m in outbox.of()}

    def call(**kwargs: Any) -> tuple[int, Any]:
        response = http.client.post(UNSUBSCRIBE, **kwargs)
        return response.status_code, response.json()

    first = call(json={"token": tokens[BOB]})
    assert first == (200, {"status": "ok"})
    answers = [
        call(json={"token": tokens[BOB]}),  # used
        call(json={"token": "B" * 43}),  # unknown
        call(json={"token": "a b"}),  # malformed
        call(json={}),
        call(),  # no body
        call(params={"token": tokens[ALICE]}),  # a token in the URL is never read here
        call(json={"user_id": str(world.users[ALICE])}),
        call(json={"token": "C" * (UnsubscribeWrite.MAX_TOKEN + 1)}),  # over-long: neutral too
        call(json={"token": tokens[ALICE] + "x" * 5000}),
    ]
    assert all(answer == first for answer in answers)
    assert world.opted_out() == {world.users[BOB]}
    # The one-click route is for mail providers only: not in the API schema, and no API
    # operation takes a token from the URL.
    spec = http.client.app.openapi()  # type: ignore[attr-defined]
    assert ONE_CLICK not in spec["paths"]
    assert UNSUBSCRIBE in spec["paths"]


def test_unsubscribe_without_a_deployment_tenant_is_unavailable(
    tmp_path: Path, world: World
) -> None:
    app, _ = auth_app(make_settings(database_url=database_url(tmp_path), default_tenant_id=None))
    with TestClient(app) as client:
        assert client.post(f"{ONE_CLICK}?token={'A' * 43}").status_code == 503
        assert client.post(UNSUBSCRIBE, json={"token": "A" * 43}).status_code == 503


# --- migration ------------------------------------------------------------------------------


def _migration() -> Any:
    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    spec = importlib.util.spec_from_file_location("m0043", next(versions.glob("0043_*.py")))
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_migration_0043_creates_and_drops_the_tables(tmp_path: Path) -> None:
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0043", "0042")
    url = database_url(tmp_path)
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=url, configure_logger=False)
    command.upgrade(cfg, "0043")
    database = Database(url)
    try:
        names = set(inspect(database.engine).get_table_names())
        for model in (Broadcast, BroadcastRecipient, BroadcastOptOut):
            assert model.__tablename__ in names
            columns = {c["name"] for c in inspect(database.engine).get_columns(model.__tablename__)}
            assert columns == {c.name for c in model.__table__.columns}
        database.dispose()
        command.downgrade(cfg, "0042")
        database = Database(url)
        names = set(inspect(database.engine).get_table_names())
        assert not names & {BROADCAST_TABLE, RECIPIENT_TABLE, OPTOUT_TABLE}
    finally:
        database.dispose()
        command.downgrade(cfg, "base")
