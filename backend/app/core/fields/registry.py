"""The one field-type palette (diff §2.1, quality-and-inspection §One field-type registry).

Mirrored by ``frontend/src/lib/fields/registry.ts``; both must equal
``tests/contract/field_types.json``. Answer shapes: ``tests/contract/README.md``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.core.cel import JsonValue

# Version of the form evaluation semantics; equals ``evaluator_version`` in the form fixtures.
EVALUATOR_VERSION = 1


@dataclass(frozen=True, slots=True)
class FieldType:
    key: str
    aliases: tuple[str, ...]
    value: str  # the answer shape checked by ``is_valid_value``
    rangeable: bool


FIELD_TYPES: tuple[FieldType, ...] = (
    FieldType("numeric", ("number",), "number", True),
    FieldType("text", (), "string", False),
    FieldType("boolean", (), "boolean", False),
    FieldType("select", (), "option", False),
    FieldType("date", (), "date", False),
    FieldType("measurement", (), "measurement", True),
    FieldType("photo", (), "attachments", False),
    FieldType("signature", (), "attachment", False),
    FieldType("instrument_ref", (), "instrument", False),
    FieldType("calculated", (), "computed", True),
)
_BY_NAME = {name: t for t in FIELD_TYPES for name in (t.key, *t.aliases)}
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # ASCII digits, shape only (as on the client)


def resolve_field_type(data_type: object) -> FieldType | None:
    """The palette entry for a ``data_type`` (key or alias); ``None`` when unknown."""
    return _BY_NAME.get(data_type) if isinstance(data_type, str) else None


def is_empty_value(value: object) -> bool:
    """Unanswered: ``None``, ``""`` or an empty list. ``0`` and ``False`` are answers."""
    return value is None or value == "" or value == []


def finite_float(value: object) -> float | None:
    """``value`` as a double when it is a finite JSON number (not a bool), else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:  # an integer beyond the double range (Infinity on the client)
        return None
    return number if math.isfinite(number) else None


def is_finite_number(value: object) -> bool:
    return finite_float(value) is not None


def _has_id(value: object, key: str) -> bool:
    return isinstance(value, dict) and isinstance(value.get(key), str) and value[key] != ""


def is_valid_value(field: Mapping[str, Any], value: JsonValue) -> bool:
    """Whether a non-empty answer has the shape of the field's type. Unknown types never match."""
    field_type = resolve_field_type(field.get("data_type"))
    kind = field_type.value if field_type is not None else None
    if kind == "number":
        return is_finite_number(value)
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "option":
        options = field.get("options") or []
        return isinstance(value, str) and any(option.get("value") == value for option in options)
    if kind == "date":
        return isinstance(value, str) and _DATE_RE.fullmatch(value) is not None
    if kind == "measurement":
        return (
            isinstance(value, dict)
            and set(value) == {"value", "unit"}
            and is_finite_number(value["value"])
            and isinstance(value["unit"], str)
            and value["unit"] in (field.get("units") or [])
        )
    if kind == "attachments":
        return isinstance(value, list) and all(_has_id(item, "attachment_id") for item in value)
    if kind == "attachment":
        return _has_id(value, "attachment_id")
    if kind == "instrument":
        return _has_id(value, "instrument_id")
    return False  # "computed" answers come from the expression, never from the user
