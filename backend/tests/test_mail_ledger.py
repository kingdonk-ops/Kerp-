"""The delivery ledger (T-0226, diff §4.7 Delivery ledger, ADR 0020).

The §4.6 send log, extended: ``delivered`` from signed SES ``Delivery`` notifications through
the hardened SNS endpoint, ``recipient_user_id`` / ``broadcast_id`` (``project_id`` is the
record's own), forward-only status changes, the stats and the searchable history. Runs on
SQLite and, in CI's PostgreSQL step, under RLS. Synthetic data only.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.db import Database
from app.core.mail import service
from app.core.mail.addresses import address_hash
from app.core.mail.models import SEND_STATUSES, MailSend, MailSuppression
from app.core.mail.queue import set_mail_enqueuer
from app.core.mail.router import install_sns_verifier
from app.core.mail.service import send_mail
from app.core.mail.sns import SnsVerifier
from app.core.tenancy.context import TenancyContext
from tests.audit_support import ALEMBIC_INI, migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, T1, T2, auth_app, make_settings, seed_user
from tests.db_support import database_url, seed_tenants
from tests.test_mail_sns import (
    BOUNCED,
    CERT_URL,
    FOREIGN_TOPIC,
    REGION,
    TOPIC,
    FakeFetcher,
    Sns,
    _bounce,
    _key_and_cert,
    _notification,
    _signed,
)

MAIL_API = "/api/v1/mail"
TENANT = TenancyContext(tenant_id=T1)
# Reads every send-log row of the tenant, project rows included (ADR 0016).
ALL = TenancyContext(tenant_id=T1, is_system_admin=True)
PROJECT = uuid.UUID("00000000-0000-4000-8000-0000000a0226")
USER = uuid.UUID("00000000-0000-4000-8000-0000000b0226")
BROADCAST = uuid.UUID("00000000-0000-4000-8000-0000000c0226")
TOKEN = "tok-SYNTHETIC-ledger-0123456789abcdef"
OTHER = "other.person@example.test"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def _send(
    db: Database,
    *,
    to: str = BOUNCED,
    message_id: str | None = None,
    status: str = "queued",
    scope: TenancyContext = TENANT,
    template: str = "auth.magic_link",
    created_at: datetime | None = None,
    **ledger: Any,
) -> uuid.UUID:
    """A send-log row written by the real ``send_mail``, then put in ``status``."""
    set_mail_enqueuer(None)
    context: dict[str, object] = {"token": TOKEN, "expires_minutes": 15}
    if template == "auth.password_changed":
        context = {}
    with db.unit_of_work(scope) as session:
        send_id = send_mail(session, template, to, context, **ledger)
        row = session.get(MailSend, send_id)
        assert row is not None
        row.status, row.provider_message_id = status, message_id
        if created_at is not None:
            row.created_at = created_at
    return send_id


def _row(db: Database, send_id: uuid.UUID) -> MailSend:
    with db.unit_of_work(ALL) as session:
        row = session.get(MailSend, send_id)
        assert row is not None
        session.expunge(row)
        return row


def _delivery(message_id: str, recipient: str = BOUNCED) -> dict[str, Any]:
    return {
        "notificationType": "Delivery",
        "mail": {"messageId": message_id, "destination": [recipient]},
        "delivery": {
            "timestamp": "2026-11-22T01:02:03.000Z",
            "recipients": [recipient],
            "smtpResponse": "250 2.6.0 Message received",
        },
    }


def _complaint(message_id: str) -> dict[str, Any]:
    return {
        "notificationType": "Complaint",
        "complaint": {
            "complainedRecipients": [{"emailAddress": BOUNCED}],
            "complaintFeedbackType": "abuse",
        },
        "mail": {"messageId": message_id},
    }


# --- model and migration --------------------------------------------------------------------


def test_delivered_is_a_status_and_the_database_refuses_unknown_ones(db: Database) -> None:
    assert SEND_STATUSES == (
        "queued",
        "sent",
        "delivered",
        "bounced",
        "complained",
        "failed",
        "suppressed",
    )
    send_id = _send(db, message_id="ses-d", status="delivered")
    assert _row(db, send_id).status == "delivered"
    with db.unit_of_work(TENANT) as session:
        row = session.get(MailSend, send_id)
        assert row is not None
        row.status = "opened"
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def _migration() -> ModuleType:
    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    spec = importlib.util.spec_from_file_location("m0039", next(versions.glob("0039_*.py")))
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_migration_0039_freezes_the_table_it_copies() -> None:
    migration = _migration()
    live = MailSend.__table__
    frozen = migration._frozen_send_log(with_ledger=True)
    assert {c.name for c in frozen.columns} == {c.name for c in live.columns}
    assert {c.name for c in frozen.constraints} == {c.name for c in live.constraints}
    assert {i.name for i in frozen.indexes} == {i.name for i in live.indexes}
    before = migration._frozen_send_log(with_ledger=False)
    assert {c.name for c in live.columns} - {c.name for c in before.columns} == set(
        migration.LEDGER_COLUMNS
    )
    assert migration.NEW_STATUSES == SEND_STATUSES
    assert set(migration.NEW_STATUSES) - set(migration.OLD_STATUSES) == {"delivered"}


@pytest.fixture
def migrated(tmp_path: Path) -> Iterator[tuple[Config, Database, str]]:
    url = database_url(tmp_path)
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=url, configure_logger=False)
    command.upgrade(cfg, "head")
    db = Database(url)
    try:
        seed_tenants(db, T1)
        yield cfg, db, url
    finally:
        db.dispose()
        command.downgrade(cfg, "base")


def test_the_migration_keeps_rows_and_its_downgrade_turns_delivered_back_into_sent(
    migrated: tuple[Config, Database, str],
) -> None:
    cfg, db, url = migrated
    delivered = _send(db, message_id="ses-1", status="delivered", recipient_user_id=USER)
    bounced = _send(db, message_id="ses-2", status="bounced")
    db.dispose()
    command.downgrade(cfg, "0038")
    old = Database(url)
    try:
        with old.unit_of_work(TENANT) as session:
            rows = dict(
                session.execute(text("SELECT provider_message_id, status FROM mail_send_log")).all()
            )
            columns = session.execute(text("SELECT * FROM mail_send_log LIMIT 1")).keys()
        assert rows == {"ses-1": "sent", "ses-2": "bounced"}
        assert "recipient_user_id" not in columns
        assert "broadcast_id" not in columns
    finally:
        old.dispose()
    command.upgrade(cfg, "head")
    new = Database(url)
    try:
        assert _row(new, delivered).status == "sent"
        assert _row(new, delivered).recipient_user_id is None
        assert _row(new, bounced).status == "bounced"
    finally:
        new.dispose()


# --- send_mail writes the ledger columns, never the address ----------------------------------


def test_send_mail_records_user_project_and_broadcast_but_never_the_address(
    db: Database,
) -> None:
    scope = TenancyContext(tenant_id=T1, project_id=PROJECT)
    send_id = _send(db, scope=scope, recipient_user_id=USER, broadcast_id=BROADCAST)
    row = _row(db, send_id)
    assert (row.project_id, row.recipient_user_id, row.broadcast_id) == (
        PROJECT,
        USER,
        BROADCAST,
    )
    assert (row.recipient_hash, row.recipient_masked) == (
        address_hash(BOUNCED),
        "b***@example.test",
    )
    values = [str(getattr(row, c.key)).lower() for c in MailSend.__table__.columns]
    assert not any(BOUNCED in value or TOKEN.lower() in value for value in values)
    # A tenant-level send has no project, and the ledger ids are optional.
    plain = _row(db, _send(db))
    assert (plain.project_id, plain.recipient_user_id, plain.broadcast_id) == (None, None, None)


# --- SES delivery notifications over the hardened SNS endpoint -------------------------------


@pytest.fixture
def sns(tmp_path: Path, db: Database) -> Iterator[Sns]:
    settings = make_settings(database_url=database_url(tmp_path), mail_sns_topic_arn=TOPIC)
    app, _ = auth_app(settings)
    fetcher = FakeFetcher()
    install_sns_verifier(app, SnsVerifier(topic_arn=TOPIC, region=REGION, fetcher=fetcher))
    with TestClient(app) as client:
        yield Sns(client, db, fetcher)


def test_a_signed_delivery_of_our_topic_marks_the_send_delivered(sns: Sns) -> None:
    send_id = _send(sns.db, message_id="ses-1", status="sent")
    assert sns.post(_notification(_delivery("ses-1"))) == 204
    row = _row(sns.db, send_id)
    assert row.status == "delivered"
    assert row.status_at is not None
    assert sns.fetcher.urls == [CERT_URL]
    assert sns.suppressed() == []  # a delivery suppresses nothing


def test_a_delivery_of_a_project_send_is_recorded_too(sns: Sns) -> None:
    # The endpoint has no project; the row of an invite (a project's send) is still found.
    scope = TenancyContext(tenant_id=T1, project_id=PROJECT)
    send_id = _send(sns.db, message_id="ses-p", status="sent", scope=scope)
    assert sns.post(_notification(_delivery("ses-p"))) == 204
    assert _row(sns.db, send_id).status == "delivered"
    project_bounce = _send(sns.db, message_id="ses-pb", status="sent", scope=scope)
    assert sns.post(_notification(_bounce("ses-pb"))) == 204
    assert _row(sns.db, project_bounce).status == "bounced"
    [suppression] = sns.suppressed()
    assert suppression.project_id is None  # the suppression list stays tenant-level


def test_repeated_and_late_notifications_change_nothing(sns: Sns) -> None:
    send_id = _send(sns.db, message_id="ses-1", status="sent")
    delivery = _notification(_delivery("ses-1"))
    assert sns.post(delivery) == 204
    first = _row(sns.db, send_id)
    # SNS delivers at least once: the same notification again is a no-op.
    assert sns.post(delivery) == 204
    assert sns.post(_notification(_delivery("ses-1"))) == 204
    again = _row(sns.db, send_id)
    assert (again.status, again.status_at, again.revision) == (
        "delivered",
        first.status_at,
        first.revision,
    )
    # A complaint after the delivery wins; a late delivery never undoes it.
    assert sns.post(_notification(_complaint("ses-1"))) == 204
    assert _row(sns.db, send_id).status == "complained"
    assert sns.post(delivery) == 204
    assert _row(sns.db, send_id).status == "complained"


def test_a_delivery_arriving_after_the_bounce_keeps_the_bounce(sns: Sns) -> None:
    send_id = _send(sns.db, message_id="ses-2", status="sent")
    assert sns.post(_notification(_bounce("ses-2"))) == 204
    assert sns.post(_notification(_delivery("ses-2"))) == 204
    assert _row(sns.db, send_id).status == "bounced"
    assert len(sns.suppressed()) == 1


def test_a_delivery_never_touches_failed_suppressed_or_queued_sends(sns: Sns) -> None:
    ids = {
        status: _send(sns.db, message_id=f"ses-{status}", status=status)
        for status in ("queued", "failed", "suppressed")
    }
    for status in ids:
        assert sns.post(_notification(_delivery(f"ses-{status}"))) == 204
    assert {status: _row(sns.db, send_id).status for status, send_id in ids.items()} == {
        "queued": "queued",
        "failed": "failed",
        "suppressed": "suppressed",
    }
    # Unknown message id, no mail block, garbage: accepted and ignored.
    assert sns.post(_notification(_delivery("ses-unknown"))) == 204
    assert sns.post(_notification({"notificationType": "Delivery", "mail": "garbage"})) == 204


def test_a_delivery_from_a_foreign_topic_or_forged_is_refused_and_changes_nothing(
    sns: Sns,
) -> None:
    send_id = _send(sns.db, message_id="ses-1", status="sent")
    assert sns.post(_notification(_delivery("ses-1"), topic=FOREIGN_TOPIC)) == 403
    assert sns.fetcher.urls == []  # refused before any certificate fetch
    other_key, _ = _key_and_cert()
    genuine = _notification(_delivery("ses-1"))
    forged = _signed({k: v for k, v in genuine.items() if k != "Signature"}, key=other_key)
    assert sns.post(forged) == 403
    off_host = {**genuine, "SigningCertURL": "https://sns.eu-central-1.amazonaws.com/x.pem"}
    unsigned = {k: v for k, v in off_host.items() if k != "Signature"}
    assert sns.post(_signed(unsigned)) == 403
    assert sns.post({**genuine, "SignatureVersion": "1"}) == 403
    assert _row(sns.db, send_id).status == "sent"


def test_a_delivery_naming_another_tenant_is_applied_under_the_deployment_tenant(
    sns: Sns,
) -> None:
    with sns.db.unit_of_work(TenancyContext(tenant_id=T2)) as session:
        foreign = send_mail(
            session, "auth.magic_link", BOUNCED, {"token": TOKEN, "expires_minutes": 1}
        )
        row = session.get(MailSend, foreign)
        assert row is not None
        row.status, row.provider_message_id = "sent", "ses-t2"
    event = _delivery("ses-t2")
    event["mail"]["tags"] = {"tenant_id": [str(T2)]}
    assert sns.post(_notification(event)) == 204
    with sns.db.unit_of_work(TenancyContext(tenant_id=T2, is_system_admin=True)) as session:
        stored = session.get(MailSend, foreign)
        assert stored is not None
        assert stored.status == "sent"


def test_record_ses_event_reports_nothing_suppressed_for_a_delivery(db: Database) -> None:
    send_id = _send(db, message_id="ses-1", status="sent")
    with db.unit_of_work(ALL) as session:
        assert service.record_ses_event(session, _delivery("ses-1")) == 0
        assert session.scalars(select(MailSuppression)).all() == []
    assert _row(db, send_id).status == "delivered"


# --- stats and the searchable history --------------------------------------------------------


class Admin:
    def __init__(self, client: TestClient, db: Database) -> None:
        from app.core.auth.access import SYSTEM_ADMIN
        from app.modules.admin import service as admin

        self.client, self.db = client, db
        alice, self.bob = seed_user(db, T1, ALICE), seed_user(db, T1, BOB)
        with db.unit_of_work(ALL) as session:
            admin.grant(session, alice, SYSTEM_ADMIN, actor_id=None)
        self._headers: dict[str, dict[str, str]] = {}

    def headers(self, email: str = ALICE) -> dict[str, str]:
        if email not in self._headers:
            body = {"email": email, "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            self._headers[email] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._headers[email]

    def get(self, path: str, email: str = ALICE, **params: Any) -> Any:
        return self.client.get(MAIL_API + path, params=params, headers=self.headers(email))


@pytest.fixture
def admin(tmp_path: Path, db: Database) -> Iterator[Admin]:
    app, _ = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        yield Admin(client, db)


DAY = datetime(2026, 11, 20, tzinfo=UTC)


def _ledger(db: Database) -> dict[str, uuid.UUID]:
    """Eleven sends over two days and two templates, one of them a project's."""
    project = TenancyContext(tenant_id=T1, project_id=PROJECT)
    ids: dict[str, uuid.UUID] = {}
    plan = [
        ("queued", "auth.magic_link", 0),
        ("sent", "auth.magic_link", 0),
        ("delivered", "auth.magic_link", 0),
        ("delivered", "auth.magic_link", 0),
        ("bounced", "auth.magic_link", 0),
        ("complained", "auth.magic_link", 0),
        ("failed", "auth.magic_link", 0),
        ("suppressed", "auth.magic_link", 0),
        ("delivered", "auth.password_changed", 0),
        ("sent", "auth.password_changed", 0),
        ("delivered", "auth.password_changed", 1),
    ]
    for n, (status, template, day) in enumerate(plan):
        ids[f"{status}-{template}-{n}"] = _send(
            db,
            to=OTHER if n == 3 else BOUNCED,
            message_id=None if status in ("queued", "failed", "suppressed") else f"ses-{n}",
            status=status,
            template=template,
            scope=project if n == 2 else TENANT,
            created_at=DAY + timedelta(days=day, hours=n),
            broadcast_id=BROADCAST if n in (2, 4) else None,
        )
    return ids


def test_only_system_admins_read_the_stats(admin: Admin) -> None:
    assert admin.client.get(f"{MAIL_API}/stats").status_code == 401
    assert admin.get("/stats", email=BOB).status_code == 403
    assert admin.get("/stats").status_code == 200


def test_stats_count_by_status_and_template_with_the_delivery_rate(admin: Admin) -> None:
    _ledger(admin.db)
    body = admin.get("/stats").json()
    assert body["totals"] == {
        "total": 11,
        "queued": 1,
        "sent": 8,  # accepted by SES: sent, delivered, bounced, complained
        "delivered": 5,  # delivered, plus the complaint (it arrived)
        "bounced": 1,
        "complained": 1,
        "failed": 1,
        "suppressed": 1,
        "delivery_rate": 0.625,
    }
    by_template = {t["template_key"]: t for t in body["by_template"]}
    assert list(by_template) == ["auth.magic_link", "auth.password_changed"]
    assert by_template["auth.password_changed"]["sent"] == 3
    assert by_template["auth.password_changed"]["delivered"] == 2
    assert by_template["auth.password_changed"]["delivery_rate"] == 0.6667
    assert by_template["auth.magic_link"]["total"] == 8
    # Counts only: no hash, masked address or id leaves the endpoint.
    assert "recipient" not in json.dumps(body)
    assert "b***" not in json.dumps(body)


def test_stats_filter_by_period_and_template(admin: Admin) -> None:
    _ledger(admin.db)
    day_two = (DAY + timedelta(days=1)).isoformat()
    later = admin.get("/stats", **{"from": day_two}).json()
    assert later["totals"]["total"] == 1
    assert later["created_from"] is not None
    earlier = admin.get("/stats", to=day_two).json()
    assert earlier["totals"]["total"] == 10  # ``to`` is exclusive
    window = admin.get(
        "/stats",
        **{"from": DAY.isoformat(), "to": (DAY + timedelta(hours=2)).isoformat()},
    ).json()
    assert window["totals"]["total"] == 2
    only = admin.get("/stats", template="auth.password_changed").json()
    assert only["template_key"] == "auth.password_changed"
    assert only["totals"]["total"] == 3
    assert [t["template_key"] for t in only["by_template"]] == ["auth.password_changed"]
    empty = admin.get("/stats", template="no.such").json()
    assert empty["totals"]["total"] == 0
    assert empty["totals"]["delivery_rate"] is None
    assert empty["by_template"] == []


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-11-21T00:00:00Z", "to": "2026-11-20T00:00:00Z"},  # reversed
        {"from": "2026-11-21T00:00:00"},  # no offset
        {"to": "yesterday"},
        {"template": ""},
        {"template": "x" * 101},
    ],
)
def test_bad_stats_filters_are_422(admin: Admin, params: dict[str, str]) -> None:
    assert admin.get("/stats", **params).status_code == 422


def test_the_history_filters_by_template_status_period_and_broadcast(admin: Admin) -> None:
    ids = _ledger(admin.db)
    every = admin.get("/sends", limit=200).json()
    assert len(every) == 11
    assert every[0]["created_at"] > every[-1]["created_at"]  # newest first
    project_row = next(s for s in every if s["project_id"] is not None)
    assert project_row["project_id"] == str(PROJECT)
    delivered = admin.get("/sends", status="delivered").json()
    assert len(delivered) == 4
    assert {s["status"] for s in delivered} == {"delivered"}
    template = admin.get("/sends", template="auth.password_changed").json()
    assert len(template) == 3
    both = admin.get("/sends", template="auth.password_changed", status="sent").json()
    assert [s["id"] for s in both] == [str(ids["sent-auth.password_changed-9"])]
    day_two = (DAY + timedelta(days=1)).isoformat()
    assert len(admin.get("/sends", **{"from": day_two}).json()) == 1
    assert len(admin.get("/sends", to=day_two).json()) == 10
    broadcast = admin.get("/sends", broadcast_id=str(BROADCAST)).json()
    assert {s["broadcast_id"] for s in broadcast} == {str(BROADCAST)}
    assert len(broadcast) == 2
    assert admin.get("/sends", broadcast_id=str(uuid.uuid4())).json() == []
    assert admin.get("/sends", status="opened").status_code == 422
    assert admin.get("/sends", broadcast_id="nope").status_code == 422
    reversed_period = {"from": day_two, "to": DAY.isoformat()}
    assert admin.get("/sends", **reversed_period).status_code == 422


def test_the_history_looks_up_an_exact_address_by_its_hash(admin: Admin) -> None:
    ids = _ledger(admin.db)
    # Normalised before hashing: case and surrounding blanks do not matter.
    found = admin.get("/sends", address="  Other.Person@Example.TEST ").json()
    assert [s["id"] for s in found] == [str(ids["delivered-auth.magic_link-3"])]
    assert found[0]["recipient_hash"] == address_hash(OTHER)
    assert found[0]["recipient_masked"] == "o***@example.test"
    assert OTHER not in json.dumps(found)
    # Exact, not a prefix or a domain search.
    assert admin.get("/sends", address="other.person@example.tes").json() == []
    assert len(admin.get("/sends", address=BOUNCED).json()) == 10
    refused = admin.get("/sends", address="not an address")
    assert refused.status_code == 422
    assert "not an address" not in refused.text  # the input is never echoed


def test_the_history_and_stats_stay_in_the_callers_tenant(admin: Admin) -> None:
    _ledger(admin.db)
    with admin.db.unit_of_work(TenancyContext(tenant_id=T2)) as session:
        send_mail(session, "auth.magic_link", OTHER, {"token": TOKEN, "expires_minutes": 1})
    assert admin.get("/stats").json()["totals"]["total"] == 11
    assert len(admin.get("/sends", address=OTHER).json()) == 1


def test_the_service_filters_compose(db: Database) -> None:
    _ledger(db)
    filters = service.SendFilter(
        template_key="auth.magic_link",
        created_from=DAY + timedelta(hours=1),
        created_to=DAY + timedelta(hours=6),
        recipient_hash=address_hash(BOUNCED),
    )
    with db.unit_of_work(ALL) as session:
        rows = service.list_sends(session, filters)
        stats = service.send_stats(session, filters)
    # Hours 1-5 of the magic-link sends, minus the one to OTHER (hour 3).
    assert sorted(r.status for r in rows) == ["bounced", "complained", "delivered", "sent"]
    assert (stats.totals.total, stats.totals.sent, stats.totals.delivered) == (4, 4, 2)
    assert stats.totals.delivery_rate == 0.5
    assert list(stats.by_template) == ["auth.magic_link"]
