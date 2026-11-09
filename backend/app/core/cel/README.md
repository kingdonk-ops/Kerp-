# `app.core.cel` — CEL evaluation, server runtime

CEL (Common Expression Language) replaces JSONLogic for every dynamic rule (`visible_if`,
`required_if`, validation, dynamic math, range checks, hold gating — consolidation §3,
EDGE-SCREENS §2). The client evaluates the same expressions offline with
`frontend/src/lib/cel`; this package re-evaluates them on the server (sync ingest, hard gates).

It lives in `app/core` (platform kernel), not `app/modules`: it owns no tables, routes or
tenant data, and every module may call it.

```python
from app.core.cel import CelError, CelOk, evaluate

result = evaluate(
    "fields.steel_temp >= fields.dew_point + 3.0",
    {"fields": {"steel_temp": 18.5, "dew_point": 12.0}},
)
match result:
    case CelOk(value=value):  # value is KERP CEL JSON: True here
        ...
    case CelError(code=code, message=message):  # code: CelErrorCode, e.g. NO_SUCH_KEY
        ...
```

- **Context and result encoding** ("KERP CEL JSON"): `tests/contract/README.md`. Plain JSON numbers
  are CEL doubles; ints are `{"$int": "3"}`, timestamps `{"$timestamp": "..."}` (TD-040).
  A malformed context raises `ValueError`/`TypeError` (caller bug); CEL failures are returned.
- **Error codes** (`CelErrorCode`, `errors.py`): one taxonomy shared with the client; celpy's
  `CELEvalError` is classified by its underlying exception class.
- **Caching**: compiled programs are cached per expression text (`lru_cache`, 1024 entries).
- **Lazy import**: `celpy` (and `lark`) are imported only inside functions (T-0022);
  importing `app.core.cel` does not load them.
- **Fail closed**: a gate passes **only** on `CelOk(value=True)` — check
  `isinstance(result, CelOk) and result.value is True`. Every `CelError` and every non-bool value
  (including `CelOk("a")`, `CelOk(None)`) blocks. Never use truthiness.
- **Strict operators** (`strict.py`): celpy 0.5 returns values where CEL says error (`1 / 2.0`,
  `2.5 * 2`, `true && 'a'`, `'abc'[0]`, `[1][-1]`, `string(null)`, `filter()` on a null flag,
  ...). The wrapper overrides `+ - * / %`, `&& ||` and `[]` in celpy's function table, runs a
  strict `Evaluator` subclass (`Environment(runner_class=...)`) that requires bool macro
  predicates and restricts `string()` to the types CEL converts, and rejects at compile time
  what cel-js's checker rejects (`has(x)` without a field selection, literal non-bool `&&`/`||`
  operands and macro predicates, celpy's non-standard `min()`/`reduce()`).
- **Limits**: expressions over 4096 UTF-8 bytes return `INVALID_ARGUMENT` before parsing.
- **Parity**: `tests/test_cel_conformance.py` runs every fixture in
  `tests/contract/conformance_fixtures.json`. Remaining celpy deviations are pinned there as
  `known_gap` fixtures (TD-041); none fails open. Avoid int-left mixed ordering (`1 < 2.0`) and
  `== null` on fields that may hold a value (both error on the server).
- Only standard CEL functions are available (`size()`, not `count()` — TD-042).
