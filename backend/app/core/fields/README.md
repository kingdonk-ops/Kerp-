# `app.core.fields` — the one field-type registry and form-template engine (T-0110)

One palette of field types and one set of template semantics for every form, ITP, issue
template and asset-schema field (diff §2.1, `quality-and-inspection.md` §One field-type
registry). The client mirrors it in `frontend/src/lib/fields` (palette, answer shapes) and
`frontend/src/shells/form` (engine). It lives in `app/core`: no tables, routes or tenant data.

```python
from app.core.fields import evaluate_form, lint_template

issues = lint_template(template)            # [] when valid; LintIssue(code, path, message)
result = evaluate_form(template, answers, {"asset": {...}})   # template must lint clean
result.valid, result.errors, result.failed, result.values, result.visible, result.required
```

## Contract (shared with the client)

`tests/contract/README.md` describes all three files; both runtimes run all of them.

| File | Holds | Server suite |
|---|---|---|
| `field_types.json` | the palette: key, aliases, answer shape, `rangeable` | `tests/test_fields_conformance.py` |
| `form_fixtures.json` | templates + answers → expected evaluation; contexts both runtimes reject | same |
| `conformance_fixtures.json` | CEL expressions (T-0017), incl. the T-0110 edge set | `tests/test_cel_conformance.py` |

`EVALUATOR_VERSION` (1) equals `evaluator_version` in `form_fixtures.json`. Change the
semantics → bump it on both runtimes and in the file (template revisions will pin it, TD-472).

## Palette (`registry.py`)

`numeric` (alias `number`), `text`, `boolean`, `select`, `date`, `measurement`, `photo`,
`signature`, `instrument_ref`, `calculated`. Answer shapes are in `tests/contract/README.md`.
An unanswered field is absent, `null`, `""` or `[]`; `0` and `false` are answers.

## Template (`lint.py`)

```jsonc
{
  "default_language": "en",                       // BCP 47, default "en"
  "fields": [{
    "field_key": "dft_avg",                       // lower snake case, unique, not a CEL reserved word
    "label": {"en": "Average DFT"},               // localised string
    "help": "…",                                  // localised string, optional
    "data_type": "calculated",                    // a palette key or alias
    "expression": "(fields.dft_1 + fields.dft_2) / 2.0",   // calculated only
    "options": [{"value": "A", "label": "…"}],    // select only
    "units": ["mm", "in"],                        // measurement only
    "required": true, "required_if": "<CEL>", "visible_if": "<CEL>",
    "validation": {"cel_expression": "<CEL>", "error_message": "…"},
    "range": {"min": 240, "max": "asset.attributes.dft_max"}   // numeric, measurement, calculated
  }],
  "sections": [{"section_key": "s1", "title": "…", "fields": ["dft_avg"]}]   // optional grouping
}
```

- **Localised strings** (EDGE-SCREENS §2.2, TD-183): `label`, `help`, option `label`,
  `validation.error_message`, section `title` are a non-empty plain string or a per-locale map
  keyed by BCP 47 tag. `LOCALISED_EMPTY_MAP` for `{}`, `LOCALISED_MISSING_DEFAULT` when the map
  has no entry for `default_language` (tags compare case-insensitively), `LOCALISED_INVALID`
  otherwise. Keys, option values and CEL must be plain strings (`INVALID_KEY`,
  `INVALID_OPTIONS`, `INVALID_CEL`).
- **Other codes**: `TEMPLATE_INVALID`, `FIELD_INVALID`, `UNKNOWN_PROPERTY` (a misspelt
  `visibleIf` is an error, not silently ignored), `INVALID_DEFAULT_LANGUAGE`, `DUPLICATE_KEY`,
  `UNKNOWN_FIELD_TYPE`, `INVALID_CONFIG` (type-specific property missing or misplaced, bad
  `range`), `INVALID_SECTION`. Issues come in document order with a path such as
  `fields[2].validation.error_message`.
- CEL is compiled (`app.core.cel.compile_expression`) but not type-checked, and field
  references are not resolved yet (TD-473).

## Evaluation (`evaluator.py`) — same on the client

1. **`fields`** in CEL holds the visible, answered input fields, then each visible
   `calculated` field in template order (it sees the calculated fields before it; a reference
   to a later one is `NO_SUCH_KEY`). A value sent for a calculated field is ignored. `context`
   adds variables but cannot replace `fields`.
2. **Visibility** runs to a fixpoint: a hidden answer leaves `fields`, which can hide its
   dependants. If it never settles (at most `len(fields) + 1` passes), only the fields visible
   in both of the last two passes stay (TD-233).
3. Per visible field, first match wins: calculated expression failed → `rule_error` (CEL
   code); unanswered → `required` if `required` or `required_if` is not exactly `false`;
   answer of the wrong shape → `type_mismatch`; `validation` → `rule_error` (CEL code or
   `NON_BOOL_RESULT`) or `invalid` (carries the template's `error_message`); `range` →
   `rule_error` (CEL code, or `NON_NUMERIC_RESULT` when a bound or the reading is not a finite
   double) or the key is added to **`failed`**.
4. **`failed` does not block**: an out-of-range reading is a failed result to record (and to
   raise an NCR from, EDGE-SCREENS §2.1), not an invalid form. `valid` is "no errors".
5. **Fail closed** everywhere: a rule passes only on exactly `true`; a malformed answer or
   context value (bad `$int`, impossible `$timestamp`) is `INVALID_ARGUMENT` for every rule.

## Writing rules that agree on both runtimes

Plain JSON numbers are CEL **doubles** (TD-040). Compare number fields with double literals:
`fields.count == 2.0`, `fields.grade in [1.0, 2.0]`. `fields.count == 2` is true on the client
and an error on the server (pinned gap, TD-471; it fails closed, but a `visible_if` written
that way hides the field on the server and drops its answer). Guard optional fields with
`has(fields.x)`. Avoid `matches()` beyond plain ASCII patterns, string functions other than
`contains` / `startsWith` / `endsWith` / `matches` / `size`, and `timestamp()` on text that is
not strict RFC 3339 — each has a pinned gap in `conformance_fixtures.json` (TD-041, TD-471).
