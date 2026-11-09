"""Server-side CEL evaluation (celpy), mirrored by ``frontend/src/lib/cel``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from app.core.cel.errors import CelErrorCode, classify_celpy_error, short_message
from app.core.cel.strict import strict_functions, strict_runner_class
from app.core.cel.values import JsonValue, UnencodableValueError, decode_context, encode_value

if TYPE_CHECKING:
    import celpy
    from lark import Token, Tree

PROGRAM_CACHE_SIZE = 1024
# Same cap on the client (frontend/src/lib/cel/evaluate.ts), counted in UTF-8 bytes.
MAX_EXPRESSION_BYTES = 4096


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

    return celpy.Environment(runner_class=strict_runner_class())


class _MacroArgumentError(Exception):
    """A macro used with an argument CEL rejects at parse time (e.g. ``has(x)``)."""


class _StaticTypeError(Exception):
    """An operand whose literal type can never match (cel-js's checker rejects these)."""


_PREDICATE_MACROS = frozenset({"all", "exists", "exists_one", "filter"})
# celpy-only extension macros; not CEL and not available on the client.
_NON_STANDARD_MACROS = frozenset({"min", "reduce"})


def _unwrap(node: object) -> object:
    """Follow single-child wrapper nodes (expr -> conditionalor -> ... -> primary)."""
    from lark import Tree

    while (
        isinstance(node, Tree)
        and node.data not in ("member_dot", "literal", "list_lit", "map_lit")
        and len(node.children) == 1
    ):
        node = node.children[0]
    return node


def _is_non_bool_literal(node: object) -> bool:
    from lark import Token, Tree

    inner = _unwrap(node)
    if not isinstance(inner, Tree):
        return False
    if inner.data in ("list_lit", "map_lit"):
        return True
    if inner.data == "literal":
        token = inner.children[0]
        return isinstance(token, Token) and token.type != "BOOL_LIT"
    return False


def _check_tree(ast: Tree[Token]) -> frozenset[str]:
    """Static checks celpy skips; returns the called function/method names.

    celpy reports an unknown function as an undeclared reference (KERP: NO_MATCHING_OVERLOAD),
    accepts ``has(x)`` on a plain variable, and evaluates ``true || 'a'`` / ``l.filter(v, 1)``
    by truthiness; CEL / cel-js reject all of these.
    """
    from lark import Token, Tree

    called: set[str] = set()
    for node in ast.iter_subtrees():
        if (
            node.data in ("conditionalor", "conditionaland")
            and len(node.children) == 2
            and any(_is_non_bool_literal(child) for child in node.children)
        ):
            raise _StaticTypeError(f"non-bool literal operand of {node.data}")
        if node.data not in ("ident_arg", "member_dot_arg"):
            continue
        names = [str(child) for child in node.children if isinstance(child, Token)]
        called.update(names)
        arguments = [child for child in node.children if isinstance(child, Tree)]
        if node.data == "ident_arg" and names == ["has"]:
            selected = arguments[0].children if arguments else []
            if len(selected) != 1 or getattr(_unwrap(selected[0]), "data", None) != "member_dot":
                raise _MacroArgumentError("has() requires a single field selection argument")
        if node.data == "member_dot_arg" and set(names) & _NON_STANDARD_MACROS:
            raise _StaticTypeError(f"{names[0]}() is not a CEL function")
        if node.data == "member_dot_arg" and set(names) & _PREDICATE_MACROS:
            macro_args = arguments[1].children if len(arguments) > 1 else []
            if len(macro_args) == 2 and _is_non_bool_literal(macro_args[1]):
                raise _StaticTypeError(f"non-bool literal predicate in {names[0]}()")
    return frozenset(called)


@lru_cache(maxsize=PROGRAM_CACHE_SIZE)
def _compile(expression: str) -> _Compiled:
    """Parse + plan once per expression text.

    Raises ``celpy.CELParseError``, ``_MacroArgumentError`` or ``_StaticTypeError``.
    """
    environment = _environment()
    ast = environment.compile(expression)
    called = _check_tree(ast)
    return _Compiled(environment.program(ast, functions=strict_functions()), called)


def evaluate(expression: str, context: Mapping[str, JsonValue]) -> CelResult:
    """Evaluate ``expression`` against ``context`` (top-level variables, KERP CEL JSON).

    CEL failures are returned as ``CelError``; a malformed ``context`` is a caller bug and
    raises ``ValueError``/``TypeError``.
    """
    from celpy.celparser import CELParseError
    from celpy.evaluation import CELEvalError

    activation = decode_context(context)
    if len(expression.encode("utf-8")) > MAX_EXPRESSION_BYTES:
        return CelError(
            CelErrorCode.INVALID_ARGUMENT,
            f"expression longer than {MAX_EXPRESSION_BYTES} bytes",
        )
    try:
        compiled = _compile(expression)
    except (CELParseError, _MacroArgumentError) as exc:
        return CelError(CelErrorCode.PARSE_ERROR, short_message(str(exc)))
    except _StaticTypeError as exc:
        return CelError(CelErrorCode.NO_MATCHING_OVERLOAD, str(exc))
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
