"""Structural lint of a form template (EDGE-SCREENS §2.2 schema); stable codes, JSON paths.

A template that lints clean is safe for ``evaluate_form``. Localised strings (§2.2
amendment, TD-183): every user-facing string is a non-empty plain string or a per-locale
map keyed by BCP 47 tag that has the template's ``default_language``; keys, option values
and CEL are plain strings, never maps.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.core.cel import compile_expression
from app.core.fields.registry import is_finite_number, resolve_field_type

DEFAULT_LANGUAGE = "en"
_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,62}")
# BCP 47 shape (language, then subtags); not a registry lookup.
_TAG_RE = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*")
# CEL reserved words: a field with such a key could not be read as ``fields.<key>``.
_RESERVED = frozenset(
    {"as", "break", "const", "continue", "else", "false", "for", "function", "if", "import"}
    | {"in", "let", "loop", "namespace", "null", "package", "return", "true", "var", "void"}
    | {"while"}
)
_TEMPLATE_KEYS = frozenset({"default_language", "fields", "sections"})
_VALIDATION_KEYS = {"cel_expression", "error_message"}
_VALIDATION_KEYS = {"cel_expression", "error_message"}
_FIELD_KEYS = frozenset(
    {"field_key", "label", "help", "data_type", "options", "units", "expression", "required"}
    | {"required_if", "visible_if", "validation", "range"}
)


@dataclass(frozen=True, slots=True)
class LintIssue:
    code: str
    path: str  # e.g. ``fields[2].validation.error_message``
    message: str


def lint_template(template: object) -> list[LintIssue]:
    """All problems of ``template``, in document order; empty when it is valid."""
    issues: list[LintIssue] = []

    def add(code: str, path: str, message: str) -> None:
        issues.append(LintIssue(code, path, message))

    if not isinstance(template, dict) or not isinstance(template.get("fields"), list):
        return [LintIssue("TEMPLATE_INVALID", "", "a template is an object with a `fields` list")]
    _unknown(template, _TEMPLATE_KEYS, "", add)
    language = template.get("default_language", DEFAULT_LANGUAGE)
    if not isinstance(language, str) or not _TAG_RE.fullmatch(language):
        add("INVALID_DEFAULT_LANGUAGE", "default_language", "must be a BCP 47 tag")
        language = None  # reported once; the per-string default check is skipped

    def text(value: object, path: str) -> None:
        """A user-facing string: non-empty plain string, or a locale map with the default."""
        if isinstance(value, str) and value.strip():
            return
        if not isinstance(value, dict):
            add("LOCALISED_INVALID", path, "must be a non-empty string or a per-locale map")
        elif not value:
            add("LOCALISED_EMPTY_MAP", path, "a per-locale map needs at least one entry")
        elif not all(
            isinstance(entry, str) and entry.strip() and _TAG_RE.fullmatch(tag)
            for tag, entry in value.items()
        ):
            add("LOCALISED_INVALID", path, "entries are non-empty strings keyed by BCP 47 tag")
        elif language is not None and language.lower() not in {tag.lower() for tag in value}:
            add("LOCALISED_MISSING_DEFAULT", path, f"no entry for default language `{language}`")

    def cel(value: object, path: str) -> None:
        if not isinstance(value, str):
            add("INVALID_CEL", path, "CEL is a plain string and is never localised")
            return
        error = compile_expression(value)
        if error is not None:
            add("INVALID_CEL", path, f"{error.code.value}: {error.message}")

    keys: set[str] = set()
    for index, field in enumerate(template["fields"]):
        path = f"fields[{index}]"
        if not isinstance(field, dict):
            add("FIELD_INVALID", path, "a field is an object")
            continue
        _unknown(field, _FIELD_KEYS, path + ".", add)
        key = field.get("field_key")
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key) or key in _RESERVED:
            add("INVALID_KEY", f"{path}.field_key", "lower snake case, not a CEL reserved word")
        elif key in keys:
            add("DUPLICATE_KEY", f"{path}.field_key", f"`{key}` is used twice")
        else:
            keys.add(key)
        text(field.get("label"), f"{path}.label")
        if "help" in field:
            text(field["help"], f"{path}.help")
        field_type = resolve_field_type(field.get("data_type"))
        if field_type is None:
            add("UNKNOWN_FIELD_TYPE", f"{path}.data_type", "not in the core/fields palette")
            continue
        kind = field_type.value
        if "required" in field and not isinstance(field["required"], bool):
            add("INVALID_CONFIG", f"{path}.required", "must be a boolean")
        for rule in ("visible_if", "required_if"):
            if rule in field:
                cel(field[rule], f"{path}.{rule}")
        if (kind == "computed") != ("expression" in field):
            add("INVALID_CONFIG", f"{path}.expression", "required for, and only for, `calculated`")
        elif kind == "computed":
            cel(field["expression"], f"{path}.expression")
        if (kind == "option") != ("options" in field):
            add("INVALID_CONFIG", f"{path}.options", "required for, and only for, `select`")
        elif kind == "option":
            _options(field["options"], f"{path}.options", add, text)
        units = field.get("units")
        if (kind == "measurement") != ("units" in field):
            add("INVALID_CONFIG", f"{path}.units", "required for, and only for, `measurement`")
        elif kind == "measurement" and not (
            isinstance(units, list)
            and units
            and all(isinstance(unit, str) and unit for unit in units)
            and len(set(units)) == len(units)
        ):
            add("INVALID_CONFIG", f"{path}.units", "a non-empty list of distinct unit codes")
        if "validation" in field:
            validation = field["validation"]
            if not isinstance(validation, dict) or set(validation) != _VALIDATION_KEYS:
                add("INVALID_CONFIG", f"{path}.validation", "{cel_expression, error_message}")
            else:
                cel(validation["cel_expression"], f"{path}.validation.cel_expression")
                text(validation["error_message"], f"{path}.validation.error_message")
        if "range" in field:
            limits = field["range"]
            if (
                not field_type.rangeable
                or not isinstance(limits, dict)
                or not limits
                or not set(limits) <= {"min", "max"}
            ):
                add("INVALID_CONFIG", f"{path}.range", "`min` and/or `max`, on a numeric type only")
                continue
            for name, limit in limits.items():
                if isinstance(limit, str):
                    cel(limit, f"{path}.range.{name}")
                elif not is_finite_number(limit):
                    add("INVALID_CONFIG", f"{path}.range.{name}", "a finite number or a CEL string")
    _sections(template.get("sections", []), keys, add, text)
    return issues


type _Add = Callable[[str, str, str], None]
type _Text = Callable[[object, str], None]


def _unknown(item: Mapping[str, Any], allowed: frozenset[str], prefix: str, add: _Add) -> None:
    for name in item:
        if name not in allowed:
            add("UNKNOWN_PROPERTY", f"{prefix}{name}", "not part of the template schema")


def _options(options: object, path: str, add: _Add, text: _Text) -> None:
    if not isinstance(options, list) or not options:
        add("INVALID_OPTIONS", path, "a non-empty list of {value, label}")
        return
    seen: set[str] = set()
    for index, option in enumerate(options):
        value = option.get("value") if isinstance(option, dict) else None
        if not isinstance(option, dict) or set(option) != {"value", "label"}:
            add("INVALID_OPTIONS", f"{path}[{index}]", "an option is {value, label}")
            continue
        if not isinstance(value, str) or not value or value in seen:
            add("INVALID_OPTIONS", f"{path}[{index}].value", "a distinct plain string")
        else:
            seen.add(value)
        text(option["label"], f"{path}[{index}].label")


def _sections(sections: object, keys: set[str], add: _Add, text: _Text) -> None:
    if not isinstance(sections, list):
        add("INVALID_SECTION", "sections", "must be a list")
        return
    section_keys: set[str] = set()
    placed: set[str] = set()
    for index, section in enumerate(sections):
        path = f"sections[{index}]"
        if not isinstance(section, dict) or set(section) != {"section_key", "title", "fields"}:
            add("INVALID_SECTION", path, "a section is {section_key, title, fields}")
            continue
        key, members = section["section_key"], section["fields"]
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key) or key in section_keys:
            add("INVALID_KEY", f"{path}.section_key", "distinct, lower snake case")
        else:
            section_keys.add(key)
        text(section["title"], f"{path}.title")
        if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
            add("INVALID_SECTION", f"{path}.fields", "a list of field keys")
            continue
        for member in members:
            if member not in keys or member in placed:
                add("INVALID_SECTION", f"{path}.fields", f"`{member}` is unknown or already placed")
            placed.add(member)
