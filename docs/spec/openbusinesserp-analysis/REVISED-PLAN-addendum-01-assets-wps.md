# Revised Plan — Addendum 01: Module decisions, Asset/Item model & WPS/Weld register

> Extends `REVISED-PLAN-field-operations.md`. Resolves the open decisions and adds the asset-component / welding-quality model. Everything verifiable is routed through the central **rules / verifications / checks** engine (§5).

---

## 1. Module-keep decisions (resolves §9 D1/D4)
- **Keep:** `carbon`, `esg`, `service`, `project_controls`.
- **Remove:** `value`.
- **`crm` → redefined as a basic People/Company directory** (see §2). **No sales pipeline** (drop leads/opportunities/stages/forecast/win-loss/activities-as-pipeline).
- Net effect on §4 "KEEP": add back `carbon`, `esg`, `service`, `project_controls`, `crm`(basic); drop `value` from the kept set.

## 2. `crm` (basic) — People & Company directory
- **Identity:** business → effectively a **relationship/notes layer over `contacts`**. prefix=`/crm` (or fold into `/contacts`). **Depends on:** [contacts, users, file_versions].
- **Purpose:** every person or company **contacted in the system** lives here — clients, contractors, consultants, suppliers, **staff** — with **notes and files**. Not a pipeline.
- **Entities (kept/added, trimmed from the full CRM):**
  - keep `oe_crm_account`(company: name, industry, country, website, parent_account_id, tags, owner_user_id, status) as the **company** record.
  - keep `oe_crm_activity` **only as a notes/interaction log** (kind, subject, body, author, date) — not pipeline stages.
  - **drop** `oe_crm_pipeline_stage(_config)`, `oe_crm_win_loss_reason`, `oe_crm_lead`, `oe_crm_opportunity(_stage_history)`, `oe_crm_forecast`.
  - **add** file attachment (via `file_references`/`file_versions`) on account + contact.
- **Relation to `contacts`:** `contacts` remains the canonical **person** master (incl. `is_platform_user`/`user_id` for staff); `crm` groups them under companies and holds **notes + files + tags**. Recommendation: one directory — a person appears once in `contacts`, optionally linked to a company account, with notes/files.
- **Features:** [F1] companies · [F2] people (clients/contractors/staff) via contacts · [F3] notes/interaction log · [F4] files (versioned) · [F5] tags/classification. *(Removed: pipeline, leads, opportunities, forecasting.)*

---

## 3. Asset model extensions (extends `asset_management` §6)

### 3.1 Recursive sub-assets
- An **Asset can have sub-Assets** (recursive, any depth), and a **sub-Asset can also have RSWs**.
- **Model change:** `oe_asset_node` already has `parent_id`. Relax the strict 4-level rule to: `site → area → asset → (asset…)* → rsw`, i.e. an `asset`-level node may parent another `asset`-level node (sub-asset), and **any asset or sub-asset may parent RSWs**. Add `path`/`depth` for tree queries; keep `level` for the primary four, with sub-assets carrying `level=asset` + `is_sub_asset=true`.
- Tasks/look-aheads/constraints/crews still lodge at **RSW**; quality (ITP/inspection) lodges at **Asset, sub-Asset, RSW, or Item** (§3.3).

### 3.2 Items on an asset (components)
- An **Asset (or sub-Asset) can have Items** — physical components that form part of inspection: **valves, welds, structural items**, etc.
- **New entity** `oe_asset_item`(asset_id, item_type[valve|weld|structural|instrument|support|…], tag/number, spec_ref, discipline, parent_item_id?, custom_fields(jsonb per type schema), status, inspectable(bool), rsw_id?, metadata_).
  - Reuses the **dynamic type/field schema** (`oe_asset_field_def`/`_value`, §6.4) so item types (Valve, Weld, Beam…) carry their own fields and validation.
  - An item may be tied to an RSW (the discipline work package that installs/tests it).
- **Items are inspectable:** inspections/ITPs (`inspections`/`qms`/`construction_control`) anchor to `asset_item` as well as asset/RSW (extends §6.6 — contextual tab lists item-level inspections; global register gains an Item column/filter).
- **Features (asset_management additions):** [F11] recursive sub-assets · [F12] asset items (typed components) · [F13] item-level inspection anchoring.

### 3.3 Inspection anchoring (updated)
Quality records may be lodged at: **Asset · sub-Asset · RSW · Item (incl. Weld)**. The global `/inspections` register filters by any level; the contextual Asset-Details → Inspections tab aggregates the asset + its descendants (sub-assets, RSWs, items).

---

## 4. NEW MODULE — `wps` (Welding Procedure Specifications & Welder Qualification)
Quality-critical register linking procedures ↔ qualified people ↔ welds ↔ inspection.

- **Identity:** compliance · prefix=`/wps` · **Depends on:** [projects, contacts/users, asset_management, qms/inspections, file_versions, validation]. **Dependents:** inspections (weld inspection).
- **Purpose:** hold the **WPS register**, record which **staff are qualified** to weld to each spec, enforce that a **weld references a WPS** and is assigned **only to a welder qualified to that WPS**, and make welds (and items) **inspectable** under the rules engine.

### 4.1 Entities
- `oe_wps_record`(project_id|library, code, title, welding_process[SMAW|GTAW|GMAW|FCAW|SAW|…], standard_ref[ASME IX|ISO 15614|AWS D1.1|…], base_material_group(P/Group no.), filler_material(SFA/F-no/A-no), position(s), thickness_range_min/max, diameter_range, joint_types, pwht, pqr_ref, revision, status[draft|approved|withdrawn], document_id, approved_by/at, metadata_)
- `oe_welder` (the person as a welder) `(contact_id|user_id, stamp/id, name, employer[staff|subcontractor_ref], status, metadata_)` — or reuse `contacts` with a welder flag.
- `oe_welder_qualification`(welder_id, wps_id, qualified_date, expiry_date, **continuity_last_welded_at**, qualified_range(position/thickness/diameter/material actually qualified — may be a subset of the WPS), test_type, certificate_document_id, status[valid|expired|suspended|revoked], verified_by/at)  ← **staff ↔ WPS "qualified to weld to this spec"**
- `oe_weld` (a weld is an **asset Item** of type `weld`; either `oe_asset_item` with `item_type=weld` + these fields, or a dedicated table FK'd to `asset_item`) `(asset_item_id|asset_id, weld_number, wps_id (**required**), welder_qualification_id|welder_id (**required, must be qualified to wps_id**), joint_type, size/diameter, thickness, material, nde_required(bool), nde_types[VT|PT|MT|RT|UT], status[fit-up|welded|nde_pending|accepted|rejected|cut-out], welded_at, metadata_)
- `oe_weld_nde_result`(weld_id, nde_type, performed_at, technician, result[accept|reject], report_document_id)  (may reuse `construction_control._test_result`).

### 4.2 Enforcement (the rules this module guarantees)
These are **verifications**, enforced through the central engine (§5), not ad-hoc code:
- **R-WPS-1:** a `weld` must reference an **approved** `wps_record`.
- **R-WPS-2:** a `weld`'s assigned welder must hold a **`welder_qualification` for that `wps_id` that is valid** (not expired/suspended/revoked) **and covers** the weld's position/thickness/diameter/material (qualification range ⊇ weld parameters).
- **R-WPS-3:** **continuity** — the welder's qualification continuity window (e.g., ≤ 6 months since `continuity_last_welded_at`) must not have lapsed at the weld date.
- **R-WPS-4:** a `weld` cannot move to **accepted** until required NDE results are **accept** and any hold/witness points are released (ties to `construction_control` hold gates).
- Attempting to assign an unqualified welder → **blocked with a finding** (same pattern as OBE completion gates: 422 + finding), surfaced in the UI.

### 4.3 Features
[F1] WPS register (process/standard/material/position/thickness ranges, PQR ref, revisions, approval) · [F2] welder records · [F3] **welder qualifications** (staff↔WPS, range, expiry, continuity, certificate) · [F4] **welds** (as asset items) requiring WPS + qualified welder · [F5] **enforced welder↔WPS qualification** (range + validity + continuity) · [F6] NDE results + acceptance gating · [F7] weld/item **inspection** via qms/inspections · [F8] WPS/qualification documents (versioned).

### 4.4 Interactions / adaptation
- **asset_management:** welds/items hang off assets/sub-assets (§3); the global `/inspections` and Asset-Details Inspections tab include weld-level records.
- **qms/inspections/construction_control:** provide the inspection runs, ITP hold points and NDE/test results for welds and items.
- **credentials:** the general professional-credentials registry (kept) handles non-welding tickets; `wps` is the **weld-specific** qualification layer (could be modelled as a specialisation of `credentials`, but kept distinct because of the WPS-range matching logic).

---

## 5. Verifications run through the rules/verifications/checks engine
All the above checks (welder↔WPS qualification + range + continuity, certificate/credential validity, inspection/NDE acceptance, source-data prerequisites, P6 mandatory fields on asset/RSW, custom-field constraints) are **not** bespoke per-module code — they are **rules evaluated by the central verification engine**, reusing OBE's existing machinery:
- **`validation`** (rule sets over any target) + **`eac`** rules engine (repurposed to physical/quality predicates) + the **completion-gate pattern** (`core/validation` gates that block a state transition and return findings) + **`compliance`/`compliance_ai`** DSL for author-defined checks.
- **Pattern:** a verifiable action (accept a weld, close an inspection, mark an RSW complete, confirm a cutting plan, receive goods) calls `run_checks(context, rule_set)`; any **error** finding blocks the transition with a surfaced finding; **warnings** are advisory. Rules are **data** (rule sets/DSL), so new checks (e.g., a client-specific NDE frequency) are configuration, not code.
- **Registry:** define a rule set per verifiable entity — `weld.accept`, `welder.assign`, `inspection.close`, `rsw.complete`, `goods.receive`, `cutting.confirm`, `asset.status_change` — so verification is uniform and auditable (findings logged).

---

## 6. Updated decisions status
- **D1 (optional keeps):** carbon ✅ keep · esg ✅ keep · service ✅ keep · crm ✅ keep (basic directory) · project_controls ✅ keep · **value ❌ remove** · property_dev/webhook_leads/portal/accommodation/prefab — *still your call* (default: keep `portal` read-only, drop `property_dev`).
- **D3 (scope anchor):** RSW remains the lowest **work** anchor; **Items** (incl. welds) are the lowest **inspection** anchor under an asset/sub-asset.
- New open point **D6:** model `wps` as its own module (recommended, above) vs. a specialisation inside `credentials`+`asset_management`. Default: **own module**, tightly integrated.

---

*Addendum 01 to the Field-Operations revised plan @ `a96d525`. New: `wps` module; extended `asset_management` (sub-assets, items, item/weld inspection); `crm` redefined as a basic people/company directory; `value` removed; carbon/esg/service/project_controls kept; all verifications routed through the central rules engine.*
