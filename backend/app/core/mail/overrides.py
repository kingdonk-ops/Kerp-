"""Automated template overrides (T-0227, diff §4.7 Automated templates, ADR 0021, TD-400).

The registered templates stay the shipped defaults in the i18n catalogues. A System admin
rewords, per template and locale, the subject and the text blocks (intro, outro, action
label) as plain text with only the placeholders the template declares; every save is a new
revision (``mail_template_override.version``), audited in the tenant-level chain
``mail-template:<key>:<locale>``. A reset makes the shipped default current again and keeps
every revision.

* ``effective_override`` is what the worker's ``deliver`` renders with: the current override
  of the first locale of the recipient's chain (``en-NZ`` -> ``en`` -> default -> ``en-AU``)
  that has an override or shipped catalogue text. It is read from the database in the send's
  own unit of work, so the next send uses a change at once, in every API and worker process
  (no cache to go stale).
* ``preview`` renders with synthetic sample values; ``send_test`` queues the template to the
  caller's own address only (``TEST_SEND_LIMIT`` per ``TEST_SEND_WINDOW``).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.config import Settings
from app.core.db import utcnow
from app.core.mail.addresses import normalize_address
from app.core.mail.i18n import catalog, catalog_locales, locale_chain
from app.core.mail.models import MailSend, MailTemplateOverride
from app.core.mail.templates import (
    TEMPLATES,
    MailTemplate,
    RenderedMail,
    TemplateText,
    override_problems,
    render,
    sample_context,
    shipped_text,
)
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy
from app.core.timestamps import aware_utc

LOCALE_PATTERN: Final = r"^[a-z]{2,3}(-[A-Z]{2})?$"
# Test sends: at most this many e-mails the caller queued to itself per window.
TEST_SEND_LIMIT: Final = 5
TEST_SEND_WINDOW: Final = timedelta(minutes=10)

logger = logging.getLogger("kerp.mail")


class OverrideInvalidError(ValueError):
    """The text does not fit the template (422); ``problems``: field -> reason."""

    def __init__(self, problems: dict[str, str]) -> None:
        super().__init__(f"invalid template override: {sorted(problems)}")
        self.problems = problems


class TestSendRateLimitedError(Exception):
    """Too many test sends (429); ``retry_after`` in whole seconds."""

    __test__ = False  # not a pytest class

    def __init__(self, retry_after: int) -> None:
        super().__init__("too many test sends")
        self.retry_after = retry_after


def _tenancy(session: Session) -> TenancyContext:
    tenancy = session_tenancy(session)
    if tenancy is None:
        raise RuntimeError("mail template overrides need a session bound to a tenant")
    return tenancy


def _tenant_level(session: Session) -> TenancyContext:
    tenancy = _tenancy(session)
    if tenancy.project_id is not None:
        raise RuntimeError("mail template overrides are written by a tenant-level session")
    return tenancy


def text_of(row: MailTemplateOverride) -> TemplateText:
    return TemplateText(
        subject=row.subject, intro=row.intro, outro=row.outro, action_label=row.action_label
    )


def clean(template: MailTemplate, text: TemplateText) -> TemplateText:
    """``text`` with ``\\r\\n`` / ``\\r`` as ``\\n`` and surrounding blanks stripped, checked
    against ``template`` (``OverrideInvalidError``)."""

    def tidy(value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()

    cleaned = TemplateText(
        subject=tidy(text.subject),
        intro=tidy(text.intro),
        outro=tidy(text.outro),
        action_label=None if text.action_label is None else tidy(text.action_label),
    )
    problems = override_problems(template, cleaned)
    if problems:
        raise OverrideInvalidError(problems)
    return cleaned


# --- reads ----------------------------------------------------------------------------------


def _current_rows(session: Session, tenant_id: uuid.UUID) -> list[MailTemplateOverride]:
    query = select(MailTemplateOverride).where(
        MailTemplateOverride.tenant_id == tenant_id,
        MailTemplateOverride.project_id.is_(None),
        MailTemplateOverride.is_current.is_(True),
        MailTemplateOverride.deleted_at.is_(None),
    )
    return list(session.scalars(query))


def current_override(session: Session, key: str, locale: str) -> MailTemplateOverride | None:
    """The tenant's current override of ``key`` in exactly ``locale``."""
    tenancy = _tenancy(session)
    query = select(MailTemplateOverride).where(
        MailTemplateOverride.tenant_id == tenancy.tenant_id,
        MailTemplateOverride.project_id.is_(None),
        MailTemplateOverride.template_key == key,
        MailTemplateOverride.locale == locale,
        MailTemplateOverride.is_current.is_(True),
        MailTemplateOverride.deleted_at.is_(None),
    )
    return session.scalars(query.limit(1)).first()


def effective_override(
    session: Session, template: MailTemplate, locale: str | None, default_locale: str
) -> TemplateText | None:
    """The override a send of ``template`` to a recipient in ``locale`` uses, else ``None``
    (the shipped text). Walks the locale chain: at each locale an override wins over the
    catalogue, and the first locale with either decides. An override that no longer fits the
    template (a parameter was removed since it was saved) is skipped and logged."""
    tenancy = _tenancy(session)
    chain = locale_chain(locale, default_locale)
    query = select(MailTemplateOverride).where(
        MailTemplateOverride.tenant_id == tenancy.tenant_id,
        MailTemplateOverride.project_id.is_(None),
        MailTemplateOverride.template_key == template.key,
        MailTemplateOverride.locale.in_(chain),
        MailTemplateOverride.is_current.is_(True),
        MailTemplateOverride.deleted_at.is_(None),
    )
    by_locale = {row.locale: row for row in session.scalars(query)}
    subject_key = f"mail.{template.key}.subject"
    for tag in chain:
        row = by_locale.get(tag)
        if row is not None:
            text = text_of(row)
            if override_problems(template, text):
                logger.warning(
                    "mail template override no longer fits its template; shipped text used",
                    extra={"kind": template.key, "locale": tag, "version": row.version},
                )
                return None
            return text
        if subject_key in catalog(tag):
            return None
    return None


@dataclass(frozen=True, slots=True)
class LocaleEntry:
    locale: str
    shipped: TemplateText
    override: MailTemplateOverride | None


@dataclass(frozen=True, slots=True)
class TemplateEntry:
    template: MailTemplate
    locales: tuple[LocaleEntry, ...]


def list_templates(session: Session, default_locale: str) -> list[TemplateEntry]:
    """Every registered template with its shipped text and current override per locale: the
    locales that ship a catalogue, plus any locale the tenant overrode."""
    tenancy = _tenancy(session)
    current: dict[tuple[str, str], MailTemplateOverride] = {
        (row.template_key, row.locale): row for row in _current_rows(session, tenancy.tenant_id)
    }
    shipped_locales = catalog_locales()
    entries: list[TemplateEntry] = []
    for template in TEMPLATES.all():
        overridden = sorted(tag for key, tag in current if key == template.key)
        tags = [*shipped_locales, *(tag for tag in overridden if tag not in shipped_locales)]
        entries.append(
            TemplateEntry(
                template=template,
                locales=tuple(
                    LocaleEntry(
                        locale=tag,
                        shipped=shipped_text(template, tag, default_locale),
                        override=current.get((template.key, tag)),
                    )
                    for tag in tags
                ),
            )
        )
    return entries


def list_revisions(session: Session, key: str, locale: str) -> list[MailTemplateOverride]:
    """Every revision of ``key`` in ``locale``, newest first (``TemplateError``: unknown)."""
    template = TEMPLATES.get(key)
    tenancy = _tenancy(session)
    query = select(MailTemplateOverride).where(
        MailTemplateOverride.tenant_id == tenancy.tenant_id,
        MailTemplateOverride.project_id.is_(None),
        MailTemplateOverride.template_key == template.key,
        MailTemplateOverride.locale == locale,
        MailTemplateOverride.deleted_at.is_(None),
    )
    return list(session.scalars(query.order_by(MailTemplateOverride.version.desc())))


# --- writes ---------------------------------------------------------------------------------


def _chain_key(key: str, locale: str) -> str:
    return f"mail-template:{key}:{locale}"


def _retire_current(session: Session, tenancy: TenancyContext, key: str, locale: str) -> int:
    """Clear ``is_current`` on the current row (if any); return its version (0: none)."""
    row = current_override(session, key, locale)
    if row is None:
        return 0
    row.is_current = False
    row.updated_by = tenancy.user_id
    session.flush()
    return row.version


def save_override(
    session: Session, key: str, locale: str, text: TemplateText, *, actor_id: uuid.UUID | None
) -> MailTemplateOverride:
    """Make ``text`` the current wording of ``key`` in ``locale``: a new revision.

    ``TemplateError``: unknown template; ``OverrideInvalidError``: the text does not fit it.
    A concurrent save of the same template and locale loses on the unique indexes
    (``IntegrityError``, the caller answers 409)."""
    template = TEMPLATES.get(key)
    cleaned = clean(template, text)
    tenancy = _tenant_level(session)
    latest = session.scalar(
        select(func.max(MailTemplateOverride.version)).where(
            MailTemplateOverride.tenant_id == tenancy.tenant_id,
            MailTemplateOverride.project_id.is_(None),
            MailTemplateOverride.template_key == template.key,
            MailTemplateOverride.locale == locale,
        )
    )
    previous = _retire_current(session, tenancy, template.key, locale)
    row = MailTemplateOverride(
        tenant_id=tenancy.tenant_id,
        project_id=None,
        template_key=template.key,
        locale=locale,
        version=(latest or 0) + 1,
        subject=cleaned.subject,
        intro=cleaned.intro,
        outro=cleaned.outro,
        action_label=cleaned.action_label,
        is_current=True,
        created_by=actor_id,
        updated_by=actor_id,
        source="api",
    )
    session.add(row)
    session.flush()
    audit.append(
        session,
        _chain_key(template.key, locale),
        {
            "action": "update",
            "template_key": template.key,
            "locale": locale,
            "version": row.version,
            "previous_version": previous or None,
            "override_id": str(row.id),
        },
        source="api",
        actor_id=actor_id,
    )
    return row


def reset_override(session: Session, key: str, locale: str, *, actor_id: uuid.UUID | None) -> bool:
    """Make the shipped default current again (every revision is kept). ``False`` when there
    was no override (nothing changes, nothing is audited). ``TemplateError``: unknown."""
    template = TEMPLATES.get(key)
    tenancy = _tenant_level(session)
    previous = _retire_current(session, tenancy, template.key, locale)
    if not previous:
        return False
    audit.append(
        session,
        _chain_key(template.key, locale),
        {"action": "reset", "template_key": template.key, "locale": locale, "version": previous},
        source="api",
        actor_id=actor_id,
    )
    return True


# --- preview and test send ------------------------------------------------------------------


def preview(
    session: Session,
    key: str,
    locale: str,
    settings: Settings,
    *,
    web_origin: str,
    draft: TemplateText | None = None,
) -> RenderedMail:
    """``key`` in ``locale`` rendered with synthetic sample values: with ``draft`` (checked
    like a save, nothing is stored), else with what a send would use now."""
    template = TEMPLATES.get(key)
    if draft is not None:
        text: TemplateText | None = clean(template, draft)
    else:
        text = effective_override(session, template, locale, settings.mail_default_locale)
    return render(
        template,
        sample_context(template),
        locale=locale,
        default_locale=settings.mail_default_locale,
        app_name=settings.app_name,
        web_origin=web_origin,
        override=text,
    )


def _retry_after(session: Session, user_id: uuid.UUID, now: datetime) -> int | None:
    since = now - TEST_SEND_WINDOW
    recent = sorted(
        aware_utc(moment)
        for moment in session.scalars(
            select(MailSend.created_at).where(
                MailSend.project_id.is_(None),
                MailSend.created_by == user_id,
                MailSend.recipient_user_id == user_id,
                MailSend.created_at >= since,
            )
        )
    )
    if len(recent) < TEST_SEND_LIMIT:
        return None
    oldest = recent[-TEST_SEND_LIMIT]
    return max(1, int((oldest + TEST_SEND_WINDOW - now).total_seconds()) + 1)


def send_test(
    session: Session, key: str, locale: str, *, user_id: uuid.UUID, email: str
) -> uuid.UUID:
    """Queue ``key`` in ``locale`` with synthetic sample values to the caller's own address
    (``email``, the signed-in account's; nothing in the request names a recipient). Rendered
    by the worker like any send, so it shows what the next real send will look like.

    Rate limit: ``TestSendRateLimitedError`` once the caller queued ``TEST_SEND_LIMIT``
    e-mails to itself in the last ``TEST_SEND_WINDOW``. Audited. Returns the send-log id."""
    # Imported here: ``service`` imports this module for ``deliver``.
    from app.core.mail.service import send_mail

    template = TEMPLATES.get(key)
    tenancy = _tenant_level(session)
    now = utcnow()
    retry_after = _retry_after(session, user_id, now)
    if retry_after is not None:
        raise TestSendRateLimitedError(retry_after)
    send_id = send_mail(
        session,
        template.key,
        normalize_address(email),
        sample_context(template),
        locale,
        recipient_user_id=user_id,
    )
    audit.append(
        session,
        _chain_key(template.key, locale),
        {
            "action": "test_send",
            "template_key": template.key,
            "locale": locale,
            "send_id": str(send_id),
        },
        source="api",
        actor_id=tenancy.user_id or user_id,
    )
    return send_id
