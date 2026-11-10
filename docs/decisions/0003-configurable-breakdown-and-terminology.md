# ADR 0003 — Configurable work breakdown, anchor roles and client terminology

- **Status:** accepted (user, 2026-11-10; see "Amendments on acceptance")
- **Date:** 2026-11-10
- **Resolves (on acceptance):** TD-071
- **Evidence:** [`docs/research/legacy-spreadsheet-inventory.md`](../research/legacy-spreadsheet-inventory.md)
- **Affects:** T-0101, T-0103, T-0105, T-0106, T-0107, T-0108, T-0111, T-0112, T-0115, T-0116, T-0118, T-0124, T-0131 (M1 freeze), E-04-01, E-04-03, E-04-07
- **Amends:** CLAUDE.md §6 "The anchor model is law", `docs/spec/modules/asset-and-tagging.md` and `docs/spec/modules/quality-and-inspection.md`

## Context

The spec fixes one hierarchy: Facility → Area → Unit → System → Subsystem → Tag/…/**RSW**, with
RSW as the hard-coded leaf work anchor and Items as the inspection anchor. Task names and rule
sets bake that in (`rsw.complete`, `rsw_id` on Items, "RSW code" on time lines).

The operations spreadsheets show at least three real structures (inventory §2):

| | Level 1 | Level 2 | Level 3 | Level 4 | Work anchor | Inspected at |
|---|---|---|---|---|---|---|
| Remediation A | Site | Asset | **RSW** | Item (weld, HDNR, CP) | RSW | RSW + Item |
| Remediation B (front-end planning) | Site | Asset | **RSW** | Activity (B/P/G/W…) | RSW | Activity |
| CUI department | Scope | Job Package | **Line** | (sub-line) | Line | **Line** |

The CUI team has no site, asset or RSW. A hard-coded RSW leaf means either CUI cannot use KERP,
or it fakes "Line = RSW" and every screen, report and export shows the wrong words. Product
success depends on fitting how each client issues job packs, not on one vocabulary.

T-0101 and the M1 freeze (T-0131) have not started, so changing this now costs a spec edit;
after M1 it is a core-table migration.

## Decision

**Guiding principle — examples, not formats.** The supplied spreadsheets are *reference
examples* the platform must be able to reproduce out of the box. They are **not** the shape of
the system. Every structure below (levels, fields, form sections, table columns, free-text
areas, register columns, numbering, import columns) is configuration that a project can extend
or change — add levels, add items, add columns to a repeating table, add text/photo/sign-off
sections, rename anything — without a code change or migration. The starting packs in §4 ship
those example layouts; nothing in code may assume them.

Keep **one** ltree tree table and **one** item table. Replace the hard-coded level names and the
RSW rule with per-project **configuration**, and make every rule refer to a level's **role**,
never its name.

### 1. Breakdown template (per project, from a client pack)

New config table `oe_asset_level_def` (project-scoped, `BaseRecordMixin`, RLS):

| Column | Purpose |
|---|---|
| `key` | stable machine key, e.g. `site`, `asset`, `rsw`, `job_package`, `line` |
| `depth` / `allowed_parent_keys` | where the level sits; allows optional or skipped levels |
| `label_key` | i18n key; the label itself comes from the terminology pack (§3) |
| `roles` | set of: `grouping`, `physical` (a thing you can walk to), **`work_anchor`**, **`inspection_anchor`**, `progress_unit`, `schedule_link` (P6 activity) |
| `code_pattern` | optional validation/generation pattern; codes are always **text** |
| `field_schema_id` | JSONSchema for the level's `custom_fields` (priority, shutdown, UOM…) |

`oe_asset_node` gains `level_def_id` (FK to `oe_asset_level_def.id`; `key` is unique only per project, enforced by a unique `(project_id, key)`). The `type` enumeration in
the spec becomes data. Rules:

- **Exactly one level per project carries `work_anchor`.** Work (progress, time lines, NCRs,
  completion) anchors to nodes of that level. For remediation that is `rsw`; for CUI it is
  `line`. The invariant "no BOQ anchor anywhere" is unchanged.
- **`inspection_anchor` may be on several levels** (CUI: line; remediation: rsw and item).
- `progress_unit` levels carry `budget_qty`, `uom`, `budget_hours`, `earned_qty` (FEP m²/kg/Lm;
  CUI metres scanned).
- The node's `code` is free text (handles shapes like `101.1`, `SI-001`, `L-100-XX001-1AA1-C1 C1EA`; examples synthetic).
  A computed `display_code` template (e.g. `{asset} / {activity} / {rsw}`) replaces ad-hoc
  `Control_Code` columns.

### 2. Rename RSW-specific columns to role-based ones

| Spec today | Becomes |
|---|---|
| `oe_asset_item.rsw_id` | `work_anchor_id` (FK → node whose level has `work_anchor`) |
| rule set `rsw.complete` | `work_anchor.complete` |
| "RSW code" on `field_time._line` (prose only today: `KERP-CONSOLIDATION-AND-DIFF.md` §1) | `work_anchor_id` |
| NCR/punch "anchored to Asset/RSW/Item" | anchored to any node or item; `anchor_role` recorded |

Multi-anchor documents (one NDT request covering several RSWs) use a link table
`oe_inspections_request_anchor(request_id, node_id | item_id)` instead of one FK.

### 3. Terminology packs (labels are data, not code)

All UI strings still go through `t()` (CLAUDE.md §6). Level and domain nouns resolve through a
**terminology layer** before the locale bundle: `t("level.work_anchor")` → project pack →
client pack → default. A pack is a small JSON map, e.g. remediation `{"level.rsw": "RSW",
"doc.fic": "FIC"}`; CUI `{"level.line": "Line", "level.job_package": "Job Pack"}`. Nav, column
headers, breadcrumbs, PDFs and exports all use it, so a CUI user never sees "RSW".

### 4. Client packs extend the existing config packs (T-0108)

A client pack (`POST /assets/config-packs/{key}/apply`, idempotent) bundles:

1. breakdown template (level defs + roles),
2. terminology pack,
3. item types (weld, HDNR, compensation plate, bolt group, CUI test point…),
4. form/ITP templates (FIC bolt tensioning, concrete pour, UTT, coating report, CUI scan sheet),
5. numbering patterns per template (`{prefix}-{project}-W-{seq:4}`, `{project}-NDTR-{seq:3}`,
   with `Rev` suffix rules),
6. register types (instrument, inspected plant/PPE, material class, competency),
7. **import mapping profiles** for that client's spreadsheets (§5),
8. rule sets (completion gates, hold points) written against roles.

Starting packs: `remediation-rsw` (Site/Asset/RSW/Item), `remediation-fep` (adds Activity with
budget qty), `cui-line` (Scope/Job Package/Line). A project can clone and edit its pack; edits
after work exists are **additive only** (add levels/labels/fields; never re-key an existing
level) to keep history valid.

### 5. Import mapping profiles

The ingestion wizard (T-0107) saves a **mapping profile**: header synonyms → target
(level key, node field, `custom_fields` path, item type), tree mode (`ParentRow`, path column,
or one column per level), update-by-ID mode, and value parsers. Mandatory behaviours from the
inventory: identifier columns are read as text and date-typed cells in them are flagged; `#N/A`
style errors become nulls with warnings; multi-value cells (`101.1 and 101.3 ; 102`) split into
several anchors; dry-run shows row errors before apply.

### 6. Forms bind to roles, not names

Template header fields bind to context by role/level key (`context.level('asset').name`,
`context.work_anchor.code`), so one "Bolt tensioning FIC" works under any pack that has the
levels it needs; a template declares the roles it requires and cannot be attached elsewhere.

Templates are fully user-editable on the one-engine field registry: add/remove/reorder
sections, add columns to repeating tables, add free-text areas, photo pages, sketches, extra
hold/review points and extra sign-off blocks. Registers work the same way: a register type is a
JSONSchema, so a project can add columns to its equipment, material or staff register at any
time. Template and register-type changes are **versioned** — existing records keep the revision
they were captured on (records pin `template_revision_id`), new records use the latest.

## Consequences

- **Spec edits** (separate PR after acceptance): `asset-and-tagging.md` (level defs, roles,
  `work_anchor_id`), `quality-and-inspection.md` (request-anchor link, role binding),
  CLAUDE.md §6 wording (as amended by Amendment 1): "work anchors to a **work-anchor node** —
  at most one per root-to-leaf path, never nested (RSW by default); inspections to any
  `inspection_anchor` level or an Item. No BOQ anchor anywhere."
- **Task changes** (same set as the Affects header and TD-071):
  - T-0101 — adds `oe_asset_level_def` (roles, unique `(project_id, key)`), `level_def_id` on
    nodes and the one-work-anchor-per-path invariant, never nested (Amendment 1; replaces "RSW is
    the leaf work anchor").
  - T-0103 — `oe_asset_item.rsw_id` → `work_anchor_id`.
  - T-0105 / T-0106 — "preserve inspection/RSW ties" becomes "preserve inspection/work-anchor
    ties"; P6 activities link to the level with the `schedule_link` role.
  - T-0107 — saved import mapping profiles, text-safe identifier parsing, multi-value split.
  - T-0108 — default packs become client packs (levels, terminology, item types, templates,
    numbering, register types, mapping profiles, rule sets).
  - T-0111 — "anchors at any level (Asset / sub-Asset / RSW / Item)" becomes "any
    `inspection_anchor` level or Item"; header fields bind by role.
  - T-0112 / T-0118 — register types are versioned JSONSchemas so a project can add columns.
  - T-0115 — adds `oe_inspections_request_anchor` (one request ↔ many nodes/items).
  - T-0116 — NCRs anchor to any node or item and record `anchor_role`; completion block is on
    the work-anchor level.
  - T-0124 — `rsw.complete` → `work_anchor.complete`.
  - T-0131 — freezes the role names and `oe_asset_level_def` shape, not level names.
  - E-04-01 / E-04-03 / E-04-07 — "RSW-linked/anchored" becomes "work-anchor-linked/anchored"
    when these epics are broken down.
  - Probably one new S task: terminology layer in the frontend `t()` resolver.
- **Cost:** one extra join for labels/roles (cacheable per project); pack versioning must be
  designed so pack updates do not rewrite existing projects.
- **Risk:** too much freedom produces unreportable data. Mitigated by fixed **roles** — every
  cross-project report (progress, inspection status, open NCRs) groups by role, so dashboards
  work across clients whatever the levels are called.
- **Not decided here:** whether a project may have two parallel trees (physical location vs.
  work packages) linked many-to-many. Every file reviewed fits one tree; raise if a client
  needs both.

## Amendments on acceptance (user, 2026-11-10, TD-071)

Accepted with these changes. Where they conflict with anything above (Decision or Consequences), they win. The spec
and task edits are tracked in TD-112.

1. **One work anchor per branch, not per project.** Replaces "exactly one level per project
   carries `work_anchor`": every root-to-leaf path has **at most one** work-anchor node, and work
   anchors are never nested. A path may have none yet (inspection without work, Amendment 5);
   booking work needs one on the path (TD-113, user 2026-11-10). A project may use several breakdown templates side by side
   (e.g. remediation and CUI under one contract, or work at Asset level with no RSW).
2. **One tree plus collections.** Keep one physical/work tree. Job packs, shutdown windows,
   test packs and work plans (ADR 0006) are named **collections** linked to nodes
   many-to-many. This settles the "two parallel trees" question left open above.
3. **Restructure with history.** Replaces "edits after work exists are additive only":
   move / split / merge / retire operations keep node lineage (`superseded_by`,
   `derived_from`). Inspections, time and NCRs stay on the original node and roll up through
   the lineage. Level keys are still never re-keyed.
4. **Configurable code uniqueness.** Each level def sets whether codes are unique per parent,
   per level or per project.
5. **Inspection without work.** Inspections (condition surveys, CUI scans) may sit on an
   `inspection_anchor` node with no work under it yet; findings (TD-106) can raise the work.
6. **Terminology per language, with plurals.** Terminology packs carry values per locale
   (at least en and de) and plural forms.
7. **Pack upgrades are offered, never forced.** A changed client pack shows existing projects
   a "changes available" prompt with a diff; nothing is rewritten automatically (as ADR 0004).
8. **Time without a work anchor.** Time lines may book to a delay or overhead code (standby,
   induction) with no work anchor (see TD-108).
