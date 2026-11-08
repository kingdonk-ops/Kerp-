# Module Specifications · Batch 8 (NEW modules): cutting · asset_management · wps

> Full specs for the three new modules of the **Field Operations Platform** (authoritative scope: `../FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md`). Same format as the other `MODULE-SPEC` batches: Identity · Depends/Dependents · Purpose · Entities (fields) · Endpoints · State/logic · Verification rules (via the central engine) · Features `[F#]` · Failure/removal.

### Resolved decisions baked into this batch
- **D5 — P6:** **one-way import (.xer / Primavera XML) + export back for re-import into P6.** No live bi-directional sync. Import builds/updates the asset tree; export emits progress actuals + updated dates as a P6-importable file. (Owned by `asset_management` ingestion + `integrations`.)
- **D7 — `crm` folded into `contacts`:** no separate `crm` module. `contacts` becomes the single people/company directory (clients, contractors, **staff**, suppliers) with company grouping, notes/interaction log, tags, and versioned files. (Reflected in deps below: welders/subbies/vendors reference `contacts`.)
- **D8 — portal co-sign/scheduler phased:** portal read-only v1 → co-sign (reports/ITPs) v2 → visit scheduler + client NCR v3. (Spec'd in the Final Plan §6; the co-sign/NCR/visit actions reuse `signing`, `ncr`, `review_authority` + the verification engine.)

---

## 1. `cutting` — Cutting Optimisation

- **Identity:** category=business · optional · prefix=`/cutting` · **Depends on:** `[projects, site_inventory, validation]` · **Dependents:** — (feeds `site_inventory`).
- **Purpose:** produce cutting plans for **bars (1-D)** and **sheets/plates (2-D)** from a known or custom cut list; compute **measured yield, waste and cost**; manage **offcut reuse**; and on confirmation write a **verified site-inventory update** (consume stock, add offcuts).

### Entities
- `oe_cutting_plan` → id, tenant_id, project_id, name, material_profile (e.g. `rebar_T16`, `plate_10mm`), stock_length_mm | sheet_w_mm/sheet_h_mm, kerf_mm, optimisation_kind[`bar_1d`|`sheet_2d`], source[`scope`|`custom`], status[`draft`|`optimised`|`confirmed`|`cancelled`], stock_count, used_length_or_area, yield_pct, waste_pct, offcut_reuse_pct, unit_cost, currency, computed_at, confirmed_at, confirmed_by, metadata_
- `oe_cutting_cut_list_item` → id, plan_id(FK), ref, description, profile, length_mm | width_mm/height_mm, quantity, from_offcut_id(nullable), sort_order
- `oe_cutting_stock_bar` → id, plan_id(FK), stock_index, source_offcut_id(nullable), cuts_json (ordered segments), offcut_length_or_area, waste_length_or_area
- `oe_cutting_offcut` → id, plan_id(FK), profile, length_mm | w/h_mm, location_id(→site_inventory location), reuse_state[`available`|`reserved`|`consumed`], created_from_stock_bar_id
- `oe_cutting_inventory_link` → id, plan_id(FK), site_inventory_movement_id, kind[`consume_stock`|`add_offcut`], confirmed

### Endpoints
- `POST /cutting/plans` · `GET /cutting/plans?project_id=` · `GET /cutting/plans/{id}` · `PATCH /cutting/plans/{id}` (edit stock/kerf/cut list)
- `POST /cutting/plans/{id}/cut-list` (set known or **custom** cut list) · `POST /cutting/plans/{id}/cut-list/import` (from a scope/CSV)
- `POST /cutting/plans/{id}/optimise` → runs nesting, returns bars/sheets + yield/waste/cost
- `GET /cutting/plans/{id}/offcuts` · `GET /cutting/offcuts?project_id=&profile=&state=available` (reusable-offcut register)
- `POST /cutting/plans/{id}/confirm` → **verification `cutting.confirm`**, then writes `site_inventory` movements (consume stock, add offcuts); idempotent.

### State / logic
`draft → optimised → confirmed` (terminal) / `cancelled`. Optimiser: 1-D first-fit-decreasing / best-fit for bars; 2-D guillotine/shelf nesting for sheets; honours kerf; may draw stock from **existing available offcuts** before new stock. On **confirm**, computes net stock consumption + new offcuts and posts `site_inventory` movements under a transaction; re-confirm is a no-op (idempotency link).

### Verification rules (engine)
Rule set **`cutting.confirm`**: stock availability ≥ required (site_inventory on-hand), cut list non-empty, every custom cut ≤ stock length/sheet size, plan status `optimised`. Error findings block confirmation.

### Features
[F1] known cut list (from scope) · [F2] **custom cut list** · [F3] 1-D bar optimisation · [F4] 2-D sheet/plate optimisation · [F5] kerf handling · [F6] measured yield / waste / cost · [F7] **offcut register + reuse** · [F8] **confirmed site-inventory update** (consume + add offcuts) · [F9] reusable-offcut search across plans.

### Failure
Disabled → no cutting plans; `site_inventory` unaffected. (Replaces the old catalog's "confirmed update of BOQ & requisition" — BOQ/requisition are out of scope in the pivot.)

---

## 2. `asset_management` — Asset Hierarchy, Items & Operations

- **Identity:** category=core · prefix=`/assets` · **Depends on:** `[projects, users, teams, validation]` · **Dependents:** `inspections, qms, construction_control, tasks, schedule, progress, documents, file_*, moc, wps, cutting(indirect)`.
- **Purpose:** the operational backbone — decompose scope into a recursive **Site → Area → Asset(→ sub-Asset…)* → RSW** tree, hang **typed Items** (valves/welds/structural) off assets, and anchor tasks, quality/inspections, files and MoC to it. Populated by **P6 import**, **Excel/CSV**, or **manual**; exports progress + dates back to P6.

### 2.1 Entities — hierarchy & schema
- `oe_asset_node` → id, tenant_id, project_id, parent_id(self-FK, nullable), level[`site`|`area`|`asset`|`rsw`], is_sub_asset(bool), path(materialised, e.g. `/site1/areaA/TX01/`), depth, name, code, asset_tag, category_id(FK), type_id(FK), discipline_code[`civil`|`structural`|`mechanical`|`electrical`|`piping`|`instrumentation`|…], status, percent_complete, **external keys**: external_source[`p6`|`excel`|`manual`], external_id, wbs_code, activity_id, synced_at, planned_start, planned_finish, budgeted_units, primary_resource_id, subcontractor_contact_id(→contacts), custom_fields(jsonb), sort_order, metadata_
  - Constraints: `rsw` nodes must parent no children except Items; `site`/`area` parent only their level or below; `asset` may parent `asset`(sub) and `rsw`. `path`/`depth` maintained on move.
- `oe_asset_category` → id, project_id, name, sort_order
- `oe_asset_type` → id, category_id(FK), name, applies_to[`asset`|`item`], description
- `oe_asset_field_def` → id, type_id(FK), key, label, data_type[`string`|`longtext`|`number`|`date`|`datetime`|`boolean`|`select`|`multiselect`|`doc_link`|`entity_link`], options(jsonb), unit, mandatory, constraints(jsonb: regex/min/max/required_on_status), p6_mandatory(bool), sort_order
- `oe_asset_field_value` → id, node_id|item_id, field_def_id(FK), value(jsonb)
- `oe_asset_layout` → id, type_id(FK), layout_json (Form/Inspection-Designer output: cards, accordions, read-only spec panels, status badges, columns)

### 2.2 Entities — items
- `oe_asset_item` → id, tenant_id, project_id, asset_id(FK→asset_node where level=asset), parent_item_id(self-FK, nullable), item_type_id(FK→asset_type where applies_to=item; e.g. Valve/Weld/Beam/Instrument), tag, spec_ref, discipline, rsw_id(FK, nullable: the work package that installs/tests it), status, inspectable(bool, default true), custom_fields(jsonb), metadata_
  - A **Weld** is an item with `item_type=weld`; its weld-specific fields + rules live in `wps` (§3), which references `asset_item_id`.

### 2.3 Entities — provisioning & ingestion
- `oe_asset_config_pack` → id, key, name, description, taxonomy_json (categories/types/field defs), layouts_json, disciplines(jsonb), itp_templates_json, p6_field_rules(jsonb), is_builtin
- `oe_asset_import_job` → id, project_id, source[`p6_xer`|`p6_xml`|`excel`|`manual`], filename, status[`queued`|`parsing`|`review`|`applied`|`failed`], parsed_summary(jsonb), mapping(jsonb), errors(jsonb line-by-line), created_by
- `oe_asset_p6_export` → id, project_id, kind[`progress`|`dates`|`full`], generated_at, format[`xer`|`xml`|`csv`], storage_key, filter(jsonb)

### 2.4 Endpoints
- Tree: `GET /assets/tree?project_id=` · `POST /assets/nodes` · `GET/PATCH /assets/nodes/{id}` · `POST /assets/nodes/{id}/move` · `DELETE /assets/nodes/{id}`
- Items: `POST /assets/nodes/{assetId}/items` · `GET /assets/nodes/{assetId}/items` · `GET/PATCH /assets/items/{id}` · `GET /assets/items?project_id=&type=&status=`
- Schema: `GET/POST /assets/categories` · `GET/POST /assets/types` · `GET/POST /assets/types/{id}/fields` · `GET/PUT /assets/types/{id}/layout`
- Inspections (contextual): `GET /assets/nodes/{id}/inspections` · `GET /assets/items/{id}/inspections` (aggregates asset + descendants; data from `inspections`/`qms`)
- Ingestion: `POST /assets/import` (p6/excel) → job · `GET /assets/import/{jobId}` · `POST /assets/import/{jobId}/apply` (atomic, after review) 
- **P6 export (D5):** `POST /assets/p6-export` (kind=progress|dates|full) → file for re-import into P6 · `GET /assets/p6-export/{id}` (download). Mirrored under `integrations` (`POST /integrations/p6/import`, `GET /integrations/p6/export`).
- Packs: `GET /assets/config-packs` · `POST /assets/config-packs/{key}/apply?project_id=`

### 2.5 Logic — ingestion & round-trip (D5)
- **P6 import:** async worker (`core/job_runner`) parses `.xer`/XML → WBS top nodes → **Site/Area**, lower WBS/activity-code groups → **Assets**, discipline-tagged activity bands/work packages → **RSWs**. Carries WBS codes & Activity IDs as **permanent external keys**, durations, planned start/finish, budgeted units/quantities, activity codes (discipline/contractor/system). Enforces P6 mandatory fields on later edits (verification `asset.p6_fields`). Import → `review` → `apply` (atomic provisioning).
- **Excel/CSV import:** wizard maps columns (Site_Name, Area_Name, Asset_Tag, Asset_Category, Asset_Type, RSW_Name, Discipline + custom) → 4-level tree; validates parent-child + mandatory schema; line-by-line errors; atomic apply.
- **Manual:** interactive tree build.
- **P6 export (round-trip):** emit a P6-importable file of **progress actuals (% complete / earned units)** and **updated/forecast dates** keyed by Activity ID/WBS, so the corporate P6 can re-import. No live sync; file-based both ways.

### 2.6 Asset Details tabs (modular)
`[Overview]` (designer cards + metadata + status) · `[RSWs]` (child work packages by discipline + task metrics) · `[Items]` (typed components incl. welds) · `[Inspections & ITPs]` (contextual) · `[Files]` (CDE docs/drawings/manuals/dockets linked to the asset) · `[Tasks]` (RSW activities/punch/constraints) · `[MOC]`. Layout per type is drag-drop designed (`oe_asset_layout`), no code.

### 2.7 Verification rules (engine)
`asset.status_change` (required fields on status, children complete before parent), `asset.p6_fields` (WBS/Activity/dates/discipline mandatory when external_source=p6), `asset.import_validate` (parent-child + schema + mandatory), custom per-type field constraints. All via `run_checks`.

### 2.8 Features
[F1] recursive Site/Area/Asset/**sub-Asset**/RSW tree · [F2] **typed Items** (valves/welds/structural) · [F3] dynamic categories/types/custom-field schema · [F4] page/layout designer · [F5] modular asset tabs · [F6] **P6 import** (.xer/XML → tree) · [F7] **P6 export / round-trip** (progress + dates) · [F8] Excel/CSV bulk import · [F9] manual build · [F10] external integration keys · [F11] default configuration packs · [F12] contextual + global inspection anchoring (asset/sub-asset/RSW/item).

### 2.9 Failure
Core backbone — disabling it breaks the operational model (tasks/quality/items lose their anchor). Supersedes the old thin `assets` module.

---

## 3. `wps` — WPS Register, Welder Qualification & Welds

- **Identity:** category=compliance · prefix=`/wps` · **Depends on:** `[projects, contacts, asset_management, inspections, qms, construction_control, file_versions, validation]` · **Dependents:** `inspections` (weld inspection), `project_controls`/reporting (weld KPIs).
- **Purpose:** hold the **Welding Procedure Specification** register, record which **people are qualified** to each WPS (and over what range), enforce that every **weld** references an approved WPS and is assigned **only to a welder qualified to that WPS**, and gate weld acceptance on NDE — all through the verification engine.

### 3.1 Entities
- `oe_wps_record` → id, tenant_id, scope[`project`|`library`], project_id(nullable), code, title, welding_process[`SMAW`|`GTAW`|`GMAW`|`FCAW`|`SAW`|…], standard_ref[`ASME IX`|`ISO 15614`|`AWS D1.1`|…], base_material_group, filler_material (SFA/F-no/A-no), positions(jsonb), thickness_min_mm, thickness_max_mm, diameter_min_mm, diameter_max_mm, joint_types(jsonb), pwht(bool), pqr_ref, revision, status[`draft`|`approved`|`withdrawn`], document_id(→documents), approved_by, approved_at, metadata_
- `oe_welder` → id, project_id, contact_id(→contacts; the person), stamp (welder ID), employer_kind[`staff`|`subcontractor`], subcontractor_contact_id(nullable), status[`active`|`inactive`], metadata_
- `oe_welder_qualification` → id, welder_id(FK), wps_id(FK), qualified_date, expiry_date, **continuity_last_welded_at**, qualified_range(jsonb: positions/thickness/diameter/material actually qualified — subset of the WPS), test_type, certificate_document_id(→file_versions/documents), status[`valid`|`expired`|`suspended`|`revoked`], verified_by, verified_at
- `oe_weld` → id, project_id, asset_item_id(FK→asset_item of type weld), weld_number, **wps_id(FK, required)**, **welder_qualification_id(FK, required)**, joint_type, size_mm, thickness_mm, diameter_mm, material, nde_required(bool), nde_types(jsonb: VT/PT/MT/RT/UT), status[`fit_up`|`welded`|`nde_pending`|`accepted`|`rejected`|`cut_out`], welded_at, metadata_
- `oe_weld_nde_result` → id, weld_id(FK), nde_type, performed_at, technician_contact_id, result[`accept`|`reject`], report_document_id, notes  *(may reuse `construction_control._test_result`)*

### 3.2 Endpoints
- WPS: `POST /wps/records` · `GET /wps/records?project_id=&process=&standard=` · `GET/PATCH /wps/records/{id}` · `POST /wps/records/{id}/approve` · `POST /wps/records/{id}/withdraw`
- Welders & qualifications: `POST /wps/welders` · `GET /wps/welders?project_id=` · `POST /wps/welders/{id}/qualifications` · `GET /wps/welders/{id}/qualifications` · `PATCH /wps/qualifications/{id}` (verify/suspend/revoke) · `GET /wps/qualifications?wps_id=&status=valid` (who may weld to this spec)
- Welds: `POST /wps/welds` (asset_item_id + wps_id + welder_qualification_id) · `GET /wps/welds?project_id=&asset_id=&status=` · `GET/PATCH /wps/welds/{id}` · `POST /wps/welds/{id}/nde` (add NDE result) · `POST /wps/welds/{id}/accept` · `POST /wps/welds/{id}/reject` · `POST /wps/welds/{id}/cut-out`
- Reporting: `GET /wps/welds/register?project_id=` (weld register w/ filters + PDF/Excel export) · `GET /wps/continuity-watch` (qualifications near expiry/continuity lapse).

### 3.3 Weld state machine
`fit_up → welded → nde_pending → accepted` / `rejected → cut_out → (re-weld) fit_up`. Transitions gated by the engine (§3.4). `welded` requires a valid welder assignment; `accepted` requires NDE accept (where required) + hold-point release.

### 3.4 Verification rules (engine) — enforced, not ad-hoc
- **`welder.assign` / weld create-update:**
  - **R-WPS-1:** weld's `wps_id` → an **approved** `oe_wps_record`.
  - **R-WPS-2:** the assigned `welder_qualification` is **for that `wps_id`**, status `valid`, and its `qualified_range` **covers** the weld's position/thickness/diameter/material.
  - **R-WPS-3:** **continuity** — `continuity_last_welded_at` within the standard's window (e.g. ≤ 6 months) at `welded_at`.
- **`weld.accept`:**
  - **R-WPS-4:** all **required NDE** results = `accept`; any ITP **hold/witness points** released (`construction_control`); no open NCR on the weld.
- Violations → **blocked with findings** (422-style), surfaced in UI; findings logged (auditable). Rules are data → a client's extra NDE-frequency rule is configuration.

### 3.5 Interactions / adaptation
- **asset_management:** welds/items hang off assets/sub-assets; the global `/inspections` register and the Asset-Details Inspections tab include weld-level records (anchored to `asset_item`).
- **qms/inspections/construction_control:** supply inspection runs, ITP hold points, and NDE/test results.
- **contacts** (post-D7): welders, technicians and subcontractor employers are `contacts`.
- **credentials:** general tickets stay in `credentials`; `wps` is the weld-specific qualification layer (range-matching logic) — kept distinct.

### 3.6 Features
[F1] WPS register (process/standard/material/position/thickness/diameter ranges, PQR, revisions, approval) · [F2] welders · [F3] **welder qualifications** (staff↔WPS, range, expiry, continuity, certificate) · [F4] **welds** (asset items) requiring approved WPS + qualified welder · [F5] **enforced welder↔WPS qualification** (range + validity + continuity) · [F6] NDE results + **acceptance gating** · [F7] weld/item **inspection** via qms/inspections · [F8] weld register + continuity watch + export · [F9] versioned WPS/qualification documents.

### 3.7 Failure
Disabled → no welding quality control; welds (if any) become plain inspectable items with no procedure/qualification enforcement. Keep for any welding scope.

---

*Batch 8 @ `a96d525`. Companion authoritative scope: `../FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md` (D5/D7/D8 resolved here). crm folded into contacts; P6 import+export round-trip; portal co-sign/scheduler phased v1→v3.*
