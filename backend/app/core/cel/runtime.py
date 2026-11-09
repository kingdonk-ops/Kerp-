"""Server-side CEL evaluation (celpy), mirrored by ``frontend/src/lib/cel``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from app.core.cel.errors import CelErrorCode, classify_celpy_error, short_message
from app.core.cel.values import JsonValue, UnencodableValueError, decode_context, encode_value

if TYPE_CHECKING:
    import celpy

PROGRAM_CACHE_SIZE = 1024


@dataclass(frozen=True, slots=True)
class CelOk:
    """Successful evaluation; ``value`` is KERP CEL JSON (``tests/contract/README.md``)."""

    value: JsonValue


@dataclass(frozen=True, slots=True)
class CelError:
    """Failed evaluation (parse or runtime) with a runtime-independent code."""

    code: CelErrorCode
    message: str


type CelResult = CelOk | CelError


@dataclass(frozen=True, slots=True)
class _Compiled:
    program: celpy.Runner
    called_functions: frozenset[str]


@lru_cache(maxsize=1)
def _environment() -> celpy.Environment:
    import celpy

    return celpy.Environment()


@lru_cache(maxsize=PROGRAM_CACHE_SIZE)
def _compile(expression: str) -> _Compiled:
    """Parse + plan once per expression text (raises ``celpy.CELParseError``)."""
    from lark import Token

    environment = _environment()
    ast = environment.compile(expression)
    called = frozenset(
        str(child)
        for node in ast.iter_subtrees()
        if node.data in ("ident_arg", "member_dot_arg")
        for child in node.children
        if isinstance(child, Token)
    )
    return _Compiled(environment.program(ast), called)


def evaluate(expression: str, context: Mapping[str, JsonValue]) -> CelResult:
    """Evaluate ``expression`` against ``context`` (top-level variables, KERP CEL JSON).

    CEL failures are returned as ``CelError``; a malformed ``context`` is a caller bug and
    raises ``ValueError``/``TypeError``.
    """
    from celpy.celparser import CELParseError
    from celpy.evaluation import CELEvalError

    activation = decode_context(context)
    try:
        compiled = _compile(expression)
    except CELParseError as exc:
        return CelError(CelErrorCode.PARSE_ERROR, short_message(str(exc)))
    try:
        result = compiled.program.evaluate(activation)
    except Exception as exc:  # celpy surfaces CELEvalError and, rarely, bare exceptions
        return _error(exc, compiled)
    if isinstance(result, CELEvalError):
        return _error(result, compiled)
    try:
        return CelOk(encode_value(result))
    except UnencodableValueError as exc:
        return CelError(CelErrorCode.EVAL_ERROR, str(exc))


def _error(exc: BaseException, compiled: _Compiled) -> CelError:
    code = classify_celpy_error(exc, compiled.called_functions)
    message = str(exc.args[0]) if exc.args else type(exc).__name__
    return CelError(code, short_message(message))
