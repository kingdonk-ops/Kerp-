"""Shared field-type palette and form-template cases on the server engine (T-0110).

``tests/contract/field_types.json`` and ``tests/contract/form_fixtures.json`` are asserted
by the client too (``frontend/src/lib/fields/registry.test.ts``,
``frontend/src/shells/form/conformance.test.ts``). Schema: ``tests/contract/README.md``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from app.core.cel import evaluate
from app.core.fields import (
    EVALUATOR_VERSION,
    FIELD_TYPES,
    evaluate_form,
    lint_template,
    resolve_field_type,
)

CONTRACT = Path(__file__).resolve().parents[2] / "tests" / "contract"


def load_document(path: Path, key: str) -> dict[str, Any]:
    """Load a contract file; raise (never skip) if it is missing or ``key`` is empty."""
    if not path.is_file():
        raise RuntimeError(f"contract file not found: {path}")
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document.get(key), list) or not document[key]:
        raise RuntimeError(f"contract file has no {key}: {path}")
    return document


# Loaded at collection time on purpose: a missing/empty file is a collection error.
FORMS = load_document(CONTRACT / "form_fixtures.json", "cases")
CASES: list[dict[str, Any]] = FORMS["cases"]


def test_loader_rejects_missing_and_empty_files(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not found"):
        load_document(tmp_path / "absent.json", "cases")
    empty = tmp_path / "empty.json"
    empty.write_text('{"cases": []}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="no cases"):
        load_document(empty, "cases")


def test_palette_equals_the_shared_contract() -> None:
    contract = load_document(CONTRACT / "field_types.json", "types")["types"]
    assert [{**asdict(t), "aliases": list(t.aliases)} for t in FIELD_TYPES] == contract
    # diff §2.1: the palette every form, ITP and asset-schema field draws from.
    spec = {"numeric", "text", "measurement", "photo", "signature", "instrument_ref", "calculated"}
    assert spec <= {t.key for t in FIELD_TYPES}
    assert resolve_field_type("number") is resolve_field_type("numeric") is FIELD_TYPES[0]
    assert resolve_field_type("barcode") is None
    assert resolve_field_type(None) is None


def test_evaluator_version_is_pinned_and_names_are_unique() -> None:
    assert FORMS["evaluator_version"] == EVALUATOR_VERSION
    names = [case["name"] for case in CASES]
    assert len(names) == len(set(names))
    assert {case["template"] for case in CASES} == set(FORMS["templates"])


def test_importing_the_package_does_not_import_celpy() -> None:
    code = "import sys, app.core.fields; assert not {'celpy', 'lark'} & set(sys.modules)"
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize("name", sorted(FORMS["templates"]))
def test_fixture_templates_lint_clean(name: str) -> None:
    issues = lint_template(FORMS["templates"][name])
    if name in FORMS["unlinted_templates"]:
        assert {issue.code for issue in issues} == {"INVALID_CEL", "UNKNOWN_FIELD_TYPE"}
    else:
        assert issues == []


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_form_case(case: dict[str, Any]) -> None:
    result = evaluate_form(FORMS["templates"][case["template"]], case["values"], case["context"])
    errors = {
        key: {k: v for k, v in asdict(error).items() if v is not None and k != "message"}
        for key, error in result.errors.items()
    }
    actual = {
        "visible": list(result.visible),
        "values": result.values,
        "required": list(result.required),
        "failed": list(result.failed),
        "errors": errors,
    }
    # Compared as JSON text: 1 and 1.0 are the same double, but true is not 1.
    assert _canonical(actual) == _canonical(case["expected"])
    assert result.valid is (not case["expected"]["errors"])
    # An invalid answer carries the template's message, for the renderer to localise.
    assert all(error.message for error in result.errors.values() if error.kind == "invalid")


def _canonical(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float):
        try:
            return ("double", float(value))
        except OverflowError:  # an integer beyond the double range reads as Infinity
            return ("double", float("inf"))
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return {key: _canonical(item) for key, item in value.items()}


@pytest.mark.parametrize("context", FORMS["invalid_contexts"], ids=json.dumps)
def test_invalid_context_is_rejected(context: dict[str, Any]) -> None:
    with pytest.raises((ValueError, TypeError)):
        evaluate("true", context)
