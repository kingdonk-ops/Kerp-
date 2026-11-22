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
* **One-click unsubscribe** (T-0228, RFC 8058): a template with a ``list_unsubscribe_path``
  (an API path) also carries ``List-Unsubscribe: <<web origin><path>?token=<secret>>`` and
  ``List-Unsubscribe-Post: List-Unsubscribe=One-Click``. That header is the one other place
  its secret goes (in a query string, which the access log never records).
* A ``multiline_param`` (the body of a broadcast, T-0228) may hold up to
  ``MAX_MULTILINE_VALUE`` characters of plain text with line breaks; it is escaped like any
  value.

Core and modules register their templates with ``register_template``; the registry is
process-wide (``TEMPLATES``).

**Overrides** (T-0227, diff §4.7 Automated templates, TD-400): a System admin may reword the
subject and the text blocks (intro, outro, action label) of a template per locale, as plain text
with only the placeholders the template declares (``placeholders()``). ``override_problems``
is the gate: no HTML, no URL, no link secret, no unknown or computed placeholder. The renderer
still escapes every value and builds the one link itself, so an override can neither move nor
reveal a secret. Without an override the i18n catalogue's shipped text is used.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, get_args
from urllib.parse import quote

from app.core.mail.i18n import FALLBACK_LOCALE, placeholder_fields, shipped, t

KEY_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{1,39}\.[a-z][a-z0-9_]{1,59}$")
MAX_CONTEXT_VALUE: Final = 500
MAX_MULTILINE_VALUE: Final = 10_000
# Control characters a multi-line value may not hold (tab and line feed are allowed).
_VALUE_CONTROL: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u2028\u2029]")
_API_PATH: Final = re.compile(r"^/api/v1/[a-z0-9/_-]+$")
LIST_UNSUBSCRIBE_POST: Final = "List-Unsubscribe=One-Click"
ContextValue = str | int
_SECRET_CHARS: Final = re.compile(r"^[A-Za-z0-9._~-]{16,512}$")


# The audience groups the template editor lists templates by (diff §4.7 Editor details).
Audience = Literal["internal_staff", "client", "external_party", "deployment_wide"]
AUDIENCES: Final[tuple[str, ...]] = get_args(Audience)
# Placeholders every template may use besides its own parameters.
COMMON_PLACEHOLDERS: Final = ("app_name",)


class TemplateError(ValueError):
    """Unknown template, or a context that does not fit it."""


@dataclass(frozen=True, slots=True)
class MailTemplate:
    """``params``: the context keys shown in the text. ``secret``: the context key carried
    only in the link fragment (``link_path`` required with it). ``public_link``: the link is
    an ordinary page of the app (the lobby, T-0063) and carries no secret and no fragment;
    a template must say so explicitly, so a link that should carry a secret never goes out
    without one by mistake. ``audience``: the group the template editor lists it under.
    ``required``: an override may reword it but never leave its subject or a text block empty
    (sign-in, invite, password reset, RFI call-outs; diff §4.7 Editor details).
    ``multiline_param``: the one parameter that may hold long plain text with line breaks
    (T-0228). ``list_unsubscribe_path``: the API path of the one-click unsubscribe endpoint
    (RFC 8058); it needs a ``secret``, which then also goes into that header's query."""

    key: str
    params: tuple[str, ...] = ()
    link_path: str | None = None
    secret: str | None = None
    public_link: bool = False
    audience: Audience = "deployment_wide"
    required: bool = False
    multiline_param: str | None = None
    list_unsubscribe_path: str | None = None

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
        if self.audience not in AUDIENCES:
            raise ValueError(f"mail template {self.key!r}: unknown audience {self.audience!r}")
        if self.multiline_param is not None and self.multiline_param not in self.params:
            raise ValueError(f"mail template {self.key!r}: the multi-line value is a parameter")
        if self.list_unsubscribe_path is not None and (
            self.secret is None or not _API_PATH.match(self.list_unsubscribe_path)
        ):
            raise ValueError(
                f"mail template {self.key!r}: List-Unsubscribe needs a secret and an API path"
            )

    @property
    def has_action(self) -> bool:
        return self.link_path is not None

    def blocks(self) -> tuple[str, ...]:
        """The texts an override may reword: ``subject``, ``intro``, ``outro`` and, with a
        link, ``action_label``."""
        return ("subject", "intro", "outro") + (("action_label",) if self.has_action else ())

    def placeholders(self) -> tuple[str, ...]:
        """The ``{name}`` placeholders its texts may use: its parameters and ``app_name``.
        Never the secret."""
        return tuple(dict.fromkeys((*self.params, *COMMON_PLACEHOLDERS)))

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
            if isinstance(value, str) and name == self.multiline_param:
                if len(value) > MAX_MULTILINE_VALUE or _VALUE_CONTROL.search(value):
                    raise TemplateError(f"mail context {name!r} is too long or not plain text")
            elif isinstance(value, str) and (len(value) > MAX_CONTEXT_VALUE or "\n" in value):
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
class TemplateText:
    """The rewordable texts of a template in one locale, unformatted (``{name}`` placeholders).
    ``action_label`` is ``None`` for a template without a link."""

    subject: str
    intro: str
    outro: str
    action_label: str | None = None


def shipped_text(template: MailTemplate, locale: str | None, default: str) -> TemplateText:
    """The shipped default of ``template`` in ``locale`` (the i18n catalogue, with its
    fallback chain), unformatted."""
    own = f"mail.{template.key}"
    return TemplateText(
        subject=shipped(f"{own}.subject", locale, default=default),
        intro=shipped(f"{own}.intro", locale, default=default),
        outro=shipped(f"{own}.outro", locale, default=default),
        action_label=shipped(f"{own}.action", locale, default=default)
        if template.has_action
        else None,
    )


# --- override checks (T-0227, TD-400) --------------------------------------------------------

MAX_BLOCK: Final = {"subject": 200, "intro": 2000, "outro": 2000, "action_label": 80}
# Something that looks like markup: a tag, a comment, a doctype or a processing instruction.
_HTML: Final = re.compile(r"<[/!?]?[A-Za-z]|<!|<\?")
# A link of any kind: the renderer adds the one link a template has, nothing else may.
_URL: Final = re.compile(
    r"(?i)(?:\b[a-z][a-z0-9+.-]*://|\bwww\.|\b(?:mailto|javascript|vbscript):)"
)
_CONTROL: Final = re.compile(r"[\x00-\x09\x0b-\x1f\x7f\u2028\u2029]")
_LINE_BREAK: Final = re.compile(r"[\r\n]")


def override_problems(template: MailTemplate, text: TemplateText) -> dict[str, str]:
    """Why ``text`` may not override ``template`` (field -> reason; empty = acceptable).

    Plain text only: no HTML, no URL (the renderer places the action link), no control
    characters, no line break in the subject or the action label. Placeholders: only the
    template's own (``placeholders()``), as a bare ``{name}``; never the link secret, never an
    attribute, index, conversion or format spec. The subject and (with a link) the action
    label are never empty; the intro and outro of a ``required`` template neither.
    """
    problems: dict[str, str] = {}
    declared = set(template.placeholders())
    values = {
        "subject": text.subject,
        "intro": text.intro,
        "outro": text.outro,
        "action_label": text.action_label,
    }
    if not template.has_action and text.action_label is not None:
        problems["action_label"] = "this template has no action link"
    for block in template.blocks():
        value = values[block]
        if value is None or not value.strip():
            if block in ("subject", "action_label") or template.required:
                problems[block] = "must not be empty"
            continue
        if len(value) > MAX_BLOCK[block]:
            problems[block] = f"at most {MAX_BLOCK[block]} characters"
        elif _CONTROL.search(value) or (
            block in ("subject", "action_label") and _LINE_BREAK.search(value)
        ):
            problems[block] = "plain text only: no control characters or line breaks here"
        elif _HTML.search(value):
            problems[block] = "plain text only: HTML is not allowed"
        elif _URL.search(value):
            problems[block] = "no links: the action link is added when the e-mail is sent"
        else:
            reason = _placeholder_problem(template, value, declared)
            if reason is not None:
                problems[block] = reason
    return problems


def _placeholder_problem(template: MailTemplate, value: str, declared: set[str]) -> str | None:
    try:
        fields = placeholder_fields(value)
    except ValueError:
        return "unbalanced braces: write placeholders as {name}"
    for name, conversion, spec in fields:
        if template.secret is not None and name.split(".")[0].split("[")[0] == template.secret:
            return "the link secret is never shown in the text"
        if name not in declared or conversion is not None or spec:
            allowed = ", ".join(f"{{{p}}}" for p in sorted(declared))
            return f"unknown placeholder {{{name}}}; allowed: {allowed}"
    return None


# Synthetic values for previews and test sends (never real data).
SAMPLE_VALUES: Final[dict[str, ContextValue]] = {
    "expires_minutes": 15,
    "expires_at": "1 January 2030 09:00",
    "project_code": "SAMPLE-001",
    "project_name": "Sample project",
    "inviter_name": "Sam Sample",
    "user_name": "Sam Sample",
    "reason": "This is a sample reason.",
}
SAMPLE_SECRET: Final = "sample-link-not-valid-0000"


def sample_context(template: MailTemplate) -> dict[str, ContextValue]:
    """A context for ``template`` made of synthetic sample values; the secret (if any) is a
    placeholder token that opens nothing."""
    context: dict[str, ContextValue] = {
        name: SAMPLE_VALUES.get(name, f"Sample {name.replace('_', ' ')}")
        for name in template.params
    }
    if template.secret is not None:
        context[template.secret] = SAMPLE_SECRET
    return context


@dataclass(frozen=True, slots=True)
class RenderedMail:
    """``headers``: extra message headers (``List-Unsubscribe``, T-0228), else empty."""

    subject: str
    text: str
    html: str
    headers: tuple[tuple[str, str], ...] = ()


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

    def find(self, key: str) -> MailTemplate | None:
        return self._templates.get(key)

    def all(self) -> tuple[MailTemplate, ...]:
        return tuple(self._templates[k] for k in sorted(self._templates))


TEMPLATES = TemplateRegistry()


def register_template(template: MailTemplate) -> MailTemplate:
    return TEMPLATES.add(template)


# Core templates. Modules register theirs from their own code (invites: T-0059, password
# reset: T-0077, access-request decisions: T-0063).
MAGIC_LINK = register_template(
    MailTemplate(
        "auth.magic_link",
        params=("expires_minutes",),
        link_path="/magic-link",
        secret="token",
        required=True,
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
        required=True,
    )
)
PASSWORD_CHANGED = register_template(MailTemplate("auth.password_changed"))


def _paragraph(text: str) -> str:
    return "<br>\n".join(html.escape(line) for line in text.split("\n"))


def render(
    template: MailTemplate,
    context: Mapping[str, ContextValue],
    *,
    locale: str | None,
    default_locale: str = FALLBACK_LOCALE,
    app_name: str,
    web_origin: str,
    override: TemplateText | None = None,
) -> RenderedMail:
    """Subject, text and HTML of ``template`` in ``locale`` (else the default locale).

    ``override``: a System admin's wording (T-0227) instead of the shipped catalogue text. It
    must pass ``override_problems`` (``ValueError`` otherwise); its placeholders are filled
    like the catalogue's, every value is still escaped and the link is still built here. An
    empty intro or outro (allowed for a template that is not ``required``) is left out."""
    values: dict[str, object] = {name: context[name] for name in template.params}
    values["app_name"] = app_name
    own = f"mail.{template.key}"

    def text_of(key: str) -> str:
        return t(key, locale, default=default_locale, **values)

    if override is not None:
        problems = override_problems(template, override)
        if problems:
            raise ValueError(f"mail template {template.key!r}: unusable override {problems}")
        subject = override.subject.strip().format_map(values)
        intro = override.intro.strip().format_map(values)
        outro = override.outro.strip().format_map(values)
        action = (override.action_label or "").strip().format_map(values)
    else:
        subject = text_of(f"{own}.subject")
        intro, outro = text_of(f"{own}.intro"), text_of(f"{own}.outro")
        action = text_of(f"{own}.action") if template.has_action else ""
    footer = text_of("mail.common.footer")
    lang = locale or default_locale
    text_lines: list[str] = [intro, ""] if intro else []
    html_lines: list[str] = [f"<p>{_paragraph(intro)}</p>"] if intro else []
    if template.link_path is not None:
        url = f"{web_origin.rstrip('/')}{template.link_path}"
        if template.secret is not None:
            url += f"#{quote(str(context[template.secret]))}"
        fallback = text_of("mail.common.link_fallback")
        text_lines += [f"{action}: {url}", ""]
        safe_url = html.escape(url, quote=True)
        html_lines += [
            f'<p><a href="{safe_url}" style="font-weight:bold">{html.escape(action)}</a></p>',
            f"<p>{html.escape(fallback)}<br>{html.escape(url)}</p>",
        ]
    if outro:
        text_lines += [outro, ""]
        html_lines.append(f"<p>{_paragraph(outro)}</p>")
    text_lines += ["--", footer, ""]
    html_lines.append(f'<hr><p style="font-size:12px;color:#555555">{html.escape(footer)}</p>')
    headers: tuple[tuple[str, str], ...] = ()
    if template.list_unsubscribe_path is not None and template.secret is not None:
        token = quote(str(context[template.secret]), safe="")
        target = f"{web_origin.rstrip('/')}{template.list_unsubscribe_path}?token={token}"
        headers = (
            ("List-Unsubscribe", f"<{target}>"),
            ("List-Unsubscribe-Post", LIST_UNSUBSCRIBE_POST),
        )
    body = "\n".join(html_lines)
    document = (
        f'<!DOCTYPE html>\n<html lang="{html.escape(lang)}"><head><meta charset="utf-8">'
        f"<title>{html.escape(subject)}</title></head>\n"
        f'<body style="font-family:Arial,sans-serif;font-size:14px">\n{body}\n</body></html>\n'
    )
    return RenderedMail(subject=subject, text="\n".join(text_lines), html=document, headers=headers)
