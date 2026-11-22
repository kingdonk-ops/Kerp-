"""Outbound e-mail core service (T-0058, diff §4.6): import boundary, record contract,
templates, enqueue-after-commit, delivery with the fake transport (retry, permanent failure,
suppression), the SES transport, start-up configuration and the jobs-module hook.

Runs on SQLite and, in CI, on PostgreSQL under RLS against the migrated schema. Synthetic data.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import subprocess
import sys
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from saq import Job as SaqJob
from sqlalchemy import select

from app.core.auth.models import User
from app.core.auth.service import create_user
from app.core.config import Settings
from app.core.db import Base, Database, contract_violations
from app.core.mail import i18n
from app.core.mail.addresses import (
    InvalidAddressError,
    address_hash,
    mask_address,
    normalize_address,
)
from app.core.mail.models import SEND_TABLE, SUPPRESSION_TABLE, MailSend, MailSuppression
from app.core.mail.queue import QueuedMail, set_mail_enqueuer
from app.core.mail.service import Delivery, RetryMail, deliver, send_mail, suppress
from app.core.mail.templates import (
    MAGIC_LINK,
    TEMPLATES,
    MailTemplate,
    TemplateError,
    TemplateRegistry,
    render,
)
from app.core.mail.transport import (
    FakeTransport,
    LogTransport,
    MailConfigError,
    PermanentMailError,
    SesTransport,
    TransientMailError,
    check_mail_config,
    transport_from_settings,
)
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import expected_policy_names, rls_tables
from app.main import create_app
from app.modules.jobs import mail as jobs_mail
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, T1, auth_app, make_settings, seed_field_worker
from tests.db_support import database_url

BACKEND = Path(__file__).resolve().parents[1]
CORE = BACKEND / "app" / "core"
TOKEN = "tok-SYNTHETIC-0123456789abcdefABCDEF"  # synthetic, URL-safe
BOB_MAIL = "Bob.Example@Example.Test"
TOPIC = "arn:aws:sns:ap-southeast-2:123456789012:kerp-mail"
SCOPE = TenancyContext(tenant_id=T1)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


class Captured:
    """A mail enqueuer that keeps what core hands it."""

    def __init__(self) -> None:
        self.mails: list[QueuedMail] = []

    def __call__(self, mails: Sequence[QueuedMail]) -> None:
        self.mails.extend(mails)


@pytest.fixture
def captured() -> Iterator[Captured]:
    hook = Captured()
    set_mail_enqueuer(hook)
    yield hook
    set_mail_enqueuer(None)


def _settings(**overrides: Any) -> Settings:
    return make_settings(web_origin="https://kerp.example.test", **overrides)


def _queue(db: Database, to: str = BOB_MAIL, token: str = TOKEN) -> uuid.UUID:
    with db.unit_of_work(SCOPE) as session:
        return send_mail(session, "auth.magic_link", to, {"token": token, "expires_minutes": 15})


def _row(db: Database, send_id: uuid.UUID) -> MailSend:
    with db.unit_of_work(SCOPE) as session:
        row = session.get(MailSend, send_id)
        assert row is not None
        session.expunge(row)
        return row


def _values(row: Base) -> list[str]:
    return [str(getattr(row, c.key)) for c in row.__table__.columns]  # type: ignore[attr-defined]


# --- boundary and contract ------------------------------------------------------------------


def _module_imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            names += [f"{node.module}.{alias.name}" for alias in node.names]
    return names


def test_core_never_imports_a_module() -> None:
    # Diff §4.6: the enqueue reaches core through the hook the jobs module installs.
    offenders = [
        f"{path.relative_to(BACKEND).as_posix()}: {name}"
        for path in sorted(CORE.rglob("*.py"))
        for name in _module_imports(path)
        if name == "app.modules" or name.startswith("app.modules.")
    ]
    assert offenders == []


def test_importing_core_mail_loads_no_module_and_no_boto3() -> None:
    code = (
        "import sys\n"
        "import app.core.mail.service, app.core.mail.router, app.core.mail.sns\n"
        "import app.core.mail.transport, app.core.auth.field\n"
        "loaded = sorted(m for m in sys.modules if m.startswith('app.modules'))\n"
        "assert not loaded, loaded\n"
        "assert 'boto3' not in sys.modules and 'botocore' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], cwd=BACKEND, check=True)


def test_both_tables_meet_the_record_contract_under_standard_rls() -> None:
    for table in (SEND_TABLE, SUPPRESSION_TABLE):
        assert [p for p in contract_violations(Base.metadata) if p.startswith(table)] == []
        assert table in rls_tables(Base.metadata)
        assert expected_policy_names(Base.metadata.tables[table]) == (
            "project_isolation_policy",
            "tenant_level_read_policy",
            "allowed_projects_read_policy",
        )


# --- addresses ------------------------------------------------------------------------------


def test_addresses_are_normalised_hashed_and_masked() -> None:
    assert normalize_address("  Bob.Example@Example.Test ") == "bob.example@example.test"
    assert mask_address(BOB_MAIL) == "b***@example.test"
    assert address_hash(BOB_MAIL) == address_hash("bob.example@example.test")
    assert len(address_hash(BOB_MAIL)) == 64
    for bad in ("", "no-at", "a@b", "a@b.c\r\nBcc: x@y.test", "a@b.test, c@d.test", "<a@b.test>"):
        with pytest.raises(InvalidAddressError):
            normalize_address(bad)


# --- templates ------------------------------------------------------------------------------


def test_every_registered_template_renders_in_en_au_without_missing_keys() -> None:
    catalog = i18n.catalog("en-AU")
    assert TEMPLATES.all(), "no templates registered"
    for template in TEMPLATES.all():
        missing = [key for key in template.i18n_keys() if key not in catalog]
        assert missing == [], f"{template.key}: {missing}"
        context: dict[str, str | int] = dict.fromkeys(template.params, 7)
        if template.secret:
            context[template.secret] = TOKEN
        rendered = render(
            template, context, locale="en-AU", app_name="KERP", web_origin="https://k.test"
        )
        for part in (rendered.subject, rendered.text, rendered.html):
            assert f"mail.{template.key}" not in part
            assert "mail.common" not in part
            assert "{" not in part.replace("{font", "")  # no unfilled placeholder


def test_the_magic_link_puts_the_token_only_in_the_link_fragment() -> None:
    rendered = render(
        MAGIC_LINK,
        {"token": TOKEN, "expires_minutes": 15},
        locale="en-AU",
        app_name="KERP",
        web_origin="https://kerp.example.test/",
    )
    link = f"https://kerp.example.test/magic-link#{TOKEN}"
    assert TOKEN not in rendered.subject
    assert rendered.text.count(TOKEN) == 1
    assert link in rendered.text
    assert rendered.html.count(TOKEN) == 2  # href + the copyable fallback, both the link
    assert rendered.html.count(link) == 2
    assert "15 minutes" in rendered.text
    lowered = rendered.html.lower()
    for remote in ("<img", "src=", "<script", "<link", "url(", "@import"):
        assert remote not in lowered
    assert lowered.count("http") == 2  # only the link (twice)


def test_a_public_link_is_a_plain_page_of_the_app_without_a_fragment() -> None:
    """A template may link an ordinary page (the lobby, T-0063) only when it says so
    (``public_link``); it then carries no secret and its URL has no fragment."""
    template = MailTemplate(
        "access_requests.approved",
        params=("project_code", "project_name"),
        link_path="/",
        public_link=True,
    )
    rendered = render(
        template,
        {"project_code": "SYN", "project_name": "Synthetic site"},
        locale="en-AU",
        app_name="KERP",
        web_origin="https://kerp.example.test/",
    )
    assert "https://kerp.example.test/\n" in rendered.text
    assert "#" not in rendered.text
    assert rendered.html.count('href="https://kerp.example.test/"') == 1
    assert rendered.html.lower().count("http") == 2  # the link and its copyable fallback
    with pytest.raises(ValueError, match="public link"):
        MailTemplate("test.bad", link_path="/x", secret="token", public_link=True)
    with pytest.raises(ValueError, match="public link"):
        MailTemplate("test.bad", public_link=True)


def test_html_escapes_context_values() -> None:
    registry = TemplateRegistry()
    template = registry.add(MailTemplate("test.escaped", params=("expires_minutes",)))
    with pytest.raises(i18n.MissingTranslationError):  # no catalog entry for it
        render(template, {"expires_minutes": 1}, locale="en-AU", app_name="K", web_origin="x")
    rendered = render(
        MAGIC_LINK,
        {"token": TOKEN, "expires_minutes": 15},
        locale="en-AU",
        app_name="<b>KERP</b>",
        web_origin="https://k.test",
    )
    assert "<b>KERP</b>" not in rendered.html
    assert "&lt;b&gt;KERP&lt;/b&gt;" in rendered.html


def test_locale_falls_back_to_the_language_then_the_default() -> None:
    assert i18n.locale_chain("en-NZ") == ("en-NZ", "en", "en-AU")
    assert i18n.locale_chain(None, "de-DE") == ("de-DE", "en-AU")
    assert i18n.t("mail.auth.magic_link.action", "fr-FR") == "Sign in"
    with pytest.raises(i18n.MissingTranslationError):
        i18n.t("mail.nope", "en-AU")
    with pytest.raises(i18n.MissingTranslationError, match="app_name"):
        i18n.t("mail.common.footer", "en-AU")


def test_template_context_is_checked() -> None:
    with pytest.raises(TemplateError, match="unknown"):
        TEMPLATES.get("auth.nope")
    with pytest.raises(TemplateError, match="needs context"):
        MAGIC_LINK.check_context({"token": TOKEN})
    with pytest.raises(TemplateError, match="string or an integer"):
        MAGIC_LINK.check_context({"token": TOKEN, "expires_minutes": 1.5})
    with pytest.raises(TemplateError, match="URL-safe"):
        MAGIC_LINK.check_context({"token": "short", "expires_minutes": 15})
    with pytest.raises(TemplateError, match="line break"):
        MAGIC_LINK.check_context({"token": TOKEN, "expires_minutes": "1\n2"})
    with pytest.raises(ValueError, match="secret"):
        MailTemplate("test.bad", link_path="/x")
    with pytest.raises(ValueError, match="never a text parameter"):
        MailTemplate("test.bad", params=("token",), link_path="/x", secret="token")
    with pytest.raises(ValueError, match="<area>"):
        MailTemplate("Bad Key")
    with pytest.raises(ValueError, match="plain path"):
        MailTemplate("test.bad", link_path="https://evil.test", secret="token")
    registry = TemplateRegistry()
    registry.add(MailTemplate("test.one"))
    registry.add(MailTemplate("test.one"))  # the same definition again is fine
    with pytest.raises(ValueError, match="already registered"):
        registry.add(MailTemplate("test.one", params=("x",)))


# --- request side: enqueue only, after commit ------------------------------------------------


def test_send_mail_only_enqueues_after_commit(db: Database, captured: Captured) -> None:
    transport = FakeTransport()
    with db.unit_of_work(SCOPE) as session:
        send_id = send_mail(
            session, "auth.magic_link", BOB_MAIL, {"token": TOKEN, "expires_minutes": 15}
        )
        assert captured.mails == []  # nothing before the commit
    assert [m.send_id for m in captured.mails] == [send_id]
    assert transport.sent == []  # nothing is ever sent from the request
    mail = captured.mails[0]
    assert (mail.to, mail.tenant_id, mail.context["token"]) == (
        "bob.example@example.test",
        T1,
        TOKEN,
    )
    assert TOKEN not in repr(mail)
    assert "bob" not in repr(mail)
    row = _row(db, send_id)
    assert (row.status, row.template_key, row.recipient_masked) == (
        "queued",
        "auth.magic_link",
        "b***@example.test",
    )
    assert row.recipient_hash == address_hash(BOB_MAIL)
    assert not any(TOKEN in value or "bob.example" in value for value in _values(row))


def _send_then_fail(db: Database) -> None:
    with db.unit_of_work(SCOPE) as session:
        send_mail(session, "auth.magic_link", BOB_MAIL, {"token": TOKEN, "expires_minutes": 1})
        raise RuntimeError("business action failed")


def test_a_rollback_sends_nothing(db: Database, captured: Captured) -> None:
    with pytest.raises(RuntimeError, match="business action"):
        _send_then_fail(db)
    with db.unit_of_work(SCOPE) as session:
        nested = session.begin_nested()
        send_mail(session, "auth.magic_link", BOB_MAIL, {"token": TOKEN, "expires_minutes": 1})
        nested.rollback()
        kept = send_mail(session, "auth.magic_link", ALICE, {"token": TOKEN, "expires_minutes": 1})
    assert [m.send_id for m in captured.mails] == [kept]


def test_without_a_queue_the_send_stays_queued(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    set_mail_enqueuer(None)
    with caplog.at_level(logging.WARNING, logger="kerp.mail"):
        send_id = _queue(db)
    assert "stay queued" in caplog.text
    assert TOKEN not in caplog.text
    assert _row(db, send_id).status == "queued"


def test_an_enqueue_failure_never_undoes_the_commit(db: Database) -> None:
    def broken(_mails: Sequence[QueuedMail]) -> None:
        raise ConnectionError("redis down")

    set_mail_enqueuer(broken)
    try:
        send_id = _queue(db)
    finally:
        set_mail_enqueuer(None)
    assert _row(db, send_id).status == "queued"


def test_send_mail_refuses_bad_input_before_writing(db: Database) -> None:
    with db.unit_of_work(SCOPE) as session:
        with pytest.raises(TemplateError):
            send_mail(session, "auth.nope", BOB_MAIL, {})
        with pytest.raises(InvalidAddressError):
            send_mail(session, "auth.magic_link", "x", {"token": TOKEN, "expires_minutes": 1})
        assert session.scalars(select(MailSend)).all() == []
    with contextmanager(db.session)(None) as unbound, pytest.raises(RuntimeError, match="bound"):
        send_mail(unbound, "auth.magic_link", BOB_MAIL, {"token": TOKEN, "expires_minutes": 1})


def test_magic_link_request_queues_the_mail_and_never_sends_it(
    tmp_path: Path, db: Database, captured: Captured
) -> None:
    seed_field_worker(db, T1, ALICE)  # PER-14: may sign in by magic link (T-0057)
    settings = _settings(database_url=database_url(tmp_path))
    app, _ = auth_app(settings)  # default sender: the mail service
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/magic-link", json={"email": ALICE})
        assert response.status_code == 202
        unknown = client.post("/api/v1/auth/magic-link", json={"email": "nobody@example.test"})
        assert unknown.status_code == 202
    assert [m.template_key for m in captured.mails] == ["auth.magic_link"]
    token = str(captured.mails[0].context["token"])
    with db.unit_of_work(SCOPE) as session:
        rows = session.scalars(select(MailSend)).all()
        assert len(rows) == 1
        assert not any(token in value for value in _values(rows[0]))


# --- worker side: deliver --------------------------------------------------------------------


def _mail(db: Database, captured: Captured, to: str = BOB_MAIL) -> QueuedMail:
    _queue(db, to)
    return captured.mails[-1]


def test_deliver_sends_and_records_the_provider_id(db: Database, captured: Captured) -> None:
    transport = FakeTransport()
    mail = _mail(db, captured)
    delivery = Delivery(db, transport, _settings())
    assert deliver(delivery, mail, final_attempt=False) == "sent"
    [message] = transport.sent
    assert message.to == "bob.example@example.test"
    assert f"https://kerp.example.test/magic-link#{TOKEN}" in message.rendered.text
    row = _row(db, mail.send_id)
    assert (row.status, row.attempts) == ("sent", 1)
    assert row.provider_message_id is not None
    assert row.provider_message_id.startswith("fake-")
    assert row.sent_at is not None
    assert not any(TOKEN in value for value in _values(row))
    # SAQ delivers at least once: a second run sends nothing more.
    assert deliver(delivery, mail, final_attempt=False) == "sent"
    assert len(transport.sent) == 1


def test_transient_errors_retry_then_fail_on_the_last_attempt(
    db: Database, captured: Captured
) -> None:
    transport = FakeTransport()
    transport.fail_with(TransientMailError("ses:Throttling"), TransientMailError("ses:Throttling"))
    mail = _mail(db, captured)
    delivery = Delivery(db, transport, _settings())
    with pytest.raises(RetryMail):
        deliver(delivery, mail, final_attempt=False)
    row = _row(db, mail.send_id)
    assert (row.status, row.attempts, row.error_code) == ("queued", 1, "ses:Throttling")
    assert deliver(delivery, mail, final_attempt=True) == "failed"
    row = _row(db, mail.send_id)
    assert (row.status, row.attempts) == ("failed", 2)
    assert transport.sent == []


def test_a_retry_after_a_transient_error_can_succeed(db: Database, captured: Captured) -> None:
    transport = FakeTransport()
    transport.fail_with(TransientMailError("ses:ServiceUnavailable"))
    mail = _mail(db, captured)
    delivery = Delivery(db, transport, _settings())
    with pytest.raises(RetryMail):
        deliver(delivery, mail, final_attempt=False)
    assert deliver(delivery, mail, final_attempt=False) == "sent"
    row = _row(db, mail.send_id)
    assert (row.status, row.attempts, row.error_code) == ("sent", 2, None)


def test_a_permanent_failure_is_logged_failed_and_never_retried(
    db: Database, captured: Captured, caplog: pytest.LogCaptureFixture
) -> None:
    transport = FakeTransport()
    transport.fail_with(PermanentMailError("ses:MessageRejected"))
    mail = _mail(db, captured)
    with caplog.at_level(logging.INFO, logger="kerp.mail"):
        assert deliver(Delivery(db, transport, _settings()), mail, final_attempt=False) == "failed"
    row = _row(db, mail.send_id)
    assert (row.status, row.error_code) == ("failed", "ses:MessageRejected")
    assert TOKEN not in caplog.text
    assert "bob.example" not in caplog.text


def test_a_failed_send_never_rolls_back_the_business_action(
    db: Database, captured: Captured
) -> None:
    transport = FakeTransport()
    transport.fail_with(PermanentMailError("ses:MessageRejected"))
    with db.unit_of_work(SCOPE) as session:  # the caller's business action plus its e-mail
        user = create_user(session, tenant_id=T1, email="carol@example.test", password=None).id
        send_mail(session, "auth.magic_link", BOB_MAIL, {"token": TOKEN, "expires_minutes": 1})
    status = deliver(Delivery(db, transport, _settings()), captured.mails[-1], final_attempt=True)
    assert status == "failed"
    with db.unit_of_work(SCOPE) as session:
        assert session.get(User, user) is not None


def test_a_suppressed_address_is_skipped_and_logged_suppressed(
    db: Database, captured: Captured
) -> None:
    with db.unit_of_work(SCOPE) as session:
        suppress(session, BOB_MAIL, "bounce", detail="Permanent/General")
        again = suppress(session, BOB_MAIL.upper(), "complaint")  # idempotent per address
        assert again.reason == "bounce"
    transport = FakeTransport()
    mail = _mail(db, captured)
    assert deliver(Delivery(db, transport, _settings()), mail, final_attempt=False) == (
        "suppressed"
    )
    assert transport.sent == []
    assert _row(db, mail.send_id).status == "suppressed"
    with db.unit_of_work(SCOPE) as session:
        [row] = session.scalars(select(MailSuppression)).all()
        assert row.recipient_masked == "b***@example.test"
        assert not any("bob.example" in value for value in _values(row))


def test_template_problems_fail_the_send(db: Database, captured: Captured) -> None:
    mail = _mail(db, captured)
    broken = QueuedMail.from_payload({**mail.to_payload(), "context": {}})
    assert deliver(Delivery(db, FakeTransport(), _settings()), broken, final_attempt=False) == (
        "failed"
    )
    assert _row(db, mail.send_id).error_code == "template:KeyError"


def test_a_missing_row_is_reported(db: Database, captured: Captured) -> None:
    mail = _mail(db, captured)
    ghost = QueuedMail.from_payload({**mail.to_payload(), "send_id": str(uuid.uuid4())})
    assert deliver(Delivery(db, FakeTransport(), _settings()), ghost, final_attempt=False) == (
        "missing"
    )


def test_development_without_a_web_origin_links_to_the_dev_server(
    db: Database, captured: Captured
) -> None:
    transport = FakeTransport()
    mail = _mail(db, captured)
    deliver(Delivery(db, transport, make_settings()), mail, final_attempt=False)
    assert "http://localhost:5173/magic-link#" in transport.sent[0].rendered.text


# --- transports and start-up configuration --------------------------------------------------


class StubSes:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[dict[str, object]] = []

    def send_email(self, **request: object) -> dict[str, object]:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return {"MessageId": "ses-message-1"}


def _message() -> Any:
    from app.core.mail.transport import OutboundMessage

    rendered = render(
        MAGIC_LINK,
        {"token": TOKEN, "expires_minutes": 15},
        locale=None,
        app_name="KERP",
        web_origin="https://k.test",
    )
    return OutboundMessage("bob@example.test", rendered, "auth.magic_link")


def _client_error(code: str, status: int) -> Exception:
    from botocore.exceptions import ClientError

    response: Any = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}
    return ClientError(response, "SendEmail")


def test_ses_transport_sends_with_the_configuration_set() -> None:
    client = StubSes()
    transport = SesTransport(
        region="ap-southeast-2",
        from_address="KERP <no-reply@example.test>",
        configuration_set="kerp-mail",
        client=client,
    )
    assert transport.send(_message()) == "ses-message-1"
    [request] = client.requests
    assert request["FromEmailAddress"] == "KERP <no-reply@example.test>"
    assert request["ConfigurationSetName"] == "kerp-mail"
    assert request["Destination"] == {"ToAddresses": ["bob@example.test"]}
    content: Any = request["Content"]
    assert set(content["Simple"]["Body"]) == {"Text", "Html"}


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (("Throttling", 400), TransientMailError),
        (("TooManyRequestsException", 429), TransientMailError),
        (("InternalFailure", 500), TransientMailError),
        (("MessageRejected", 400), PermanentMailError),
        (("MailFromDomainNotVerifiedException", 400), PermanentMailError),
    ],
)
def test_ses_errors_are_transient_or_permanent(error: tuple[str, int], kind: type) -> None:
    transport = SesTransport(
        region="ap-southeast-2",
        from_address="no-reply@example.test",
        client=StubSes(_client_error(*error)),
    )
    with pytest.raises(kind) as caught:
        transport.send(_message())
    assert caught.value.code == f"ses:{error[0]}"


def test_ses_network_errors_are_transient() -> None:
    from botocore.exceptions import EndpointConnectionError

    error = EndpointConnectionError(endpoint_url="https://email.ap-southeast-2.amazonaws.com")
    transport = SesTransport(
        region="ap-southeast-2", from_address="no-reply@example.test", client=StubSes(error)
    )
    with pytest.raises(TransientMailError, match="EndpointConnectionError"):
        transport.send(_message())


def test_ses_client_is_built_lazily_in_the_deployment_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import boto3
    from botocore.config import Config

    calls: list[tuple[str, str]] = []
    configs: list[Config] = []

    def fake_client(service: str, region_name: str, config: Config) -> StubSes:
        calls.append((service, region_name))
        configs.append(config)
        return StubSes()

    monkeypatch.setattr(boto3, "client", fake_client)
    settings = _settings(
        mail_transport="ses", mail_from_address="no-reply@example.test", aws_region="eu-central-1"
    )
    transport = transport_from_settings(settings)
    assert isinstance(transport, SesTransport)
    assert calls == []
    transport.send(_message())
    transport.send(_message())
    assert calls == [("sesv2", "eu-central-1")]
    # Bounded (T-0346): one send ends well inside the mail job's timeout, so a slow SES
    # cannot outlive the job and send the same mail again on the job's retry.
    [config] = configs
    assert (config.connect_timeout, config.read_timeout) == (5, 20)
    assert config.retries == {"total_max_attempts": 2}
    budget = config.retries["total_max_attempts"] * (config.connect_timeout + config.read_timeout)
    assert budget < jobs_mail.MAIL_TIMEOUT_S


def _production(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "production",
        "mail_transport": "ses",
        "mail_from_address": "no-reply@example.test",
        "mail_sns_topic_arn": TOPIC,
        "web_origin": "https://kerp.example.test",
        "jwt_private_key": None,
    }
    values.update(overrides)
    return make_settings(**values)


def test_production_start_up_fails_when_mail_is_not_configured() -> None:
    check_mail_config(_production())  # complete: fine
    for missing in ("mail_from_address", "mail_sns_topic_arn", "web_origin"):
        with pytest.raises(MailConfigError, match="not configured"):
            check_mail_config(_production(**{missing: None}))
    with pytest.raises(MailConfigError, match="KERP_MAIL_TRANSPORT=ses"):
        check_mail_config(_production(mail_transport=None))
    with pytest.raises(MailConfigError, match="KERP_MAIL_TRANSPORT=ses"):
        check_mail_config(_production(mail_transport="log"))
    with pytest.raises(MailConfigError, match="KERP_AWS_REGION"):
        check_mail_config(_production(aws_region="eu-central-1"))
    with pytest.raises(MailConfigError, match="e-mail address"):
        check_mail_config(_settings(mail_from_address="no-at-sign"))
    with pytest.raises(MailConfigError, match="needs KERP_MAIL_FROM_ADDRESS"):
        transport_from_settings(_settings(mail_transport="ses"))


def test_the_api_refuses_to_start_in_production_without_mail() -> None:
    app = create_app(_production(mail_transport=None))
    with pytest.raises(MailConfigError), TestClient(app):
        pass


def test_the_log_transport_refuses_production_and_logs_in_development(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(MailConfigError, match="never runs in production"):
        LogTransport(_production())
    transport = transport_from_settings(make_settings(environment="development"))
    assert isinstance(transport, LogTransport)
    with caplog.at_level(logging.WARNING, logger="kerp.mail"):
        assert transport.send(_message()).startswith("log-")
    assert "magic-link#" in caplog.text  # a developer can follow the link


# --- the jobs module's hook ------------------------------------------------------------------


class MemoryRedis:
    """The async Redis calls the payload store makes, in memory (TTL recorded, not enforced)."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.ttl: dict[str, int | None] = {}

    async def set(self, name: str, value: str, ex: int | None = None) -> bool:
        self.data[name], self.ttl[name] = value.encode(), ex
        return True

    async def get(self, name: str) -> bytes | None:
        return self.data.get(name)

    async def delete(self, *names: str) -> int:
        return sum(self.data.pop(name, None) is not None for name in names)


class FakeQueue:
    """The interactive queue as ``jobs.mail`` uses it: an async Redis and ``enqueue``."""

    def __init__(self) -> None:
        self.redis = MemoryRedis()
        self.jobs: list[SaqJob] = []

    async def enqueue(self, job_or_func: SaqJob) -> SaqJob | None:
        self.jobs.append(job_or_func)
        return job_or_func


def test_the_jobs_module_installs_the_hook_and_enqueues_ids_only(
    db: Database,
) -> None:
    queue = FakeQueue()

    async def scenario() -> None:
        dispatcher = jobs_mail.install(queue, asyncio.get_running_loop())
        try:
            # ``after_commit`` runs in FastAPI's worker threads: hand over from a thread.
            worker = threading.Thread(target=_queue, args=(db,))
            worker.start()
            await asyncio.to_thread(worker.join)
            await dispatcher.drain()
        finally:
            jobs_mail.uninstall()

    asyncio.run(scenario())
    [job] = queue.jobs
    assert job.function == jobs_mail.SEND_MAIL
    assert job.retries == jobs_mail.MAIL_RETRIES
    assert job.retry_backoff
    assert job.ttl == -1  # the SAQ record is dropped when the job finishes
    assert TOKEN not in json.dumps(job.kwargs)
    assert "bob" not in json.dumps(job.kwargs)
    assert job.kwargs is not None
    key = jobs_mail.payload_key(job.kwargs["send_id"])
    assert TOKEN in queue.redis.data[key].decode()
    assert queue.redis.ttl[key] == jobs_mail.PAYLOAD_TTL_S


def test_a_failed_enqueue_is_logged_and_leaves_the_row_queued(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    class Broken(FakeQueue):
        async def enqueue(self, job_or_func: SaqJob) -> SaqJob | None:
            raise ConnectionError("redis down")

    async def scenario() -> uuid.UUID:
        dispatcher = jobs_mail.install(Broken(), asyncio.get_running_loop())
        try:
            send_id = _queue(db)  # on the loop's own thread
            await dispatcher.drain()
            return send_id
        finally:
            jobs_mail.uninstall()

    with caplog.at_level(logging.ERROR, logger="kerp.jobs"):
        send_id = asyncio.run(scenario())
    assert "stays queued" in caplog.text
    assert TOKEN not in caplog.text
    assert _row(db, send_id).status == "queued"


def _job_ctx(queue: FakeQueue, db: Database, transport: FakeTransport, attempts: int) -> Any:
    job = SaqJob(function=jobs_mail.SEND_MAIL, retries=jobs_mail.MAIL_RETRIES)
    job.attempts = attempts  # the worker counts the attempt before it runs the function
    return {"job": job} | jobs_mail.worker_context(queue, Delivery(db, transport, _settings()))


def test_the_worker_job_delivers_retries_and_cleans_up(db: Database, captured: Captured) -> None:
    queue = FakeQueue()
    mail = _mail(db, captured)
    asyncio.run(jobs_mail.put(queue, mail))
    kwargs = queue.jobs[0].kwargs or {}
    transport = FakeTransport()
    transport.fail_with(TransientMailError("ses:Throttling"))
    with pytest.raises(RetryMail):  # SAQ retries it with back-off
        asyncio.run(jobs_mail.send_mail_job(_job_ctx(queue, db, transport, 1), **kwargs))
    key = jobs_mail.payload_key(mail.send_id)
    assert asyncio.run(queue.redis.get(key)) is not None  # kept for the retry
    status = asyncio.run(jobs_mail.send_mail_job(_job_ctx(queue, db, transport, 2), **kwargs))
    assert status == "sent"
    assert len(transport.sent) == 1
    assert asyncio.run(queue.redis.get(key)) is None


def test_the_last_attempt_records_failed(db: Database, captured: Captured) -> None:
    queue = FakeQueue()
    mail = _mail(db, captured)
    asyncio.run(jobs_mail.put(queue, mail))
    transport = FakeTransport()
    transport.fail_with(TransientMailError("ses:Throttling"))
    ctx = _job_ctx(queue, db, transport, jobs_mail.MAIL_RETRIES)
    assert asyncio.run(jobs_mail.send_mail_job(ctx, **(queue.jobs[0].kwargs or {}))) == "failed"
    assert _row(db, mail.send_id).status == "failed"


def test_an_expired_payload_fails_the_send(db: Database, captured: Captured) -> None:
    queue = FakeQueue()
    mail = _mail(db, captured)
    kwargs = jobs_mail.saq_job(mail).kwargs or {}
    ctx = _job_ctx(queue, db, FakeTransport(), 1)
    assert asyncio.run(jobs_mail.send_mail_job(ctx, **kwargs)) == "failed"
    row = _row(db, mail.send_id)
    assert (row.status, row.error_code) == ("failed", "payload_expired")


def test_a_missing_payload_never_overwrites_a_send_that_was_settled(
    db: Database, captured: Captured
) -> None:
    """SAQ runs a job at least once: a re-run after the send (payload deleted) keeps ``sent``."""
    queue = FakeQueue()
    mail = _mail(db, captured)
    asyncio.run(jobs_mail.put(queue, mail))
    kwargs = queue.jobs[0].kwargs or {}
    transport = FakeTransport()
    assert asyncio.run(jobs_mail.send_mail_job(_job_ctx(queue, db, transport, 1), **kwargs)) == (
        "sent"
    )
    asyncio.run(jobs_mail.send_mail_job(_job_ctx(queue, db, transport, 2), **kwargs))
    row = _row(db, mail.send_id)
    assert (row.status, row.error_code) == ("sent", None)
    assert len(transport.sent) == 1


def test_a_payload_of_another_send_is_refused(db: Database, captured: Captured) -> None:
    queue = FakeQueue()
    mail = _mail(db, captured)
    other = QueuedMail.from_payload({**mail.to_payload(), "tenant_id": str(uuid.uuid4())})
    asyncio.run(jobs_mail.put(queue, other))
    kwargs = jobs_mail.saq_job(mail).kwargs or {}
    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(jobs_mail.send_mail_job(_job_ctx(queue, db, FakeTransport(), 1), **kwargs))
