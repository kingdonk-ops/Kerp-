"""Automated template overrides (T-0227, diff §4.7 Automated templates + Editor details,
ADR 0021, TD-400).

A System admin rewords a registered template per locale (subject, intro, outro, action label)
as plain text with only its declared placeholders; revisions are kept, a reset restores the
shipped default, the next send uses the change, previews use synthetic values and a test send
goes only to the caller's own address. Runs on SQLite and, in CI's PostgreSQL step, under RLS.
Synthetic data only.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import app.modules.access_requests.service
import app.modules.admin.invites  # noqa: F401  (registers the invite template)
from app.core.audit.models import AuditEntry
from app.core.db import Base, Database, contract_violations
from app.core.mail import i18n, overrides
from app.core.mail.addresses import address_hash
from app.core.mail.models import OVERRIDE_TABLE, MailSend, MailTemplateOverride
from app.core.mail.queue import QueuedMail, set_mail_enqueuer
from app.core.mail.service import Delivery, deliver, send_mail
from app.core.mail.templates import (
    AUDIENCES,
    MAGIC_LINK,
    PASSWORD_CHANGED,
    SAMPLE_SECRET,
    TEMPLATES,
    MailTemplate,
    TemplateText,
    override_problems,
    render,
    sample_context,
    shipped_text,
)
from app.core.mail.transport import FakeTransport
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import expected_policy_names, rls_tables
from tests.audit_support import ALEMBIC_INI, migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, T1, T2, auth_app, make_settings, seed_user
from tests.db_support import database_url, seed_tenants

MAIL_API = "/api/v1/mail"
TENANT = TenancyContext(tenant_id=T1)
ADMIN_SCOPE = TenancyContext(tenant_id=T1, is_system_admin=True)
PROJECT = uuid.UUID("00000000-0000-4000-8000-0000000a0227")
TOKEN = "tok-SYNTHETIC-templates-0123456789abcdef"
BOB_MAIL = "bob.example@example.test"
ORIGIN = "https://kerp.example.test"
REQUIRED = {"auth.magic_link", "auth.password_reset", "admin.project_invite"}


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


class Captured:
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


def _context(template: MailTemplate) -> dict[str, str | int]:
    context: dict[str, str | int] = dict.fromkeys(template.params, 7)
    if template.secret:
        context[template.secret] = TOKEN
    return context


def _custom(template: MailTemplate, marker: str = "Custom") -> TemplateText:
    """A valid override using every declared placeholder."""
    names = " ".join(f"{{{name}}}" for name in template.placeholders())
    return TemplateText(
        subject=f"{marker} subject {{app_name}}",
        intro=f"{marker} intro {names}",
        outro=f"{marker} outro",
        action_label=f"{marker} action" if template.has_action else None,
    )


def _save(
    db: Database, key: str, text: TemplateText, locale: str = "en-AU"
) -> MailTemplateOverride:
    with db.unit_of_work(TENANT) as session:
        row = overrides.save_override(session, key, locale, text, actor_id=None)
        session.flush()
        session.expunge(row)
        return row


# --- registry: audience, required, placeholders ---------------------------------------------------


def test_every_template_declares_audience_required_and_placeholders() -> None:
    templates = {template.key: template for template in TEMPLATES.all()}
    assert {key for key, template in templates.items() if template.required} == REQUIRED
    for template in templates.values():
        assert template.audience in AUDIENCES
        assert "app_name" in template.placeholders()
        assert set(template.params) <= set(template.placeholders())
        assert template.secret not in template.placeholders()
        assert template.blocks()[:3] == ("subject", "intro", "outro")
        assert ("action_label" in template.blocks()) == template.has_action
    assert templates["admin.project_invite"].placeholders() == (
        "project_code",
        "project_name",
        "inviter_name",
        "expires_at",
        "app_name",
    )
    with pytest.raises(ValueError, match="audience"):
        MailTemplate("area.name", audience="everyone")  # type: ignore[arg-type]


def test_shipped_text_is_the_unformatted_catalogue_text() -> None:
    text = shipped_text(MAGIC_LINK, "en-NZ", "en-AU")
    catalog = i18n.catalog("en-AU")
    assert text.subject == catalog["mail.auth.magic_link.subject"]
    assert text.action_label == catalog["mail.auth.magic_link.action"]
    assert shipped_text(PASSWORD_CHANGED, "en-AU", "en-AU").action_label is None
    assert i18n.catalog_locales()[0] == "en-AU"


# --- rendering with and without an override -------------------------------------------------------


def test_every_registered_template_renders_in_en_au_with_and_without_an_override() -> None:
    assert TEMPLATES.all()
    for template in TEMPLATES.all():
        shipped = render(
            template, _context(template), locale="en-AU", app_name="KERP", web_origin=ORIGIN
        )
        custom = render(
            template,
            _context(template),
            locale="en-AU",
            app_name="KERP",
            web_origin=ORIGIN,
            override=_custom(template),
        )
        assert custom.subject == "Custom subject KERP"
        assert "Custom subject" not in shipped.subject
        assert custom.text.startswith("Custom intro ")
        for rendered in (shipped, custom):
            for part in (rendered.subject, rendered.text, rendered.html):
                assert f"mail.{template.key}" not in part
                assert "{" not in part.replace("{font", "")
            if template.secret:
                assert f"{ORIGIN}{template.link_path}#{TOKEN}" in rendered.text
                assert rendered.text.count(TOKEN) == 1
        if template.has_action:
            assert "Custom action: " in custom.text
            assert ">Custom action</a>" in custom.html


def test_an_override_still_escapes_values_and_the_renderer_builds_the_link() -> None:
    text = TemplateText(
        subject="Sign in to {app_name}",
        intro="Line one {expires_minutes}\nLine two",
        outro="Bye & thanks",
        action_label="Go",
    )
    rendered = render(
        MAGIC_LINK,
        {"token": TOKEN, "expires_minutes": "<b>9</b>"},
        locale="en-AU",
        app_name="K&<ERP>",
        web_origin=ORIGIN,
        override=text,
    )
    assert "<b>9</b>" not in rendered.html
    assert "&lt;b&gt;9&lt;/b&gt;" in rendered.html
    assert "<title>Sign in to K&amp;&lt;ERP&gt;</title>" in rendered.html
    assert "Line one &lt;b&gt;9&lt;/b&gt;<br>\nLine two" in rendered.html
    assert "Bye &amp; thanks" in rendered.html
    assert f'href="{ORIGIN}/magic-link#{TOKEN}"' in rendered.html


def test_a_non_required_template_may_leave_intro_or_outro_empty() -> None:
    text = TemplateText(subject="Password changed", intro="", outro="")
    assert override_problems(PASSWORD_CHANGED, text) == {}
    rendered = render(
        PASSWORD_CHANGED, {}, locale="en-AU", app_name="KERP", web_origin=ORIGIN, override=text
    )
    assert rendered.text.startswith("--\n")
    assert "<p></p>" not in rendered.html


def test_render_refuses_an_override_that_does_not_fit() -> None:
    with pytest.raises(ValueError, match="unusable override"):
        render(
            MAGIC_LINK,
            {"token": TOKEN, "expires_minutes": 5},
            locale="en-AU",
            app_name="KERP",
            web_origin=ORIGIN,
            override=TemplateText(subject="{token}", intro="x", outro="y", action_label="z"),
        )


# --- the override checks --------------------------------------------------------------------------


def _valid() -> TemplateText:
    return TemplateText(
        subject="Sign in", intro="Expires in {expires_minutes}", outro="Bye", action_label="Go"
    )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("intro", "Hello <b>bold</b>", "HTML"),
        ("intro", "<!-- hidden -->", "HTML"),
        ("outro", "<img src=x>", "HTML"),
        ("intro", "Go to https://evil.example.test now", "no links"),
        ("outro", "see www.evil.example.test", "no links"),
        ("action_label", "javascript:alert(1)", "no links"),
        ("intro", "mailto:someone@example.test", "no links"),
        ("intro", "Your link: {token}", "secret"),
        ("subject", "{token.upper}", "secret"),
        ("intro", "Hello {user_name}", "unknown placeholder"),
        ("intro", "{app_name.__class__}", "unknown placeholder"),
        ("intro", "{expires_minutes!r}", "unknown placeholder"),
        ("intro", "{expires_minutes:>99999}", "unknown placeholder"),
        ("intro", "{0}", "unknown placeholder"),
        ("intro", "{}", "unknown placeholder"),
        ("intro", "Unbalanced {app_name", "unbalanced"),
        ("intro", "Unbalanced }", "unbalanced"),
        ("subject", "Two\nlines", "line breaks"),
        ("action_label", "Tab\there", "control"),
        ("intro", "Bell\x07", "control"),
        ("subject", "x" * 201, "at most 200"),
        ("subject", "   ", "must not be empty"),
        ("action_label", "", "must not be empty"),
        ("intro", "", "must not be empty"),  # magic link is required
        ("outro", " ", "must not be empty"),
    ],
)
def test_override_problems_refuse_what_is_not_plain_declared_text(
    field: str, value: str, reason: str
) -> None:
    problems = override_problems(MAGIC_LINK, _replace(field, value))
    assert list(problems) == [field]
    assert reason in problems[field]


def _replace(field: str, value: str) -> TemplateText:
    base = _valid()
    values = {
        "subject": base.subject,
        "intro": base.intro,
        "outro": base.outro,
        "action_label": base.action_label,
    }
    values[field] = value
    return TemplateText(**values)  # type: ignore[arg-type]


def test_override_problems_accept_plain_text_and_escaped_braces() -> None:
    text = TemplateText(
        subject="{app_name}: sign in",
        intro="Expires in {expires_minutes} minutes. Literal {{braces}} and 3 < 4 > 2.",
        outro="Line one\nLine two",
        action_label="Sign in",
    )
    assert override_problems(MAGIC_LINK, text) == {}
    no_action = TemplateText(subject="Changed", intro="", outro="", action_label="Go")
    assert override_problems(PASSWORD_CHANGED, no_action) == {
        "action_label": "this template has no action link"
    }


# --- storage: revisions, one current row, reset ---------------------------------------------------


def test_the_table_meets_the_record_contract_under_standard_rls() -> None:
    assert [p for p in contract_violations(Base.metadata) if p.startswith(OVERRIDE_TABLE)] == []
    assert OVERRIDE_TABLE in rls_tables(Base.metadata)
    assert expected_policy_names(Base.metadata.tables[OVERRIDE_TABLE]) == (
        "project_isolation_policy",
        "tenant_level_read_policy",
        "allowed_projects_read_policy",
    )


def _revisions(db: Database, key: str = "auth.magic_link", locale: str = "en-AU") -> list[Any]:
    with db.unit_of_work(TENANT) as session:
        return [
            (row.version, row.is_current, row.subject)
            for row in overrides.list_revisions(session, key, locale)
        ]


def test_each_save_is_a_new_revision_and_a_reset_keeps_them(db: Database) -> None:
    first = _save(db, "auth.magic_link", _custom(MAGIC_LINK, "One"))
    second = _save(db, "auth.magic_link", _custom(MAGIC_LINK, "Two"))
    assert (first.version, second.version) == (1, 2)
    assert first.source == "api"
    assert _revisions(db) == [
        (2, True, "Two subject {app_name}"),
        (1, False, "One subject {app_name}"),
    ]
    with db.unit_of_work(TENANT) as session:
        assert overrides.reset_override(session, "auth.magic_link", "en-AU", actor_id=None)
        assert not overrides.reset_override(session, "auth.magic_link", "en-AU", actor_id=None)
        assert overrides.current_override(session, "auth.magic_link", "en-AU") is None
    assert [current for _, current, _ in _revisions(db)] == [False, False]
    third = _save(db, "auth.magic_link", _custom(MAGIC_LINK, "Three"))
    assert (third.version, third.is_current) == (3, True)
    # Another locale has its own revisions.
    assert _save(db, "auth.magic_link", _custom(MAGIC_LINK), locale="de").version == 1


def test_saves_resets_and_test_sends_are_audited(db: Database, captured: Captured) -> None:
    actor = seed_user(db, T1, ALICE)
    with db.unit_of_work(TenancyContext(tenant_id=T1, user_id=actor)) as session:
        row = overrides.save_override(
            session, "auth.magic_link", "en-AU", _custom(MAGIC_LINK), actor_id=actor
        )
        overrides.send_test(session, "auth.magic_link", "en-AU", user_id=actor, email=ALICE)
        overrides.reset_override(session, "auth.magic_link", "en-AU", actor_id=actor)
        override_id = str(row.id)
    with db.unit_of_work(TENANT) as session:
        entries = session.scalars(
            select(AuditEntry)
            .where(AuditEntry.chain_key == "mail-template:auth.magic_link:en-AU")
            .order_by(AuditEntry.seq)
        ).all()
        assert [entry.payload["action"] for entry in entries] == ["update", "test_send", "reset"]
        assert entries[0].payload["override_id"] == override_id
        assert entries[0].payload["version"] == 1
        assert {entry.created_by for entry in entries} == {actor}
        assert all("subject" not in entry.payload for entry in entries)


def test_the_database_allows_one_current_row_per_template_and_locale(db: Database) -> None:
    _save(db, "auth.magic_link", _custom(MAGIC_LINK))
    with db.unit_of_work(TENANT) as session:
        session.add(
            MailTemplateOverride(
                tenant_id=T1,
                template_key="auth.magic_link",
                locale="en-AU",
                version=2,
                subject="s",
                intro="i",
                outro="o",
                action_label="a",
                is_current=True,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_save_refuses_unknown_templates_and_invalid_text(db: Database) -> None:
    from app.core.mail.templates import TemplateError

    with db.unit_of_work(TENANT) as session:
        with pytest.raises(TemplateError):
            overrides.save_override(session, "auth.nope", "en-AU", _valid(), actor_id=None)
        with pytest.raises(overrides.OverrideInvalidError) as caught:
            overrides.save_override(
                session, "auth.magic_link", "en-AU", _replace("intro", "<b>"), actor_id=None
            )
        assert list(caught.value.problems) == ["intro"]
    project = TenancyContext(tenant_id=T1, project_id=PROJECT)
    with pytest.raises(RuntimeError, match="tenant-level"), db.unit_of_work(project) as scoped:
        overrides.save_override(scoped, "auth.magic_link", "en-AU", _valid(), actor_id=None)


def test_save_normalises_line_endings_and_strips_blanks(db: Database) -> None:
    text = TemplateText(subject="  Hi  ", intro="a\r\nb\rc", outro=" o ", action_label=" Go ")
    row = _save(db, "auth.magic_link", text)
    assert (row.subject, row.intro, row.outro, row.action_label) == ("Hi", "a\nb\nc", "o", "Go")


# --- the next send uses the change ----------------------------------------------------------------


def _queue(
    db: Database,
    captured: Captured,
    *,
    locale: str | None = None,
    scope: TenancyContext = TENANT,
) -> QueuedMail:
    with db.unit_of_work(scope) as session:
        send_mail(
            session,
            "auth.magic_link",
            BOB_MAIL,
            {"token": TOKEN, "expires_minutes": 15},
            locale,
        )
    return captured.mails[-1]


def _deliver(db: Database, mail: QueuedMail) -> Any:
    transport = FakeTransport()
    settings = make_settings(web_origin=ORIGIN)
    assert deliver(Delivery(db, transport, settings), mail, final_attempt=False) == "sent"
    [message] = transport.sent
    return message.rendered


def test_the_next_send_uses_the_override_and_a_reset_restores_the_shipped_text(
    db: Database, captured: Captured
) -> None:
    shipped = i18n.t("mail.auth.magic_link.subject", "en-AU", app_name="KERP")
    assert _deliver(db, _queue(db, captured)).subject == shipped
    # Queued before the change, delivered after: the worker renders with the change.
    pending = _queue(db, captured)
    _save(db, "auth.magic_link", _custom(MAGIC_LINK, "Now"))
    rendered = _deliver(db, pending)
    assert rendered.subject == "Now subject KERP"
    assert f"{ORIGIN}/magic-link#{TOKEN}" in rendered.text
    _save(db, "auth.magic_link", _custom(MAGIC_LINK, "Later"))
    assert _deliver(db, _queue(db, captured)).subject == "Later subject KERP"
    with db.unit_of_work(TENANT) as session:
        overrides.reset_override(session, "auth.magic_link", "en-AU", actor_id=None)
    assert _deliver(db, _queue(db, captured)).subject == shipped


def test_the_override_follows_the_recipients_locale_chain(db: Database, captured: Captured) -> None:
    _save(db, "auth.magic_link", _custom(MAGIC_LINK, "AU"))
    _save(db, "auth.magic_link", _custom(MAGIC_LINK, "German"), locale="de")
    assert _deliver(db, _queue(db, captured, locale="en-NZ")).subject == "AU subject KERP"
    assert _deliver(db, _queue(db, captured, locale="de-AT")).subject == "German subject KERP"
    assert _deliver(db, _queue(db, captured, locale="fr")).subject == "AU subject KERP"


def test_a_project_send_uses_the_tenant_override(db: Database, captured: Captured) -> None:
    _save(db, "auth.magic_link", _custom(MAGIC_LINK, "Tenant"))
    project = TenancyContext(tenant_id=T1, project_id=PROJECT)
    assert _deliver(db, _queue(db, captured, scope=project)).subject == "Tenant subject KERP"


def test_another_tenants_override_is_never_used(db: Database, captured: Captured) -> None:
    seed_tenants(db, T2)
    with db.unit_of_work(TenancyContext(tenant_id=T2)) as session:
        overrides.save_override(
            session, "auth.magic_link", "en-AU", _custom(MAGIC_LINK, "T2"), actor_id=None
        )
    rendered = _deliver(db, _queue(db, captured))
    assert "T2" not in rendered.subject


def test_an_override_that_no_longer_fits_is_skipped(
    db: Database, captured: Captured, caplog: pytest.LogCaptureFixture
) -> None:
    with db.unit_of_work(TENANT) as session:
        session.add(
            MailTemplateOverride(
                tenant_id=T1,
                template_key="auth.magic_link",
                locale="en-AU",
                version=1,
                subject="Old {removed_param}",
                intro="i",
                outro="o",
                action_label="a",
                is_current=True,
            )
        )
    shipped = i18n.t("mail.auth.magic_link.subject", "en-AU", app_name="KERP")
    assert _deliver(db, _queue(db, captured)).subject == shipped
    assert "no longer fits" in caplog.text


# --- preview and sample values --------------------------------------------------------------------


def test_sample_context_fits_every_template() -> None:
    for template in TEMPLATES.all():
        context = sample_context(template)
        assert template.check_context(context) == context
        if template.secret:
            assert context[template.secret] == SAMPLE_SECRET


def test_preview_renders_the_draft_or_what_a_send_would_use(db: Database) -> None:
    settings = make_settings()
    with db.unit_of_work(TENANT) as session:
        shipped = overrides.preview(
            session, "admin.project_invite", "en-AU", settings, web_origin=ORIGIN
        )
        assert "SAMPLE-001" in shipped.subject
        assert f"{ORIGIN}/invite#{SAMPLE_SECRET}" in shipped.text
        draft = _custom(TEMPLATES.get("admin.project_invite"), "Draft")
        assert (
            overrides.preview(
                session, "admin.project_invite", "en-AU", settings, web_origin=ORIGIN, draft=draft
            ).subject
            == f"Draft subject {settings.app_name}"
        )
        # Nothing was stored by the preview.
        assert overrides.current_override(session, "admin.project_invite", "en-AU") is None


# --- API ------------------------------------------------------------------------------------------


class Admin:
    def __init__(self, client: TestClient, db: Database) -> None:
        from app.core.auth.access import SYSTEM_ADMIN
        from app.modules.admin import service as admin

        self.client, self.db = client, db
        self.alice, self.bob = seed_user(db, T1, ALICE), seed_user(db, T1, BOB)
        with db.unit_of_work(ADMIN_SCOPE) as session:
            admin.grant(session, self.alice, SYSTEM_ADMIN, actor_id=None)
        self._headers: dict[str, dict[str, str]] = {}

    def headers(self, email: str = ALICE) -> dict[str, str]:
        if email not in self._headers:
            body = {"email": email, "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            self._headers[email] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._headers[email]

    def call(self, method: str, path: str, email: str = ALICE, **kwargs: Any) -> Any:
        return self.client.request(method, MAIL_API + path, headers=self.headers(email), **kwargs)


@pytest.fixture
def admin(tmp_path: Path, db: Database, captured: Captured) -> Iterator[Admin]:
    app, _ = auth_app(make_settings(database_url=database_url(tmp_path), web_origin=ORIGIN))
    with TestClient(app) as client:
        yield Admin(client, db)


MAGIC = "/templates/auth.magic_link/en-AU"
BODY = {
    "subject": "Your {app_name} link",
    "intro": "Expires in {expires_minutes} minutes.",
    "outro": "Thanks.",
    "action_label": "Sign in now",
}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/templates", None),
        ("GET", f"{MAGIC}/revisions", None),
        ("PUT", MAGIC, BODY),
        ("DELETE", MAGIC, None),
        ("POST", f"{MAGIC}/preview", None),
        ("POST", f"{MAGIC}/test-send", None),
    ],
)
def test_only_system_admins_use_the_template_endpoints(
    admin: Admin, method: str, path: str, body: Any
) -> None:
    assert admin.client.request(method, MAIL_API + path, json=body).status_code == 401
    assert admin.call(method, path, email=BOB, json=body).status_code == 403


def test_the_list_shows_every_template_with_placeholders_shipped_text_and_override(
    admin: Admin,
) -> None:
    assert admin.call("PUT", MAGIC, json=BODY).status_code == 200
    body = admin.call("GET", "/templates").json()
    assert [entry["key"] for entry in body] == [t.key for t in TEMPLATES.all()]
    entries = {entry["key"]: entry for entry in body}
    magic = entries["auth.magic_link"]
    assert magic["audience"] == "deployment_wide"
    assert (magic["required"], magic["has_action"]) == (True, True)
    assert magic["placeholders"] == ["expires_minutes", "app_name"]
    [locale] = magic["locales"]
    assert locale["locale"] == "en-AU"
    assert locale["shipped"]["subject"] == i18n.catalog("en-AU")["mail.auth.magic_link.subject"]
    assert locale["override"]["subject"] == BODY["subject"]
    assert locale["override"]["version"] == 1
    changed = entries["auth.password_changed"]
    assert (changed["required"], changed["has_action"]) == (False, False)
    assert changed["locales"][0]["override"] is None
    assert changed["locales"][0]["shipped"]["action_label"] is None


def test_put_saves_a_revision_and_lists_it(admin: Admin) -> None:
    saved = admin.call("PUT", MAGIC, json=BODY)
    assert saved.status_code == 200
    assert saved.json()["created_by"] == str(admin.alice)
    second = admin.call("PUT", MAGIC, json={**BODY, "subject": "Again {app_name}"}).json()
    assert (second["version"], second["is_current"]) == (2, True)
    revisions = admin.call("GET", f"{MAGIC}/revisions").json()
    assert [(r["version"], r["is_current"]) for r in revisions] == [(2, True), (1, False)]


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"intro": "<p>Hi</p>"}, "intro"),
        ({"intro": "Open https://phish.example.test"}, "intro"),
        ({"action_label": "https://phish.example.test"}, "action_label"),
        ({"intro": "Token {token}"}, "intro"),
        ({"subject": "Hi {user_name}"}, "subject"),
        ({"subject": ""}, "subject"),
        ({"outro": ""}, "outro"),  # the magic link is required
    ],
)
def test_put_refuses_text_that_does_not_fit(
    admin: Admin, change: dict[str, str], field: str
) -> None:
    response = admin.call("PUT", MAGIC, json={**BODY, **change})
    assert response.status_code == 422
    assert [entry["loc"] for entry in response.json()["detail"]] == [["body", field]]
    assert admin.call("GET", f"{MAGIC}/revisions").json() == []


def test_put_validates_the_path_and_the_body_shape(admin: Admin) -> None:
    assert admin.call("PUT", "/templates/auth.nope/en-AU", json=BODY).status_code == 404
    assert admin.call("PUT", "/templates/auth.magic_link/EN", json=BODY).status_code == 422
    assert admin.call("PUT", MAGIC, json={**BODY, "html": "<b>"}).status_code == 422
    assert admin.call("PUT", MAGIC, json={**BODY, "intro": "x" * 2001}).status_code == 422
    no_action = "/templates/auth.password_changed/en-AU"
    assert admin.call("PUT", no_action, json=BODY).status_code == 422
    plain = {k: v for k, v in BODY.items() if k != "action_label"}
    assert admin.call("PUT", no_action, json={**plain, "intro": "x"}).status_code == 200


def test_a_save_that_loses_a_concurrent_race_is_409(
    admin: Admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    def lose(*args: Any, **kwargs: Any) -> None:
        raise IntegrityError("INSERT", {}, Exception("uq_mail_template_override_current"))

    monkeypatch.setattr(overrides, "save_override", lose)
    assert admin.call("PUT", MAGIC, json=BODY).status_code == 409


def test_delete_resets_to_the_shipped_default_and_keeps_revisions(admin: Admin) -> None:
    admin.call("PUT", MAGIC, json=BODY)
    assert admin.call("DELETE", MAGIC).status_code == 204
    assert admin.call("DELETE", MAGIC).status_code == 204  # idempotent
    assert admin.call("DELETE", "/templates/auth.nope/en-AU").status_code == 404
    entries = {e["key"]: e for e in admin.call("GET", "/templates").json()}
    assert entries["auth.magic_link"]["locales"][0]["override"] is None
    assert [r["is_current"] for r in admin.call("GET", f"{MAGIC}/revisions").json()] == [False]


def test_preview_renders_sample_values_with_a_draft_or_the_current_text(admin: Admin) -> None:
    current = admin.call("POST", f"{MAGIC}/preview").json()
    assert f"{ORIGIN}/magic-link#{SAMPLE_SECRET}" in current["text"]
    assert "15 minutes" in current["text"]
    draft = admin.call("POST", f"{MAGIC}/preview", json=BODY).json()
    assert draft["subject"].startswith("Your ")
    assert "Sign in now</a>" in draft["html"]
    bad = admin.call("POST", f"{MAGIC}/preview", json={**BODY, "intro": "<script>"})
    assert bad.status_code == 422
    assert admin.call("GET", f"{MAGIC}/revisions").json() == []


def test_test_send_goes_only_to_the_callers_own_address(admin: Admin, captured: Captured) -> None:
    response = admin.call("POST", f"{MAGIC}/test-send", json={"to": "someone@example.test"})
    assert response.status_code == 202
    [mail] = captured.mails
    assert mail.to == ALICE
    assert mail.context == {"token": SAMPLE_SECRET, "expires_minutes": 15}
    with admin.db.unit_of_work(ADMIN_SCOPE) as session:
        row = session.get(MailSend, uuid.UUID(response.json()["send_id"]))
        assert row is not None
        assert (row.recipient_hash, row.recipient_user_id) == (address_hash(ALICE), admin.alice)
        assert row.locale == "en-AU"


def test_test_sends_are_rate_limited(admin: Admin, captured: Captured) -> None:
    for _ in range(overrides.TEST_SEND_LIMIT):
        assert admin.call("POST", f"{MAGIC}/test-send").status_code == 202
    limited = admin.call("POST", f"{MAGIC}/test-send")
    assert limited.status_code == 429
    retry_after = int(limited.headers["Retry-After"])
    assert 0 < retry_after <= overrides.TEST_SEND_WINDOW.total_seconds() + 1
    assert len(captured.mails) == overrides.TEST_SEND_LIMIT
    assert admin.call("POST", "/templates/auth.nope/en-AU/test-send").status_code == 404


def test_overrides_stay_in_the_callers_tenant(admin: Admin) -> None:
    seed_tenants(admin.db, T2)
    with admin.db.unit_of_work(TenancyContext(tenant_id=T2)) as session:
        overrides.save_override(
            session, "auth.magic_link", "en-AU", _custom(MAGIC_LINK, "T2"), actor_id=None
        )
    entries = {e["key"]: e for e in admin.call("GET", "/templates").json()}
    assert entries["auth.magic_link"]["locales"][0]["override"] is None
    assert admin.call("GET", f"{MAGIC}/revisions").json() == []


# --- migration ------------------------------------------------------------------------------------


def _migration() -> ModuleType:
    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    spec = importlib.util.spec_from_file_location("m0042", next(versions.glob("0042_*.py")))
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_migration_0042_creates_and_drops_the_table(tmp_path: Path) -> None:
    from sqlalchemy import inspect

    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0042", "0041")
    url = database_url(tmp_path)
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=url, configure_logger=False)
    command.upgrade(cfg, "0042")
    database = Database(url)
    try:
        names = set(inspect(database.engine).get_table_names())
        assert OVERRIDE_TABLE in names
        columns = {c["name"] for c in inspect(database.engine).get_columns(OVERRIDE_TABLE)}
        assert columns == {c.name for c in MailTemplateOverride.__table__.columns}
        database.dispose()
        command.downgrade(cfg, "0041")
        database = Database(url)
        assert OVERRIDE_TABLE not in set(inspect(database.engine).get_table_names())
    finally:
        database.dispose()
        command.downgrade(cfg, "base")
