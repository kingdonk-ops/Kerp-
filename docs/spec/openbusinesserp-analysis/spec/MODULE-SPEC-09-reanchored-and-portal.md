> ## ⚠ Field-Ops pivot status (authoritative: ../FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md)
> **ADAPTED (this batch supersedes the as-built specs in batches 04/05/07a for these modules):** `progress` (physical, Asset/RSW/Item-anchored), `schedule` (P6-synced, RSW-linked), `variations` (re-anchored off RSW/Asset + external contract ref), `costmodel` → **`costmodel` (physical)** (money stripped, physical EVM). · **PORTAL:** full phased spec v1→v2→v3 (D8). · **ANCHOR MODEL (D3):** `boq_position_id` is **removed everywhere**; the lowest **work** anchor is the **RSW** (`oe_asset_node` at `level='rsw'`), the lowest **inspection** anchor is the **Item** (`oe_asset_item`, incl. welds). All former BOQ links become Asset/RSW/Item links; all contract/GL links become **external integration keys** (`external_source`,`external_id`,`synced_at`).

# OpenBusinessERP — Module Specs · Batch 9: Re-anchored kept modules + Portal (phased)

> Editable specs (format in `MODULE-SPEC-00`). These are the **field-ops target** definitions. The as-built columns (BOQ-anchored) remain in batches 04/05/07a for history; where a field differs, the **target** here wins. New/changed fields are marked **[+]** (added) / **[→]** (re-pointed) / **[-]** (dropped). Verification routes through `run_checks()` (§5 of the Final Plan). Grounded @ `a96d525`.

**Modules (4 adapted + 1 portal):** `schedule`, `progress`, `costmodel` (physical), `variations`, `portal`.

---

## 1. The re-anchoring in one picture

```
Site ─ Area ─ Asset ─ (sub-Asset…) ─ RSW ───────────────┐   ← oe_asset_node (recursive, level enum)
                                      │                   │
                                      └─ Item (valve/weld/structural…)   ← oe_asset_item  (lowest INSPECTION anchor)
                                                          │
  schedule.activity  ──[activity_id / wbs_code]──►  RSW   │   ← lowest WORK anchor
        │  (external P6 keys: external_source='p6')        │
        ▼                                                  ▼
  progress.entry ──(earned quantity against RSW/Item)──► physical EV
        │                                                  │
        ▼                                                  ▼
  costmodel(physical).control_account ──(crew hours, PPC, earned qty)──► SPI/physical-CPI (NO money)
        │
        ▼
  variations.request ──(measured delta on RSW/Asset qty)──► external_source='erp' (commercial sits outside)
```

**Three substitutions applied to every entity below**

| As-built | Field-ops target | Why |
|---|---|---|
| `boq_position_id` (FK → `oe_boq_position`) | `rsw_node_id` (FK → `oe_asset_node`, `level='rsw'`) **or** `asset_item_id` (FK → `oe_asset_item`) | `boq` removed; Asset/RSW/Item is the ground-truth anchor (D3). |
| `contract_id` / `payment_application_id` / any GL FK | `external_source` + `external_ref` (+ `synced_at`) | commercial/finance authored outside; we hold a mirror key, not a hard FK. |
| money columns (`planned_amount`, `earned_amount`, `cpi`, `cash_flow`…) | physical columns (`planned_quantity`, `earned_quantity`, `crew_hours`, `ppc_percent`) | platform owns physical ground truth; corporate owns money. |

---

## 2. schedule — field programme, P6-synced, RSW-linked
- **Identity:** core · prefix=`/schedule` · routes≈110 (−commercial/cost routes) · **Depends on:** [projects, **asset_management**] · **Dependents:** progress, costmodel(physical), commissioning, variations (dates), portal(read)
- **Entities:**
  - `_schedule`(name, schedule_type, start/end_date, data_date, status, **[+]** `external_source` ('p6'|'manual'), `external_id`, `source_file_ref`, `synced_at`, `sync_direction` enum `import|export|none`)
  - `_activity`(parent_id, wbs_code, start/end_date, duration_days, progress_pct, activity_type, dependencies, resources, **[→]** `rsw_node_ids` (was `boq_position_ids`), early/late_start/finish, total/free_float, is_critical, constraint_type/date, **[-]** ~~`cost_planned/actual`~~, remaining_duration, **[→]** `budgeted/installed_units` (physical earned units, not £), calendar_id, revision, assignee_id, **[+]** `external_activity_id` (P6 Activity ID), `external_wbs_id`)
  - `_work_order`(**[→]** `rsw_node_id` (was `boq_position_id`), `asset_item_ids`, assigned_to, planned/actual_start/end, **[-]** ~~planned/actual_cost~~, `crew_hours_planned/actual`, status)
  - `_relationship`(predecessor/successor_id, relationship_type, lag_days)
  - `_baseline`(snapshot_data, is_active, **[+]** `source` ('p6_import'|'internal'))
  - `_progress`(activity_id, progress_pct, actual_start/finish, submitted/approved_by) — *authored by `progress`; mirror for CPM recompute*
  - `_eac_link`(task_id, rule_id, predicate_json) — *now drives physical EAC (§costmodel)*
  - `_progress_entry`(progress_percent, photo_attachment_ids, geolocation, device, actual_start/finish_date)
  - **[+]** `_p6_import_job`(file_ref, format `xer|xml`, status, parsed_activities, parsed_relationships, mapping_report, unmatched_wbs, error) — P6 ingest on `core/job_runner`
  - **[+]** `_p6_export`(scope, generated_file_ref, fields `['PhysPercentComp','ActStart','ActFinish','RemDur']`, data_date, status, downloaded_at) — **D5 round-trip** out
- **Endpoints (added/changed):** `POST /schedule/import-p6` (upload .xer/.xml → `_p6_import_job`) · `GET /schedule/import-p6/{id}` · `POST /schedule/{id}/export-p6` (build `_p6_export`) · `GET /schedule/export-p6/{id}/download` · `POST /activities/{id}/link-rsw` · `GET /rsw/{node_id}/activities`.
- **Key logic / state:**
  - **P6 import:** parse .xer/XML → activities + relationships + WBS; **match WBS→Asset/RSW** by `external_wbs_id` or name rules; unmatched rows surfaced in `mapping_report` for manual link; re-import is idempotent on `external_activity_id`.
  - **CPM** (floats, critical path, constraints, calendars) still computed internally so the field board stays live between P6 syncs.
  - **P6 export (round-trip):** collect physical actuals from `progress` (percent-complete + actual dates + remaining duration), write a P6-shaped .xer/XML keyed on `external_activity_id`; **progress actuals only** (not logic/structure) so re-import into the client's P6 is non-destructive.
  - **No cost on activities** — `crew_hours` replaces `cost_*`; money EVM lives in the client's system.
- **Verification:** `schedule.p6_import` (every imported activity resolves to a WBS/RSW or lands in `unmatched`), `schedule.p6_export` (every exported activity has an `external_activity_id`; no new activities invented on export).
- **Features:** [F1] schedules + WBS activities · [F2] CPM (floats/critical/constraints) · [F3] relationships (lag) · [F4] calendars · [F5] baselines (incl. P6-imported) · [F6] work orders (RSW/Item, crew-hours) · [F7] field progress mirror (geo/photo) · [F8] **Asset/RSW links (was BOQ/BIM)** · [F9] EAC rule links (physical) · [F10] **P6 import** · [F11] **P6 export round-trip (D5)**.
- **Removal/failure impact:** core delivery backbone; progress/physical-EVM lose structure; no P6 interchange. If `asset_management` absent, activities cannot anchor work → progress earned-quantity breaks.
- **Edit notes:** _P6 field mapping on export is config (`fields` list) — extend without code. Schedule logic (relationships/WBS) is deliberately **not** exported, to keep the client's P6 authoritative on structure._

---

## 3. progress — physical site-execution measurement (Asset/RSW/Item earned quantity)
- **Identity:** core · routes≈18 · **Depends on:** [projects, **asset_management**, schedule] · **Dependents:** costmodel(physical), eac(physical), schedule (actuals → P6 export), portal(read)
- **Entities:**
  - `_entry`(**[→]** `rsw_node_id` **or** `asset_item_id` (was `boq_position_id`), `activity_id` (link to `schedule._activity`), period_label, **[→]** `earned_quantity` + `quantity_unit` **and** `percent_complete`, `crew_hours` **[+]**, recorded_by/at, geo_lat/lon, photos, `method` enum `units|milestone|percent`, `status` enum `draft|submitted|verified`)
  - `_plan`(**[→]** `rsw_node_id`/`asset_item_id`, period_label, planned_pct, `planned_quantity` **[+]**)
  - **[+]** `_milestone`(`rsw_node_id`, label, weight_pct, reached_at, evidence_ref) — rules-of-credit / milestone earning for non-linear work
  - **[+]** `_quantity_claim`(entry_id, claimed/verified_quantity, verifier_id, verified_at, finding_ref) — verification trail when a claim is checked against inspection acceptance
- **Endpoints:** `POST /progress/entry` · `POST /progress/entry/{id}/verify` · `GET /rsw/{node_id}/progress` · `GET /asset-item/{id}/progress` · `GET /progress/earned?project=…&period=…` (physical EV feed).
- **Key logic / state:**
  - **Earned quantity, not just %:** an entry books `earned_quantity` against an RSW (or Item); `percent_complete` derived from `earned/planned` or from `_milestone` weights (rules-of-credit).
  - **Caps at the anchor's installed quantity** — cannot earn beyond the RSW/Item's defined scope quantity (prevents over-claim).
  - **Verification gate (`rsw.complete` / `inspection.close`):** an RSW cannot read 100%/complete while it has open required Items/inspections or a blocking NCR — `run_checks()` blocks the `verify` transition and logs a finding (`_quantity_claim.finding_ref`).
  - **Feeds** physical EV (earned qty × anchor weight) to `costmodel(physical)` and actual dates/%-complete to `schedule` for the P6 export.
- **Verification:** `rsw.complete` (no open blocking Items/NCRs; earned ≤ planned), `progress.verify` (verifier ≠ recorder where policy requires).
- **Features:** [F1] progress entries **by earned quantity** (per RSW/Item, geo+photo) · [F2] planned-vs-actual (quantity + %) · [F3] **milestone / rules-of-credit earning** · [F4] **claim verification against inspection acceptance** · [F5] crew-hours capture (feeds productivity) · [F6] caps at anchor scope.
- **Removal/failure impact:** no physical progress → no earned value, no P6 actuals export, no PPC input. If anchor Item is a weld, progress to "complete" is gated by `wps` weld acceptance (R-WPS-4).
- **Edit notes:** _`method` per anchor type is config: structural steel by units, concrete by m³, welds by milestone (fit-up/weld/NDE). Weighting lives on the Asset/RSW record, not here._

---

## 4. costmodel (physical) — physical control accounts & physical EVM (money stripped)
- **Identity:** core · prefix=`/costmodel` · routes≈66 (money routes removed) · **Depends on:** [projects, **asset_management**, progress, schedule] · **Dependents:** eac(physical), project_controls, reporting, portal(read)
- **Entities:**
  - `_control_account`(code, classification_standard, **[→]** `rsw_node_id`/`asset_subtree_root_id` (was BOQ grouping), `responsible_id`)
  - **[→]** `_work_package` (was `_cost_line`)(control_account_id, code, unit, **[→]** `rsw_node_id`/`asset_item_id`, **[→]** `planned_quantity`/`earned_quantity` (was estimate_quantity/amount), `crew_hours_budget`)
  - **[→]** `_physical_snapshot` (was `_snapshot`)(period, **[→]** `planned_quantity`, `earned_quantity`, `installed_quantity`, `spi`, `physical_cpi` (= earned_qty / crew_hours-normalised, **not money**), **[-]** ~~forecast_eac (money)~~ → `forecast_quantity_at_completion`)
  - **[-]** ~~`_budget_line`~~ (planned/committed/actual/forecast/earned **amount**) → **removed** (money)
  - **[-]** ~~`_cash_flow`~~ (planned/actual inflow/outflow) → **removed** (money; corporate system owns cash)
  - `_labour_worker_day`(work_date, resource_id, source_module, hours) — **kept** (physical input to productivity)
  - **[+]** `_productivity`(control_account_id, period, earned_quantity, crew_hours, `unit_rate_achieved` (qty/hour), `target_rate`, variance_pct)
- **Endpoints:** `GET /costmodel/control-accounts` · `GET /costmodel/physical-snapshot?period=…` · `GET /costmodel/productivity?ca=…` · `POST /costmodel/rebuild-snapshot` (recompute from progress + worker-days).
- **Key logic / state:**
  - **Physical EVM only:** PV/EV/AC become **planned-qty / earned-qty / installed-qty**; SPI = earned/planned (qty); **physical CPI** = earned-qty per crew-hour vs target (productivity), never a money ratio.
  - **Control accounts roll up the Asset subtree** (Site→Area→Asset→RSW), replacing BOQ grouping; a CA = a responsible-owned slice of the hierarchy.
  - **Overrun → productivity alert** (achieved rate below target), not a cost-overrun alert.
  - **No cash flow, no budget money** — `eac(physical)` forecasts **quantity/duration at completion**, and hands money forecasting to the external corporate system via `external_source`.
- **Verification:** `costmodel.snapshot` (earned ≤ installed ≤ planned per CA unless flagged; every CA resolves to a live Asset subtree).
- **Features:** [F1] **physical** control accounts (Asset-subtree) · [F2] work packages (RSW/Item, qty + crew-hours) · [F3] **physical** EV snapshots (SPI / physical-CPI) · [F4] **productivity** (qty per crew-hour vs target) · [F5] labour worker-days · [F6] quantity-at-completion forecast (via eac). **[-]** ~~cash flow~~ ~~money budget lines~~ ~~money EAC/overrun~~.
- **Removal/failure impact:** no physical EVM/productivity; `project_controls` loses its physical feed. Money EVM is **intentionally** absent (out of scope — delegated to corporate system).
- **Edit notes:** _`eac`/`full_evm` are re-pointed the same way: `full_evm._measure` keeps PV/EV/AC **as quantities**; `_baseline.bac` becomes **budget-at-completion in units/hours**, not £. If a client wants money EVM, feed earned-qty out via `external_source` and let their system price it._

---

## 5. variations — field measurement & change capture (re-anchored; commercial sits outside)
- **Identity:** business · routes≈160 (authoring kept; pricing/final-account money → external) · **Depends on:** [projects, **asset_management**, progress] · **Dependents:** claims/evidence (AI binders), portal(future co-sign), external commercial system
- **What changes:** variations **captures the physical change on site** (instruction, measured quantity, daywork, disruption/EOT evidence) and **exports** it to the corporate commercial system for pricing & final account. The money-valuation and contract-administration half is **mirrored, not owned**.
- **Entities (12, re-anchored):**
  - `_notice`(ref, title, status, raised_by, **[+]** `external_source`,`external_ref` (client change-ref))
  - `_request`(classification, **[-]** ~~agreed_cost_impact~~ → `agreed_cost_impact` **mirror** (read-only from external), `contract_clause_ref` (text, not FK), **[→]** `rsw_subtree_ref`/`asset_item_refs` (was `submitted_boq_snapshot_id`))
  - **[→]** `_asset_qty_trace` (was `_boq_trace`)(change_kind, `rsw_node_id`/`asset_item_id`, qty_before/after)
  - `_order`(+`_cost_impact` **mirror**, `_schedule_impact` (owned — days, links `schedule._activity`))
  - `_site_measurement`(**[→]** `rsw_node_id`/`asset_item_id` (was boq), measured_quantity, quantity_unit, owner_signature_ref, photos, geo) — **owned, primary field artifact**
  - `_daywork_sheet`(+`_daywork_line`: labour/plant/material **quantities & hours**; rates **mirror**) — **owned** (signed on site)
  - `_disruption_claim`(baseline/impacted_productivity (from costmodel physical), labour_hours_lost) — **owned** (physical evidence)
  - `_eot_claim`(tia_delta_days, delay_analysis_id → `schedule_advanced._delay_analysis`) — **owned** (schedule evidence)
  - **[→]** `_final_account`(variations/daywork/claims total **mirror**, retention **mirror**) — **read-only mirror** from external; we do not compute money
- **Endpoints (changed):** `POST /variations/{id}/measure` (signed site measurement on RSW/Item) · `POST /variations/{id}/export` (push measured qty + daywork + evidence to external commercial via `external_source`) · `GET /variations/{id}/mirror` (pull back priced values). Pricing endpoints become **read-through** to the mirror.
- **Key logic / state:**
  - **Physical capture is authoritative** (measured quantity on an RSW/Item, signed, photo, geo); money is a mirror synced from the corporate system.
  - **Disruption/EOT evidence** is assembled from owned physical data (productivity from `costmodel(physical)`, delay windows from `schedule_advanced`) → feeds the AI **claims/defence binders** (Final Plan §7).
  - `contract_clause_ref` is free text + `external_ref`, not a FK to a removed `contracts` module.
- **Verification:** `variation.measure` (measured qty references a live RSW/Item; signed by an authorised owner), `variation.export` (no export of money we don't own; measured qty within anchor scope).
- **Features:** [F1] variation notices (+external client ref) · [F2] requests (**Asset/RSW trace**, clause text) · [F3] variation orders (schedule impact owned; cost impact mirror) · [F4] **signed site measurements on RSW/Item** · [F5] daywork sheets/lines (quantities/hours owned; rates mirror) · [F6] disruption claims (physical productivity evidence) · [F7] EOT claims (TIA from schedule_advanced) · [F8] **export to commercial + priced mirror** (was owned final account).
- **Removal/failure impact:** no field change capture → claims lose their physical evidence base; corporate commercial loses signed measurement/daywork feed.
- **Edit notes:** _Everything money is a **mirror** (`external_source`); to make the platform own pricing again, promote the mirror fields to owned and re-add a rate source. Measured-quantity and daywork capture are the pieces worth keeping on the edge device (offline-first)._

---

## 6. portal — client access, phased v1 → v2 → v3 (D8, full spec)

- **Identity:** core · prefix=`/portal` · **Depends on:** [projects, asset_management, inspections, itp, ncr, signing, documents, review_authority, meetings] · **Access:** existing `portal` magic-link + per-resource access rules, **scoped to the client's project(s)**; every client action routes through `run_checks()` and the central audit log.
- **Phasing contract:** each phase is independently shippable; v2/v3 add entities and endpoints but **reuse** `signing`, `ncr`, `review_authority`, `meetings` and the verification engine — no parallel implementations.

### v1 — read-only (shipped / near-term)
- **Capabilities:** clients **view** the Asset hierarchy (Site→Area→Asset→sub-Asset→RSW→Items) and **all registers read-only** (inspections, ITP, NCR, punchlist, weld register, documents).
- **Entities:** `_access_grant`(client_contact_id → `contacts`, project_id, scope `['assets','registers','documents']`, `asset_subtree_root_id` (optional narrowing), expires_at, magic_link_token). *(No write entities.)*
- **Endpoints:** `GET /portal/assets`, `/portal/registers/{type}`, `/portal/documents` — all filtered by grant scope; `GET /portal/me`.
- **Verification:** `portal.access` (every read filtered to granted project + subtree; no cross-tenant leakage → 404).
- **Features:** [F1] magic-link client access (scoped) · [F2] Asset hierarchy viewer · [F3] all registers read-only · [F4] document viewer (current revisions only).

### v2 — co-sign reports & ITPs
- **New capability:** client **counter-signs** completed inspection reports and ITPs (a second, client-side signature), via `signing`.
- **Entities (added):**
  - `_cosign_request`(target_kind `report|itp`, target_id, client_contact_id, status `pending|signed|declined`, requested_by/at, due_at)
  - `_cosign_record`(request_id, **signing_signature_id** (FK → `signing`), decision, signed_at, comment, declined_reason)
- **Endpoints:** `POST /portal/cosign/request` (internal raises it) · `GET /portal/cosign/pending` (client) · `POST /portal/cosign/{id}/sign` · `POST /portal/cosign/{id}/decline`.
- **State machine:** `pending → signed` (writes a `signing` signature, stamps the report/ITP as client-cosigned) | `pending → declined` (reason required; notifies internal; the report stays "internally complete, client-declined").
- **Verification (`report.cosign` / `itp.cosign`):** target must be **internally complete/approved** before a co-sign request can issue (no co-signing a draft); the signer must be the **granted client contact**; a declined co-sign **blocks** the "client-accepted" status and logs a finding. ITP co-sign additionally requires all hold/witness points on the ITP to be released.
- **Features:** [F5] co-sign requests (report/ITP) · [F6] client counter-signature via `signing` · [F7] decline with reason + internal notify · [F8] co-sign status stamped on the register.

### v3 — visit scheduler + client NCR input/sign-off
- **New capability A — visit scheduler:** clients request/organise **site visits** for **hold-point witness** and **final sign-off**, feeding `review_authority` hold-gates and `meetings`.
- **New capability B — client NCRs:** clients **raise** NCRs and **sign off** NCR closure, through `ncr`.
- **Entities (added):**
  - `_visit_booking`(purpose `hold_point_witness|final_signoff`, `review_gate_id` (FK → `review_authority`) **or** `rsw_node_id`/`asset_item_id`, requested_by (client), proposed_slots (datetimes), confirmed_slot, status `requested|confirmed|completed|cancelled`, `meeting_id` (FK → `meetings`), attendees, outcome_ref)
  - `_client_ncr_link`(ncr_id (FK → `ncr`), client_contact_id, role `raiser|signoff`, action `raised|signed_off|rejected`, at, comment) — the NCR itself lives in `ncr`; this records the client's portal action
- **Endpoints:** `POST /portal/visits/request` · `GET /portal/visits` · `POST /portal/visits/{id}/confirm` (internal) · `POST /portal/visits/{id}/complete` (outcome) · `POST /portal/ncr` (client raises → creates `ncr` record `source='client'`) · `POST /portal/ncr/{id}/signoff`.
- **State machines:**
  - *Visit:* `requested → confirmed` (internal picks a slot, creates a `meetings` entry) `→ completed` (outcome recorded; if `purpose=hold_point_witness`, releasing the gate is gated by `review_authority`) `| cancelled`.
  - *Client NCR:* `raised (source=client) → [standard ncr lifecycle] → closure`; client **sign-off** on closure is gated by `ncr.signoff` (closure evidence present, no open corrective actions).
- **Verification:**
  - `portal.visit` (a `hold_point_witness` visit must reference a **real open hold gate**; completing it does **not** itself release the gate — `review_authority` + `inspection.close` do).
  - `ncr.signoff` (client sign-off only after internal closure evidence is complete; a client **rejection** reopens the NCR and logs a finding).
- **Features:** [F9] visit scheduler (hold-point witness / final sign-off) · [F10] visits feed `review_authority` gates + `meetings` · [F11] client-raised NCRs (`source=client`) · [F12] client NCR closure sign-off (gated) · [F13] client NCR rejection reopens.
- **Removal/failure impact (whole module):** clients lose self-service visibility and co-sign/visit/NCR participation; internal teams revert to issuing reports/NCRs out-of-band. v2/v3 degrade gracefully to v1 (read-only) if `signing`/`review_authority`/`ncr` are absent.
- **Edit notes:** _Co-sign and NCR actions **never** create a second source of truth — they write through `signing`/`ncr`. The scheduler is deliberately lightweight (booking + `meetings` link); it is **not** a resource calendar. All three phases share one `_access_grant` scoping model, so narrowing a client to a sub-tree limits reads **and** writes._

---

## 7. Cross-module effects of the re-anchoring (checklist)

| Consumer | Was | Now |
|---|---|---|
| `site_inventory` | consumed against BOQ position | consumes against RSW/work-order (schedule) + `cutting` movements |
| `eac` / `full_evm` | money BAC/EV/AC | **quantity/hours** BAC/EV/AC (physical) |
| `project_controls` | cost+schedule+risk rollup | **physical** cost-proxy (productivity) + schedule + risk |
| `inspections` / `itp` | anchored to BOQ/BIM element | anchored to **Item / RSW** (D3) |
| `reporting` KPI snapshot | `budget_consumed_pct`, CPI (money) | `earned_quantity_pct`, SPI, physical-CPI, PPC |
| `erp_chat` tools | financial actions | `get_open_constraints`, `get_po_delivery_status`, `check_ppc_reliability`, `summarize_claims_timeline` |
| commercial/finance | owned in-platform | **external** (`external_source`), mirrored for display only |

---

*Batch 9 complete — re-anchored `schedule`/`progress`/`costmodel`(physical)/`variations` + full phased `portal` (v1→v2→v3). Supersedes the BOQ-anchored as-built specs for these five modules. Grounded @ `a96d525`; authoritative scope: `../FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md`.*
