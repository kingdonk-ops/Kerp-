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
    case CelOk(value=value):          # value is KERP CEL JSON: True here
        ...
    case CelError(code=code, message=message):  # code: CelErrorCode, e.g. NO_SUCH_KEY
        ...
```

- **Context and result encoding** ("KERP CEL JSON"): `tests/contract/README.md`. Plain JSON numbers
  are CEL doubles; ints are `{"$int": "3"}`, timestamps `{"$timestamp": "..."}` (TD-034).
  A malformed context raises `ValueError`/`TypeError` (caller bug); CEL failures are returned.
- **Error codes** (`CelErrorCode`, `errors.py`): one taxonomy shared with the client; celpy's
  `CELEvalError` is classified by its underlying exception class.
- **Caching**: compiled programs are cached per expression text (`lru_cache`, 1024 entries).
- **Lazy import**: `celpy` (and `lark`) are imported only inside functions (T-0022);
  importing `app.core.cel` does not load them.
- **Parity**: `tests/test_cel_conformance.py` runs every fixture in
  `tests/contract/conformance_fixtures.json`. Known celpy deviations from the CEL spec are pinned
  there as `known_gap` fixtures (TD-032) — until resolved, avoid mixed int/double arithmetic and
  `== null` on fields that may hold a value.
- Only standard CEL functions are available (`size()`, not `count()` — TD-033).
