# Module detail — Asset hierarchy & engineering tagging (`asset_management`)

Merged from legacy `asset_management` with the ltree + JSONB overhauls applied. Covers MOD-02.
The breakdown, anchor roles and terminology are **configuration** per
[ADR 0003](../../decisions/0003-configurable-breakdown-and-terminology.md) (accepted with
amendments, 2026-11-10). The supplied client spreadsheets are reference examples the starting
packs reproduce; nothing in code may assume their level names or layouts.

## `oe_asset_level_def` — breakdown template (per project)
Project-scoped config table (`BaseRecordMixin`, RLS). One row per level of the project's
breakdown; a project may use several breakdown templates side by side (e.g. remediation and CUI
under one contract, or work at Asset level with no RSW).

| Column | Purpose |
|---|---|
| `key` | stable machine key (`site`, `asset`, `rsw`, `activity`, `scope`, `job_package`, `line`…); unique `(project_id, key)`; **never re-keyed** once nodes use it |
| `depth`, `allowed_parent_keys` | where the level may sit; allows optional or skipped levels |
| `label_key` | i18n key; the label comes from the terminology pack (below) |
| `roles` | subset of `grouping`, `physical`, `work_anchor`, `inspection_anchor`, `progress_unit`, `schedule_link` |
| `code_pattern` | optional validation/generation pattern; codes are always **text** |
| `code_uniqueness` | `per_parent` \| `per_level` \| `per_project` — scope in which a node `code` must be unique |
| `field_schema_id` | JSONSchema (field registry) for the level's `custom_fields` |
| `display_code_template` | optional, e.g. `{asset} / {activity} / {rsw}`; computes `display_code` |

Role names are frozen at M1 (T-0131); level names are not. Every rule, report and form binds to a
**role** or level key, never a label.

## `oe_asset_node` — one physical/work tree
Example shapes (from the starting packs, not fixed): Site → Asset → RSW → Item (`remediation-rsw`);
Site → Asset → RSW → Activity (`remediation-fep`); Scope → Job Package → Line (`cui-line`).
- **`level_def_id`** → `oe_asset_level_def.id`; the level's `allowed_parent_keys` is enforced on
  create and move. The legacy `type` enumeration is data.
- **`parent_id`** — relational **source of truth**.
- **`tree_path ltree NOT NULL`** with a **GiST** index (`CREATE EXTENSION ltree`); a move
  updates the whole subtree in a single `ltree` statement.
- **`display_path text`** — read-only, breadcrumb UI only.
- `code` (free text, e.g. `101.1`, `SI-001`; uniqueness per the level's `code_uniqueness`),
  computed `display_code`, `discipline_code`, external keys (`wbs_code`, `activity_id`,
  `external_source`, `external_id`, `synced_at`), planned dates, PostGIS `spatial_location`
  (`PointZ`, EPSG:4326), `custom_fields jsonb`.
- `progress_unit` levels carry `budget_qty`, `uom`, `budget_hours`, `earned_qty`.
- Lineage: `status` (`active` / `retired`), `superseded_by` (node ids), `derived_from` (node ids).

### Anchor invariants (CLAUDE.md §6, ADR 0003 Amendment 1)
- **Work anchors to a work-anchor node** — a node whose level has the `work_anchor` role
  (RSW by default; Line in `cui-line`). **At most one work-anchor node on any root-to-leaf path;
  work anchors are never nested** (no work-anchor node has a work-anchor ancestor). Progress,
  time lines, NCR completion blocks and completion gates (`work_anchor.complete`) use it.
  A path may have no work anchor yet (TD-113, below).
- **Inspections anchor to any node whose level has `inspection_anchor`, or to an Item**
  (CUI: line; remediation: rsw and item). An inspection may sit on an `inspection_anchor` node
  with no work under it yet (condition surveys, CUI scans; Amendment 5); its findings (TD-106)
  can raise the work.
- **No BOQ anchor anywhere.**
- Decided (TD-113, user 2026-11-10): a path with no work-anchor node is allowed (inspection
  without work); booking work (progress, time against a work anchor, NCRs, completion) requires
  the work-anchor node on the path. When work is raised on a path without one, the user must pick
  or create the work-anchor node first; this includes a user raising follow-up work from a
  finding (TD-106). The check is `asset_management.service.work_anchor_for(node_id)` (T-0101),
  which raises `NoWorkAnchor` (422) when the path has none. Time may also book to a delay/overhead code with no work anchor (Amendment 8,
  TD-108).

### Restructure with history (Amendment 3)
`move`, `split`, `merge` and `retire` keep lineage instead of rewriting history:
- **move** — same node id, new parent; the subtree moves in one `ltree` statement; the old and
  new path are written to the audit ledger.
- **split** — the source is retired and `superseded_by` the new nodes; each new node is
  `derived_from` the source.
- **merge** — the sources are retired and `superseded_by` the target; the target is
  `derived_from` the sources.
- **retire** — `status = retired`; no new work or inspections may anchor to it.
Inspections, time and NCRs stay on the node they were recorded on and **roll up through the
lineage** (queries over a successor include its predecessors' records). Every operation
re-checks the anchor invariants and is written to the audit ledger. Level keys are never
re-keyed.

## Collections (Amendment 2)
One physical/work tree plus named **collections** linked to nodes many-to-many:
`oe_asset_collection` (`kind`: `job_pack`, `shutdown_window`, `test_pack`, `work_plan`, plus
project-defined kinds; `code`, `name`, optional `window_start`/`window_end`) and
`oe_asset_collection_member` (`collection_id`, `node_id`, unique pair). Work plans
([ADR 0006](../../decisions/0006-work-plans-layered-on-the-programme.md)) are collections. A
node may belong to any number of collections; collections never change the tree.

## `oe_asset_item` — typed inspectable items
`asset_id→node`, `parent_item_id` (self-FK), `item_type_id`, `tag`, `spec_ref`, `discipline`,
**`work_anchor_id`** (nullable; FK → a node whose level has `work_anchor`, on the same
root-to-leaf path as `asset_id`; replaces the old `rsw_id`), `status`, `inspectable`,
`custom_fields jsonb`. *A weld is an Item with `type=weld`; weld fields/rules live in `wps`.*
- **Reserved CML stub fields** (JSONSchema only — no calc engine): `cml_tag`,
  `nominal_thickness_mm`, `minimum_structural_thickness_mm`, `design_corrosion_allowance_mm`,
  `active_corrosion_rate_mm_yr`, `last_measured_thickness_mm`, `last_inspection_date`.

### Scan identifiers and scan-to-locate (Pilot-0, planner spec 2026-11-13; TD-163, Q5a)
An Item may carry scan identifiers in `oe_asset_item_identifier` (`BaseRecordMixin` + project
RLS): `item_id`, `kind` (`barcode` | `qr` | `nfc`), `value`, `is_active`. Values are trimmed; an
NFC value is the tag UID as upper-case hex without separators. An active `(project_id, kind,
value)` is unique; an Item may have several identifiers, and a replaced tag is deactivated, not
deleted (planner defaults: a separate table rather than columns, Items only — nodes get
identifiers later if needed).
- `GET /api/v1/assets/items/lookup?value=&kind=` (`kind` optional) returns the Item id, tag, item
  type, node display path, work anchor and `inspectable`; no match, or a match in another
  project → 404.
- PER-06 and PER-09 may add or deactivate identifiers, including "assign this scanned code to an
  Item" from the scan screen when online (planner default). The ingestion wizard may map
  identifier columns (T-0107 mapping profiles).
- **Scan-to-locate on the client.** The Android build reads barcodes and QR codes with the camera
  through a native plugin and NFC tag UIDs through a Capacitor NFC plugin; the web PWA reads
  barcodes and QR codes where the browser offers barcode detection and otherwise offers manual
  entry; NFC is Android only. Offline, the lookup runs against the identifiers of downloaded Items
  (`KERP-CONSOLIDATION-AND-DIFF.md` §6.4); online, a miss falls back to the lookup endpoint. A hit
  opens the Item with its downloaded inspections. Every path has a manual tag search. Plugins must
  have a permissive licence (no GPL).

## Dynamic attributes (EAV deleted)
- Values live **only** in `custom_fields jsonb` on node + item.
- `oe_asset_field_def` is **purely the JSONSchema registry** (categories/types/field defs).
- Indexed with **GIN `jsonb_path_ops`** + **functional B-tree** expression indexes on hot
  query attributes. The legacy `oe_asset_field_value` EAV table is **deleted**.
- Item types and register types are **versioned** JSONSchemas: a project can add columns at any
  time; existing records keep the revision they were captured on.

## Terminology packs (ADR 0003 §3, Amendment 6)
Level and domain nouns resolve through a terminology layer before the locale bundle:
`t("level.work_anchor")` → project pack → client pack → default. A pack maps keys to values
**per locale** (at least `en` and `de`) with **plural forms** (ICU/i18next plural categories),
e.g. remediation `{"level.rsw": {"en": {"one": "RSW", "other": "RSWs"}}}`. Nav, column headers,
breadcrumbs, PDFs and exports use it, so a CUI user never sees "RSW".

## Client packs (ADR 0003 §4, ADR 0004)
`POST /assets/config-packs/{key}/apply` (idempotent) bundles: breakdown template (level defs +
roles), terminology pack, item types, form/ITP templates, numbering patterns, register types,
import mapping profiles, and rule sets (copied into the project's rule store, ADR 0004 §2).
Starting packs: `remediation-rsw` (Site/Asset/RSW/Item), `remediation-fep` (adds Activity with
budget qty), `cui-line` (Scope/Job Package/Line).
- A project's copy of a template or type keeps a **`derived_from`** link to the pack item it
  came from (TD-107): copies, not inheritance, with a compare view.
- **Pack upgrades are offered, never forced** (Amendment 7): a changed pack shows existing
  projects "changes available" with a per-item diff; the project accepts item by item; nothing
  is rewritten automatically. New projects always get the latest pack.

## Import mapping profiles (ADR 0003 §5)
The ingestion wizard saves a versioned **mapping profile** per project: header synonyms →
target (level key, node field, `custom_fields` path, item type), tree mode (`ParentRow`, path
column, or one column per level), update-by-ID mode and value parsers. Mandatory behaviour:
identifier columns are read as **text** and date-typed cells in them are flagged; `#N/A`-style
errors become nulls with a warning; multi-value cells (`101.1 and 101.3 ; 102`) split into
several anchors; a dry run shows row errors before apply. P6 name grammar and eligibility are
**rules** in the rule store (`p6_grammar`, `p6_eligibility` kinds; ADR 0004 Amendment E), not
mapping-profile fields.

## Endpoints
Level defs CRUD (additive once used); tree CRUD + `POST /assets/nodes/{id}/move`,
`/split`, `/merge`, `/retire`; collections CRUD + membership; items CRUD; schema registry;
item scan identifiers + `GET /assets/items/lookup`; contextual inspections (`GET /assets/nodes/{id}/inspections` aggregating descendants and
lineage predecessors); terminology packs; ingestion (`POST /assets/import` → SAQ job → `/apply`
atomic, with mapping profiles); **P6 import/export** (idempotent on
`(project_id, external_activity_id)`, with the **Unmatched-Row Linking Screen**; P6 activities
link to the level with the `schedule_link` role — see
[`p6-import-and-rsw-matching.md`](p6-import-and-rsw-matching.md)); config packs
(`POST /assets/config-packs/{key}/apply`, upgrade diff + accept).
