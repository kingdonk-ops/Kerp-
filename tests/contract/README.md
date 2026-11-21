# Cross-runtime contract fixtures

## `conformance_fixtures.json` — CEL parity (T-0017)

CEL (Common Expression Language) is evaluated on the client (offline, `frontend/src/lib/cel`,
`@marcbachmann/cel-js`) and on the server (`backend/app/core/cel`, `celpy`). Every fixture in this
file runs on **both** runtimes and must produce exactly the same encoded value or the same KERP
error code (`docs/spec/KERP-CONSOLIDATION-AND-DIFF.md` §3, EDGE-SCREENS §7.1 item 2).

Suites that load it (both fail if the file is missing, has no fixtures, or has duplicate ids):

- `backend/tests/test_cel_conformance.py` (`pytest`)
- `frontend/src/lib/cel/conformance.test.ts` (`npm run test`)

### Schema

```jsonc
{
  "format": "kerp-cel-conformance/1",
  "error_codes": ["PARSE_ERROR", ...],      // the KERP taxonomy; both code enums must equal this list
  "fixtures": [
    {
      "id": "arith.int.add",               // unique, dotted
      "category": "arithmetic",            // free text, for coverage reporting
      "description": "int addition",
      "expression": "1 + 2",               // CEL source
      "context": {"fields": {"x": 2.5}},   // top-level variables, values in the encoding below
      "expect": {"value": {"$int": "3"}}   // or {"error": "<one of error_codes>"}
      // optional, only for a documented parity gap:
      // "known_gap": {"runtime": "python" | "js", "todo": "TD-xxx", "actual": {"value": ...} | {"error": ...}}
    }
  ]
}
```

`expect` is the CEL-spec result. A fixture with `known_gap` documents a divergence one runtime
currently has: the *other* runtime is asserted against `expect`, and the named runtime is asserted
against the pinned `actual` (not skipped), so a library upgrade that changes either behaviour fails
the suite and the fixture must be updated. Every gap must cite an open TODO that lists `T-0017` in
its `tasks` (currently `TD-041` and `TD-471`; both suites check this). Gap fixtures do not count
toward the T-0131 parity target.

The `edge.*` fixtures (T-0110) cover the semantics that tend to diverge: null and absent fields,
numeric types and overflow, strings and unicode, timestamps and time zones, list/map membership,
short-circuiting and errors. Their gaps are summarised in `TD-471`.

**Fail closed.** A rule passes only when the result is exactly the bool `true`
(`CelOk(value=True)` / `{ok: true, value: true}`); every error and every non-bool value blocks.

### Value encoding ("KERP CEL JSON")

JSON cannot tell `1` from `1.0`, has no 64-bit integers and no time types, so values are encoded
explicitly. The same encoding is used for contexts and results, by both wrappers.

| CEL type | JSON | Notes |
|---|---|---|
| `null_type` | `null` | |
| `bool` | `true` / `false` | |
| `string` | `"..."` | |
| `double` | plain JSON number, e.g. `3`, `2.5` | **Every plain number is a double**, also `3` (see TD-040) |
| `double` (non-finite) | `{"$double": "NaN" \| "Infinity" \| "-Infinity"}` | |
| `int` (int64) | `{"$int": "<decimal string>"}` | string keeps full 64-bit precision |
| `uint` (uint64) | `{"$uint": "<decimal string>"}` | results only |
| `timestamp` | `{"$timestamp": "<RFC 3339>"}` (uppercase `T`/`Z` only) | millisecond precision; results are UTC `YYYY-MM-DDTHH:MM:SS.sssZ` |
| `duration` | `{"$duration": "<seconds>s"}` | results only; e.g. `"5400s"`, `"1.5s"` |
| `list` | JSON array | |
| `map` | JSON object (string keys) | an object with a single `$`-prefixed key is always a tag; unknown tags are rejected |

A plain JSON integer too large for a double is `Infinity` on both runtimes (as `JSON.parse`). A
`$timestamp` must be a real instant in years 1–9999 after its offset is applied (no 30 February,
no hour 24, no leap second); anything else is a malformed context. The contexts both runtimes
must reject are listed in `form_fixtures.json` → `invalid_contexts`.

A **missing key** is simply absent from its object (`has()` is false, selecting it is
`NO_SUCH_KEY`); a key holding `null` is present. A missing **top-level** variable is
`UNDECLARED_REFERENCE`. Results that cannot be encoded (bytes, types, non-string map keys) are
reported as `EVAL_ERROR`.

### Error codes

| Code | Meaning |
|---|---|
| `PARSE_ERROR` | Expression is not valid CEL syntax (also a misused macro) |
| `UNDECLARED_REFERENCE` | Top-level variable not present in the context |
| `NO_SUCH_KEY` | Map key / field missing, or field selection on a non-map (e.g. `null`) |
| `INDEX_OUT_OF_RANGE` | List index outside the list |
| `NO_MATCHING_OVERLOAD` | Type mismatch, or unknown function / method |
| `DIVIDE_BY_ZERO` | Integer `/` or `%` by zero |
| `INT_OVERFLOW` | int64 / uint64 out of range (arithmetic, literal, conversion) |
| `INVALID_ARGUMENT` | Bad argument value: unparsable number / timestamp, invalid regex; expression over 4096 UTF-8 bytes |
| `EVAL_ERROR` | Anything else, including unencodable results |

## `field_types.json` — the field-type palette (T-0110)

The one palette of `core/fields` (diff §2.1). `backend/app/core/fields/registry.py`
(`FIELD_TYPES`) and `frontend/src/lib/fields/registry.ts` (`FIELD_TYPES`) must both equal
`types`; `backend/tests/test_fields_conformance.py` and
`frontend/src/shells/form/conformance.test.ts` assert it.

| `value` | Types | A valid (non-empty) answer |
|---|---|---|
| `number` | `numeric` (alias `number`) | finite JSON number (a CEL double); not a bool, not `{"$int": …}` |
| `string` | `text` | string |
| `boolean` | `boolean` | `true` / `false` |
| `option` | `select` | string equal to one of the field's option `value`s |
| `date` | `date` | string of the shape `YYYY-MM-DD` (ASCII digits; no calendar check) |
| `measurement` | `measurement` | exactly `{"value": <finite number>, "unit": <one of the field's units>}` |
| `attachments` | `photo` | list of objects, each with a non-empty string `attachment_id` |
| `attachment` | `signature` | object with a non-empty string `attachment_id` |
| `instrument` | `instrument_ref` | object with a non-empty string `instrument_id` |
| `computed` | `calculated` | never supplied: the value is the result of `expression` |

Unanswered is: absent, `null`, `""` or `[]`. `0` and `false` are answers. `rangeable` types
may carry a `range`.

## `form_fixtures.json` — form-template parity (T-0110)

Template semantics (visibility, calculated fields, required, answer shapes, validation, ranges)
are evaluated by `frontend/src/shells/form/engine.ts` and `backend/app/core/fields/evaluator.py`.
Every case runs on both and must give exactly `expected`. The rules are written out in
`backend/app/core/fields/README.md`.

```jsonc
{
  "format": "kerp-form-conformance/1",
  "evaluator_version": 1,                  // must equal EVALUATOR_VERSION on both runtimes
  "unlinted_templates": ["failclosed"],    // deliberately broken; every other template lints clean
  "templates": {"coating": {"fields": [...]}},
  "cases": [{
    "name": "coating: validation false is invalid",     // unique
    "template": "coating",
    "values": {"coating_adhesion_psi": 1320.5},         // answers by field_key, KERP CEL JSON
    "context": {"asset": {...}},                        // extra CEL variables
    "expected": {
      "visible": ["..."], "required": ["..."], "failed": ["..."],   // field keys, template order
      "values": {"...": 1320.5},                        // sanitised answers + calculated values
      "errors": {"coating_adhesion_psi": {"kind": "invalid"}}       // rule_error also has "code"
    }
  }],
  "invalid_contexts": [{"x": {"$int": "abc"}}]          // evaluate() must throw / raise on each
}
```

`valid` is `errors` being empty. Changing the semantics means bumping `evaluator_version` and
`EVALUATOR_VERSION` on both runtimes in the same PR.
