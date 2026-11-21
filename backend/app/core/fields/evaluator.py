"""Server-side evaluation of a form template against submitted answers.

The same semantics as the client engine (``frontend/src/shells/form/engine.ts``):
visibility to a fixpoint, hidden answers dropped, calculated fields, required /
``required_if``, answer shapes, CEL validation and ranges, all fail closed. Both run every
case in ``tests/contract/form_fixtures.json``. The template must have passed
``lint_template``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.core.cel import CelError, CelErrorCode, CelOk, CelResult, JsonValue, evaluate
from app.core.fields.registry import (
    finite_float,
    is_empty_value,
    is_valid_value,
    resolve_field_type,
)

NON_BOOL_RESULT = "NON_BOOL_RESULT"
NON_NUMERIC_RESULT = "NON_NUMERIC_RESULT"


@dataclass(frozen=True, slots=True)
class FieldError:
    """``required`` | ``type_mismatch`` | ``invalid`` (template message) | ``rule_error`` (code)."""

    kind: str
    code: str | None = None
    message: Any = None  # the template's ``error_message``, unresolved (a localised string)


@dataclass(frozen=True, slots=True)
class FormEvaluation:
    visible: tuple[str, ...]  # field keys to show, in template order
    values: dict[str, JsonValue]  # visible, non-empty answers plus the calculated values
    required: tuple[str, ...]
    failed: tuple[str, ...]  # answers outside their range: flagged, not blocking
    errors: dict[str, FieldError]

    @property
    def valid(self) -> bool:
        return not self.errors


def _run(expression: object, context: Mapping[str, JsonValue]) -> CelResult:
    """Evaluate without raising: a malformed context value fails closed like any rule error."""
    try:
        return evaluate(expression if isinstance(expression, str) else "", context)
    except (ValueError, TypeError, OverflowError) as exc:
        return CelError(CelErrorCode.INVALID_ARGUMENT, str(exc))


def _is_calculated(field: Mapping[str, Any]) -> bool:
    field_type = resolve_field_type(field.get("data_type"))
    return field_type is not None and field_type.value == "computed"


def evaluate_form(
    template: Mapping[str, Any],
    values: Mapping[str, JsonValue],
    context: Mapping[str, JsonValue] | None = None,
) -> FormEvaluation:
    """Evaluate ``values`` (keyed by ``field_key``) against ``template``.

    ``context`` adds CEL variables (``asset``, ``device``, ...); ``fields`` is always the
    form's own answers. A value sent for a calculated field is ignored and recomputed.
    """
    fields: list[Mapping[str, Any]] = list(template["fields"])
    extra = dict(context or {})

    def context_for(answers: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        return {**extra, "fields": dict(answers)}

    def materialise(visible: set[str]) -> tuple[dict[str, JsonValue], dict[str, str]]:
        """Answers of the visible input fields, then each visible calculated field in order."""
        answers: dict[str, JsonValue] = {}
        failures: dict[str, str] = {}
        for field in fields:
            key = field["field_key"]
            if key in visible and not _is_calculated(field) and not is_empty_value(values.get(key)):
                answers[key] = values[key]
        for field in fields:
            key = field["field_key"]
            if key not in visible or not _is_calculated(field):
                continue
            result = _run(field.get("expression"), context_for(answers))
            if isinstance(result, CelError):
                failures[key] = result.code.value
            elif not is_empty_value(result.value):
                answers[key] = result.value
        return answers, failures

    # Visibility to a fixpoint (a hidden answer leaves ``fields``, which can hide its
    # dependants). If it never settles, only what the last two passes agree on stays.
    visible = {field["field_key"] for field in fields}
    for _ in range(len(fields) + 1):
        rule_context = context_for(materialise(visible)[0])
        shown = {
            field["field_key"]
            for field in fields
            if "visible_if" not in field or _passes(_run(field["visible_if"], rule_context))
        }
        previous, visible = visible, shown
        if previous == visible:
            break
    else:
        visible &= previous

    answers, failures = materialise(visible)
    rule_context = context_for(answers)
    errors: dict[str, FieldError] = {}
    required: list[str] = []
    failed: list[str] = []

    def bound(spec: object) -> float | str:
        """A range bound: a number, or CEL that must give a finite double."""
        if isinstance(spec, str):
            result = _run(spec, rule_context)
            if isinstance(result, CelError):
                return result.code.value
            spec = result.value
        number = finite_float(spec)
        return NON_NUMERIC_RESULT if number is None else number

    for field in fields:
        key = field["field_key"]
        if key not in visible:
            continue
        if field.get("required") is True or (
            "required_if" in field and not _is_false(_run(field["required_if"], rule_context))
        ):
            required.append(key)
        if key in failures:
            errors[key] = FieldError("rule_error", failures[key])
            continue
        if key not in answers:
            if key in required:
                errors[key] = FieldError("required")
            continue
        value = answers[key]
        if not _is_calculated(field) and not is_valid_value(field, value):
            errors[key] = FieldError("type_mismatch")
            continue
        validation = field.get("validation")
        if validation is not None:
            result = _run(validation.get("cel_expression"), rule_context)
            if isinstance(result, CelError):
                errors[key] = FieldError("rule_error", result.code.value)
            elif not isinstance(result.value, bool):
                errors[key] = FieldError("rule_error", NON_BOOL_RESULT)
            elif not result.value:
                errors[key] = FieldError("invalid", message=validation.get("error_message"))
            if key in errors:
                continue
        limits = field.get("range")
        if limits is None:
            continue
        # The measured number: the answer itself, or ``value`` of a measurement.
        reading = finite_float(value.get("value") if isinstance(value, dict) else value)
        low = bound(limits["min"]) if "min" in limits else None
        high = bound(limits["max"]) if "max" in limits else None
        broken = next((item for item in (low, high) if isinstance(item, str)), None)
        if broken is not None or reading is None:
            errors[key] = FieldError("rule_error", broken or NON_NUMERIC_RESULT)
        elif (isinstance(low, float) and reading < low) or (
            isinstance(high, float) and reading > high
        ):
            failed.append(key)

    order = [field["field_key"] for field in fields]
    return FormEvaluation(
        visible=tuple(key for key in order if key in visible),
        values=answers,
        required=tuple(required),
        failed=tuple(failed),
        errors=errors,
    )


def _passes(result: CelResult) -> bool:
    return isinstance(result, CelOk) and result.value is True


def _is_false(result: CelResult) -> bool:
    """required_if fails closed: only an explicit ``false`` makes the field optional."""
    return isinstance(result, CelOk) and result.value is False
