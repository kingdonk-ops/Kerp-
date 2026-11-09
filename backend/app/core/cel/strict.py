"""Spec-conformant overrides for celpy 0.5 operators (fail-closed server gate, T-0017).

celpy resolves every operator through an overridable function table
(``celpy.evaluation.base_functions``); ``Environment.program(ast, functions=...)`` replaces
entries. Without these overrides celpy *returns a value* where CEL says error (or a wrong
value) — e.g. ``1 / 2.0 == 0``, ``2.5 * 2 == 5.0``, ``true && 'a' == 'a'``, ``'abc'[0]``,
``[1, 2][-1]``, ``0.0 / 0.0 == +Inf``, ``string(true) == 'True'`` — any of which can turn a
blocking rule into a passing one on the hard server gate. Raising ``TypeError`` /
``IndexError`` here becomes a ``CELEvalError`` that ``errors.classify_celpy_error`` maps
(NO_MATCHING_OVERLOAD / INDEX_OUT_OF_RANGE), exactly like celpy's own failures.

Macros and ``string()`` are not table entries: ``strict_runner_class()`` returns an
``InterpretedRunner`` whose ``Evaluator`` subclass requires bool macro predicates
(``filter``/``exists_one`` raise, ``all``/``exists`` yield an error value that ``false`` /
``true`` can still absorb) and restricts ``string()`` to the types CEL converts.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import celpy
    from celpy.evaluation import CELFunction, Context, Result
    from lark import Token, Tree

_Binary = Callable[[object, object], object]


def _numeric_kind(value: object) -> str | None:
    """CEL numeric type of a celpy value, or None if it is not a number."""
    from celpy import celtypes

    if isinstance(value, bool | celtypes.BoolType):
        return None
    if isinstance(value, celtypes.UintType):
        return "uint"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "double"
    return None


def _first_error(*operands: object) -> object | None:
    """celpy passes error values into operators; they propagate unchanged (left first)."""
    from celpy.evaluation import CELEvalError

    return next((value for value in operands if isinstance(value, CELEvalError)), None)


def _no_overload(name: str, left: object, right: object) -> TypeError:
    return TypeError(f"no such overload: {type(left).__name__} {name} {type(right).__name__}")


def _homogeneous(name: str, native: _Binary) -> _Binary:
    """Arithmetic without implicit int/uint/double promotion (CEL has none)."""

    def apply(left: object, right: object) -> object:
        error = _first_error(left, right)
        if error is not None:
            return error
        left_kind, right_kind = _numeric_kind(left), _numeric_kind(right)
        if (left_kind or right_kind) and left_kind != right_kind:
            raise _no_overload(name, left, right)
        return native(left, right)

    return apply


def _divide(left: object, right: object) -> object:
    from celpy import celtypes

    error = _first_error(left, right)
    if error is not None:
        return error
    left_kind, right_kind = _numeric_kind(left), _numeric_kind(right)
    if (left_kind or right_kind) and left_kind != right_kind:
        raise _no_overload("/", left, right)
    if isinstance(left, float) and isinstance(right, float) and right == 0.0:
        # IEEE-754: x/0.0 is NaN for x in {0, NaN}, otherwise +/-Infinity by sign.
        if left == 0.0 or math.isnan(left):
            return celtypes.DoubleType(math.nan)
        sign = math.copysign(1.0, left) * math.copysign(1.0, right)
        return celtypes.DoubleType(math.copysign(math.inf, sign))
    return operator.truediv(left, right)


def _is_bool(value: object) -> bool:
    from celpy import celtypes

    return isinstance(value, bool | celtypes.BoolType)


def _logical(absorbing: bool) -> _Binary:
    """CEL ``&&`` (absorbing=False) / ``||`` (absorbing=True), commutative.

    The absorbing value wins even against an error or non-bool operand; otherwise an
    error operand propagates, and a non-bool operand has no overload.
    """
    from celpy import celtypes
    from celpy.evaluation import CELEvalError

    def apply(left: object, right: object) -> object:
        for value in (left, right):
            if _is_bool(value) and bool(value) is absorbing:
                return celtypes.BoolType(absorbing)
        for value in (left, right):
            if isinstance(value, CELEvalError):
                return value
        if _is_bool(left) and _is_bool(right):
            return celtypes.BoolType(not absorbing)
        raise _no_overload("||" if absorbing else "&&", left, right)

    return apply


def _index(container: object, index: object) -> object:
    """``_[_]`` without Python's string indexing and negative list indexing."""
    from celpy import celtypes

    error = _first_error(container, index)
    if error is not None:
        return error
    if isinstance(container, str | bytes):
        raise _no_overload("[]", container, index)
    if isinstance(container, list):
        if _numeric_kind(index) != "int":
            raise _no_overload("[]", container, index)
        index = cast(int, index)
        if not 0 <= index < len(container):
            raise IndexError(f"index {index} out of range for list of size {len(container)}")
        return container[index]
    if isinstance(container, celtypes.MapType | dict):
        return operator.getitem(container, index)
    raise _no_overload("[]", container, index)


def strict_string(value: object) -> object:
    """CEL ``string()``: only string, bool, int, uint, double, bytes, timestamp, duration.

    celpy's ``StringType(x)`` is Python ``str()``, so ``string(null)`` was ``'None'``,
    ``string([1])`` a Python repr and ``string(true)`` ``'True'``.
    """
    from celpy import celtypes

    if _is_bool(value):
        return celtypes.StringType("true" if value else "false")
    if isinstance(value, bytes):
        return celtypes.StringType(value.decode("utf-8"))  # invalid UTF-8: ValueError
    if isinstance(value, str | int | float | datetime | timedelta):
        return celtypes.StringType(str(value))  # celpy types format per CEL (1e+20, 5400s)
    raise TypeError(f"no such overload: string({type(value).__name__})")


@lru_cache(maxsize=1)
def strict_runner_class() -> type[celpy.Runner]:
    """``InterpretedRunner`` using the strict evaluator (pass as ``runner_class``)."""
    import celpy
    from celpy.evaluation import CELEvalError, Evaluator

    def not_bool(macro: str, result: object) -> CELEvalError:
        message = f"{macro}() predicate must return bool, got {type(result).__name__}"
        return CELEvalError(message, TypeError, (message,))

    class StrictEvaluator(Evaluator):
        def sub_evaluator(self, ast: Tree[Token]) -> Evaluator:
            # Macro bodies (incl. nested macros) must use the strict evaluator too.
            return StrictEvaluator(ast, activation=self.activation)

        def member_dot_arg(self, tree: Tree[Token]) -> object:
            # A macro body raises its error (celpy's nested evaluate()); return it as a
            # value instead so `false && <macro>` / `true || <macro>` can absorb it.
            try:
                return super().member_dot_arg(tree)
            except CELEvalError as exc:
                return exc

        def build_macro_eval(self, child: Tree[Token]) -> Callable[[celpy.celtypes.Value], object]:
            predicate = super().build_macro_eval(child)
            macro = str(child.children[1])
            if macro not in ("filter", "exists_one"):
                return predicate  # map(): any result type is fine

            def checked(value: celpy.celtypes.Value) -> object:
                result = predicate(value)  # errors are raised by the nested evaluator
                if not _is_bool(result):
                    raise not_bool(macro, result)
                return result

            return checked

        def build_ss_macro_eval(
            self, child: Tree[Token]
        ) -> Callable[[celpy.celtypes.Value], object]:
            predicate = super().build_ss_macro_eval(child)
            macro = str(child.children[1])

            def checked(value: celpy.celtypes.Value) -> object:
                # all()/exists() reduce with &&/||: an error value is still absorbed by a
                # false (all) / true (exists) element, as on the client.
                result = predicate(value)
                if _is_bool(result) or isinstance(result, CELEvalError):
                    return result
                return not_bool(macro, result)

            return checked

        def function_eval(
            self, name_token: Token, exprlist: Iterable[object] | None = None
        ) -> Result:
            if name_token.value != "string":
                return super().function_eval(name_token, exprlist)  # type: ignore[arg-type]
            # Errors in the argument list propagate unchanged, as in celpy's own function_eval.
            if isinstance(exprlist, CELEvalError):
                return exprlist
            arguments = list(exprlist or [])
            for argument in arguments:
                if isinstance(argument, CELEvalError):
                    return argument
            try:
                if len(arguments) != 1:
                    raise TypeError("string() takes exactly one argument")
                return cast("Result", strict_string(arguments[0]))
            except (TypeError, ValueError) as exc:
                return CELEvalError(str(exc), exc.__class__, exc.args, token=name_token)

    class StrictRunner(celpy.InterpretedRunner):
        def evaluate(self, context: Context) -> celpy.celtypes.Value:
            evaluator = StrictEvaluator(ast=self.ast, activation=self.new_activation())
            return evaluator.evaluate(context)

    return StrictRunner


def strict_functions() -> dict[str, CELFunction]:
    """Function-table overrides passed to every ``Environment.program`` call."""
    functions: dict[str, object] = {
        "_+_": _homogeneous("+", operator.add),
        "_-_": _homogeneous("-", operator.sub),
        "_*_": _homogeneous("*", operator.mul),
        "_%_": _homogeneous("%", operator.mod),
        "_/_": _divide,
        "_&&_": _logical(absorbing=False),
        "_||_": _logical(absorbing=True),
        "_[_]": _index,
    }
    return cast("dict[str, CELFunction]", functions)
