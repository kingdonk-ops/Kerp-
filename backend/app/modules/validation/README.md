# validation — verification engine and the single rule store (T-0018, T-0034)

Verification is configuration (CLAUDE.md §6): every verifiable transition calls
`run_checks(context, rule_set)`, and the rules it runs are **data** in one rule store shared by
every rules engine in KERP (ADR 0004 §1, Amendment E). Adding a rule needs no code deploy.
Built fresh; Inspection-ERP's `Finding(key, severity, message)` shape was a reference only
(ADR 0001).

| File | What |
|---|---|
| `models.py` | `oe_validation_rule_set`, `oe_validation_rule`, `oe_validation_finding` (migration `0005`), `oe_validation_unlock_request` (migration `0013`); all `BaseRecordMixin` + RLS |
| `compiler.py` | compile-on-save: kind/engine rules, per-kind CEL context schema, RE2 regex compile |
| `defaults.py` | system default seed files `app/modules/<m>/rules/defaults/*.yaml`: `load_defaults()` |
| `service.py` | rule store (`create_rule_set`, `save_rule`, `set_rule_enabled`, `delete_rule`, `reset_rule`, `reset_rule_set`), layers (`sync_system_layer`, `sync_system_defaults`, `seed_tenant`, `seed_project`, `copy_module_defaults`), unlock (`request_unlock`, `approve_unlock`, `reject_unlock`), `resolve_rule_set`, `run_checks` |
| `schemas.py` | `RuleSpec` (input), `CheckResult` / `CheckFinding` (output), the Rules API shapes |
| `router.py` | Rules settings API (ADR 0004 §7), see below |

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

Each rule has a `version` (1 on add, +1 on every edit, enable/disable, delete, reset or
unlock) and each set a `version` (+1 on every change to one of its rules). Every change is
appended to the set's audit chain `validation.rule_set:<set id>` (T-0006) with the full rule
snapshot (`params` as canonical JSON text, because the ledger refuses floats). Saving
identical values writes nothing. Deleting is a soft delete that keeps the `rule_key`; a
deleted key cannot be saved again, only **reset** to its default. Changes with
`source="ai"` are refused until a human-confirmation flow exists (CLAUDE.md §6).

A rule has an optional `label` (shown in the Rules screen). `origin_rule_key` +
`origin_version` name the default a rule was copied from; they are set by copies and resets
only (an added rule has `origin_rule_key` NULL; an edit keeps the origin).

## Layers: system → tenant → project, copied (ADR 0004 §2, T-0034)

1. **System defaults** are seed files shipped with each module,
   `app/modules/<module>/rules/defaults/*.yaml`, one rule set per file (format in
   `defaults.py`; `key`, `kind`, `version`, optional `source_pack`, `rules`). Every rule is
   compiled when the files load; a broken file or a key used by two modules is a
   `DefaultsError` naming the file. Bump `version` on every change.
2. The `system` layer is stored as **per-tenant copies** (`tenant_id` = the tenant,
   `project_id` NULL, `layer = system`, `module`, `seed_version`). There are no tenant-less
   rows, so the T-0007 tenant RLS policies apply unchanged: tenant B never reads tenant A's
   system rows. `sync_system_layer(session)` writes the seeds into the session's tenant;
   reloading the same `version` writes nothing, a newer version rewrites the set (changed,
   added, removed and restored rules, one `system_defaults_upgraded` ledger entry), an older
   one is ignored. `sync_system_defaults(database, tenant_ids)` does it for every tenant, one
   unit of work each.
3. **Tenant creation** calls `seed_tenant(session)`: the system layer, then a copy of every
   system set into the `tenant` layer (idempotent).
4. **Project creation** copies the tenant layer into the new project's `project` layer
   (`seed_project`, called through the projects module's created listener in the creating
   transaction): the sets of modules enabled by default and sets owned by no module; with
   a chosen client pack (`source_pack`), that pack's sets too, never another pack's.
   **Enabling a module** on a project for the first time copies that module's tenant sets
   (`copy_module_defaults`, through the module loader's enable listener). An existing copy
   is never replaced: a later change to a default does not reach a running project (the
   upgrade offer is T-0036).

A copied rule keeps `locked`, `label`, `enabled` and everything else of its source and names
it in `origin_rule_key`/`origin_version`.

## What a project (or the tenant layer) can do (ADR 0004 §3)

Edit (expression, params, severity, label, sort order, action), disable/enable, soft-delete,
add (`origin_rule_key` NULL), and **reset** one rule or the whole set to the **current**
default of the parent layer (project → tenant, tenant → system). Reset brings a deleted
default back and adds a default the set lacks; resetting the whole set also soft-deletes
the rules the layer added. A rule the layer added itself has no default to reset to.

## Locked rules and the two-person unlock (ADR 0004 §5, Amendment B, TD-116)

A `locked` rule cannot be changed, disabled or deleted on the project layer
(`RuleLockedError`, API 409); the project can still **add** rules to the same set, but not
locked ones (only defaults are locked). On the tenant layer a locked rule is changed only
after an unlock: `request_unlock` (PER-13, with an ADR reference, bound to the rule
version) and `approve_unlock` by a **second, different** user (PER-13 or PER-01 until
`approval_routes`, E-06-07). The requester can never approve their own request
(`UnlockPermissionError`, and a CHECK constraint on `oe_validation_unlock_request`). The
approval records the ADR reference on the rule (`unlock_adr_reference`). Request, approval,
rejection or cancellation and the unlock itself are each a ledger entry. A rule changed after
the request cannot be unlocked by it. Unlocking the tenant default does not touch project
copies; a project picks the unlocked default up by resetting the rule. Resetting never
loosens beyond the default, so it is allowed on locked rules.

## Rules settings API (`/api/v1/validation`, ADR 0004 §7)

| Route | What |
|---|---|
| `GET /rule-sets` | tenant default sets with their rules (deleted rules included) |
| `PUT /rule-sets/{set}/rules/{key}` | add or edit (`RuleInput`) |
| `POST /rule-sets/{set}/rules/{key}/disable`, `/enable` | disable / enable |
| `DELETE /rule-sets/{set}/rules/{key}` | soft delete |
| `POST /rule-sets/{set}/rules/{key}/reset`, `POST /rule-sets/{set}/reset` | reset one / all |
| `POST /rule-sets/{set}/rules/{key}/unlock-requests` | request an unlock (`adr_reference`, `reason`) |
| `GET /unlock-requests?status=`, `POST /unlock-requests/{id}/approve`, `/reject` | decide |
| `GET /projects/{p}/rule-sets` and `/projects/{p}/rule-sets/{set}/...` | the same rule operations on the project copy |

| Layer | `check` sets | other kinds |
|---|---|---|
| Tenant defaults (read: PER-13 or PER-01) | PER-13 | PER-01 |
| Project set (read: any member) | PER-06 | PER-09 |
| Unlock | PER-13 requests; another PER-13 or PER-01 approves or rejects (the requester may withdraw) | |

So PER-09 can never change, disable, delete or reset a `check` rule (SoD). Everyone else is
403; a non-member of the project, or a set outside the path's layer/project, is 404. Locked
→ 409, compile errors → 422. Interim until T-0014's tenant roles: a "tenant" persona is one
the caller holds on any project of the tenant (as for project creation, TD-184). The
request binds no project yet (TD-122): project routes bind the path's project.

## Not here

Upgrade offers, drift report, "Try it": **T-0036**. Per-record override of a blocked check
(Amendment A): **T-0035**. Storing the project's chosen client pack (so a module enabled
later copies that pack's sets): **T-0108**. The Rules settings screen (frontend): see the
TODO raised by T-0034.
