"""Spec-conformant overrides for celpy 0.5 operators (fail-closed server gate, T-0017).

celpy resolves every operator through an overridable function table
(``celpy.evaluation.base_functions``); ``Environment.program(ast, functions=...)`` replaces
entries. Without these overrides celpy *returns a value* where CEL says error (or a wrong
value) — e.g. ``1 / 2.0 == 0``, ``2.5 * 2 == 5.0``, ``true && 'a' == 'a'``, ``'abc'[0]``,
``[1, 2][-1]``, ``0.0 / 0.0 == +Inf``, ``string(true) == 'True'`` — any of which can turn a
blocking rule into a passing one on the hard server gate. Raising ``TypeError`` /
``IndexError`` here becomes a ``CELEvalError`` that ``errors.classify_celpy_error`` maps
(NO_MATCHING_OVERLOAD / INDEX_OUT_OF_RANGE), exactly like celpy's own failures.

Macros (``all``/``exists``/``exists_one``/``filter``/``map``) are hard-wired in celpy's
evaluator and cannot be overridden here; their remaining gaps are pinned fixtures (TD-038).
"""

from __future__ import annotations

import math
import operator
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from celpy.evaluation import CELFunction

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


def _string_conversion() -> type:
    """``string()`` with CEL's lowercase bool spelling.

    The ``string`` entry is both the conversion function and the type identifier used in
    ``type(x) == string`` (celpy's ``type()`` returns the Python class). The replacement is
    therefore a ``StringType`` subclass whose *class* compares equal to ``StringType``; the
    values it creates are plain ``StringType`` instances.
    """
    from celpy import celtypes

    class _SameAsStringType(type):
        def __eq__(cls, other: object) -> bool:
            return other is celtypes.StringType or other is cls

        def __hash__(cls) -> int:
            return hash(celtypes.StringType)

    class StringConversion(celtypes.StringType, metaclass=_SameAsStringType):
        def __new__(cls, source: object = "") -> celtypes.StringType:  # type: ignore[misc]
            if _is_bool(source):
                return celtypes.StringType("true" if source else "false")
            return celtypes.StringType(source)  # type: ignore[arg-type]

    return StringConversion


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
        "string": _string_conversion(),
    }
    return cast("dict[str, CELFunction]", functions)
