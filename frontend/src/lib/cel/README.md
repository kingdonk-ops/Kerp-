# `src/lib/cel` — CEL evaluation, client runtime

Offline evaluation of the CEL rules in form / ITP templates (consolidation §3, EDGE-SCREENS §2).
The server re-evaluates the same expressions with `backend/app/core/cel`; both must agree on
every fixture in `tests/contract/conformance_fixtures.json` (`conformance.test.ts`).

```ts
import { evaluate } from "../lib/cel";

const result = evaluate("fields.visual_result == 'REJECT'", { fields: { visual_result: "REJECT" } });
if (result.ok) {
  result.value; // KERP CEL JSON: true
} else {
  result.code; // CelErrorCode, e.g. "NO_SUCH_KEY"
}
```

- **Library**: [`@marcbachmann/cel-js`](https://github.com/marcbachmann/cel-js) (MIT, pure JS, zero
  dependencies, no WASM, actively maintained). Chosen over `cel-js` (MIT, depends on chevrotain +
  ramda, last release mid-2025, ~10x slower per its successor's benchmarks). The spec diagram's
  "cel-js / WebAssembly" is not needed: a pure-JS evaluator works offline in the bundle.
- **Encoding**: `tests/contract/README.md`. Plain JSON numbers are CEL doubles; ints are
  `{"$int": "3"}` (decoded to `bigint`), timestamps `{"$timestamp": "..."}` (decoded to `Date`).
  A malformed context throws; CEL failures are returned as `{ok: false, code, message}`.
- **Error codes**: `errors.ts` (`CEL_ERROR_CODES`, equal to the fixture file's `error_codes`)
  and the cel-js code mapping.
- **Caching**: compiled programs are cached per expression text (LRU, 1024 entries).
- Results are range-checked: an int outside int64 becomes `INT_OVERFLOW` (cel-js uses unbounded
  `bigint`). Remaining divergences are pinned `known_gap` fixtures (TD-032).
