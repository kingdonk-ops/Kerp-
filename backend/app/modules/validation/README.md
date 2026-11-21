# validation — verification engine and the single rule store (T-0018, T-0034)

Verification is configuration (CLAUDE.md §6): every verifiable transition calls
`run_checks(context, rule_set)`, and the rules it runs are **data** in one rule store shared by
every rules engine in KERP (ADR 0004 §1, Amendment E). Adding a rule needs no code deploy.
Built fresh; Inspection-ERP's `Finding(key, severity, message)` shape was a reference only
(ADR 0001).

| File | What |
|---|---|
| `models.py` | `oe_validation_rule_set`, `oe_validation_rule`, `oe_validation_finding` (migration `0005`; `checked_values_hash`: `0022`), `oe_validation_unlock_request` (migration `0014`; one pending per rule: `0020`), `oe_validation_override` (migration `0022`); all `BaseRecordMixin` + RLS |
| `compiler.py` | compile-on-save: kind/engine rules, per-kind CEL context schema, RE2 regex compile |
| `defaults.py` | system default seed files `app/modules/<m>/rules/defaults/*.yaml`: `load_defaults()` |
| `service.py` | rule store (`create_rule_set`, `save_rule`, `set_rule_enabled`, `delete_rule`, `reset_rule`, `reset_rule_set`), layers (`sync_system_layer`, `sync_system_defaults`, `seed_tenant`, `seed_project`, `copy_module_defaults`), unlock (`request_unlock`, `approve_unlock`, `reject_unlock`), `resolve_rule_set`, `run_checks` |
| `overrides.py` | per-record override of a blocked check (`request_override`, `approve_override`, `reject_override`, `list_overrides`, `get_override`; binding hash and voiding used by `run_checks`) — T-0035 |
| `upgrades.py` | default-upgrade offers (`pending_updates`, `accept_update`), the tenant drift report (`drift_report`), "Try it" (`try_rule`) — T-0036 |
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
- The set is resolved by key among the sets of the session's **own** scope (its bound
  project's, and the tenant-level `tenant`/`system` sets), most specific layer first: `project`,
  then `tenant`, then `system`. Another project's set is never a candidate, even when the
  session can read it (a portfolio reader, or a `system_admin` under ADR 0016; T-0274). An
  unknown set raises `RuleSetNotFoundError`, so a misconfigured gate never passes.
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
- Every finding carries the `finding_id` it was logged under (an override request names it)
  and, in the log, the `checked_values_hash` of the values its rule checked (below).
- `result.blocked` is true when a finding has `action == "block"`; `result.reason` joins those
  findings' messages. A finding's message is the rule's `params.message` (rule data, written
  by the rule author), else `"<rule_key>: check failed"`. `warn` findings never block.

- With a record subject (`"<record_type>:<uuid>"`), a failing blocking rule (outcome `fail`;
  an `error` is never overridden) the record holds an **approved override** for passes while
  it is still bound (below): no finding, listed in `result.overrides` (`AppliedOverride`:
  `override_id`, `rule_key`, `rule_version`).
- **Caller contract (T-0035):** call `run_checks` **before** appending to the record's own
  audit chain (`<record_type>:<record_id>`) in the same transaction. Voiding a stale override
  appends to that chain on the side pool; if the caller already holds the chain's lock, the
  void waits out `lock_timeout`, is rolled back and logged, and is retried at the next
  evaluation. The findings (committed first) and the block stand either way; the stale
  override is never applied by the evaluation that found it stale (TD-316).

The signature `run_checks(context, rule_set, *, session, subject=None)` and the result shape
are frozen at M1 (T-0131).

## Per-record override of a blocked check (ADR 0004 Amendment A, T-0035)

One record may pass one blocking `check` rule with an approved, reasoned override. This is
separate from unlocking a rule (Amendment B, below). The table `oe_validation_override` has
exactly the columns of `docs/spec/modules/quality-and-inspection.md` §Per-record override
(the single source of the shape, frozen at M1, T-0131); `decision` is `pending` → `approved` |
`rejected` | `void`.

1. **Request** (`request_override`, `POST /projects/{p}/overrides` with `finding_id`, `reason`,
   optional `approver_user_id`): any member with `C` (create). Accepted only for a
   **blocking** finding (`action = block`) of a run whose subject names the record, from the
   record's latest run of that set that logged findings, while the record has no pending or
   approved override of the rule (409; a partial unique index backs it). An empty or
   whitespace reason is 422 (and a CHECK in the database). The override copies the finding's
   rule (`rule_set_key`, `rule_key`, `rule_version`) and `checked_values_hash`.
2. **Routing** (A.4): the approver persona is PER-06 QA/QC Lead; a requester holding PER-06 is
   routed to PER-02 Project Manager; a requester holding PER-02 to PER-06. Every other holder of
   the persona on the project may decide and is notified; `approver_user_id` is set when the
   request was routed to one user (named in the request, or the only holder). No holder but the
   requester → 409. **The requester never decides their own request** (403, and CHECKs
   `never_self_decided` / `never_self_routed`); a caller without the routed persona, or not
   the named approver, is 403.
3. **Notifications** (A.3, through `notifications.notify`): the approver(s) get
   `validation.override_requested` (priority high), the requester gets
   `validation.override_approved` / `validation.override_rejected`; subject
   `validation.override` + the override id, label `validation`, params `record_type`,
   `rule_set`, `rule_key`.
4. **Audit** (A.5): request, decision and void are appended to the record's activity history,
   the ledger chain `audit_chain_key` = `<record_type>:<record_id>` in the project scope
   (`validation_override_requested` / `_approved` / `_rejected` / `_voided`, with reason,
   approver persona and user, decider, comment and UTC timestamps).
5. **Binding** (A.6): `checked_values_hash` is the SHA-256 of the canonical JSON (sorted keys,
   no whitespace) of the values the rule checked: the context paths in the rule's
   `params.override_binding` (e.g. `["fields.equipment_id", "fields.spec_revision",
   "fields.inspection_date"]`; 1–32 paths under `record`, `fields` or `project` (never `actor`),
   checked on save), or the whole `record` and `fields` when the rule names none. On every
   `run_checks` of the record, a pending or approved override whose rule version or hash
   differs from this evaluation is set to `void` (`decision_comment` `rule_changed` /
   `checked_values_changed`, `decided_by` NULL) in a side unit of work after the findings, so
   the block returns; void is final, a new request answers the new finding. Decisions are
   guarded in the database (`UPDATE ... WHERE decision = 'pending'`): a decision on a row
   decided or voided meanwhile is 409, never an overwrite. Only `fail` findings can be
   requested (an `error` is 409).

`GET /projects/{p}/overrides?decision=&record_type=&record_id=` and `GET .../{id}`: every member.

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
   is never replaced: a later change to a default does not reach a running project; it is
   **offered** (below).
5. **On deploy** `sync_system_defaults` brings every tenant's system layer to the shipped
   seed versions and copies a set shipped for the **first** time into the tenant layer (a
   new key holds no tenant choice to keep), so new projects get it. A *changed* system set
   is only offered to the tenant layer.

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
the request cannot be unlocked by it. A rule has at most one pending request: a partial unique
index (`rule_id` where `status = 'pending'`, migration `0020`, T-0216) backs the service's
check, so a concurrent duplicate is the same `RuleStoreError` (409), never a second row. Unlocking the tenant default does not touch project
copies; a project picks the unlocked default up by resetting the rule. Resetting never
loosens beyond the default, so it is allowed on locked rules, with one exception on the
tenant layer: a reset (one rule or the whole set) that would **delete** a live locked rule
the tenant added itself, or **unlock** a rule the tenant locked over an unlocked default, is
refused with `RuleLockedError` (409) naming the rules, because it would skip the two-person
unlock. The project layer has no such case (it cannot add locked rules, and its locked
copies only lose their lock after a tenant unlock).

## Default upgrades are offered, never forced (ADR 0004 §4, T-0036)

`pending_updates(session, set_id)` diffs a set against the **current** default of its parent
layer (project → tenant, tenant → system), rule by rule; it writes nothing. Each project rule
(live or soft-deleted) is matched to the default with the same `rule_key`;
`origin_rule_key` only tells a copied rule (set) from one the set added itself (NULL), and
`origin_version` (the default's version when it was copied) tells whether the default has
moved on since the copy.

| `change` | When | Accept |
|---|---|---|
| `new` | the default has a rule the set lacks | adds a copy |
| `changed` | the default's version is newer than `origin_version` **and** its values differ from the set's rule (`fields` names them: the project's own edits show up here too) | replaces the rule with a copy of the default |
| `removed` | the set has a copied rule whose default was deleted | soft-deletes it |
| `removed_by_project` / `removed_by_tenant` | the set deleted its copy and the default's version is newer than the copy's `origin_version` (changed since the **copy**, not since the delete — a default changed before the delete is listed too); once listed it stays on offer until the set is reset | refused (409): a deleted rule is never re-added; **reset** brings it back on purpose |
| `key_in_use` | the set added its own rule under the key of a new default | refused (409) |

`accept_update(session, set_id, rule_key)` applies that one rule only: versioned (rule and
set), ledgered as `rule_update_accepted` with `change`, `from_layer`, `default_version` and
the rule snapshot. Anything not on offer is `RuleNotFoundError` (404). On the tenant layer
accepting may not delete or unlock a locked rule (two-person unlock, `RuleLockedError`, 409),
as for reset. A rule that only lags behind (older `origin_version`, same values) is not
offered; a project that never edited its copy sees exactly the default's changes.

New projects always get the latest defaults: they copy the tenant layer as it is at creation
(including updates the tenant accepted from the system layer).

## Tenant drift report (ADR 0004 Consequences, T-0036)

`drift_report(session)` (tenant-level session) lists, per project, set and rule, how project
rules differ from the tenant defaults: `added` (a live rule the project added, or every rule of
a set with no tenant default), `modified` (a copied rule whose values differ, `fields`),
`deleted` (a default the project deleted), `missing` (a default the copy lacks — an update
not accepted) and `default_removed` (a copied rule whose default the tenant removed). It
covers the projects the session can read (RLS: the caller's portfolio projects,
`allowed_project_ids`), never others.

## "Try it" (ADR 0004 §6, T-0036)

`try_rule(session, set_id, rule_key, context, draft=None)` evaluates the stored rule (enabled
or not) or a `draft` `RuleSpec` (compiled first, `RuleCompileError`) against a sample context
and returns a `TryResult`; it writes **nothing** (no finding, version or ledger entry).

- CEL kinds: the context is KERP CEL JSON (`params` is reserved and bound to the rule's own
  params). `outcome` is `pass` (`true`), `fail` (any other value) or `error`, with the `value`
  or the CEL `error`. A `check` rule that does not pass also returns the `finding`
  `run_checks` would log (same message rules).
  The sample is at most `MAX_SAMPLE_DEPTH` (32) levels of nested objects/lists deep (the
  context itself is level 1) and `MAX_SAMPLE_CONTEXT_BYTES` (64 KiB) as compact JSON, as
  job payloads; beyond either it is refused (422).
- Regex kinds: the context is `{"text": "..."}` (at most 20 000 characters). `autolink`
  returns every match (up to 100) with its named groups; `p6_grammar` a full match.
- A malformed sample is `SampleContextError` (422).

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
| `GET .../rule-sets/{set}/updates` | the upgrade offer (`RuleSetUpdatesRead`), tenant and project sets |
| `POST .../rule-sets/{set}/updates/{key}/accept` | accept one rule of it |
| `POST .../rule-sets/{set}/rules/{key}/try` | "Try it" (`TryRuleInput`: `context`, optional draft `rule`) |
| `GET /drift-report` | the tenant drift report (`DriftEntryRead`) |

| Layer | `check` sets | other kinds |
|---|---|---|
| Tenant defaults (read: PER-13 or PER-01) | PER-13 | PER-01 |
| Project set (read: any member) | PER-06 | PER-09 |
| Unlock | PER-13 requests; another PER-13 or PER-01 approves or rejects (the requester may withdraw) | |

An upgrade offer is read by whoever reads the set and accepted by whoever edits it; "Try it"
needs the editor of the set; the drift report needs PER-13 or PER-01. So PER-09 can never
change, disable, delete, reset or accept an update to a `check` rule (SoD). Everyone else is
403; a non-member of the project, or a set outside the path's layer/project, is 404. Locked
→ 409, compile errors → 422. Interim until T-0014's tenant roles: a "tenant" persona is one
the caller holds on any project of the tenant (as for project creation, TD-184). The
request binds no project yet (TD-122): project routes bind the path's project.

## Not here

"Try it" on a recent real context (only supplied samples so far) and offering whole rule sets
a running project lacks: see the TODOs raised by T-0036. Storing the project's chosen client pack (so a module enabled
later copies that pack's sets): **T-0108**. The Rules settings screen (frontend): see the
TODO raised by T-0034.
