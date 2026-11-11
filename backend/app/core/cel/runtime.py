"""Server-side CEL evaluation (celpy), mirrored by ``frontend/src/lib/cel``."""

from __future__ import annotations

from collections.abc import Collection, Mapping
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
    # Top-level variables the expression references (macro-bound variables excluded).
    free_identifiers: frozenset[str] = frozenset()


@lru_cache(maxsize=1)
def _environment() -> celpy.Environment:
    import celpy

    return celpy.Environment(runner_class=strict_runner_class())


class _MacroArgumentError(Exception):
    """A macro used with an argument CEL rejects at parse time (e.g. ``has(x)``)."""


class _StaticTypeError(Exception):
    """An operand whose literal type can never match (cel-js's checker rejects these)."""


_PREDICATE_MACROS = frozenset({"all", "exists", "exists_one", "filter"})
# Macros whose first argument binds a variable (``l.all(x, x > 0)`` binds ``x``).
_BINDING_MACROS = _PREDICATE_MACROS | {"map"}
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


def _free_identifiers(ast: Tree[Token]) -> frozenset[str]:
    """Top-level variable names referenced by ``ast``, without macro-bound variables.

    A macro's variable is bound only inside that macro's own arguments (``l.all(x, x > 0)``
    binds ``x`` in ``x > 0``, not in ``l`` nor anywhere outside the call), so a name that
    leaks out of its macro is reported. ``.x`` always names the top-level ``x``. Iterative,
    so deep nesting cannot hit the recursion limit.
    """
    from lark import Token, Tree

    free: set[str] = set()
    stack: list[tuple[Tree[Token], frozenset[str]]] = [(ast, frozenset())]
    while stack:
        node, bound = stack.pop()
        tokens = [str(c) for c in node.children if isinstance(c, Token)]
        subtrees = [c for c in node.children if isinstance(c, Tree)]
        if node.data == "ident":
            free.update(name for name in tokens if name not in bound)
            continue
        if node.data == "dot_ident":
            free.update(tokens)
            continue
        if node.data == "member_dot_arg" and set(tokens) & _BINDING_MACROS and len(subtrees) > 1:
            target, arguments = subtrees[0], subtrees[1].children
            variable = _unwrap(arguments[0]) if arguments else None  # ident unwraps to a Token
            if isinstance(variable, Token):
                stack.append((target, bound))
                inner = bound | {str(variable)}
                stack.extend((a, inner) for a in arguments[1:] if isinstance(a, Tree))
                continue
        stack.extend((child, bound) for child in subtrees)
    return frozenset(free)


@lru_cache(maxsize=PROGRAM_CACHE_SIZE)
def _compile(expression: str) -> _Compiled:
    """Parse + plan once per expression text.

    Raises ``celpy.CELParseError``, ``_MacroArgumentError`` or ``_StaticTypeError``.
    """
    environment = _environment()
    ast = environment.compile(expression)
    called = _check_tree(ast)
    program = environment.program(ast, functions=strict_functions())
    return _Compiled(program, called, _free_identifiers(ast))


def _compiled_or_error(expression: str) -> _Compiled | CelError:
    from celpy.celparser import CELParseError

    if len(expression.encode("utf-8")) > MAX_EXPRESSION_BYTES:
        return CelError(
            CelErrorCode.INVALID_ARGUMENT,
            f"expression longer than {MAX_EXPRESSION_BYTES} bytes",
        )
    try:
        return _compile(expression)
    except (CELParseError, _MacroArgumentError) as exc:
        return CelError(CelErrorCode.PARSE_ERROR, short_message(str(exc)))
    except _StaticTypeError as exc:
        return CelError(CelErrorCode.NO_MATCHING_OVERLOAD, str(exc))


def compile_expression(
    expression: str, variables: Collection[str] | None = None
) -> CelError | None:
    """Compile ``expression`` without evaluating it (compile-on-save); ``None`` when valid.

    Applies the same parse, length and static checks as ``evaluate``. With ``variables`` (the
    top-level names of a context schema) every free variable must be one of them, otherwise
    ``UNDECLARED_REFERENCE``, even where evaluation would short-circuit past it. Only the
    top-level names are checked: there is no server-side CEL type checker (TD-041).
    """
    compiled = _compiled_or_error(expression)
    if isinstance(compiled, CelError):
        return compiled
    if variables is not None:
        unknown = sorted(compiled.free_identifiers - set(variables))
        if unknown:
            return CelError(
                CelErrorCode.UNDECLARED_REFERENCE, f"undeclared reference to '{unknown[0]}'"
            )
    return None


def evaluate(expression: str, context: Mapping[str, JsonValue]) -> CelResult:
    """Evaluate ``expression`` against ``context`` (top-level variables, KERP CEL JSON).

    CEL failures are returned as ``CelError``; a malformed ``context`` is a caller bug and
    raises ``ValueError``/``TypeError``.
    """
    from celpy.evaluation import CELEvalError

    activation = decode_context(context)
    compiled = _compiled_or_error(expression)
    if isinstance(compiled, CelError):
        return compiled
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
