# ADR 0004 — Rule sets: shipped defaults on every project, editable per project

- **Status:** proposed
- **Date:** 2026-11-10
- **Resolves (on acceptance):** TD-072
- **Affects:** T-0018, T-0108, T-0124, T-0131 (M1 freeze), E-04-11 (handover)
- **Builds on:** ADR 0003 §4 (client packs carry rule sets)

## Context

T-0018 makes rule sets **data** (CEL evaluated by `run_checks`), and ADR 0003 bundles rule sets
in client packs. Neither says what a new project starts with, or what a project may change.

New requirement (handover module, `docs/spec/modules/handover.md`): projects choose which
events feed the automatic "what happened on my swing" list, and which text patterns auto-link
to records. The user's rule for **any** rules engine in KERP: *every project gets a default
set of rules, and each project can change, delete or add rules.*

Without one mechanism each module would invent its own settings table, defaults would be
copied by hand, and a project could silently switch off a gate the spec makes mandatory
(e.g. the calibration hard gate).

## Decision

### 1. One rule store, several kinds
Shared kernel tables (in `validation`, the T-0018 module), `BaseRecordMixin` + RLS:

- `oe_validation_rule_set(key, kind, layer, version, source_pack)`
- `oe_validation_rule(rule_set_id, rule_key, engine, expression, params JSONB, action, severity,
  enabled, locked, sort_order, origin_rule_key, origin_version)`

Tenant, project and soft-delete columns (`tenant_id`, `project_id`, `deleted_at`) come from
`BaseRecordMixin`; `project_id` is NULL on the system and tenant layers. Table names follow
the owning module (`validation`).

| `kind` | Used by | `engine` |
|---|---|---|
| `check` | `run_checks` verification gates (T-0018) | `cel` |
| `digest` | activity feeds, e.g. `handover.activity` | `cel` |
| `autolink` | text → record links, e.g. `handover.autolink` | `regex` (only kind allowed to use it) |
| `notify` | event → notification routing (later) | `cel` |

`run_checks(context, rule_set)` keeps its signature; it is just one consumer of the store.

### 2. Three layers, copied — not overlaid
1. **System defaults** — versioned seed files shipped with each module
   (`backend/app/modules/<m>/rules/defaults/*.yaml`) and with client packs (ADR 0003 §4).
2. **Tenant defaults** — seeded from the system set when a tenant is created; edited to make a
   company standard by **PER-13 Quality Director** (`check` rules) or **PER-01 Operations
   Management** (all other kinds) — see §7.
3. **Project set** — **copied** from the tenant defaults (or the chosen client pack) when the
   project is created, or when a module is first enabled on the project.

Copying instead of a live overlay keeps a running project stable when defaults change, and
makes "what rules did this project run on date X" a plain query.

### 3. What a project can do
- **Edit** a rule (expression, params, severity, label, order).
- **Disable / enable** a rule.
- **Delete** a rule — soft delete (`deleted_at`) that keeps `rule_key`, so a default upgrade
  does not bring it back.
- **Add** a new rule (`origin_rule_key` NULL).
- **Reset** one rule, or the whole set, to the current default.

Every change is versioned and written to the audit ledger (T-0006). Findings and other
outputs record the `rule_key` + version that produced them.

### 4. Default upgrades are offered, never forced
When a system or tenant default gains a new version, project admins see "Updates available"
with a per-rule diff (new, changed, removed) and accept rule by rule. New projects always get
the latest defaults.

### 5. Locked rules
Defaults can be `locked`. A locked rule cannot be deleted, disabled or loosened on a project;
the project can still **add** stricter rules. Gates the spec makes mandatory ship locked:
calibration hard gate, "open Cat A punch / NCR blocks completion", eligibility checks.
Only **PER-13 Quality Director**, at the tenant layer, can unlock a locked rule, and the
change needs an ADR reference recorded on the rule. Separation of duties: PER-09 Project
Administrator (SoD-blocked from quality holds) can never unlock, disable or loosen a `check`
rule. Whether unlocking also needs a second approver (e.g. via `approval_routes`) is open in
TD-072.

### 6. Safe editing
Expressions are compiled on save against the kind's context schema (CEL compile; regex
compile with a pattern-length limit). Regex rules use **RE2 syntax** and are evaluated with a
linear-time engine (`google-re2` on the server, an RE2-compatible subset checked on save for
the client), so project-written patterns cannot cause catastrophic backtracking. A **Try it** action runs a rule against a sample or
recent real context before it is enabled.

### 7. One settings screen
Project Settings → **Rules**: tabs by kind and module, the same component for the tenant
default layer.

| Layer | `check` rules | Other kinds |
|---|---|---|
| Tenant defaults | PER-13 Quality Director | PER-01 Operations Management |
| Project set | PER-06 QA/QC Lead | PER-09 Project Administrator |
| Unlock a locked rule | PER-13 only, with ADR reference | n/a (only `check` rules ship locked) |

## Consequences

- **T-0018** gains the store, layers, copy-on-create, soft delete, reset and locked flag — or,
  to keep T-0018 at size M, a new S task directly after it. Planner decides on acceptance.
- **T-0108** — client-pack rule sets become the source the project copy is made from.
- **T-0124** — completion and P6 gates ship as locked defaults.
- **T-0131** — the M1 freeze includes the rule-store shape and the `kind` / `engine` values.
- **E-04-11 (handover)** — uses `digest` and `autolink` kinds; no handover-specific rule tables.
- **Cost:** one copy per project per enabled module (small). Upgrade diff UI is extra work.
- **Risk:** projects drift from company standard. Mitigated by the update diff, locked rules
  and a tenant report of project rules that differ from the defaults.
