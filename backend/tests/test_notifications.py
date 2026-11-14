"""Notifications module: record contract, channels, service, API (T-0019).

Runs on SQLite and, in CI, on PostgreSQL under project RLS. All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.auth.access import Persona
from app.core.auth.models import ProjectMembership
from app.core.config import Settings
from app.core.db import Base, Database, contract_violations, utcnow
from app.core.module_loader.loader import discover_modules
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import RLS_EXEMPT, rls_tables
from app.core.tenancy.scope import OutOfScopeError
from app.modules.notifications import channels, service
from app.modules.notifications.channels import DeliveryResult, Recipient
from app.modules.notifications.models import TABLE, Notification
from app.modules.notifications.schemas import NotificationCreate, NotificationPost
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, T1, T2, auth_app, make_settings, seed_user
from tests.db_support import database_url

# Provisioning context: a System admin session reads every project row (T-0014, TD-053).
CTX_T1 = TenancyContext(tenant_id=T1, is_system_admin=True)
CTX_T2 = TenancyContext(tenant_id=T2)
CAROL = "carol@example.test"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def _project(db: Database, code: str, ctx: TenancyContext = CTX_T1) -> TenancyContext:
    with db.unit_of_work(ctx) as s:
        project_id = projects.create_project(
            s, ProjectCreate.model_validate({"code": code, "name": code})
        ).id
    return TenancyContext(tenant_id=ctx.tenant_id, project_id=project_id)


def _grant(
    db: Database,
    user: uuid.UUID,
    project: uuid.UUID | None,
    persona: Persona = Persona.FIELD_WORKER,
    tenant: uuid.UUID = T1,
) -> None:
    assert project is not None
    with db.unit_of_work(TenancyContext(tenant_id=tenant)) as s:
        s.add(
            ProjectMembership(
                tenant_id=tenant, user_id=user, member_project_id=project, persona=persona.value
            )
        )


@pytest.fixture
def home(db: Database) -> TenancyContext:
    return _project(db, "HOME")


@pytest.fixture
def members(db: Database, home: TenancyContext) -> dict[str, uuid.UUID]:
    users = {email: seed_user(db, T1, email) for email in (ALICE, BOB, CAROL)}
    _grant(db, users[ALICE], home.project_id)
    _grant(db, users[BOB], home.project_id)
    return users


def _data(recipient: uuid.UUID, **fields: Any) -> NotificationCreate:
    values: dict[str, Any] = {"recipient_id": str(recipient), "kind": "handover.received"}
    values.update(fields)
    return NotificationCreate.model_validate(values)


def _notify(
    db: Database,
    ctx: TenancyContext,
    recipient: uuid.UUID,
    channel_names: tuple[str, ...] = ("in_app",),
    **fields: Any,
) -> service.Delivery:
    with db.unit_of_work(ctx) as s:
        return service.notify(s, _data(recipient, **fields), channels=channel_names)


def _list(
    db: Database, ctx: TenancyContext, recipient: uuid.UUID, **kwargs: Any
) -> list[Notification]:
    with db.unit_of_work(ctx) as s:
        return service.list_notifications(s, recipient, **kwargs)


class RecordingChannel:
    """A fake second channel (stands in for FCM push or e-mail)."""

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.sent: list[tuple[uuid.UUID, Recipient]] = []

    def deliver(self, notification: Notification, recipient: Recipient) -> DeliveryResult:
        self.sent.append((notification.id, recipient))
        return DeliveryResult(channel=self.name, ok=True)


class RefusingChannel:
    name = "refusing"

    def deliver(self, notification: Notification, recipient: Recipient) -> DeliveryResult:
        return DeliveryResult(channel=self.name, ok=False, detail="no_device")


class BrokenChannel:
    name = "broken"

    def deliver(self, notification: Notification, recipient: Recipient) -> DeliveryResult:
        raise ConnectionError("synthetic outage")


@pytest.fixture
def fake() -> Iterator[RecordingChannel]:
    channel = RecordingChannel()
    channels.register_channel("fake", lambda: channel)
    channels.register_channel("broken", BrokenChannel)
    channels.register_channel("refusing", RefusingChannel)
    yield channel
    channels.unregister_channel("fake")
    channels.unregister_channel("refusing")
    channels.unregister_channel("broken")


# --- contract -------------------------------------------------------------------------------


def test_notification_table_meets_the_record_contract_and_is_under_rls() -> None:
    assert TABLE in Base.metadata.tables
    assert [p for p in contract_violations(Base.metadata) if p.startswith(TABLE)] == []
    assert TABLE in rls_tables(Base.metadata)
    assert TABLE not in RLS_EXEMPT


def test_notifications_module_is_loaded() -> None:
    manifest = discover_modules().get("notifications")
    assert manifest.toggleable is False
    assert set(manifest.depends_on) == {"projects"}


# --- request validation ---------------------------------------------------------------------


def test_defaults() -> None:
    data = _data(uuid.uuid4())
    assert (data.priority, data.pinned, data.label, data.params) == ("normal", False, None, {})


@pytest.mark.parametrize(
    "fields",
    [
        {"priority": "urgent"},
        {"kind": "Handover"},
        {"kind": ""},
        {"label": "Handover Label"},
        {"pinned": True},  # a pin needs the key of the action that clears it
        {"pin_key": "handover:1"},  # a key without a pin
        {"subject_type": "handover.handover"},  # subject type without id
        {"subject_id": str(uuid.uuid4())},  # subject id without type
        {"params": {"Bad Key": "x"}},
        {"params": {"nested": {"a": 1}}},
        {"params": {f"k{i}": i for i in range(21)}},
        {"tenant_id": str(T2)},
    ],
)
def test_bad_notifications_are_rejected(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _data(uuid.uuid4(), **fields)


def test_the_api_body_cannot_pin() -> None:
    with pytest.raises(ValidationError):
        NotificationPost.model_validate(
            {"recipient_id": str(uuid.uuid4()), "kind": "a.b", "pinned": True, "pin_key": "k"}
        )


def test_settings_require_the_in_app_channel() -> None:
    assert Settings().notification_channels == ("in_app",)
    assert Settings(notification_channels=("in_app", "fcm")).notification_channels == (
        "in_app",
        "fcm",
    )
    with pytest.raises(ValidationError):
        Settings(notification_channels=("fcm",))


# --- channel registry -----------------------------------------------------------------------


def test_the_in_app_channel_is_registered_by_default() -> None:
    assert "in_app" in channels.registered_channels()
    assert channels.get_channel("in_app").name == "in_app"
    with pytest.raises(channels.UnknownChannelError):
        channels.get_channel("carrier_pigeon")


def test_a_channel_name_is_registered_once(fake: RecordingChannel) -> None:
    with pytest.raises(ValueError, match="already registered"):
        channels.register_channel("fake", RecordingChannel)
    channels.register_channel("fake", lambda: fake, replace=True)  # explicit replace is fine
    with pytest.raises(ValueError, match="in_app"):
        channels.unregister_channel("in_app")


def test_the_in_app_channel_only_confirms_a_stored_row_for_its_recipient() -> None:
    owner, other, project = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    note = Notification(id=uuid.uuid4(), recipient_id=owner)
    in_app = channels.InAppChannel()
    assert in_app.deliver(note, Recipient(T1, project, owner)) == DeliveryResult("in_app", True)
    assert in_app.deliver(note, Recipient(T1, project, other)) == DeliveryResult(
        "in_app", False, "not_stored"
    )


# --- delivery -------------------------------------------------------------------------------


def test_a_created_notification_is_listable_by_its_recipient_and_can_be_marked_read(
    db: Database, home: TenancyContext, members: dict[str, uuid.UUID]
) -> None:
    alice, bob = members[ALICE], members[BOB]
    delivery = _notify(db, home, alice, params={"handover_no": 7})
    assert [(r.channel, r.ok) for r in delivery.results] == [("in_app", True)]
    note_id = delivery.notification.id

    listed = _list(db, home, alice)
    assert [n.id for n in listed] == [note_id]
    assert (listed[0].kind, listed[0].params, listed[0].read_at) == (
        "handover.received",
        {"handover_no": 7},
        None,
    )
    assert _list(db, home, bob) == []  # only the recipient sees it

    with db.unit_of_work(home) as s:
        read = service.mark_read(s, note_id, alice)
        first = read.read_at
    assert first is not None
    with db.unit_of_work(home) as s:
        again = service.mark_read(s, note_id, alice).read_at
        assert again is not None
        assert again.replace(tzinfo=None) == first.replace(tzinfo=None)  # idempotent
    assert _list(db, home, alice, unread_only=True) == []
    assert [n.id for n in _list(db, home, alice)] == [note_id]


def test_only_the_recipient_marks_read(
    db: Database, home: TenancyContext, members: dict[str, uuid.UUID]
) -> None:
    note_id = _notify(db, home, members[ALICE]).notification.id
    with db.unit_of_work(home) as s, pytest.raises(OutOfScopeError):
        service.mark_read(s, note_id, members[BOB])
    with db.unit_of_work(home) as s, pytest.raises(OutOfScopeError):
        service.mark_read(s, uuid.uuid4(), members[ALICE])


def test_the_recipient_must_be_a_member_of_the_project(
    db: Database, home: TenancyContext, members: dict[str, uuid.UUID]
) -> None:
    with db.unit_of_work(home) as s, pytest.raises(service.RecipientNotMemberError):
        service.notify(s, _data(members[CAROL]), channels=("in_app",))
    with db.unit_of_work(home) as s, pytest.raises(service.RecipientNotMemberError):
        service.notify(s, _data(uuid.uuid4()), channels=("in_app",))


def test_the_service_needs_a_project_bound_session(
    db: Database, members: dict[str, uuid.UUID]
) -> None:
    with db.unit_of_work(CTX_T1) as s, pytest.raises(RuntimeError, match="project"):
        service.notify(s, _data(members[ALICE]))


def test_one_notification_goes_to_every_configured_channel(
    db: Database, home: TenancyContext, members: dict[str, uuid.UUID], fake: RecordingChannel
) -> None:
    alice = members[ALICE]
    delivery = _notify(db, home, alice, ("in_app", "fake"))
    assert [(r.channel, r.ok) for r in delivery.results] == [("in_app", True), ("fake", True)]
    assert home.project_id is not None
    expected = Recipient(tenant_id=T1, project_id=home.project_id, user_id=alice)
    assert fake.sent == [(delivery.notification.id, expected)]
    assert [n.id for n in _list(db, home, alice)] == [delivery.notification.id]


def test_a_failing_channel_does_not_block_in_app_delivery(
    db: Database,
    home: TenancyContext,
    members: dict[str, uuid.UUID],
    fake: RecordingChannel,
    caplog: pytest.LogCaptureFixture,
) -> None:
    alice = members[ALICE]
    delivery = _notify(db, home, alice, ("in_app", "broken", "missing", "refusing", "fake"))
    assert [(r.channel, r.ok, r.detail) for r in delivery.results] == [
        ("in_app", True, None),
        ("broken", False, "ConnectionError"),
        ("missing", False, "unknown_channel"),
        ("refusing", False, "no_device"),
        ("fake", True, None),
    ]
    assert len(fake.sent) == 1  # channels after the failing one still run
    assert [n.id for n in _list(db, home, alice)] == [delivery.notification.id]
    assert "broken" in caplog.text
    assert "synthetic outage" not in caplog.text  # no channel payload or error text in logs


def test_in_app_is_always_delivered_first(
    db: Database, home: TenancyContext, members: dict[str, uuid.UUID], fake: RecordingChannel
) -> None:
    delivery = _notify(db, home, members[ALICE], ("fake",))
    assert [r.channel for r in delivery.results] == ["in_app", "fake"]
    delivery = _notify(db, home, members[ALICE], ("fake", "in_app"))
    assert [r.channel for r in delivery.results] == ["in_app", "fake"]


def test_the_default_channels_come_from_settings(
    db: Database,
    home: TenancyContext,
    members: dict[str, uuid.UUID],
    fake: RecordingChannel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service, "get_settings", lambda: Settings(notification_channels=("in_app", "fake"))
    )
    with db.unit_of_work(home) as s:
        delivery = service.notify(s, _data(members[ALICE]))
    assert [r.channel for r in delivery.results] == ["in_app", "fake"]


# --- order, pins ----------------------------------------------------------------------------


def _age(db: Database, ctx: TenancyContext, note_id: uuid.UUID, minutes: int) -> None:
    with db.unit_of_work(ctx) as s:
        note = s.get(Notification, note_id)
        assert note is not None
        note.created_at = utcnow() - timedelta(minutes=minutes)


def test_the_list_is_pinned_first_then_priority_then_newest(
    db: Database, home: TenancyContext, members: dict[str, uuid.UUID]
) -> None:
    alice = members[ALICE]
    made: dict[str, uuid.UUID] = {}
    for name, minutes, fields in [
        ("old_low", 50, {"priority": "low"}),
        ("new_low", 5, {"priority": "low"}),
        ("old_critical", 40, {"priority": "critical"}),
        ("normal", 1, {}),
        ("high", 30, {"priority": "high"}),
        ("pinned_low", 60, {"priority": "low", "pinned": True, "pin_key": "a"}),
        ("pinned_high", 70, {"priority": "high", "pinned": True, "pin_key": "b"}),
    ]:
        made[name] = _notify(db, home, alice, **fields).notification.id
        _age(db, home, made[name], minutes)
    names = {v: k for k, v in made.items()}
    order = [names[n.id] for n in _list(db, home, alice)]
    assert order == [
        "pinned_high",
        "pinned_low",
        "old_critical",
        "high",
        "normal",
        "new_low",
        "old_low",
    ]
    page = [names[n.id] for n in _list(db, home, alice, limit=2, offset=2)]
    assert page == ["old_critical", "high"]


def test_a_pin_stays_until_the_caller_action_clears_it(
    db: Database, home: TenancyContext, members: dict[str, uuid.UUID]
) -> None:
    alice, bob = members[ALICE], members[BOB]
    key = f"handover.ack:{uuid.uuid4()}"
    for user in (alice, bob):
        _notify(db, home, user, priority="high", label="handover", pinned=True, pin_key=key)
    note = _list(db, home, alice)[0]
    assert (note.pinned, note.label, note.priority) == (True, "handover", "high")
    with db.unit_of_work(home) as s:
        service.mark_read(s, note.id, alice)
    assert _list(db, home, alice)[0].pinned is True  # reading does not unpin

    with db.unit_of_work(home) as s:
        assert service.clear_pins(s, pin_key=key, recipient_id=alice) == 1
    cleared = _list(db, home, alice)[0]
    assert (cleared.pinned, cleared.unpinned_at is not None) == (False, True)
    assert _list(db, home, bob)[0].pinned is True  # Bob has not acknowledged yet
    with db.unit_of_work(home) as s:
        assert service.clear_pins(s, pin_key=key) == 1  # every remaining recipient
        assert service.clear_pins(s, pin_key=key) == 0
    assert _list(db, home, bob)[0].pinned is False


# --- tenancy --------------------------------------------------------------------------------


def test_notifications_stay_in_their_project_and_tenant(
    db: Database, home: TenancyContext, members: dict[str, uuid.UUID]
) -> None:
    alice = members[ALICE]
    other = _project(db, "OTHER")
    _grant(db, alice, other.project_id)
    note_id = _notify(db, home, alice).notification.id
    assert _list(db, other, alice) == []
    with db.unit_of_work(other) as s, pytest.raises(OutOfScopeError):
        service.mark_read(s, note_id, alice)
    with db.unit_of_work(other) as s:
        assert service.clear_pins(s, pin_key="x") == 0

    foreign = _project(db, "HOME", CTX_T2)
    foreign_ctx = TenancyContext(tenant_id=T2, project_id=foreign.project_id)
    with db.unit_of_work(foreign_ctx) as s:
        assert service.list_notifications(s, alice) == []
        with pytest.raises(OutOfScopeError):
            service.mark_read(s, note_id, alice)


# --- HTTP (real tokens, memberships from the store) -----------------------------------------


class Api:
    def __init__(self, client: TestClient, db: Database) -> None:
        self.client, self.db = client, db
        self.users = {email: seed_user(db, T1, email) for email in (ALICE, BOB, CAROL)}
        self.home = _project(db, "HOME")
        _grant(db, self.users[ALICE], self.home.project_id, Persona.PROJECT_ADMINISTRATOR)
        _grant(db, self.users[BOB], self.home.project_id, Persona.FIELD_WORKER)

    def headers(self, email: str) -> dict[str, str]:
        body = {"email": email, "password": PASSWORD}  # tenant is the deployment's (T-0037)
        response = self.client.post("/api/v1/auth/login", json=body)
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def url(self, ctx: TenancyContext | None = None) -> str:
        return f"/api/v1/notifications/projects/{(ctx or self.home).project_id}/notifications"


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    settings = make_settings(database_url=database_url(tmp_path))
    app, _runtime = auth_app(settings)
    with TestClient(app) as client:
        yield Api(client, db)


def test_the_api_round_trip(api: Api) -> None:
    alice, bob = api.headers(ALICE), api.headers(BOB)
    body = {
        "recipient_id": str(api.users[BOB]),
        "kind": "shift.briefing",
        "priority": "critical",
        "label": "safety",
        "params": {"area": "Unit 3"},
    }
    created = api.client.post(api.url(), json=body, headers=alice)
    assert created.status_code == 201, created.text
    payload = created.json()
    note = payload["notification"]
    assert (note["recipient_id"], note["priority"], note["pinned"], note["read_at"]) == (
        str(api.users[BOB]),
        "critical",
        False,
        None,
    )
    assert payload["deliveries"] == [{"channel": "in_app", "ok": True, "detail": None}]

    listed = api.client.get(api.url(), headers=bob)
    assert listed.status_code == 200
    assert [n["id"] for n in listed.json()] == [note["id"]]
    assert api.client.get(api.url(), headers=alice).json() == []  # Alice sent, Bob receives

    read = api.client.post(f"{api.url()}/{note['id']}/read", headers=bob)
    assert read.status_code == 200
    assert read.json()["read_at"] is not None
    assert api.client.get(api.url() + "?unread_only=true", headers=bob).json() == []


def test_the_api_checks_membership_and_capability(api: Api) -> None:
    alice, bob, carol = (api.headers(e) for e in (ALICE, BOB, CAROL))
    body = {"recipient_id": str(api.users[BOB]), "kind": "a.b"}
    note = api.client.post(api.url(), json=body, headers=alice).json()["notification"]
    # Bob is a field worker: reads his own notifications, cannot send.
    assert api.client.post(api.url(), json=body, headers=bob).status_code == 403
    # Carol is no member: the project does not exist for her.
    assert api.client.get(api.url(), headers=carol).status_code == 404
    assert api.client.post(f"{api.url()}/{note['id']}/read", headers=carol).status_code == 404
    # Alice is a member but not the recipient: Bob's notification does not exist for her.
    assert api.client.post(f"{api.url()}/{note['id']}/read", headers=alice).status_code == 404
    # A recipient outside the project is refused.
    stranger = {"recipient_id": str(api.users[CAROL]), "kind": "a.b"}
    assert api.client.post(api.url(), json=stranger, headers=alice).status_code == 422
    # The API cannot pin (no API action could clear it).
    pinned = {**body, "pinned": True, "pin_key": "k"}
    assert api.client.post(api.url(), json=pinned, headers=alice).status_code == 422
    # No token: 401.
    assert api.client.get(api.url()).status_code == 401


def test_the_api_validates_paging(api: Api) -> None:
    bob = api.headers(BOB)
    assert api.client.get(api.url() + "?limit=0", headers=bob).status_code == 422
    assert api.client.get(api.url() + "?limit=201", headers=bob).status_code == 422
    assert api.client.get(api.url() + "?offset=-1", headers=bob).status_code == 422


def test_a_deleted_project_is_404(api: Api) -> None:
    bob = api.headers(BOB)
    with api.db.unit_of_work(CTX_T1) as s:
        projects.delete_project(s, api.home.project_id)  # type: ignore[arg-type]
    assert api.client.get(api.url(), headers=bob).status_code == 404
