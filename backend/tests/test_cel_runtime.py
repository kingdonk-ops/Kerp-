"""Server CEL wrapper behaviour not covered by the shared fixtures (T-0017)."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core import cel
from app.core.cel import CelError, CelErrorCode, CelOk, compile_expression, evaluate
from app.core.cel.errors import classify_celpy_error
from app.core.cel.runtime import _compile
from app.core.cel.values import UnencodableValueError, encode_value

BACKEND = Path(__file__).resolve().parents[1]


def test_importing_the_wrapper_does_not_import_celpy() -> None:
    code = "import sys, app.core.cel; assert 'celpy' not in sys.modules, 'celpy imported eagerly'"
    subprocess.run([sys.executable, "-c", code], cwd=BACKEND, check=True)


def test_public_api() -> None:
    assert set(cel.__all__) >= {
        "evaluate",
        "compile_expression",
        "CelOk",
        "CelError",
        "CelErrorCode",
        "JsonValue",
    }


def test_compiled_programs_are_cached() -> None:
    _compile.cache_clear()
    for value in (1.0, 2.0, 3.0):
        assert evaluate("fields.x > 1.5", {"fields": {"x": value}}) == CelOk(value > 1.5)
    info = _compile.cache_info()
    assert (info.misses, info.hits) == (1, 2)


def test_parse_error_is_reported_not_raised() -> None:
    result = evaluate("1 +", {})
    assert isinstance(result, CelError)
    assert result.code is CelErrorCode.PARSE_ERROR
    assert result.message


def test_undeclared_reference_message_is_short() -> None:
    result = evaluate("missing_var", {})
    assert isinstance(result, CelError)
    assert result.code is CelErrorCode.UNDECLARED_REFERENCE
    assert result.message == "undeclared reference to 'missing_var'"


@pytest.mark.parametrize(
    "expression",
    ["b'abc'", "type(1)", "{1: 'a'}"],
    ids=["bytes", "type", "int-keyed-map"],
)
def test_unencodable_results_are_eval_errors(expression: str) -> None:
    result = evaluate(expression, {})
    assert isinstance(result, CelError)
    assert result.code is CelErrorCode.EVAL_ERROR


@pytest.mark.parametrize(
    "context",
    [
        {"fields": {"$unknown": "1"}},
        {"fields": {"$int": "12.5"}},
        {"fields": {"$int": 12}},
        {"fields": {"$int": "9223372036854775808"}},
        {"fields": {"$uint": "1"}},
        {"fields": {"$duration": "1s"}},
        {"fields": {"$double": "nan"}},
        {"fields": {"$timestamp": "2024-01-01"}},
        {"fields": {"$timestamp": "2024-01-01T00:00:00"}},
        {"fields": {"$timestamp": "2024-01-01t00:00:00Z"}},
        {"fields": {"$timestamp": "2024-01-01T00:00:00z"}},
        {"fields": object()},
    ],
    ids=[
        "unknown-tag",
        "int-not-integer",
        "int-not-string",
        "int-out-of-range",
        "uint-input",
        "duration-input",
        "double-bad-name",
        "timestamp-date-only",
        "timestamp-no-offset",
        "timestamp-lowercase-t",
        "timestamp-lowercase-z",
        "non-json",
    ],
)
def test_invalid_context_raises(context: dict[str, object]) -> None:
    with pytest.raises((ValueError, TypeError)):
        evaluate("true", context)  # type: ignore[arg-type]


def test_context_tags() -> None:
    context = {
        "fields": {
            "nan": {"$double": "NaN"},
            "neg_inf": {"$double": "-Infinity"},
            "ts": {"$timestamp": "2024-06-01T10:30:00.123456+02:00"},
        }
    }
    assert evaluate("fields.nan != fields.nan", context) == CelOk(True)
    assert evaluate("fields.neg_inf", context) == CelOk({"$double": "-Infinity"})
    # Offsets are normalised to UTC and sub-millisecond digits dropped (JS Date parity).
    assert evaluate("fields.ts", context) == CelOk({"$timestamp": "2024-06-01T08:30:00.123Z"})


def test_encode_value_plain_python_types() -> None:
    assert encode_value(True) is True
    assert encode_value(3) == {"$int": "3"}
    assert encode_value(2.5) == 2.5
    assert encode_value(float("nan")) == {"$double": "NaN"}
    assert encode_value("s") == "s"
    assert encode_value((1.5, None)) == [1.5, None]
    assert encode_value(datetime(2024, 1, 1, tzinfo=UTC)) == {
        "$timestamp": "2024-01-01T00:00:00.000Z"
    }
    assert encode_value(timedelta(seconds=-90)) == {"$duration": "-90s"}
    assert encode_value(timedelta(microseconds=250)) == {"$duration": "0.00025s"}
    with pytest.raises(UnencodableValueError):
        encode_value(2**63)
    with pytest.raises(UnencodableValueError):
        encode_value(datetime(2024, 1, 1))  # naive datetimes are ambiguous


def test_expression_length_cap_counts_utf8_bytes_before_compiling() -> None:
    _compile.cache_clear()
    at_limit = "'" + "é" * 2047 + "'"  # 2 + 2047 * 2 = 4096 bytes
    assert isinstance(evaluate(at_limit, {}), CelOk)
    over = "'" + "é" * 2047 + "x'"  # 4097 bytes
    result = evaluate(over, {})
    assert isinstance(result, CelError)
    assert result.code is CelErrorCode.INVALID_ARGUMENT
    assert _compile.cache_info().currsize == 1  # the rejected text never reached the cache


@pytest.mark.parametrize(
    ("expression", "code"),
    [
        ("1[0]", CelErrorCode.NO_MATCHING_OVERLOAD),
        ("[1, 2][fields.missing]", CelErrorCode.NO_SUCH_KEY),
        ("fields.missing[0]", CelErrorCode.NO_SUCH_KEY),
        ("fields.missing / 2.0", CelErrorCode.NO_SUCH_KEY),
        ("has()", CelErrorCode.PARSE_ERROR),
        ("has(fields.a, fields.b)", CelErrorCode.PARSE_ERROR),
        ("[] && true", CelErrorCode.NO_MATCHING_OVERLOAD),
        ("true + 1", CelErrorCode.NO_MATCHING_OVERLOAD),
        ("true || {}", CelErrorCode.NO_MATCHING_OVERLOAD),
    ],
)
def test_strict_operator_edges(expression: str, code: CelErrorCode) -> None:
    result = evaluate(expression, {"fields": {"a": 1.0}})
    assert isinstance(result, CelError)
    assert result.code is code


def test_strict_operator_values() -> None:
    assert evaluate("{'a': 1}['a']", {}) == CelOk({"$int": "1"})
    assert evaluate("1.0 / 0.0 == 1.0 / 0.0", {}) == CelOk(True)
    assert evaluate("fields.n / 0.0", {"fields": {"n": {"$double": "NaN"}}}) == CelOk(
        {"$double": "NaN"}
    )
    assert evaluate("string(1u) + string(fields.t)", {"fields": {"t": True}}) == CelOk("1true")


def test_string_table_entry_is_untouched() -> None:
    # string() strictness lives in the evaluator, so the `string` type identifier keeps
    # being celpy's StringType (type(x) == string, type(type(x)) == type(string)).
    from app.core.cel.strict import strict_functions

    assert "string" not in strict_functions()


@pytest.mark.parametrize(
    ("expression", "outcome"),
    [
        ("string()", CelErrorCode.NO_MATCHING_OVERLOAD),
        ("string(1, 2)", CelErrorCode.NO_MATCHING_OVERLOAD),
        ("string(1 / 0)", CelErrorCode.DIVIDE_BY_ZERO),
        ("string(b'\\xff')", CelErrorCode.INVALID_ARGUMENT),
        ("string(fields.missing)", CelErrorCode.NO_SUCH_KEY),
        ("[1, 0].filter(x, 1 / x > 0)", CelErrorCode.DIVIDE_BY_ZERO),
    ],
)
def test_strict_string_and_macro_errors(expression: str, outcome: CelErrorCode) -> None:
    result = evaluate(expression, {"fields": {}})
    assert isinstance(result, CelError)
    assert result.code is outcome


def test_strict_macros_leave_valid_predicates_alone() -> None:
    context = {"fields": {"r": [1.0, 0.0, 2.5]}}
    assert evaluate("fields.r.filter(v, v > 0.5)", context) == CelOk([1.0, 2.5])
    assert evaluate("fields.r.exists_one(v, v > 2.0)", context) == CelOk(True)
    assert evaluate("fields.r.map(v, v * 2.0)", context) == CelOk([2.0, 0.0, 5.0])
    assert evaluate("string(-2) + string(2.5)", {}) == CelOk("-22.5")


def test_returned_eval_error_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    # celpy may return a CELEvalError as the result value instead of raising it.
    import celpy

    from app.core.cel import runtime

    class _Program:
        def evaluate(self, activation: object) -> object:
            return celpy.CELEvalError("divide by zero", ZeroDivisionError, ("x",))

    stub = runtime._Compiled(_Program(), frozenset())  # type: ignore[arg-type]
    monkeypatch.setattr(runtime, "_compile", lambda expression: stub)
    result = evaluate("anything", {})
    assert result == CelError(CelErrorCode.DIVIDE_BY_ZERO, "divide by zero")


def test_negative_and_fractional_durations_match_client() -> None:
    assert evaluate("duration('-90s')", {}) == CelOk({"$duration": "-90s"})
    assert evaluate("duration('-1.5s')", {}) == CelOk({"$duration": "-1.5s"})
    assert evaluate("duration('250us')", {}) == CelOk({"$duration": "0.00025s"})


class _OddError(Exception):
    pass


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (ZeroDivisionError("x"), CelErrorCode.DIVIDE_BY_ZERO),
        (IndexError("x"), CelErrorCode.INDEX_OUT_OF_RANGE),
        (KeyError("x"), CelErrorCode.NO_SUCH_KEY),
        (TypeError("x"), CelErrorCode.NO_MATCHING_OVERLOAD),
        (ValueError("overflow"), CelErrorCode.INT_OVERFLOW),
        (ValueError("bad"), CelErrorCode.INVALID_ARGUMENT),
        (_OddError("x"), CelErrorCode.EVAL_ERROR),
    ],
)
def test_classify_plain_exceptions(exc: Exception, code: CelErrorCode) -> None:
    assert classify_celpy_error(exc, frozenset()) is code


def test_classify_unknown_celpy_error() -> None:
    import celpy

    assert (
        classify_celpy_error(celpy.CELEvalError("odd", RuntimeError, ("x",)), frozenset())
        is CelErrorCode.EVAL_ERROR
    )
    assert classify_celpy_error(celpy.CELEvalError("odd"), frozenset()) is CelErrorCode.EVAL_ERROR


# --- compile_expression: compile-on-save without evaluating (T-0018) ---------------------


def test_compile_expression_accepts_declared_variables() -> None:
    declared = frozenset({"fields", "params"})
    assert compile_expression("fields.t >= params.min && has(fields.x)", declared) is None
    # Macro-bound variables are not free references.
    assert compile_expression("fields.l.all(i, i.ok) && [1].exists(z, z > 0)", declared) is None
    assert compile_expression("fields.l.all(i, i.m.exists(j, j == i.k))", declared) is None
    assert compile_expression("fields.l.map(i, i * 2.0).all(i, i > 0.0)", declared) is None
    assert compile_expression("anything > 1.0") is None  # no schema: syntax only


@pytest.mark.parametrize(
    ("expression", "code"),
    [
        ("fields.t >", CelErrorCode.PARSE_ERROR),
        ("has(fields)", CelErrorCode.PARSE_ERROR),
        ("true || 'a'", CelErrorCode.NO_MATCHING_OVERLOAD),
        ("record.x > 1.0", CelErrorCode.UNDECLARED_REFERENCE),
        ("true || record", CelErrorCode.UNDECLARED_REFERENCE),  # short-circuit is no excuse
        (".record", CelErrorCode.UNDECLARED_REFERENCE),
        # A macro variable is bound only inside its own macro call.
        ("fields.l.filter(x, x.y).size() > 0 && x", CelErrorCode.UNDECLARED_REFERENCE),
        ("fields.l.all(record, record) && record.ok", CelErrorCode.UNDECLARED_REFERENCE),
        ("record.all(r, r)", CelErrorCode.UNDECLARED_REFERENCE),  # target is outside the scope
        ("fields.l.all(f, .f)", CelErrorCode.UNDECLARED_REFERENCE),  # .f is the top-level f
        ("fields.l.exists(record.a, true)", CelErrorCode.UNDECLARED_REFERENCE),  # binds nothing
        ("x" * 4097, CelErrorCode.INVALID_ARGUMENT),
    ],
)
def test_compile_expression_rejects(expression: str, code: CelErrorCode) -> None:
    error = compile_expression(expression, frozenset({"fields"}))
    assert isinstance(error, CelError)
    assert error.code == code


def test_compile_expression_names_the_undeclared_variable() -> None:
    error = compile_expression("fields.a == record.b", frozenset({"fields"}))
    assert error == CelError(CelErrorCode.UNDECLARED_REFERENCE, "undeclared reference to 'record'")
