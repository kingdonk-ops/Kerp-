"""KERP CEL JSON <-> celpy values (encoding: ``tests/contract/README.md``).

Plain JSON numbers are always CEL doubles; ints, timestamps and the other types JSON
cannot express are single-key ``$tag`` objects. The client wrapper implements the same
encoding, which is what makes the two runtimes comparable.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from celpy import celtypes

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1

_INT_RE = re.compile(r"-?(0|[1-9][0-9]*)")
_RFC3339_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})", re.IGNORECASE
)
_DOUBLE_NAMES = {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}


class UnencodableValueError(TypeError):
    """A CEL result has no KERP CEL JSON form (bytes, type values, non-string map keys)."""


def decode_context(context: Mapping[str, JsonValue]) -> dict[str, celtypes.Value]:
    """Decode an evaluation context; raises ``ValueError``/``TypeError`` if malformed."""
    return {str(name): decode_value(value) for name, value in context.items()}


def decode_value(value: JsonValue) -> celtypes.Value:
    from celpy import celtypes

    if value is None:
        return None
    if isinstance(value, bool):
        return celtypes.BoolType(value)
    if isinstance(value, int | float):
        return celtypes.DoubleType(float(value))
    if isinstance(value, str):
        return celtypes.StringType(value)
    if isinstance(value, list):
        return celtypes.ListType([decode_value(item) for item in value])
    if isinstance(value, dict):
        if len(value) == 1:
            ((key, inner),) = value.items()
            if key.startswith("$"):
                return _decode_tag(key, inner)
        return celtypes.MapType(
            {celtypes.StringType(key): decode_value(item) for key, item in value.items()}
        )
    raise TypeError(f"not a KERP CEL JSON value: {type(value).__name__}")


def _decode_tag(tag: str, inner: JsonValue) -> celtypes.Value:
    from celpy import celtypes

    if not isinstance(inner, str):
        raise ValueError(f"{tag} value must be a string")
    if tag == "$int":
        if not _INT_RE.fullmatch(inner) or not INT64_MIN <= int(inner) <= INT64_MAX:
            raise ValueError(f"invalid $int: {inner!r}")
        return celtypes.IntType(int(inner))
    if tag == "$double":
        if inner not in _DOUBLE_NAMES:
            raise ValueError(f"invalid $double: {inner!r}")
        return celtypes.DoubleType(_DOUBLE_NAMES[inner])
    if tag == "$timestamp":
        if not _RFC3339_RE.fullmatch(inner):
            raise ValueError(f"invalid $timestamp (RFC 3339 with offset): {inner!r}")
        moment = datetime.fromisoformat(inner).astimezone(UTC)
        # JS Date has millisecond precision; truncate so both runtimes see the same value.
        moment = moment.replace(microsecond=moment.microsecond // 1000 * 1000)
        return celtypes.TimestampType(moment)
    raise ValueError(f"unsupported tag in a context: {tag}")


def encode_value(value: object) -> JsonValue:
    """Encode a CEL result; raises ``UnencodableValueError`` if it has no JSON form."""
    from celpy import celtypes

    if value is None:
        return None
    if isinstance(value, bool | celtypes.BoolType):
        return bool(value)
    if isinstance(value, celtypes.UintType):
        return {"$uint": str(int(value))}
    if isinstance(value, int):
        if not INT64_MIN <= value <= INT64_MAX:
            raise UnencodableValueError(f"int out of int64 range: {value}")
        return {"$int": str(int(value))}
    if isinstance(value, float):
        if math.isnan(value):
            return {"$double": "NaN"}
        if math.isinf(value):
            return {"$double": "Infinity" if value > 0 else "-Infinity"}
        return float(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, datetime):
        return {"$timestamp": _format_timestamp(value)}
    if isinstance(value, timedelta):
        return {"$duration": _format_duration(value)}
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise UnencodableValueError("map with non-string keys")
        return {str(key): encode_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [encode_value(item) for item in value]
    raise UnencodableValueError(f"no KERP CEL JSON form for {type(value).__name__}")


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise UnencodableValueError("naive datetime")
    moment = value.astimezone(UTC)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def _format_duration(value: timedelta) -> str:
    total_us = (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds
    sign = "-" if total_us < 0 else ""
    seconds, micros = divmod(abs(total_us), 1_000_000)
    fraction = f".{micros:06d}".rstrip("0") if micros else ""
    return f"{sign}{seconds}{fraction}s"
