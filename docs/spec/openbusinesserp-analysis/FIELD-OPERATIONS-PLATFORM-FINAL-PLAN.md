# Field Operations Platform — Final Plan

> **Consolidated, authoritative plan** merging `REVISED-PLAN-field-operations.md` + `REVISED-PLAN-addendum-01-assets-wps.md` + the latest decisions. Supersedes both for the module register and scope; the detailed per-module entity/field specs live in `spec/MODULE-SPEC-0X-*.md` (mark removed modules per the banner added to each). Grounded in the OpenBusinessERP source @ `a96d525`.

**Product:** an **operational delivery / field-execution engine** — ingestion, edge-capture and operational reconciliation. It **owns no corporate financial or master-schedule records**; it owns **field ground truth** (physical delivery state, installed quantities, plant/labour presence, delay logs, quality/weld records, photos) and feeds verified actuals back to the systems of record (corporate ERP, Primavera P6, corporate safety). Single company — currency/date/locale/tax live in **Project Settings**, not an i18n module.

**Four pillars:** Logistics · Execution · Defense & Claims · Controls & Insights (+ Platform + Asset/Quality backbone).

---

## 1. Module register (final)

### 1.1 ADDED (new modules)
| Module | Purpose |
|---|---|
| **`cutting`** | Bar/sheet cutting optimisation from known/custom cut list; yield/waste/cost; offcut reuse; **confirmed site-inventory update**. |
| **`asset_management`** | Site→Area→Asset(→sub-Asset…)→RSW hierarchy **+ typed Items** (valves/welds/structural); P6/.xer+XML & Excel/manual ingest; dynamic schema; page designer; contextual+global inspections; default config packs. |
| **`wps`** | WPS register + welder qualifications (staff↔WPS) + welds; enforces welder↔WPS qualification (range/validity/continuity) + NDE acceptance; welds/items inspectable. *(Own module — confirmed.)* |
| **(in `integrations`)** P6/ERP/safety **ingestion surface** | Idempotent batch/webhook endpoints: `POST /integrations/p6/import`, `POST /integrations/purchasing/po-lines`, safety KPI sync. |

### 1.2 MODIFIED (scope changed)
| Module | Change |
|---|---|
| `procurement` | **Basic**: POs (internal or ERP-mirrored), goods receipts→gate/OS&D, requisitions, vendor mgmt. Drop monetary posting. |
| `supplier_catalogs` | **Basic**: vendor master, price lists, **purchase requests→purchasing**, goods receipts, stock. Drop 3-way match/KYC/scorecards/Peppol/POs/invoices. |
| `subcontractors` | **Basic**: agreements (+**versioned files**), retention (physical), ratings. Drop prequal/payment-applications/lien-waivers; contract FK→external ref. |
| `resource_summary` | Rolls resource demand **from tasks/look-ahead** (not BOQ), procurement-ready, with **required-by dates**. |
| `crm` | **Basic people/company directory** over `contacts` — clients/contractors/**staff**, notes + versioned files, tags. **No pipeline** (drop leads/opps/stages/forecast). |
| `costmodel`, `full_evm` | **Physical EVM only** — earned **quantity**, crew hours, PPC, variance vs imported P6 targets. No currency/tax/revenue. |
| `eac` | Rules engine **repurposed** to physical/quality predicates (drop currency/tax). Part of the verification engine (§5). |
| `project_controls` | **Kept**, read-only board, **physical metrics only** (no EVM-money columns). |
| `field_time` | Keep hours (operational); **export hours to corporate payroll** instead of an internal payroll batch. |
| `progress`, `schedule`, `site_inventory`, `site_logistics`, `variations`, `cost_recovery` | **Re-anchored** off removed modules (see §4): `boq_position_id`→Asset/RSW/Item; contract FK→external ref; no GL posting. |
| **`portal`** | **Kept read-only now; re-developed later** with the client feature set in §6. |

### 1.3 KEPT (operational & platform, unchanged in intent)
**Logistics:** site_logistics, site_inventory, equipment · **Execution:** schedule, schedule_advanced (Last Planner), progress, tasks, daily_diary, field_diary, field_time, fieldreports, forms, voice, phonelog, site_prep, site_supervision, inspections, ncr, punchlist, qms, construction_control, safety, hse_advanced, commissioning, temporary_works, requirements, credentials, review_authority, project_route, defects_liability · **Defense & Claims:** variations, changeorders, moc, change_intelligence, claims_evidence, reconciliation, cost_recovery · **Controls & Insights:** reporting, dashboards, dashboard, bi_dashboards, project_intelligence, timeline, risk · **Documents/CDE:** documents, transmittals, correspondence, connectors, inbound_email, inbound_capture, file_versions, file_approvals, file_comments, file_distribution, file_favorites, file_references, file_saved_views, file_search, file_tags, file_transmittals, file_trash, plan_room, signing, record_publishing, source_data, authority_submission · **Collaboration:** collaboration, collaboration_locks, global_presence, meetings, rfi, submittals, notifications, contacts, interface_management, deadlines · **AI/Search:** ai_agents, search, retrieval, erp_chat (claims/operational focus, §5/§7) · **Ops/sustainability:** carbon, esg, service, closeout, prefab, resources · **Platform:** projects, users, teams, validation, approval_routes, enterprise_workflows, pipelines, jobs, saved_views, integrations, backup, uploads, resumable_uploads, client_errors, admin, module_builder, onboarding, cases, data_import.

### 1.4 REMOVED
- **Estimating/BOQ:** boq, assemblies, methodology, rom_estimate, ai_estimator, ai, estimate_basis, estimate_rollup, allowances, preliminaries, formwork, waste_factors, norm_expansion, labor_rates, postcalc, takeoff, dwg_takeoff, rebar_schedule, (libs: measurement, price_breakdown).
- **Cost/resource data:** costs, catalog, cost_match, match_elements, fx, price_index.
- **BIM/CAD/coordination:** cad, bim_hub, smart_views (+ already-absent clash/bcf/geo_hub/pointcloud/bimlv/opencde_api/coordination_hub/bim_requirements/cost_explorer/match/design_options/architecture_map/hse_frame/cde).
- **Tendering/commercial:** tendering, bid_management, rfq_bidding, contracts.
- **Finance/accounting (all monetary):** finance, gl, banking, receivables, sales, recurring, items, inventory, cost_plan, einvoice, funding, cvr, **value**, payroll, appointments.
- **Regional/tax:** dach_pack, uk_pack, us_pack, us_ca_pack, us_tx_pack, india_pack, latam_pack, mexico_pack, middle_east_pack, asia_pac_pack, china_pack, sa_pack, russia_pack, payment_clock, tax_withholding, einvoice_clearance.
- **i18n:** i18n_foundation → **Project Settings**.
- **This turn:** **property_dev, webhook_leads, accommodation**.

### 1.5 Count
189 (build) → remove ~98 → keep ~91 (incl. modified) → add 3 modules (`cutting`, `asset_management`, `wps`) + the P6/ERP ingestion surface in `integrations` → **~94 modules**, reshaped around the four pillars + the Asset/Quality backbone.

---

## 2. RBAC — persona → visibility matrix (via `users`/`teams` + project/portfolio scope)
| Persona | Scope |
|---|---|
| Operations Management | **All projects** + all-projects dashboard |
| Purchasing & Procurement Dept · Health & Safety Manager · HR · Quality Manager | All projects (overview, read) |
| Project Manager | All projects **they manage** |
| Site Superintendent | Everything for **their project** |
| Supervisor · Planner · QA/QC Lead · Admin · Stores Person | **Their project only** |
| Inspector | **Their site only** (Asset `Site` scope) |
Implemented as business-role presets + per-user project/site scope; `cases` seeds a walkthrough set per persona. "Site-only" resolves to an Asset-hierarchy **Site** node.

---

## 3. Asset & Quality backbone

### 3.1 Hierarchy (recursive)
`Site → Area → Asset (→ sub-Asset …)* → RSW` — an Asset may parent **sub-Assets** (any depth), and Assets/sub-Assets may parent **RSWs** (discipline work packages: Civil/Structural/Mech/Electrical/Piping/Instrumentation). Tasks/look-aheads/constraints/crews lodge at **RSW**.

### 3.2 Items & inspection anchoring
- Assets/sub-Assets carry **Items** (`oe_asset_item`: valves, welds, structural, instruments…) using the dynamic type/field schema; items are **inspectable**.
- **Inspections/ITPs anchor at Asset · sub-Asset · RSW · Item.** Global `/inspections` register filters across all levels + batch PDF/Excel export; the Asset-Details Inspections tab aggregates the asset + descendants.
- Entities: `oe_asset_node`(parent_id, level, is_sub_asset, path, depth, category_id, type_id, discipline_code, external keys wbs_code/activity_id/external_source/external_id/synced_at, planned dates, budgeted_units, custom_fields) · `oe_asset_category`/`_type`/`_field_def`/`_field_value`/`_layout` · `oe_asset_item` · `oe_asset_config_pack` · `oe_asset_import_job`.
- Ingestion: **P6 .xer/XML** (WBS→Site/Area, activity groups→Assets, discipline bands→RSWs; carries WBS/Activity IDs as permanent external keys, durations, planned dates, budgeted units, activity codes; enforces P6 mandatory fields) · **Excel/CSV** bulk wizard · **manual**. Default **configuration packs** provision taxonomy/layouts/disciplines/ITPs/P6 rules at project creation.

### 3.3 `wps` — WPS, welder qualification, welds (own module)
- `oe_wps_record`(code, welding_process, standard_ref, base_material_group, filler_material, positions, thickness/diameter ranges, pqr_ref, revision, status, document_id) · `oe_welder`(contact/user ref, stamp, employer) · `oe_welder_qualification`(welder_id, wps_id, qualified_date, expiry_date, continuity_last_welded_at, qualified_range, certificate_document_id, status) · `oe_weld`(= asset Item type `weld`: weld_number, **wps_id required**, **welder_qualification/welder_id required & qualified**, joint/size/thickness/material, nde_required, nde_types, status) · `oe_weld_nde_result`.
- **Enforced rules** (via §5): R-WPS-1 weld references an **approved** WPS · R-WPS-2 welder holds a **valid** qualification for that WPS **covering** the weld's range · R-WPS-3 **continuity** not lapsed · R-WPS-4 no **accept** until NDE accept + hold points released. Unqualified assignment → blocked with a finding.

---

## 4. Dependency-impact adaptations (kept modules re-pointed off removed ones)
| Removed | Kept module affected | Adaptation |
|---|---|---|
| `boq` (`boq_position_id`) | progress, schedule, site_inventory, site_logistics, variations, resource_summary | Replace with **Asset/RSW/Item** anchor (progress/quality against scope items, not BOQ). |
| `costs`/`assemblies`/`price_index`/`fx` | supplier_catalogs, cost_recovery | No internal cost DB; physical quantities only; unit prices from vendor lists/ERP mirror; single-currency project setting. |
| `contracts` | subcontractors, cost_recovery, variations | Contract FK → **external contract reference**; drop SOV/progress-claim features. |
| `finance`/`gl`/`banking`/`sales`/`receivables` | costmodel, full_evm, field_time | Monetary posting removed → **physical EVM**; append-only operational ledgers; field_time exports to corporate payroll. |
| `items`/`inventory` | cutting, site_inventory | Stock → `site_inventory` (+ basic supplier stock); `cutting` writes site_inventory. |
| `bim_hub`/`cad`/`clash`/`smart_views` | construction_control, punchlist, ncr | Drop/soften BIM & clash refs (nullable/optional); element refs → plain tag. |
| `i18n_foundation` | tax/country/FX | → **Project Settings** (single company). |
Rule: a kept feature existing **only** for a removed capability is removed; otherwise re-pointed to Asset/RSW/Item or an external integration key.

---

## 5. Verifications — one central rules/checks engine
Every verifiable action routes through **`run_checks(context, rule_set)`** built on the kept **`validation`** + repurposed **`eac`** + the **completion-gate** pattern + **`compliance`/`compliance_ai`** DSL. Error findings **block** the transition (surfaced in UI); warnings advise. Rules are **data** (rule sets/DSL), so new checks are configuration. Rule sets per verifiable entity: `weld.accept`, `welder.assign`, `inspection.close`, `rsw.complete`, `goods.receive`, `cutting.confirm`, `asset.status_change`, `report.cosign`, `itp.cosign`, `ncr.signoff`. Findings are logged (auditable).

---

## 6. `portal` — client read-only now, re-developed later (future feature set)
Kept read-only in the near term; **future redevelopment** gives clients:
- **View the Asset hierarchy** (Site→Area→Asset→sub-Asset→RSW→Items).
- **View all registers** (inspections/ITP/NCR/punch/weld/documents… read-only).
- **Co-sign completed reports** and **co-sign ITPs** (counter-signature via `signing`, gated by §5 `report.cosign`/`itp.cosign`).
- **Scheduling for site visits** — organise **hold-point witness** and **final sign-off** visits (a lightweight scheduler feeding `review_authority`/hold-gates/meetings).
- **Input and sign-off of NCRs** (client-raised NCRs + sign-off, through `ncr` + §5 `ncr.signoff`).
Entities to add on redevelopment: client co-sign records (reuse `signing`), visit bookings (hold-point witness/final), client-raised NCR flag. Access via existing `portal` magic-link + per-resource access rules, scoped to the client's project(s).

> **Full phased spec (v1 read-only → v2 co-sign → v3 scheduler + client NCR):** `spec/MODULE-SPEC-09-reanchored-and-portal.md` §6.

---

## 7. Platform refactor & integration boundaries (summary)
- **Persistence:** drop `oe_gl_*`/`oe_finance_*`/`oe_einvoice_*`; double-entry triggers → **append-only operational ledgers**; hard FKs to removed authoring modules → **external integration keys** (`external_source`,`external_id`,`synced_at`); new **ingestion staging** (P6 raw/parsed, PO-line mirrors, safety KPI snapshots); blob storage grows (dockets, daywork tickets, weld/NDE reports, evidence).
- **Backend:** P6 parser on `core/job_runner`; **OS&D** service (PO vs gate-receipt deltas); strip removed endpoints; idempotent ingestion webhooks/batch sync; EVM kernels → physical metrics.
- **AI/search:** `file_search` OCR → dockets/daywork/receipts (key-value: docket#, qty, stamp); vectors → **claims/defence** correlation (weather/diary/late-receipts/MoC → evidence binders); `erp_chat` tools drop financial actions for `get_open_constraints`/`get_po_delivery_status`/`check_ppc_reliability`/`summarize_claims_timeline`.
- **Client:** tablet/edge-first; **Gate Delivery** (barcode/docket capture, +/- qty, signature), **Look-Ahead Board** (3–6 wk constraint Kanban→PPC), **Daily Workface Log**; `useOfflineSync()` tier-1 (IndexedDB + conflict replay); `navCatalog.ts` → 4 pillars.
- **Boundaries:** Inbound — PO lines (ERP), XER/XML (P6), KPI (safety). Outbound — progress actuals→P6; receipt confirmations/OS&D + docket images→ERP AP 3-way match; signed daywork/plant/labour logs→procurement/commercial for claim verification.

---

## 8. Open points
- **D5:** P6 round-trip — one-way ingest + export vs bi-directional with conflict rules.
- **D3 (resolved):** RSW = lowest **work** anchor; **Item** (incl. weld) = lowest **inspection** anchor.
- **D7 (new):** `crm` as its own module vs folded into `contacts` (recommend fold: one directory).
- **D8 (new):** portal co-sign/scheduling build — phase it (read-only v1 → co-sign v2 → visit scheduler v3)?

---

*Final plan @ `a96d525`. Detailed entity/field specs: `spec/MODULE-SPEC-01…07b` (removed modules flagged per each file's pivot-status banner). This document is the authoritative scope/register; the `MASTER-REDEVELOPMENT-PLAN.md` remains the original full-ERP reference.*

---
## 9. Decisions resolved (update)
- **D5 — P6:** file **import (.xer/XML)** retained + **export back for re-import into P6** (progress actuals + dates). No live bi-directional sync. Spec'd in `spec/MODULE-SPEC-08-new-modules.md` §2.5.
- **D7 — crm:** **folded into `contacts`** (single people/company directory: clients/contractors/staff/suppliers, company grouping, notes log, tags, versioned files; no pipeline). No separate `crm` module.
- **D8 — portal:** co-sign/scheduler **phased** — v1 read-only → v2 co-sign reports & ITPs → v3 visit scheduler (hold-point witness/final sign-off) + client NCR input/sign-off (reusing `signing`, `ncr`, `review_authority` + the verification engine).
- Full specs for the three new modules (`cutting`, `asset_management`, `wps`): `spec/MODULE-SPEC-08-new-modules.md`.
- **Re-anchored kept modules** (`schedule` P6-synced, `progress` physical earned-quantity, `costmodel` physical EVM, `variations` re-anchored + external commercial mirror) **and the full phased `portal` spec**: `spec/MODULE-SPEC-09-reanchored-and-portal.md`. This batch applies the BOQ→Asset/RSW/Item substitution (D3) and the external-integration-key substitution to those modules, and **supersedes** their BOQ-anchored as-built specs in batches 04/05/07a.
