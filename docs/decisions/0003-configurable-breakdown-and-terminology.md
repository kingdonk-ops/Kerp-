# ADR 0003 — Configurable work breakdown, anchor roles and client terminology

- **Status:** proposed
- **Date:** 2026-11-10
- **Resolves (on acceptance):** TD-071
- **Evidence:** [`docs/research/legacy-spreadsheet-inventory.md`](../research/legacy-spreadsheet-inventory.md)
- **Affects:** T-0101, T-0103, T-0105, T-0106, T-0107, T-0108, T-0111, T-0116, T-0124, T-0131 (M1 freeze), E-04-01, E-04-03, E-04-07
- **Amends:** CLAUDE.md §6 "The anchor model is law" and `docs/spec/modules/asset-and-tagging.md`

## Context

The spec fixes one hierarchy: Facility → Area → Unit → System → Subsystem → Tag/…/**RSW**, with
RSW as the hard-coded leaf work anchor and Items as the inspection anchor. Task names and rule
sets bake that in (`rsw.complete`, `rsw_id` on Items, "RSW code" on time lines).

The operations spreadsheets show at least three real structures (inventory §2):

| | Level 1 | Level 2 | Level 3 | Level 4 | Work anchor | Inspected at |
|---|---|---|---|---|---|---|
| Remediation (Yandi) | Site | Asset | **RSW** | Item (weld, HDNR, CP) | RSW | RSW + Item |
| Remediation (FEP) | Site | Asset | **RSW** | Activity (B/P/G/W…) | RSW | Activity |
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

`oe_asset_node` gains `level_key` (FK to the project's level def). The `type` enumeration in
the spec becomes data. Rules:

- **Exactly one level per project carries `work_anchor`.** Work (progress, time lines, NCRs,
  completion) anchors to nodes of that level. For remediation that is `rsw`; for CUI it is
  `line`. The invariant "no BOQ anchor anywhere" is unchanged.
- **`inspection_anchor` may be on several levels** (CUI: line; remediation: rsw and item).
- `progress_unit` levels carry `budget_qty`, `uom`, `budget_hours`, `earned_qty` (FEP m²/kg/Lm;
  CUI metres scanned).
- The node's `code` is free text (handles `923.1`, `SI-004`, `L-420-PR013-1LA3-C1 C4EA`).
  A computed `display_code` template (e.g. `{asset} / {activity} / {rsw}`) replaces ad-hoc
  `Control_Code` columns.

### 2. Rename RSW-specific columns to role-based ones

| Spec today | Becomes |
|---|---|
| `oe_asset_item.rsw_id` | `work_anchor_id` (FK → node whose level has `work_anchor`) |
| rule set `rsw.complete` | `work_anchor.complete` |
| `field_time._line.rsw_code` | `work_anchor_id` |
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
style errors become nulls with warnings; multi-value cells (`209.1 and 209.3 ; 906`) split into
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
  CLAUDE.md §6 wording: "work anchors to the project's **work-anchor level** (RSW by default),
  inspections to any `inspection_anchor` level; no BOQ anchor anywhere."
- **Task changes:** T-0101 adds `oe_asset_level_def` and the one-work-anchor invariant;
  T-0103 renames `rsw_id`; T-0107 adds mapping profiles + text-safe identifier parsing; T-0108
  becomes client packs; T-0124 renames `rsw.complete`; T-0131 freezes the role names, not level
  names. Probably one new S task: terminology layer in the frontend `t()` resolver.
- **Cost:** one extra join for labels/roles (cacheable per project); pack versioning must be
  designed so pack updates do not rewrite existing projects.
- **Risk:** too much freedom produces unreportable data. Mitigated by fixed **roles** — every
  cross-project report (progress, inspection status, open NCRs) groups by role, so dashboards
  work across clients whatever the levels are called.
- **Not decided here:** whether a project may have two parallel trees (physical location vs.
  work packages) linked many-to-many. Every file reviewed fits one tree; raise if a client
  needs both.
