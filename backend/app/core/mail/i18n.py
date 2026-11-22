"""The e-mail i18n layer: ``t(key, locale, **params)`` over JSON catalogs (diff §4.6).

Catalogs live in ``locales/<BCP 47 tag>.json`` (flat ``key -> text`` with ``{name}``
placeholders). A locale without a catalog, or a key missing from it, falls back to the
language's catalog (``en`` for ``en-NZ``), then to the default locale. A key missing
everywhere, or a placeholder without a value, raises ``MissingTranslationError``: an e-mail is
never sent with a raw key or a blank in it.
"""

from __future__ import annotations

import json
import string
from functools import cache
from importlib.resources import files
from typing import Final

FALLBACK_LOCALE: Final = "en-AU"
_FORMATTER: Final = string.Formatter()


class MissingTranslationError(LookupError):
    """A key with no text in any catalog of the chain, or a placeholder with no value."""


@cache
def catalog(locale: str) -> dict[str, str]:
    """The catalog of exactly ``locale`` (``{}`` when there is none)."""
    resource = files(__package__ or "app.core.mail") / "locales" / f"{locale}.json"
    if not resource.is_file():
        return {}
    data = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ValueError(f"mail catalog {locale!r} must map strings to strings")
    return data


def locale_chain(locale: str | None, default: str = FALLBACK_LOCALE) -> tuple[str, ...]:
    """``en-NZ`` → ``("en-NZ", "en", <default>, "en-AU")`` without repeats."""
    chain: list[str] = []
    for tag in (locale, locale.split("-")[0] if locale else None, default, FALLBACK_LOCALE):
        if tag and tag not in chain:
            chain.append(tag)
    return tuple(chain)


def catalog_locales() -> tuple[str, ...]:
    """The locales that ship a catalog (``en-AU`` first)."""
    folder = files(__package__ or "app.core.mail") / "locales"
    tags = sorted(
        entry.name.removesuffix(".json")
        for entry in folder.iterdir()
        if entry.name.endswith(".json") and entry.is_file()
    )
    return tuple(sorted(tags, key=lambda tag: tag != FALLBACK_LOCALE))


def placeholders(text: str) -> frozenset[str]:
    """The ``{name}`` placeholders of ``text``."""
    return frozenset(name for _, name, _, _ in _FORMATTER.parse(text) if name)


def placeholder_fields(text: str) -> list[tuple[str, str | None, str]]:
    """Every replacement field of ``text`` as ``(field name, conversion, format spec)``; an
    empty field name (``{}``) comes back as ``""``. ``ValueError`` for unbalanced braces."""
    return [
        (name, conversion, spec or "")
        for _, name, spec, conversion in _FORMATTER.parse(text)
        if name is not None
    ]


def shipped(key: str, locale: str | None, *, default: str = FALLBACK_LOCALE) -> str:
    """The unformatted text of ``key`` in the first catalog of the chain that has it."""
    for tag in locale_chain(locale, default):
        text = catalog(tag).get(key)
        if text is not None:
            return text
    raise MissingTranslationError(f"no text for {key!r} in {locale_chain(locale, default)}")


def t(key: str, locale: str | None, *, default: str = FALLBACK_LOCALE, **params: object) -> str:
    """The text of ``key`` in the first catalog of the chain that has it, filled with
    ``params``."""
    for tag in locale_chain(locale, default):
        text = catalog(tag).get(key)
        if text is not None:
            missing = placeholders(text) - params.keys()
            if missing:
                raise MissingTranslationError(f"{key}: no value for {sorted(missing)}")
            return text.format_map(params)
    raise MissingTranslationError(f"no text for {key!r} in {locale_chain(locale, default)}")
