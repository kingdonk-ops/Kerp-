# Module detail — Asset hierarchy & engineering tagging (`asset_management`)

Merged from legacy `asset_management` with the ltree + JSONB overhauls applied. Covers MOD-02.

## `oe_asset_node` — functional hierarchy
Facility → Area → Unit → System → Subsystem → Tag / Loop / Spool / Equipment / **RSW**.
- **`parent_id`** — relational **source of truth**.
- **`tree_path ltree NOT NULL`** with a **GiST** index (`CREATE EXTENSION ltree`); a move
  updates the whole subtree in a single `ltree` statement.
- **`display_path text`** — read-only, breadcrumb UI only.
- `level`, `discipline_code`, external keys (`wbs_code`, `activity_id`, `external_source`,
  `external_id`, `synced_at`), planned dates, `budgeted_units`, PostGIS `spatial_location`
  (`PointZ`, EPSG:4326), `custom_fields jsonb`.
- **RSW** is the leaf **work anchor** (no BOQ anchor anywhere).

## `oe_asset_item` — typed inspectable items
`asset_id→node`, `parent_item_id` (self-FK), `item_type_id`, `tag`, `spec_ref`, `discipline`,
`rsw_id` (nullable), `status`, `inspectable`, `custom_fields jsonb`. *A weld is an Item with
`type=weld`; weld fields/rules live in `wps`.*
- **Reserved CML stub fields** (JSONSchema only — no calc engine): `cml_tag`,
  `nominal_thickness_mm`, `minimum_structural_thickness_mm`, `design_corrosion_allowance_mm`,
  `active_corrosion_rate_mm_yr`, `last_measured_thickness_mm`, `last_inspection_date`.

## Dynamic attributes (EAV deleted)
- Values live **only** in `custom_fields jsonb` on node + item.
- `oe_asset_field_def` is **purely the JSONSchema registry** (categories/types/field defs).
- Indexed with **GIN `jsonb_path_ops`** + **functional B-tree** expression indexes on hot
  query attributes. The legacy `oe_asset_field_value` EAV table is **deleted**.

## Endpoints
Tree CRUD + `POST /assets/nodes/{id}/move`; items CRUD; schema registry; contextual
inspections (`GET /assets/nodes/{id}/inspections` aggregating descendants); ingestion
(`POST /assets/import` → SAQ job → `/apply` atomic); **P6 import/export** (idempotent on
`(project_id, external_activity_id)`, with the **Unmatched-Row Linking Screen**); config packs
(`POST /assets/config-packs/{key}/apply`).
