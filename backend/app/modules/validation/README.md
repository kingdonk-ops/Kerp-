# validation — verification engine and the single rule store (T-0018)

Verification is configuration (CLAUDE.md §6): every verifiable transition calls
`run_checks(context, rule_set)`, and the rules it runs are **data** in one rule store shared by
every rules engine in KERP (ADR 0004 §1, Amendment E). Adding a rule needs no code deploy.
Built fresh; Inspection-ERP's `Finding(key, severity, message)` shape was a reference only
(ADR 0001).

| File | What |
|---|---|
| `models.py` | `oe_validation_rule_set`, `oe_validation_rule`, `oe_validation_finding` (`BaseRecordMixin`, RLS, migration `0005`) |
| `compiler.py` | compile-on-save: kind/engine rules, per-kind CEL context schema, RE2 regex compile |
| `service.py` | `create_rule_set`, `save_rule`, `delete_rule`, `resolve_rule_set`, `run_checks` |
| `schemas.py` | `RuleSpec` (input), `CheckResult` / `CheckFinding` (output) |
| `router.py` | no routes yet (the Rules settings API is T-0034) |

## Calling a gate

```python
from app.modules.validation.service import run_checks

result = run_checks(
    {"record": {...}, "fields": {...}, "actor": {...}, "now": {"$timestamp": "..."}},
    "p6.import.commit",
    session=session,                 # bound to the caller's tenant/project
    subject=f"p6_import:{import_id}",
)
if result.blocked:
    raise TransitionBlocked(result.reason)   # e.g. "Torque wrench TW-12: calibration expired"
```

- The context is KERP CEL JSON (`app/core/cel/README.md`). `params` is reserved: each rule
  sees its own `params` there.
- The set is resolved by key among the sets the session can see, most specific layer first:
  `project`, then `tenant`, then `system`. An unknown set raises `RuleSetNotFoundError`, so a
  misconfigured gate never passes.
- The enabled `check` rules run in `sort_order`, then `rule_key`. A rule passes **only** when
  its expression returns `true`; any other value or a CEL error does not pass (fail closed).
- Every rule that does not pass becomes a finding (`outcome` `fail` or `error`), returned in
  `result.findings` and written to `oe_validation_finding` with `run_id`, `subject`, the set's
  key and version, and the `rule_key` and rule version (ADR 0004 §3). Passing rules are not
  logged; `result.evaluated` counts the rules that ran.
- Findings are committed in their **own** unit of work on a dedicated pool
  (`Database.side_write_sessionmaker`), not in the caller's transaction, so they survive when
  the gate raises and the caller rolls back — a blocked transition always leaves its findings
  (the record the T-0035 override needs), and they likewise outlive a failure at the caller's
  own commit. The dedicated pool means the write never waits on the request pool the caller's
  connection belongs to (that pool can be wholly held by requests sitting inside `run_checks`,
  and reusing it would risk a client-side deadlock that times out and 500s); on PostgreSQL a
  short `lock_timeout` keeps an FK wait on a row the caller still holds from hanging. The rule
  set must already be committed. On SQLite (local tests only) call `run_checks` before writing
  in the caller's transaction, or the second writer waits for the database lock.
- Ties between visible sets of one layer (possible only on SQLite, see uniqueness below)
  break by set id, deterministically.
- `result.blocked` is true when a finding has `action == "block"`; `result.reason` joins those
  findings' messages. A finding's message is the rule's `params.message` (rule data, written
  by the rule author), else `"<rule_key>: check failed"`. `warn` findings never block.

The signature `run_checks(context, rule_set, *, session, subject=None)` and the result shape
are frozen at M1 (T-0131).

## The rule store

| `kind` | Used by | `engine` | CEL context variables |
|---|---|---|---|
| `check` | `run_checks` gates | `cel` | `record`, `fields`, `actor`, `project`, `now`, `params` |
| `digest` | activity feeds (`handover.activity`) | `cel` | `event`, `actor`, `project`, `now`, `params` |
| `notify` | event → notification routing | `cel` | `event`, `actor`, `project`, `now`, `params` |
| `autolink` | text → record links (`handover.autolink`) | `regex` | — |
| `p6_grammar` | P6 activity-name grammar (p6 spec §5.1) | `regex` | named groups ⊆ `asset`, `activity_type`, `anchor_code` (required), `suffix`, `window` |
| `p6_eligibility` | P6 placeholder eligibility (§5.3) | `cel` | `task_code`, `task_name`, `task_type`, `wbs_path`, the grammar groups, `params` |

`layer` is `system`, `tenant` or `project`; `project_id` is NULL on `system`/`tenant`
(CHECK `layer_scope`). `(tenant_id, project_id, layer, key)` is unique; on PostgreSQL the
constraint is `NULLS NOT DISTINCT`, so tenant-level sets are unique too (on SQLite only the
service's duplicate check guards them). A project session creates `project` sets; a tenant-level session
`system`/`tenant` sets. Allowed `action`s: `check` → `block` | `warn`; `p6_eligibility` →
`placeholder` | `no_placeholder`; other kinds any snake_case word. `severity` is `info`,
`warning` or `error`. The context-variable lists are an implementer default awaiting
confirmation (spec-gap TD-139).

### Compile on save (ADR 0004 §6)

`save_rule` compiles before anything is written and raises `RuleCompileError` with the reason:

- the engine must match the kind (`regex` only for `autolink` and `p6_grammar`);
- CEL goes through `app.core.cel.compile_expression`: the same parser, 4096-byte cap and
  static checks as evaluation (safe subset, TD-041), and every top-level variable must be in
  the kind's context schema. A macro variable counts as bound only inside its own macro
  call (`l.all(x, x.ok) && x` is rejected). Field-level types are not checked (no server CEL type checker);
- regex compiles with **RE2** (`google-re2`, imported lazily): linear time, so lookaround and
  backreferences are rejected; patterns are capped at 1024 characters;
- `params` must be valid KERP CEL JSON.

### Versions and audit (ADR 0004 §3)

Each rule has a `version` (1 on add, +1 on every edit or delete) and each set a `version` (+1
on every change to one of its rules). Every change is appended to the set's audit chain
`validation.rule_set:<set id>` (T-0006) with the full rule snapshot (`params` as canonical
JSON text, because the ledger refuses floats). Saving identical values writes nothing.
Deleting is a soft delete that keeps the `rule_key`; a deleted key cannot be saved again
(reset is T-0034). Changes with `source="ai"` are refused until a human-confirmation flow
exists (CLAUDE.md §6).

## Not here

Layers, copy-on-create, reset, locked rules and unlock: **T-0034** (`locked` and
`origin_*` are stored but not enforced yet). Upgrade offers, drift report, "Try it":
**T-0036**. Per-record override of a blocked check (Amendment A): **T-0035**.
