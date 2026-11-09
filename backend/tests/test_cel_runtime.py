"""Server CEL wrapper behaviour not covered by the shared fixtures (T-0017)."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core import cel
from app.core.cel import CelError, CelErrorCode, CelOk, evaluate
from app.core.cel.errors import classify_celpy_error
from app.core.cel.runtime import _compile
from app.core.cel.values import UnencodableValueError, encode_value

BACKEND = Path(__file__).resolve().parents[1]


def test_importing_the_wrapper_does_not_import_celpy() -> None:
    code = "import sys, app.core.cel; assert 'celpy' not in sys.modules, 'celpy imported eagerly'"
    subprocess.run([sys.executable, "-c", code], cwd=BACKEND, check=True)


def test_public_api() -> None:
    assert set(cel.__all__) >= {"evaluate", "CelOk", "CelError", "CelErrorCode", "JsonValue"}


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
