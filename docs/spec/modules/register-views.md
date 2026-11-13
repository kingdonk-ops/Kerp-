# Module detail — Register views: any column, any filter, saved views

Modules `saved_views` (T-0020) and the frontend `RegisterPage` shell (T-0025), applied first to
the asset / work-anchor register (T-0128) and then to every register (scaffolds, scope changes,
plan lines, inspections, NCRs, registers of equipment/credentials…). Epic **E-01-01** holds the
work beyond T-0020/T-0025.

Requirement (user): the asset / RSW information the planning spreadsheet holds is **not hard
coded** — it comes from the breakdown levels, item types and their field definitions
(ADR 0003) — and the register lets people **search and filter by any column, add and remove
columns, and create, edit and save those filters/views**.

Reference example: the front-end planning workbook — per Site / Asset / RSW / Activity row:
priority, control code, defect description, scope, drawing ref, shutdown flag, budget qty, unit,
budget hrs, WFE/encapsulation hrs, scaffold hrs, hazardous material, priority class, scoped,
scope validation, qty validation, proposed new scope/qty, client verified, RSW shutdown,
scaffold, scaffold shut, grating/guards/props, grouping/workpack, sequence priority, offsite
fab, outsource, contractor comments, client response, RSW PDF link; plus a summary sheet of
budget hours by asset split Online / Shutdown, with and without scaffolding. Every one of those
columns must be reproducible as configuration (field defs on the RSW / Activity levels) and the
summary as a grouped saved view. Examples synthetic.

Precedence: module detail (3).

---

## 1. Column catalogue

Each module that exposes a register declares a **register descriptor** in its manifest
(`register_key`, base entity, default columns). The catalogue of columns for a register is
built at runtime from:

| Source | Examples | Notes |
|---|---|---|
| core columns | code, name, status, created/updated | fixed per entity |
| level / item-type fields | `custom_fields` paths from `oe_asset_field_def` JSONSchemas (T-0102), per level or item type | appear automatically when a field def is added; label from terminology pack |
| ancestor columns | Site, Asset, any level's code/name/field | via ltree path; e.g. RSW rows show their Asset and Site |
| child rollups | sum of Budget Hrs of activities, count of items, max status | computed by SAQ rollup tables, not live joins on large trees |
| cross-module lookups | open NCR count, latest inspection result, scaffold tag status, P6 start/finish, plan membership | provided by the owning module's `service.py` as a **column provider** (no cross-module table access) |
| computed | `display_code` template, age in days, "% complete" | CEL expression over the row (one-engine CEL, T-0017) |

Each column: `id`, `label_key`, `type` (text, number, date, datetime, bool, enum, multi-enum,
user, node-ref, money-mirror, file-link), `filter_ops`, `sortable`, `groupable`,
`aggregations` (sum, avg, min, max, count, count-distinct), `permission` (column hidden when the
viewer lacks read rights — RLS and field-level), `source` (above).

## 2. Filters

- Filter = AND/OR tree of conditions `{column, op, value}`; ops depend on type: `eq`, `neq`,
  `in`, `not_in`, `contains`, `starts_with`, `is_empty`, `between`, `gt/lt`, relative dates
  (`within_next 7d`, `overdue`), `me`, `my_teams`, `subtree_of(node)`.
- Quick search box = full-text over the text columns in the view.
- Server compiles the tree to SQLAlchemy against a **whitelist** from the column catalogue
  (never raw SQL; JSONB paths use the GIN-indexed `custom_fields`). Unknown column/op → 422.
- Same AST evaluated client-side over the Dexie cache for offline Tier-1 registers.
- Performance target: 50k rows, filtered + sorted page < 1 s p95 (keyset pagination).

## 3. Saved views

`oe_saved_view` (`BaseRecordMixin`, project RLS):
`register_key`, `name`, `owner_user_id`, `visibility` (`private`, `team`, `project`,
`portal` — shareable to client portal layouts), `is_default` (per user, and one project default
set by PER-09), `columns` (ordered: id, width, pinned, visible), `filter` (AST), `sort`,
`group_by` (+ aggregates and totals row), `conditional_format` (rules: CEL condition → style,
e.g. `Scoped == "No"` → amber), `density`, `schema_version`.

- **Team visibility (user, 2026-11-13; TD-230, TD-244).** A `team` view names **one or more
  teams** (a view-team link, not a column). Every named team must be linked to the view's project
  (diff §4.1 Teams). The view is visible only in that project, to its owner, the project's
  PER-09 and the members of any named team. A team that is deleted or unlinked from the project
  stops seeing the view. When no named team is left, the view is visible to its owner (and
  PER-09) only. The `my_teams` filter op means the caller's teams linked to the bound project.
- Create / edit / save / save-as / rename / delete; edit of shared views by owner or PER-09.
- **Add / remove / reorder columns** from the catalogue; "Add new field…" shortcut opens the
  field-def editor for the level/item type (PER-09 only; additive, versioned per ADR 0003).
- A view that references a column that no longer exists keeps working: the column shows as
  "missing" and is skipped, never an error.
- URL carries the view id + unsaved diff so a view can be shared as a link.
- Views are per register and per project; a client pack can ship default views (e.g.
  "FEP — RSW only", "FEP — summary by asset, Online/Shutdown").

## 4. Actions on a view

- Export current view (CSV / XLSX) — labels from the terminology pack; large exports as SAQ jobs.
- Bulk edit selected rows (fields the user may edit; audited), bulk link to a plan, bulk raise
  scope change / inspection request.
- Select rows → "Create work plan lines" (planner) or "Add to job pack".
- Import back via the mapping profile (ADR 0003 §5) using the same column ids.

## 5. Acceptance (feeds T-0020, T-0025, T-0128 and E-01-01)

1. Adding a field def to the RSW level makes it available as a column and filter without a
   deploy (test).
2. Filter on a `custom_fields` number with `between` + an ancestor column (Site) returns the
   right rows and uses the index (EXPLAIN check in test).
3. Saved view round trip (columns, widths, filter, sort, group-by + sum) reproduces the state.
4. Shared view visible to the team; private view not; portal view visible only through the
   portal layout.
5. Removed column → view still loads, column flagged missing.
6. Grouped view by Asset with sum of Budget Hrs split by the `Shutdown` field reproduces the
   FEP summary from fixture data (synthetic).
