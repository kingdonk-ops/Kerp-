# Revised Plan — Field Operations & Delivery-Execution Platform

> **Pivot.** This revision reshapes the OpenBusinessERP spec set from a full-lifecycle ERP into an **operational delivery / field-execution engine**: an ingestion, edge-capture and operational-reconciliation hub. It **owns no corporate financial or master-schedule records** — it owns *observational field ground truth* (physical delivery state, installed quantities, plant/labour presence, delay logs, photos) and feeds verified actuals back to the upstream systems of record (corporate ERP, Primavera P6, corporate safety).
>
> Applies to the module register + specs in `docs/openbusinesserp-analysis/`. Grounds dependency impacts in `models.py`/`depends` evidence gathered this session @ `a96d525`.

---

## 1. Four operational pillars (the new product shape)
1. **Logistics** — gate receiving & dockets, Over/Short/Damaged (OS&D), site inventory, basic procurement/supplier/subcontractor, cutting optimisation, resource statements.
2. **Execution** — Last Planner look-ahead, daily workface log, BOQ/scope progress capture, field time, forms, quality, safety.
3. **Defense & Claims** — field daywork logs, change orders & MoC, dispute evidence binder, reconciliation, cost recovery.
4. **Controls & Insights** — physical EVM (actual qty vs target), constraint & PPC analytics, risk, dashboards, project intelligence.
Plus **Platform** (projects/users/teams/validation/approval/jobs/integrations/CDE/AI) and the new **Asset Management** hierarchy (§6).

---

## 2. ADD — new & modified modules

### 2.1 `cutting` — Cutting Optimisation (NEW)
- **Identity:** business · optional · prefix=`/cutting` · **Depends on:** [projects, site_inventory] · **Dependents:** —
- **Purpose:** cutting plans for bars and sheets from a known or custom cut list, with measured yield, waste and cost, offcut reuse, and a **confirmed update to site inventory**.
- **Entities (proposed):** `oe_cutting_plan`(project_id, stock_profile, stock_length/sheet_size, kerf, status, yield_pct, waste_pct, offcut_reuse_pct, cost, currency, confirmed_at/by) · `oe_cutting_cut_list`(plan_id, source: boq|custom, item_ref, profile, length/size, quantity) · `oe_cutting_result_bar`(plan_id, stock_index, cuts_json, offcut_length, waste_length) · `oe_cutting_offcut`(plan_id, profile, length, location_id, reuse_state) · `oe_cutting_inventory_update`(plan_id, site_inventory_movement_id, confirmed).
- **Key logic:** 1-D bar nesting / 2-D sheet nesting → yield/waste; offcut register for reuse; on confirm, writes `site_inventory` movements (consume stock, add offcuts).
- **Features:** [F1] known cut list (from scope) · [F2] **custom cut list** · [F3] 1-D bar optimisation · [F4] 2-D sheet optimisation · [F5] measured yield/waste/cost · [F6] offcut register + reuse · [F7] **confirmed site-inventory update**.
- **Note:** the old catalog's "confirmed update of the BOQ and requisition quantities" is **replaced** by "confirmed update to site inventory" (BOQ/requisition are out of scope in this pivot).

### 2.2 `resource_summary` — Resource Summary (MODIFIED)
- **Change:** rolls resource splits **across the tasks** (not "across the estimate"/BOQ) into a **procurement-ready statement, with required-by dates**.
- **Depends on (was [boq]) → now [tasks, schedule]**. Entities: `oe_resource_summary_snapshot`(project_id, generated_at, total, line_count, payload) + add `required_by_date` per resource line.
- **Features:** [F1] resource demand rolled from **tasks/look-ahead** · [F2] procurement-ready statement · [F3] **required-by dates** (drives procurement timing).
- **Adaptation:** BOQ linkage removed; demand is derived from task/resource assignments and schedule need-by dates.

### 2.3 `supplier_catalogs` — Basic Supplier Catalogs & Vendor Management (SIMPLIFIED)
- **Scope cut:** keep vendor master, price lists, **purchase requests (sent through to purchasing)**, goods receipts, stock control. **Drop:** 3-way-match engine, KYC, scorecards, tolerance profiles, commodity codes, Peppol invoices (monetary/compliance surface).
- **Kept entities:** `_vendor`, `_item_category`, `_catalog_item`, `_price_list`, `_catalog_entry`, `_pr`(+`_pr_line`), `_gr`(+`_gr_line`), `_warehouse`, `_stock_balance`, `_stock_movement`. **Removed:** `_po`/`_po_line` (POs now owned upstream/by basic procurement), `_invoice`/`_invoice_line`, `_match_record`, `_kyc_document`, `_scorecard`, `_tolerance_profile`, `_commodity_code`.
- **Features:** [F1] vendor master · [F2] price lists · [F3] **purchase requests → handoff to purchasing/procurement** · [F4] goods receipts · [F5] stock control (balances/movements). (Dropped: 3-way match, KYC, scorecards.)

### 2.4 `procurement` — Basic Procurement (SIMPLIFIED)
- **Scope cut:** keep purchase orders, goods receipts, vendor management; feed OS&D (§5). Drop retainage-release ledger if not needed for field ops (retain only if subcontractor retention needs it — keep flag, drop monetary posting).
- **Kept entities:** `_po`(+`_po_item`), `_goods_receipt`(+`_gr_item`), `_requisition`(+`_req_item`). **Note:** POs may be **mirrors of upstream corporate POs** (external integration keys `external_po_number`) rather than internally authored — see §5.
- **Features:** [F1] purchase orders (internal or ERP-mirrored) · [F2] goods receipts (+rejections) → **gate receiving/OS&D** · [F3] requisitions · [F4] vendor management.

### 2.5 `subcontractors` — Subcontractor Management (SIMPLIFIED)
- **Scope cut:** keep **agreements, file storage & versioning, retention and rating**. **Drop:** prequalification, payment applications (monetary), payment-application lines, lien waivers, certificates-as-compliance (keep simple cert storage via files).
- **Kept/added entities:** `_subcontractor`, `_subcontractor_contact`, `_agreement` (retention_percent/release_event), `_rating`, `_retention_ledger` (physical retention held/released, no GL posting). **Added:** agreement **file storage + versioning** (link to `file_versions`). **Removed:** `_prequalification`, `_payment_application`(+`_line`), `_lien_waiver`, `_certificate` (fold into files), `_work_package` (optional — keep if work packages map to RSW, see §6).
- **Features:** [F1] subcontractors · [F2] agreements (+**versioned files**) · [F3] retention (held/released, physical) · [F4] performance ratings.
- **Adaptation:** former `contract_id`/`prime_contract_id` FKs to the removed `contracts` module → drop or replace with a free-text/external contract reference.

### 2.6 `risk` — Risk Register (KEPT, unchanged)
- Keep as specced (Batch 7b): register, scoring/tiering, mitigation/contingency, escalation. Strip the monetary `response_cost`/`impact_cost` emphasis toward **schedule/operational impact** if desired (optional).

### 2.7 `cases` — Walkthroughs + the **operational RBAC/persona model** (EXTENDED)
`cases` lets a team author/start/pin its own walkthroughs. This revision also **defines the persona → project-visibility matrix** the platform's access model must enforce (and that cases are authored per-persona):

| Persona | Visibility scope |
|---|---|
| **Operations Management** | **All projects** + an all-projects dashboard |
| **Purchasing & Procurement Dept** | All projects (overview) |
| **Health & Safety Manager** | All projects (overview) |
| **HR** | All projects (overview) |
| **Quality Manager** | All projects (overview) |
| **Project Manager** | All projects **they manage** |
| **Site Superintendent** | Everything for **their project** |
| **Supervisor** | **Their project only** |
| **Planner** | Their project only |
| **QA/QC Lead** | Their project only |
| **Admin** | Their project only |
| **Stores Person** | Their project only |
| **Inspector** | **Their site only** |

- **Implements via:** `users`/`teams` + a project/portfolio scope resolver. "All-projects-overview" personas get a read scope across the portfolio (dashboards + registers, not edit). Per-project personas resolve to their assigned project(s); "site/that-site-only" resolves to a sub-project/site scope (ties to Asset `Site`, §6).
- **Action:** extend the RBAC layer (business-role presets / per-user project scope, see `openbusinesserp-architecture.md §5`) with these 13 presets; `cases` seeds a walkthrough set per persona.

### 2.8 `asset_management` — Asset Hierarchy & Operations (NEW, large) — see §6.

---

## 3. REMOVE — modules dropped in this pivot
Per the instruction + the architecture narrative ("drop gl, finance, einvoice, funding, cvr … no corporate financial or master-schedule records").

**Estimating & BOQ (all):** `boq`, `assemblies`, `methodology`, `rom_estimate`, `ai_estimator`, `ai`, `estimate_basis`, `estimate_rollup`, `allowances`, `preliminaries`, `formwork`, `waste_factors`, `norm_expansion`, `labor_rates`, `design_options`, `postcalc`.
**Cost & resource data (all):** `costs`, `catalog`, `cost_explorer`*, `cost_match`, `match`*, `match_elements`, `fx`, `price_index`. (*already absent in this build.)
**Quantity/drawing:** `rebar_schedule`.
**BIM/CAD/coordination (all):** `cad`, `bim_hub`, `bim_requirements`*, `clash`*, `clash_ai_triage`*, `clash_cost_impact`*, `coordination_hub`*, `smart_views`, `bcf`*, `opencde_api`*, `geo_hub`*, `pointcloud`*, `bimlv`*. (* absent in this build.)
**Tendering/commercial:** `tendering`, `bid_management`, `rfq_bidding`, `contracts` (full), and **full** `procurement`/`subcontractors`/`supplier_catalogs` (replaced by basic versions, §2).
**Finance/EVM/accounting (all monetary):** `finance`, `einvoice`, `funding`, `full_evm` (→ physical EVM only, §4), `costmodel` (→ physical only), `cvr`, `project_controls`*(keep as read-only controls board, see note), `value`, `payroll`, plus the accounting core the catalog omitted but the narrative drops: **`gl`, `banking`, `receivables`, `sales`, `recurring`, `items`, `inventory`** (replaced by `site_inventory` + basic supplier stock), `cost_plan`, `data_import`(optional keep), `appointments`.
**Regional/tax (all):** `dach_pack`, `uk_pack`, `us_pack`, `us_ca_pack`, `us_tx_pack`, `india_pack`, `latam_pack`, `mexico_pack`, `middle_east_pack`, `asia_pac_pack`, `china_pack`, `sa_pack`, `russia_pack`, `payment_clock`, `tax_withholding`, `einvoice_clearance`.
**i18n:** `i18n_foundation` → **folded into Project Settings** (single company; currency/date/locale are project settings, not a module).

> `project_controls` is listed in the pivot's Pillar 4 as a read-only board — **keep** it but strip cost/EVM-money columns to physical metrics. `value` (change exposure, cost recovery, time saved) — keep only if cost_recovery/claims remain useful; otherwise drop.

---

## 4. KEEP — the operational core (by pillar)
- **Logistics:** `site_logistics`, `site_inventory`, `equipment`, `procurement`(basic), `supplier_catalogs`(basic), `subcontractors`(basic), **`cutting`**(new), `resource_summary`(modified).
- **Execution:** `schedule`, `schedule_advanced` (Last Planner), `progress`, `tasks`, `daily_diary`, `field_diary`, `field_time`, `fieldreports`, `forms`, `voice`, `phonelog`, `site_prep`, `site_supervision`, `inspections`, `ncr`, `punchlist`, `qms`, `construction_control`, `safety`, `hse_advanced`, `hse_frame`*, `commissioning`, `temporary_works`, `requirements`, `credentials`, `review_authority`, `project_route`, `defects_liability`. (*if present in target build.)
- **Defense & Claims:** `variations`, `changeorders`, `moc`, `change_intelligence`, `claims_evidence`, `reconciliation`, `cost_recovery`.
- **Controls & Insights:** `reporting`, `dashboards`, `dashboard`, `bi_dashboards`, `project_intelligence`, `timeline`, `risk`.
- **Documents/CDE:** `documents` (CDE), `transmittals`, `correspondence`, `connectors`, `inbound_email`, `inbound_capture`, `file_versions`, `file_approvals`, `file_comments`, `file_distribution`, `file_favorites`, `file_references`, `file_saved_views`, `file_search`, `file_tags`, `file_transmittals`, `file_trash`, `plan_room`, `signing`, `record_publishing`, `source_data`, `authority_submission`.
- **Collaboration:** `collaboration`, `collaboration_locks`, `global_presence`, `meetings`, `rfi`, `submittals`, `notifications`, `contacts`, `interface_management`, `deadlines`.
- **AI/Search:** `ai_agents`, `search`, `retrieval`, `erp_chat` (refocused — §7).
- **Ops/assets:** **`asset_management`**(new, replaces/absorbs `assets`), `closeout`, `prefab`, `accommodation`, `service`?, `carbon`?/`esg`? (keep if sustainability tracking required), `property_dev`? (drop unless real-estate sales needed), `crm`?/`webhook_leads`?/`portal` (keep `portal` for client read-only; `crm` optional).
- **Platform:** `projects`, `users`, `teams`, `validation`, `eac` (repurposed to physical quantity rules, §4), `approval_routes`, `enterprise_workflows`, `pipelines`, `jobs`, `saved_views`, `integrations` (+ **new P6/ERP ingestion**, §5), `backup`, `uploads`, `resumable_uploads`, `client_errors`, `admin`, `module_builder`, `onboarding`, `cases`.

---

## 5. Dependency-impact & adaptation (the "check it doesn't break another" work)
Removing core modules breaks references held by **kept** modules. Each is adapted, not left dangling:

| Removed | Kept modules that referenced it | Adaptation |
|---|---|---|
| **`boq`** (`boq_position_id` everywhere) | `progress`(boq_position_id), `schedule`(boq_position_ids on activities), `site_inventory`(boq_position_id), `site_logistics`(delivery_line.boq_position_id), `resource_summary`, `takeoff`(removed), `tasks`(no), `variations`(boq snapshots) | Replace `boq_position_id` with a lightweight **Scope Item** anchored to **Asset/RSW** (§6). "Progress against BOQ items" → **progress against scope items / RSW**. `variations` BOQ-snapshot features drop; keep site-measurement + daywork. |
| **`costs`/`assemblies`/`price_index`/`fx`** | `supplier_catalogs`(vendor prices, self-contained), `cost_recovery` | No internal cost DB: operational modules carry **physical quantities**, not rates. Any unit-cost shown comes from vendor price lists or ERP mirror, not `costs`. `fx` gone → single-currency project setting. |
| **`contracts`** (`contract_id`/`contract_line_id`) | `subcontractors`(prime_contract_id, contract_id), `cost_recovery`, `variations`(affected_contract_id), `payment_clock`(removed) | Replace contract FKs with an **external contract reference** (string/external key). Progress-claim/SOV features drop; keep retention (physical) + ratings. |
| **`finance`/`gl`/`banking`/`sales`/`receivables`** | `costmodel`, `full_evm`, `cvr`(removed), `recurring`(removed), `einvoice`(removed) | All monetary posting removed. `costmodel`/`full_evm` → **physical EVM only**: Earned **Quantity**, crew hours, PPC, variance vs imported P6 targets — no currency/tax/revenue. Append-only **operational ledgers** (site measurements, dayworks, delivery dockets) replace double-entry. |
| **`items`/`inventory`** | `sales`(removed), `cutting`(new), `site_inventory` | Warehouse stock → **`site_inventory`** + basic supplier stock. `cutting` writes to `site_inventory`, not `inventory`. |
| **`payroll`/`labor_rates`** | `field_time`(feeds payroll), `certified_payroll`(removed) | `field_time` keeps **hours** (operational), stops feeding a payroll batch; export hours to **corporate payroll** instead (§5 export). |
| **`eac`/`costmodel` money kernel** | physical EVM | `eac` rules engine **repurposed** to physical quantity/earned-qty rules over canonical data (drop currency/tax). |
| **`i18n_foundation`** | tax configs, country registry, FX | Folded into **Project Settings** (single company): currency, dates, locale, calendars, tax rate as project settings. |
| **`bim_hub`/`cad`/`clash`/`smart_views`** | `construction_control`(bim_element_ref), `punchlist`(clash_result_id), `ncr`(clash_result_id), `markups`(removed), `quantity_link` on BOQ | Drop BIM element refs and clash links from kept modules (make nullable/optional), or replace with a **plain element/location tag**. `construction_control.bim_element_ref` → optional free-text; `punchlist.clash_result_id`/`ncr.clash_result_id` → removed. |
| **`tendering`/`bid_management`/`rfq_bidding`** | none kept | Clean removal (no kept dependents). |

**Rule applied:** where a kept feature's only purpose was the removed capability (e.g., BOQ quantity-link on progress; GL posting on inventory movement; payment applications on subcontractors), the feature is **removed**; where the kept module still has operational value, the reference is **re-pointed** to Asset/RSW/Scope-Item or an external integration key.

---

## 6. NEW MODULE — `asset_management` (Asset Hierarchy & Operations)
Replaces/absorbs the thin `assets` module. The operational backbone of the platform.

- **Identity:** core · prefix=`/assets` · **Depends on:** [projects, users, teams] · **Dependents:** inspections, qms, construction_control, tasks, schedule, progress, documents(CDE), file_*, moc.
- **Purpose:** decompose project scope into a strict 4-level tree and hang execution, quality, files and tasks off it.

### 6.1 Hierarchy (4 levels)
`SITE → AREA → ASSET → RSW (Removable Scope of Work)`.
- **Site** — top physical boundary/facility/route, anchored to the project record (also the "site-only" RBAC scope, §2.7).
- **Area** — functional zone/elevation/parcel/process unit.
- **Asset** — physical deliverable / tag number / system (the primary unit for completion, inspections, documents).
- **RSW** — lowest tactical node under an asset, **segregated by discipline** (Civil, Structural, Mechanical, Electrical, Piping, Instrumentation). **Task-lodgement anchor** (tasks/look-aheads/constraints/crews lodge here) and **quality anchor** (ITPs/inspections lodge at Asset or RSW).

### 6.2 Entities (proposed)
- `oe_asset_node`(project_id, parent_id, level[site|area|asset|rsw], name, code, asset_tag, category_id, type_id, discipline_code, status, **external keys**: wbs_code, activity_id, external_source, external_id, synced_at, planned_start/finish, budgeted_units, primary_resource_id, subcontractor_ref, custom_fields(jsonb), metadata_)
- `oe_asset_category`(project_id, name) · `oe_asset_type`(category_id, name) · `oe_asset_field_def`(type_id, key, label, data_type[string|number|date|select|multiselect|doc_link|entity_link], options, unit, mandatory, constraints(regex/range/required-on-status), p6_mandatory) · `oe_asset_field_value`(asset_id, field_def_id, value)
- `oe_asset_layout`(type_id, layout_json)  ← the Form/Inspection Designer output (cards/accordions/panels/badges)
- `oe_asset_config_pack`(key, name, taxonomy_json, layouts_json, disciplines, itp_templates_json, p6_field_rules) ← default provisioning packs
- `oe_asset_import_job`(project_id, source[p6_xer|p6_xml|excel|manual], filename, status, parsed_summary, errors, created_by) ← ingestion

### 6.3 Ingestion (Path A: P6, Path B: Excel/manual)
- **P6 (.xer / Primavera XML):** async worker (`core/job_runner`) parses WBS→(Site/Area), lower WBS/activity-code groups→Assets, discipline-tagged activity bands/work packages→RSWs. Carries over WBS codes & Activity IDs (**permanent external integration keys**), durations, planned start/finish, budgeted units/quantities, activity codes (discipline/contractor/system). Enforces P6 mandatory fields (WBS_Code/Activity_ID, planned dates, Discipline_Code, Primary_Resource/Subcontractor) on edits for round-trip.
- **Excel/CSV:** bulk-upload wizard mapping flat/tiered sheets → 4-level tree (Site_Name, Area_Name, Asset_Tag, Asset_Category, Asset_Type, RSW_Name, Discipline + custom fields); validates parent-child + mandatory schema, line-by-line errors, atomic provisioning.
- **Manual:** interactive tree construction from the Asset Manager.

### 6.4 Dynamic schema & page designer
- Per **Item Type**, define custom fields (string/number/date/select/multiselect/doc-link/entity-link) with validation + mandatory constraints (incl. P6 fields). The Asset Details page hydrates fields/types/rules from the type's definition.
- The **Form/Inspection Designer** doubles as the **Asset Information Page Designer** (drag-drop multi-column cards, collapsible accordions, read-only spec panels, status badges — no code).

### 6.5 Asset Details tabs (modular)
`[Overview]` (designer cards + metadata + status) · `[RSWs]` (sub-work-packages by discipline, status, task metrics) · `[Files]` (CDE: docs, shop drawings, vendor manuals, models, delivery dockets linked to the asset) · `[Inspections & ITPs]` (contextual, §6.6) · `[Tasks]` (1–5 day activities, punch items, constraint walkdowns on child RSWs) · `[MOC]`.

### 6.6 Inspections — contextual vs global
- **Contextual** (Asset Details → Inspections tab): ITPs + runs where `asset_id` = this asset **or its child RSWs**.
- **Global register** (`/inspections`): master filterable table of **all** project quality records with faceted search (Site/Area/Discipline/Status/Inspector/Subcontractor/Date) + batch PDF/Excel export. Backed by the kept `inspections`/`qms`/`construction_control` modules, now **anchored to Asset/RSW** (replacing BOQ anchors).

### 6.7 Provisioning — default configuration packs
New-project wizard offers a **Default Configuration Pack** (e.g., "Industrial Substation & Power"): asset taxonomy (categories/types + field schemas), page templates per type, execution disciplines, pre-built ITPs/inspection templates, P6 sync field-mapping + mandatory-attribute rules. Selectable by System or Project Administrators at creation or later from Project Settings.

- **Features:** [F1] 4-level hierarchy (Site/Area/Asset/RSW) · [F2] P6 XER/XML ingestion (→ async parse) · [F3] Excel/CSV bulk import · [F4] manual tree build · [F5] dynamic categories/types/custom-field schema · [F6] page/layout designer · [F7] modular asset tabs · [F8] contextual + global inspections · [F9] external integration keys (round-trip) · [F10] default configuration packs.

---

## 7. Platform refactor notes (from the pivot narrative)
- **Persistence:** drop `oe_gl_*`/`oe_finance_*`/`oe_einvoice_*` etc.; double-entry balancing triggers → **append-only operational ledgers** (site measurements, dayworks, dockets). Replace hard FKs to removed authoring modules (PO/vendor/schedule-activity) with **composite external integration keys** (`external_source`, `external_id`, `synced_at`). New **ingestion staging** tables (P6 raw/parsed, PO-line mirrors, aggregated safety snapshots TRIFR/LTIFR/open-audits). Blob storage grows (docket photos, signed daywork tickets, field markups, dispute evidence).
- **Backend (FastAPI/async):** new **parser layer** for P6 (.xer/XML) on the job-runner; **OS&D service** computing inbound-PO vs gate-receipt deltas; strip hundreds of endpoints (contracts 260→basic refs, remove rfq 44, remove finance 74); replace transactional updates with **idempotent ingestion webhooks / batch sync** (`POST /api/v1/integrations/p6/import`, `POST /api/v1/integrations/purchasing/po-lines`). `costmodel`/`full_evm` kernels repurposed to **physical** metrics (earned qty, crew hours, PPC, variance).
- **AI/search:** `file_search` OCR refocuses from admin docs to **dockets / signed daywork / receipts** (key-value extraction: docket #, received qty, signatory stamp). `core/vector` embeddings shift to **claims/defence** (correlate weather, diary, delayed receipts, MoC → evidence binders). `erp_chat` tools drop financial actions (`propose_invoice_payment`) for operational ones (`get_open_constraints`, `get_po_delivery_status`, `check_ppc_reliability`, `summarize_claims_timeline`).
- **Client (React/Zustand):** tablet/edge-first; key screens = **Gate Delivery** (barcode/docket capture, +/- qty steppers for short/damaged, signature pad), **Look-Ahead Board** (3–6 wk constraint Kanban → PPC), **Daily Workface Log** (plant hours, headcount, physical qty vs scope). `useOfflineSync()` becomes **tier-1** (IndexedDB buffer + conflict-resolution replay). `navCatalog.ts` collapses to the **4 pillars**.
- **Integration boundaries:** platform owns **no** corporate financial/master-schedule records; owns field ground truth. **Inbound:** PO lines (corporate ERP), XER/XML (P6), KPI sync (corporate safety). **Outbound exports:** progress actuals → P6; receipt confirmations/OS&D + docket images → ERP AP 3-way match; signed daywork + plant/labour logs → procurement/commercial for claim verification.

---

## 8. Revised module count (indicative)
Starting 189 → remove ~95 (estimating/cost/BIM/tendering/finance/regional/accounting-core/i18n) → keep ~94 operational/platform → add 2 (`cutting`, `asset_management`) + a P6/ERP ingestion surface in `integrations` → **~96 modules**, reshaped around 4 pillars. (Exact count depends on the optional keeps flagged with `?` in §4 — confirm those.)

## 9. Open decisions (needs your call)
- **D1:** Keep or drop the optional-`?` modules in §4 (carbon/esg/service/property_dev/crm/webhook_leads/project_controls/value).
- **D2:** `field_time` — export hours to corporate payroll (recommended) vs keep a light internal payroll draft.
- **D3:** Scope Item model — is it a thin new entity, or do RSWs *are* the scope items? (recommend: RSW is the lowest scope/progress anchor; a Scope Item is optional sub-RSW granularity.)
- **D4:** `project_controls`/`value` — keep as physical-only boards or drop.
- **D5:** P6 round-trip — one-way ingest + export, or true bi-directional sync with conflict rules?

---

*Revised plan applied to the spec set @ `a96d525`. New specs: `cutting`, `asset_management`, and basic `procurement`/`supplier_catalogs`/`subcontractors`/`resource_summary`. Removals + adaptations per §3/§5. Companion: `MASTER-REDEVELOPMENT-PLAN.md` (original full-ERP plan) and the `spec/` batches (mark removed modules struck when you finalise).*
