"""KERP CEL error taxonomy and the mapping of celpy errors onto it.

The code list must equal ``error_codes`` in ``tests/contract/conformance_fixtures.json``
(asserted by ``tests/test_cel_conformance.py``; the client enum is asserted the same way).
"""

from __future__ import annotations

from enum import StrEnum


class CelErrorCode(StrEnum):
    """Runtime-independent CEL error codes (``tests/contract/README.md``)."""

    PARSE_ERROR = "PARSE_ERROR"
    UNDECLARED_REFERENCE = "UNDECLARED_REFERENCE"
    NO_SUCH_KEY = "NO_SUCH_KEY"
    INDEX_OUT_OF_RANGE = "INDEX_OUT_OF_RANGE"
    NO_MATCHING_OVERLOAD = "NO_MATCHING_OVERLOAD"
    DIVIDE_BY_ZERO = "DIVIDE_BY_ZERO"
    INT_OVERFLOW = "INT_OVERFLOW"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    EVAL_ERROR = "EVAL_ERROR"


_UNDECLARED_PREFIX = "undeclared reference to '"


def undeclared_name(message: str) -> str | None:
    """Name from celpy's ``undeclared reference to 'x' (in activation ...)`` message."""
    if not message.startswith(_UNDECLARED_PREFIX):
        return None
    return message[len(_UNDECLARED_PREFIX) :].split("'", 1)[0]


def short_message(message: str) -> str:
    """Drop celpy's activation dump from a message (it can be kilobytes long)."""
    return message.split(" (in activation", 1)[0][:500]


def classify_celpy_error(exc: BaseException, called_functions: frozenset[str]) -> CelErrorCode:
    """Map a celpy ``CELEvalError`` (or a bare Python exception) to a KERP code.

    celpy reports ``CELEvalError(message, underlying exception class, underlying args)``;
    the underlying class is what identifies the failure. ``called_functions`` are the
    function/method names called in the expression: celpy reports an unknown function as an
    undeclared reference, cel-js as a missing overload — KERP uses NO_MATCHING_OVERLOAD.
    """
    from celpy.evaluation import CELEvalError

    if isinstance(exc, CELEvalError):
        args = exc.args
        message = str(args[0]) if args else ""
        cause = args[1] if len(args) > 1 and isinstance(args[1], type) else None
        cause_args = args[2] if len(args) > 2 else None
    else:
        message = str(exc)
        cause = type(exc)
        cause_args = exc.args

    if cause is None:
        return CelErrorCode.EVAL_ERROR
    if issubclass(cause, ZeroDivisionError):
        return CelErrorCode.DIVIDE_BY_ZERO
    if issubclass(cause, IndexError):
        return CelErrorCode.INDEX_OUT_OF_RANGE
    if issubclass(cause, KeyError):
        name = undeclared_name(message)
        if name is None:
            return CelErrorCode.NO_SUCH_KEY
        if name in called_functions:
            return CelErrorCode.NO_MATCHING_OVERLOAD
        return CelErrorCode.UNDECLARED_REFERENCE
    if issubclass(cause, TypeError):
        # celpy: selecting a field on a non-map value (e.g. null); cel-js: no_such_key.
        if "does not support field selection" in message:
            return CelErrorCode.NO_SUCH_KEY
        return CelErrorCode.NO_MATCHING_OVERLOAD
    if issubclass(cause, ValueError):
        # celpy labels every ValueError "return error for overflow"; only a bare
        # ValueError("overflow") is a real range error (int64/uint64 arithmetic,
        # literal, conversion). Others are unparsable arguments (int('x'), bad timestamp).
        if tuple(cause_args or ()) == ("overflow",):
            return CelErrorCode.INT_OVERFLOW
        return CelErrorCode.INVALID_ARGUMENT
    if message == "match error":  # invalid RE2 pattern in matches()
        return CelErrorCode.INVALID_ARGUMENT
    return CelErrorCode.EVAL_ERROR
