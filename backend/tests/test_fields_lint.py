"""Template lint: structure, palette and localised strings (T-0110, EDGE-SCREENS §2.2)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.core.fields import DEFAULT_LANGUAGE, lint_template

VALID: dict[str, Any] = {
    "default_language": "de",
    "fields": [
        {
            "field_key": "substrate",
            "label": {"de": "Untergrund", "en-GB": "Substrate"},
            "help": "Wie vorgefunden",
            "data_type": "select",
            "required": True,
            "options": [
                {"value": "STEEL", "label": {"DE": "Stahl"}},
                {"value": "CONCRETE", "label": "Beton"},
            ],
        },
        {
            "field_key": "thickness",
            "label": "Dicke",
            "data_type": "measurement",
            "units": ["mm", "in"],
            "range": {"min": 5.8, "max": "asset.attributes.max_thickness"},
            "visible_if": "fields.substrate == 'STEEL'",
            "required_if": "has(fields.substrate)",
            "validation": {
                "cel_expression": "fields.thickness.value > 0.0",
                "error_message": {"de": "Muss positiv sein"},
            },
        },
        {
            "field_key": "double_thickness",
            "label": "x2",
            "data_type": "calculated",
            "expression": "fields.thickness.value * 2.0",
        },
    ],
    "sections": [
        {
            "section_key": "surface",
            "title": {"de": "Oberfläche"},
            "fields": ["substrate", "thickness"],
        }
    ],
}


def edited(path: list[Any], value: Any = None, *, delete: bool = False) -> dict[str, Any]:
    """A copy of ``VALID`` with the item at ``path`` replaced (or removed)."""
    template = copy.deepcopy(VALID)
    target: Any = template
    for step in path[:-1]:
        target = target[step]
    if delete:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return template


def test_valid_template_has_no_issues() -> None:
    assert lint_template(VALID) == []
    assert DEFAULT_LANGUAGE == "en"
    # `default_language` defaults to `en`; plain strings need no entry for it.
    assert lint_template({"fields": [{"field_key": "a", "label": "A", "data_type": "text"}]}) == []


DELETE = object()
F0, F1, F2, S0 = ["fields", 0], ["fields", 1], ["fields", 2], ["sections", 0]
OPTION_1 = [*F0, "options", 1]
RULE = [*F1, "validation"]
CASES: list[tuple[str, Any, str, str]] = [
    ("not-an-object", [], "TEMPLATE_INVALID", ""),
    ("fields-not-a-list", {"fields": {}}, "TEMPLATE_INVALID", ""),
]


def case(name: str, path: list[Any], value: Any, code: str, at: str | None = None) -> None:
    """Edit ``VALID`` at ``path``; expect one issue ``code`` there (or at ``at``)."""
    where = "".join(f"[{step}]" if isinstance(step, int) else f".{step}" for step in path)
    template = edited(path, delete=True) if value is DELETE else edited(path, value)
    CASES.append((name, template, code, where.lstrip(".") if at is None else at))


case("unknown-template-property", ["title"], "x", "UNKNOWN_PROPERTY")
case("bad-default-language", ["default_language"], "german!", "INVALID_DEFAULT_LANGUAGE")
case("field-not-an-object", F2, "x", "FIELD_INVALID")
case("misspelt-rule-property", [*F1, "visibleIf"], "true", "UNKNOWN_PROPERTY")
case("key-not-snake-case", [*F2, "field_key"], "DoubleThickness", "INVALID_KEY")
case("key-is-a-cel-reserved-word", [*F2, "field_key"], "in", "INVALID_KEY")
case("key-is-localised", [*F2, "field_key"], {"de": "x"}, "INVALID_KEY")
case("duplicate-key", [*F2, "field_key"], "thickness", "DUPLICATE_KEY")
case("unknown-type", [*F2, "data_type"], "barcode", "UNKNOWN_FIELD_TYPE")
case("label-missing", [*F2, "label"], DELETE, "LOCALISED_INVALID")
case("label-blank", [*F2, "label"], "  ", "LOCALISED_INVALID")
case("label-empty-map", [*F0, "label"], {}, "LOCALISED_EMPTY_MAP")
case("label-without-default", [*F0, "label"], {"en": "Substrate"}, "LOCALISED_MISSING_DEFAULT")
case("label-bad-tag", [*F0, "label"], {"de": "x", "german!": "y"}, "LOCALISED_INVALID")
case("label-blank-entry", [*F0, "label"], {"de": ""}, "LOCALISED_INVALID")
case("help-empty-map", [*F0, "help"], {}, "LOCALISED_EMPTY_MAP")
case(
    "option-label-no-default", [*OPTION_1, "label"], {"en": "Beton"}, "LOCALISED_MISSING_DEFAULT"
)
case("option-value-localised", [*OPTION_1, "value"], {"de": "BETON"}, "INVALID_OPTIONS")
case("option-value-duplicate", [*OPTION_1, "value"], "STEEL", "INVALID_OPTIONS")
case("option-not-value-label", OPTION_1, {"value": "STEEL"}, "INVALID_OPTIONS")
case("options-empty", [*F0, "options"], [], "INVALID_OPTIONS")
case("options-on-a-calculated-field", [*F2, "options"], [], "INVALID_CONFIG")
case("select-without-options", [*F0, "options"], DELETE, "INVALID_CONFIG")
case("required-not-bool", [*F0, "required"], "yes", "INVALID_CONFIG")
case("error-message-empty-map", [*RULE, "error_message"], {}, "LOCALISED_EMPTY_MAP")
case(
    "error-message-no-default", [*RULE, "error_message"], {"fr": "x"}, "LOCALISED_MISSING_DEFAULT"
)
case("validation-without-message", RULE, {"cel_expression": "true"}, "INVALID_CONFIG")
case("validation-cel-invalid", [*RULE, "cel_expression"], "fields.x >", "INVALID_CEL")
case("visible-if-localised", [*F1, "visible_if"], {"de": "true"}, "INVALID_CEL")
case(
    "required-if-non-standard-function", [*F1, "required_if"], "fields.r.min() > 1.0", "INVALID_CEL"
)
case("calculated-without-expression", [*F2, "expression"], DELETE, "INVALID_CONFIG")
case("expression-on-a-select", [*F0, "expression"], "1.0", "INVALID_CONFIG")
case("expression-invalid", [*F2, "expression"], "1 +", "INVALID_CEL")
case("measurement-without-units", [*F1, "units"], DELETE, "INVALID_CONFIG")
case("measurement-duplicate-units", [*F1, "units"], ["mm", "mm"], "INVALID_CONFIG")
case("units-on-a-select", [*F0, "units"], ["mm"], "INVALID_CONFIG")
case("range-on-a-select", [*F0, "range"], {"min": 1}, "INVALID_CONFIG")
case("range-empty", [*F1, "range"], {}, "INVALID_CONFIG")
case("range-unknown-bound", [*F1, "range"], {"minimum": 1}, "INVALID_CONFIG")
case("range-bound-bool", [*F1, "range"], {"min": True}, "INVALID_CONFIG", at="fields[1].range.min")
case(
    "range-bound-bad-cel", [*F1, "range"], {"max": "1 +"}, "INVALID_CEL", at="fields[1].range.max"
)
case("sections-not-a-list", ["sections"], {}, "INVALID_SECTION")
case("section-shape", S0, {"section_key": "s"}, "INVALID_SECTION")
case("section-key", [*S0, "section_key"], "Surface Prep", "INVALID_KEY")
case("section-title-empty-map", [*S0, "title"], {}, "LOCALISED_EMPTY_MAP")
case(
    "section-title-without-default", [*S0, "title"], {"en": "Surface"}, "LOCALISED_MISSING_DEFAULT"
)
case("section-fields-not-keys", [*S0, "fields"], [1], "INVALID_SECTION")
case("section-unknown-field", [*S0, "fields"], ["nope"], "INVALID_SECTION")
case("section-field-twice", [*S0, "fields"], ["substrate", "substrate"], "INVALID_SECTION")


@pytest.mark.parametrize(
    ("template", "code", "path"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_lint_reports(template: Any, code: str, path: str) -> None:
    issues = lint_template(template)
    assert [(issue.code, issue.path) for issue in issues] == [(code, path)]
    assert issues[0].message


def test_issues_come_in_document_order() -> None:
    template = edited([*F0, "label"], {})
    template["fields"][2]["expression"] = "1 +"
    paths = [issue.path for issue in lint_template(template)]
    assert paths == ["fields[0].label", "fields[2].expression"]
