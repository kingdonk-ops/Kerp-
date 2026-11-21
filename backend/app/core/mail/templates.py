"""Registered e-mail templates and their rendering: plain text plus simple HTML (diff §4.6).

A template is a set of i18n keys (``mail.<template key>.subject|intro|action|outro``) and the
context parameters it needs. Rules every template obeys by construction:

* **Secrets only inside link fragments.** A template with a ``link_path`` builds its one link
  as ``<web origin><link_path>#<secret>``; the secret context value is used nowhere else
  (never in the subject or the text), and no other context value may hold it. A template
  that links an ordinary page (``public_link=True``) has no secret: ``<web origin><link_path>``.
* **No remote content.** The HTML has no images, no external styles, no scripts and no
  tracking pixel; its only URL is the link above.
* Every context value is escaped in the HTML.

Core and modules register their templates with ``register_template``; the registry is
process-wide (``TEMPLATES``).
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from urllib.parse import quote

from app.core.mail.i18n import FALLBACK_LOCALE, t

KEY_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{1,39}\.[a-z][a-z0-9_]{1,59}$")
MAX_CONTEXT_VALUE: Final = 500
ContextValue = str | int
_SECRET_CHARS: Final = re.compile(r"^[A-Za-z0-9._~-]{16,512}$")


class TemplateError(ValueError):
    """Unknown template, or a context that does not fit it."""


@dataclass(frozen=True, slots=True)
class MailTemplate:
    """``params``: the context keys shown in the text. ``secret``: the context key carried
    only in the link fragment (``link_path`` required with it). ``public_link``: the link is
    an ordinary page of the app (the lobby, T-0063) and carries no secret and no fragment;
    a template must say so explicitly, so a link that should carry a secret never goes out
    without one by mistake."""

    key: str
    params: tuple[str, ...] = ()
    link_path: str | None = None
    secret: str | None = None
    public_link: bool = False

    def __post_init__(self) -> None:
        if not KEY_PATTERN.match(self.key):
            raise ValueError(f"mail template key {self.key!r} must look like '<area>.<name>'")
        if self.public_link:
            if self.link_path is None or self.secret is not None:
                raise ValueError(
                    f"mail template {self.key!r}: a public link needs a link_path and no secret"
                )
        elif (self.link_path is None) != (self.secret is None):
            raise ValueError(f"mail template {self.key!r}: a link needs a secret and vice versa")
        if self.link_path is not None and not re.match(r"^/[a-z0-9/_-]*$", self.link_path):
            raise ValueError(f"mail template {self.key!r}: link_path must be a plain path")
        if self.secret is not None and self.secret in self.params:
            raise ValueError(f"mail template {self.key!r}: the secret is never a text parameter")

    def i18n_keys(self) -> tuple[str, ...]:
        parts: tuple[str, ...] = ("subject", "intro", "outro")
        common: tuple[str, ...] = ("mail.common.footer",)
        if self.link_path is not None:
            parts += ("action",)
            common += ("mail.common.link_fallback",)
        return tuple(f"mail.{self.key}.{part}" for part in parts) + common

    def context_keys(self) -> frozenset[str]:
        return frozenset(self.params) | ({self.secret} if self.secret else frozenset())

    def check_context(self, context: Mapping[str, object]) -> dict[str, ContextValue]:
        """A copy of ``context`` if it has exactly the template's keys with plain values."""
        expected = self.context_keys()
        if set(context) != expected:
            raise TemplateError(
                f"mail template {self.key!r} needs context {sorted(expected)}, "
                f"got {sorted(context)}"
            )
        checked: dict[str, ContextValue] = {}
        for name, value in context.items():
            if isinstance(value, bool) or not isinstance(value, str | int):
                raise TemplateError(f"mail context {name!r} must be a string or an integer")
            if isinstance(value, str) and (len(value) > MAX_CONTEXT_VALUE or "\n" in value):
                raise TemplateError(f"mail context {name!r} is too long or has a line break")
            checked[name] = value
        if self.secret is not None:
            secret = checked[self.secret]
            if not isinstance(secret, str) or not _SECRET_CHARS.match(secret):
                raise TemplateError(f"mail context {self.secret!r} is not a URL-safe token")
            if any(str(v) == secret for k, v in checked.items() if k != self.secret):
                raise TemplateError("the secret may only appear in the link fragment")
        return checked


@dataclass(frozen=True, slots=True)
class RenderedMail:
    subject: str
    text: str
    html: str


class TemplateRegistry:
    def __init__(self) -> None:
        self._templates: dict[str, MailTemplate] = {}

    def add(self, template: MailTemplate) -> MailTemplate:
        if template.key in self._templates and self._templates[template.key] != template:
            raise ValueError(f"mail template {template.key!r} is already registered")
        self._templates[template.key] = template
        return template

    def get(self, key: str) -> MailTemplate:
        try:
            return self._templates[key]
        except KeyError:
            raise TemplateError(f"unknown mail template {key!r}") from None

    def all(self) -> tuple[MailTemplate, ...]:
        return tuple(self._templates[k] for k in sorted(self._templates))


TEMPLATES = TemplateRegistry()


def register_template(template: MailTemplate) -> MailTemplate:
    return TEMPLATES.add(template)


# Core templates. Modules register theirs from their own code (invites: T-0059, password
# reset: T-0077, access-request decisions: T-0063).
MAGIC_LINK = register_template(
    MailTemplate(
        "auth.magic_link", params=("expires_minutes",), link_path="/magic-link", secret="token"
    )
)


# Self-service password reset (T-0077, diff §4.2): the link ``/reset-password#<token>`` and
# the notice sent to the account's address once its password was changed.
PASSWORD_RESET = register_template(
    MailTemplate(
        "auth.password_reset",
        params=("expires_minutes",),
        link_path="/reset-password",
        secret="token",
    )
)
PASSWORD_CHANGED = register_template(MailTemplate("auth.password_changed"))


def render(
    template: MailTemplate,
    context: Mapping[str, ContextValue],
    *,
    locale: str | None,
    default_locale: str = FALLBACK_LOCALE,
    app_name: str,
    web_origin: str,
) -> RenderedMail:
    """Subject, text and HTML of ``template`` in ``locale`` (else the default locale)."""
    values: dict[str, object] = {name: context[name] for name in template.params}
    values["app_name"] = app_name

    def text_of(key: str) -> str:
        return t(key, locale, default=default_locale, **values)

    own = f"mail.{template.key}"
    subject = text_of(f"{own}.subject")
    intro, outro = text_of(f"{own}.intro"), text_of(f"{own}.outro")
    footer = text_of("mail.common.footer")
    lang = locale or default_locale
    text_lines = [intro, ""]
    html_lines = [f"<p>{html.escape(intro)}</p>"]
    if template.link_path is not None:
        url = f"{web_origin.rstrip('/')}{template.link_path}"
        if template.secret is not None:
            url += f"#{quote(str(context[template.secret]))}"
        action, fallback = text_of(f"{own}.action"), text_of("mail.common.link_fallback")
        text_lines += [f"{action}: {url}", ""]
        safe_url = html.escape(url, quote=True)
        html_lines += [
            f'<p><a href="{safe_url}" style="font-weight:bold">{html.escape(action)}</a></p>',
            f"<p>{html.escape(fallback)}<br>{html.escape(url)}</p>",
        ]
    text_lines += [outro, "", "--", footer, ""]
    html_lines += [
        f"<p>{html.escape(outro)}</p>",
        f'<hr><p style="font-size:12px;color:#555555">{html.escape(footer)}</p>',
    ]
    body = "\n".join(html_lines)
    document = (
        f'<!DOCTYPE html>\n<html lang="{html.escape(lang)}"><head><meta charset="utf-8">'
        f"<title>{html.escape(subject)}</title></head>\n"
        f'<body style="font-family:Arial,sans-serif;font-size:14px">\n{body}\n</body></html>\n'
    )
    return RenderedMail(subject=subject, text="\n".join(text_lines), html=document)
