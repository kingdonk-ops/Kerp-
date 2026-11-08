# Feature Scout — review

**TL;DR.** KERP's spec is already remarkably deep — the Project-AIP port (MODULE-SPEC-11) and the workpacks/AWP layer put it *ahead* of most commercial field tools on verification, hash-chained records and delay attribution. So my job is less "add modules" and more "borrow the handful of proven abilities the spec is still missing and that fit the anchor/physical/single-company doctrine." The biggest unclaimed differentiator is **turning `asset_management` + `inspections` into a true asset-integrity engine** (corrosion/thickness loops, condition-based re-inspection, CML history) — nobody in construction field software does this, and it is exactly KERP's industrial/energy niche (borrowed from RBI tools like DNV/Credo and GE APM). Second: **instrument-calibration-gated readings with a traceability chain** is half-specced in `forms`/`eligibility` — finish it, because it is a genuine evidence-grade moat. My top picks are all ENHANCEMENTs of existing modules, not new scope; I flag three borrowed abilities (pay apps, takeoff/estimating, CMMS spare-parts) that would **violate the commercial boundary** and should be refused. Everything below maps a named product's ability → a concrete feature on a named KERP module, with NEW/ENHANCEMENT/ALREADY-SPECCED and effort.

---

## 1 · How KERP already compares (so I don't re-propose what exists)

Before borrowing, the parity picture, because it changes what is worth adding:

| KERP already has, matching / beating the market | Equivalent in leading tools |
|---|---|
| Hash-chained append-only sign-off + event ledger (`inspections` F2, `inspection_requests` F5) | *Stronger than* Procore/Fieldwire, which store edit history but not tamper-evident chains |
| Client-vs-contractor delay attribution from witness no-shows (`inspection_requests` F10–F12) | **No commercial equivalent** — this is novel; Procore/Dalux capture the inspection but not the attributable delay |
| One no-code forms+item_types engine, versioned, immutable revisions (`forms`, `item_types`) | Matches Novade/Fulcrum no-code; versioning immutability beats most |
| Offline-first sync ledger with idempotent `client_op_id` (`field_diary`, `inspections` F1) | Matches Dalux Field / Fieldwire offline [dalux.com, fieldwire.com] |
| Weld register with enforced WPS↔welder-qual↔NDE gating (`wps` R-WPS-1..4) | Matches WeldEye's WPS-compliance + cert-expiry alerts [kemppi.com] |
| AWP EWP→PWP→CWP→IWP constraint engine (`awp`) | Matches InEight/O3; ahead of Procore |
| Forensic delay windows/fragnets → EOT (`schedule_advanced` F8) | Matches the *method* used by HKA/Socotec forensic practice [hka.com, socotec.us] |
| Physical EVM (quantity/crew-hours, no money) (`costmodel`, `full_evm`) | Deliberately narrower than InEight/ProNovos (which carry cost) — correct given the boundary |

So the gaps worth borrowing are concentrated in: **asset integrity / condition monitoring**, **inspection UX niceties**, **document/CDE measurement & compare**, **logistics telemetry**, **safety leading-indicators**, and **analytics packaging**. I work through them by pillar.

---

## 2 · Findings by KERP pillar

Legend: **NEW** = not in spec · **ENH** = enhances an existing feature · **SPEC** = already specced (parity note only). Effort S/M/L per CLAUDE.md sizing.

### 2.1 Asset & Quality backbone — inspections / ITP / NCR / weld (the niche)

| Borrowed ability (product) | Concrete KERP feature | Module | Value | Effort | Status |
|---|---|---|---|---|---|
| **Corrosion/thickness-monitoring loops + CML history** (GE APM RBI 580, DNV/Credo) [gevernova.com, dnv.com] | "Condition Monitoring Location" Item subtype with a time-series of thickness/readings, min-thickness & corrosion-rate calc, remaining-life flag → NCR | `asset_management` + `inspections` write-back | **The** differentiator for energy/industrial; turns inspection data into integrity trend | **L** | **NEW** |
| **Risk-based / condition-based re-inspection interval** (API 580/581; Maximo Health scoring) [ibm.com] | Inspection `programmes` trigger extended: `trigger_type='condition'` — next-due computed from corrosion rate / health score, not just calendar | `inspections` (F5 programmes) | Fewer wasted inspections, defensible intervals | **M** | **ENH** |
| **Instrument calibration *gates the reading* + instrument enters traceability chain** (half-specced) | Finish the `forms` instrument-ref field → `eligibility.instrument` hard block on out-of-cal gauge; stamp instrument id + cal cert into the signed record | `forms` F10 / `eligibility` / `inspections` | Evidence-grade: a reading on an expired gauge is impossible, not just flagged | **M** | **ENH** (specced, un-finished) |
| **Weld "heat map" / visual weld-map on isometric** (WeldEye, TWI WeldTrace) [kemppi.com] | Render weld status (fit_up→accepted→rejected) as colour pins on the ISO drawing via `markups` + `wps` register | `markups` + `wps` | Instant weld-progress read for QC/client | **M** | **NEW** |
| **Filler/consumable batch + heat-input traceability per weld** (WeldEye) [kemppi.com] | Add `filler_batch`, `shielding_gas_batch`, `heat_input` to `oe_weld`; pull into weld register export | `wps` (F4/F8) | Full weld traceability to batch — client/ASME audit ask | **S** | **ENH** |
| **Spec/clause picker shared across RFI/inspection/NCR** (Procore Nov-2025 Specification Picker) [procore.com] | One "standard-ref / spec-section" picker reused by `requirements`, `rfi`, `qms`, `ncr` | `requirements` + cross-module | Consistent spec linkage; less free-text | **S** | **ENH** |
| **Inspection "waiver with photo + reason, not rejection"** (Dalux self-inspection) [dalux.com] | Already covered by `inspection_requests` waiver_rule + reason codes | `inspection_requests` | — | — | **SPEC** |
| **Scope-ingest: master scope doc → per-line items** (Inspection-ERP `scope_ingest`) [03-extension-modules] | Background-job extractor splitting an MDR/scope PDF into Items for the asset tree (pluggable per content type, human-confirmed) | `asset_management` / `data_import` | Fast onboarding of a job's inspection population; already proven in the as-built | **M** | **NEW** (in reference, not KERP spec) |

### 2.2 Logistics

| Borrowed ability (product) | Concrete KERP feature | Module | Value | Effort | Status |
|---|---|---|---|---|---|
| **Equipment telemetry → calibration/maintenance-due + utilisation** (Maximo meter-based PM, Asset Health) [ibm.com] | `equipment` already has `_telemetry` + `_maintenance_schedule`; add **meter-triggered** PM (hours/km) and a calibration-due feed into `eligibility.instrument` | `equipment` + `eligibility` | Measuring instruments *are* equipment — one register, gated | **M** | **ENH** |
| **Failure codes / failure hierarchy on breakdowns** (Maximo Asset Work Registry) [ibm.com] | Add a `failure_code` taxonomy to `equipment._damage_report` / `_work_order` | `equipment` | Reliability analytics, warranty claims | **S** | **ENH** |
| **Delivery ETA / gate-slot clash + QR gate check-in** (HammerTech, SiteAware gate logistics) | `site_logistics` gates already have slots/capacity; add QR/vehicle check-in event + clash warning via `run_checks` | `site_logistics` | Smoother gate; ties delivery to goods-receipt | **S** | **ENH** |
| **Reusable-offcut / material-yield optimisation** | Already specced | `cutting` | — | — | **SPEC** |
| **Spare-parts inventory valuation, min/max reorder, ABC** (Maximo/Fiix stores) | ❌ *Do not borrow the money/valuation half.* Physical reorder-point already in `site_inventory` F4 | `site_inventory` | Boundary risk — see §3 | — | **REFUSE (money)** |

### 2.3 Execution / LPS / progress

| Borrowed ability (product) | Concrete KERP feature | Module | Value | Effort | Status |
|---|---|---|---|---|---|
| **Constraint log with make-ready status + owner + "need-by"** (Touchplan, LCI LPS) [touchplan.io] | `schedule_advanced` has `_constraint`; surface a **constraint board** with ageing + owner + auto-link to RFI/submittal/procurement that resolves it | `schedule_advanced` (F3) | Make-ready visibility is the heart of LPS; the data exists, the board doesn't | **M** | **ENH** |
| **PPC + variance-reason (RNC) weekly dashboard** (Touchplan Basic Variance Report) [touchplan.io] | `schedule_advanced` has PPC + `_rnc`; add the standard PPC trend + Pareto-of-reasons widget | `bi_dashboards` over `schedule_advanced` | Standard LPS metric, client-facing | **S** | **ENH** |
| **Commitment "promise" reliability by crew/sub** (Last Planner) | Derive PPC per last-planner / subcontractor from `_commitment` | `schedule_advanced` + `reporting` | Accountability; feeds sub ratings | **S** | **ENH** |
| **Rules-of-credit / earning-rules library** (InEight progress) | `progress` has milestone weights; add a reusable **rules-of-credit template** per item-type (e.g. pipe: fit 40 / weld 40 / test 20) | `progress` + `item_types` | Consistent, auditable % earning | **M** | **ENH** |
| **Daily "workface" log that auto-assembles from the day's records** (TD-008 edge screen) | A Daily Workface Log screen rolling up progress/diary/time/NCR by RSW | `field_diary` / `fieldreports` UI | Named gap TD-008; one-tap end-of-day | **M** | **NEW (UI)** |

### 2.4 Documents / CDE

| Borrowed ability (product) | Concrete KERP feature | Module | Value | Effort | Status |
|---|---|---|---|---|---|
| **Drawing version-compare / overlay (old vs new rev)** (Bluebeam, ACC, Dalux) [autodesk.com] | Visual diff of two `file_versions` of a sheet in the viewer; highlight changed regions | `markups` + `documents` | Catches silent rev changes — a classic claim source | **M** | **NEW** |
| **Scale calibration + on-drawing measurement** (Bluebeam, ACC Takeoff snap-to-line) [autodesk.com] | Already specced | `markups` F2 (`_scale_config`) | — | — | **SPEC** |
| **Transmittal with read-receipt / ack + response-due** (ACC, Aconex) | Already specced | `transmittals` / `file_transmittals` | — | — | **SPEC** |
| **Model/BIM element → issue & inspection link** (Dalux Field, Fieldwire) [dalux.com] | KERP softened BIM refs to plain tags (`construction_control._element_ref`). Keep plain; optionally store a `bim_guid` string on `asset_links` (no FK, no viewer) | `documents` / `asset_management` | Lightweight BIM traceability without a BIM engine | **S** | **ENH (bounded)** |
| **Quantity takeoff from drawings → BOQ** (ACC/Bluebeam takeoff) | ❌ Takeoff feeds estimating/BOQ — out of scope, and no BOQ anchor allowed | — | Anchor/boundary violation — see §3 | — | **REFUSE** |

### 2.5 Defense & Claims (the second niche)

| Borrowed ability (product) | Concrete KERP feature | Module | Value | Effort | Status |
|---|---|---|---|---|---|
| **Windows / time-impact forensic delay with fragnets** (HKA, Socotec, SCL Protocol) [hka.com, socotec.us] | Already specced | `schedule_advanced` F8 | — | — | **SPEC** |
| **Auto delay-attribution from witness no-show → EOT candidate** | Already specced (and novel) | `inspection_requests` F10–F12 | — | — | **SPEC** |
| **Contemporaneous "records completeness" score for a claim window** (forensic practice — records are king) | A **claim-readiness meter**: for a chosen window, score coverage of diaries/photos/time/NCR/late-receipts per RSW | `claims_evidence` + `reconciliation` | Tells you *now* where your record is thin, before the dispute | **M** | **NEW** |
| **SCL-Protocol-aware float ownership / concurrency note on EOT** [oberoimehak, hka.com] | Add `float_ownership` + `concurrency_note` fields to `_eot_claim`; surface in the binder | `variations` / `schedule_advanced` | Defensible entitlement position | **S** | **ENH** |
| **Disruption: measured-mile productivity baseline** (forensic disruption claims) | `variations._disruption_claim` has baseline vs impacted productivity; add a **measured-mile selector** over `costmodel._productivity` | `variations` + `costmodel` | Industry-standard disruption method | **M** | **ENH** |

### 2.6 Controls / BI

| Borrowed ability (product) | Concrete KERP feature | Module | Value | Effort | Status |
|---|---|---|---|---|---|
| **Threshold alert rules on any KPI + throttle + channel** (ProNovos, Power BI alerts) | Already specced | `bi_dashboards` F4 `_alert_rule` | — | — | **SPEC** |
| **Benchmark a KPI vs portfolio / prior projects** (InEight, ProNovos) | Cross-project KPI comparison view (physical SPI/PPC/defect-rate only) | `bi_dashboards` / `project_controls` | Portfolio learning, single company | **M** | **NEW** |
| **Scheduled branded report pack to recipients** (Procore, ACC) | Already specced | `reporting` F2 | — | — | **SPEC** |
| **"Inbox / ball-in-court" my-actions rollup** (Procore, Aconex ball-in-court) | `dashboard` has per-user inbox; extend to a cross-module ball-in-court (RFI/submittal/NCR/IR/co-sign) | `dashboard` | One place for "what's waiting on me" | **S** | **ENH** |

### 2.7 Portal

| Borrowed ability (product) | Concrete KERP feature | Module | Value | Effort | Status |
|---|---|---|---|---|---|
| **Client self-service witness booking + calendar** (part-specced v3) | Already specced | `portal` v3 F9–F10 | — | — | **SPEC** |
| **Public read-only share-link snapshot** (Inspection-ERP `guest_portal`, Procore) [03-extension] | Already specced (portal v1 magic-link) | `portal` v1 | — | — | **SPEC** |
| **Client-facing "live quality score / open-item" tile** (Procore client view) | A read-only client KPI tile (open NCRs, hold-points pending client, co-sign backlog) | `portal` + `bi_dashboards` | Client sees their own bottlenecks — reinforces delay attribution | **S** | **NEW** |
| **Client e-signature on notices/ITR** | Already specced | `portal` v2 + `signing` | — | — | **SPEC** |

### 2.8 Safety (leading indicators — thin in spec)

| Borrowed ability (product) | Concrete KERP feature | Module | Value | Effort | Status |
|---|---|---|---|---|---|
| **Leading-indicator ratio (observations/TBT per incident), site safety score** (HammerTech, Cority) | A safety KPI tile: observation rate, TBT attendance, open CAPA ageing, TRIR/LTIFR | `safety`/`hse_advanced` + `bi_dashboards` | Proactive safety signal; client/HSE ask | **S** | **ENH** |
| **Pre-task / Take-5 gated by worker competency + permit** (HammerTech) | Tie `hse_advanced._ptw` prerequisites to `eligibility.person` (ticket valid) before permit issue | `hse_advanced` + `eligibility` | No permit to an unqualified worker | **S** | **ENH** |
| **Worker site-access gate on induction/cert expiry** (HammerTech access control) | `credentials` + `eligibility.person` block site assignment on expired induction | `credentials`/`teams` | Mobilisation compliance | **S** | **ENH** |

---

## 3 · Differentiators to lean into (the "why KERP wins its niche")

1. **Asset-integrity engine inside a field tool.** No construction field platform (Procore, Fieldwire, Dalux) does corrosion/thickness loops, remaining-life and condition-based re-inspection; the RBI tools that do (GE APM, DNV, Credo [gevernova.com, dnv.com]) are heavyweight integrity suites with no field-execution/claims layer. KERP's `asset_management` Item model + `inspections` write-back + `eligibility` is *one schema change away* from owning this. **This is the single strongest borrowed-ability opportunity.**
2. **Instrument-calibration-gated, traceability-grade readings.** Half-specced already (`forms` F10, `eligibility.instrument`). Finishing it makes every measurement provably taken on an in-cal instrument — an evidence-grade claim/audit moat that Procore/Dalux cannot match.
3. **Offline-first witness scheduling + automatic client-vs-contractor delay attribution.** Already specced (`inspection_requests`) and genuinely novel — market has nothing equivalent. Protect it; make it the headline demo.
4. **Evidence-grade records end-to-end** (hash-chained sign-off, append-only ledgers, signed as-builts). Already specced; package it as "defensible by construction."
5. **Weld traceability to batch + visual weld-map** (borrowed from WeldEye [kemppi.com]) — a cheap, high-impact add that fits the energy/industrial buyer.

---

## 4 · Top 15 features to add (prioritised)

1. **Condition-monitoring loops + CML thickness history + remaining-life flag** (`asset_management`+`inspections`) — turn inspection data into integrity trending; the niche-defining borrow from RBI tools. *(L)*
2. **Finish instrument-calibration gate + traceability stamp** (`forms`/`eligibility`/`inspections`) — make out-of-cal readings impossible, not just warned. *(M)*
3. **Condition/risk-based re-inspection interval** (`inspections` programmes) — defensible, cheaper inspection scheduling. *(M)*
4. **LPS constraint board (make-ready, owner, need-by, auto-linked blockers)** (`schedule_advanced`) — data exists, surface it. *(M)*
5. **PPC + variance-reason (RNC) weekly dashboard** (`bi_dashboards`) — the standard LPS metric, client-facing. *(S)*
6. **Visual weld-map on isometrics + batch/heat-input traceability** (`wps`+`markups`) — WeldEye-grade weld evidence. *(M/S)*
7. **Drawing revision version-compare/overlay** (`markups`+`documents`) — catches silent rev changes, a claim source. *(M)*
8. **Claim-readiness / records-completeness meter per window** (`claims_evidence`) — know where your record is thin before the dispute. *(M)*
9. **Scope-ingest: master scope PDF → Items (background, human-confirmed)** (`asset_management`) — fast job onboarding, proven in the as-built. *(M)*
10. **Cross-module ball-in-court / my-actions inbox** (`dashboard`) — one place for "waiting on me." *(S)*
11. **Meter-triggered PM + calibration-due feeding eligibility** (`equipment`+`eligibility`) — instruments are equipment; gate on them. *(M)*
12. **Safety leading-indicator KPI tile + permit-gated-by-competency** (`hse_advanced`/`safety`+`eligibility`). *(S)*
13. **Rules-of-credit earning-template library per item-type** (`progress`+`item_types`) — consistent, auditable %. *(M)*
14. **Shared spec/standard-clause picker across RFI/NCR/inspection/requirements** (cross-module) — less free-text, Procore-style. *(S)*
15. **Client live quality-score tile in the portal** (`portal`+`bi_dashboards`) — client sees their own bottlenecks, reinforcing delay attribution. *(S)*

---

## 5 · Recommendations table (atomic, decidable)

| # | Recommendation | Rationale | Effort | Priority | Affects |
|---|---|---|---|---|---|
| R1 | Add a condition-monitoring-location Item subtype with reading time-series, corrosion-rate & remaining-life | Niche-defining; no field competitor has it (RBI tools do) | L | **P1** | `asset_management`, `inspections`, `item_types`; Phase 2+ |
| R2 | Finish the instrument-calibration gate + traceability stamp (already half-specced) | Evidence-grade moat; small delta on specced work | M | **P1** | `forms` F10, `eligibility`, `inspections`; MODULE-SPEC-11 |
| R3 | Extend inspection programmes with `trigger_type='condition'` (risk/condition-based interval) | Defensible, cheaper inspection scheduling | M | **P1** | `inspections` F5 |
| R4 | Build the LPS constraint board (make-ready + owner + need-by + auto-linked blockers) | LPS make-ready is the point of `schedule_advanced`; data exists | M | **P1** | `schedule_advanced` F3; TD-008 edge screens |
| R5 | Ship PPC + variance-reason (RNC) weekly dashboard widget | Standard LPS KPI, client-facing, cheap | S | **P1** | `bi_dashboards`, `schedule_advanced` |
| R6 | Add filler/gas batch + heat-input to `oe_weld` and a visual weld-map overlay | WeldEye-grade weld traceability; energy/industrial ask | S/M | **P1** | `wps`, `markups` |
| R7 | Drawing revision version-compare/overlay in the viewer | Catches silent rev changes; claim defence | M | **P2** | `markups`, `documents`, `file_versions` |
| R8 | Claim-readiness / records-completeness meter per delay window | Proactive claim defence; fits the second niche | M | **P2** | `claims_evidence`, `reconciliation` |
| R9 | Scope-ingest extractor: master scope doc → Items (ARQ, human-confirmed) | Proven in the as-built (`scope_ingest`); fast onboarding | M | **P2** | `asset_management`, `data_import`, `jobs` |
| R10 | Cross-module ball-in-court inbox | Single "waiting on me" surface | S | **P2** | `dashboard` |
| R11 | Meter-triggered PM + calibration-due → `eligibility.instrument` | Unifies instruments/equipment under one gated register | M | **P2** | `equipment`, `eligibility` |
| R12 | Safety leading-indicator tile + permit-gated-by-competency | Proactive safety; client/HSE ask | S | **P2** | `safety`, `hse_advanced`, `eligibility` |
| R13 | Rules-of-credit earning-template library per item-type | Consistent auditable % earning | M | **P2** | `progress`, `item_types` |
| R14 | Shared spec/standard-clause picker across registers | Consistency, less free-text (Procore Spec Picker) | S | **P3** | `requirements`, `rfi`, `qms`, `ncr` |
| R15 | Client live quality-score tile in portal | Client sees own bottlenecks; reinforces attribution | S | **P3** | `portal`, `bi_dashboards` |
| R16 | Store optional `bim_guid` string on asset/document links (no FK, no viewer) | Lightweight BIM traceability without a BIM engine | S | **P3** | `documents`, `asset_management` |
| R17 | SCL-aware float-ownership + concurrency note on EOT; measured-mile disruption selector | Defensible entitlement, standard methods | S/M | **P3** | `variations`, `schedule_advanced`, `costmodel` |

---

## 6 · Conflicts & risks

- **Commercial-boundary violations to REFUSE (borrowed abilities that break the doctrine):**
  - **Pay applications / progress billing** (Procore, ACC Cost [autodesk.com]) — owned by the ERP; KERP mirrors money only. Reject.
  - **Quantity takeoff → BOQ / estimating** (Bluebeam, ACC Takeoff, Inspection-ERP `estimating`) — needs a BOQ anchor, which is explicitly forbidden ("No BOQ anchor anywhere"). Reject; keep takeoff out even though `markups` has measurement.
  - **CMMS spare-parts valuation / stores inventory value / ABC analysis** (Maximo, Fiix) — that is money and master-inventory ownership. Keep `site_inventory` physical only (reorder-point ✓, valuation ✗).
- **Anchor-model risks:** R1 (condition-monitoring) must hang readings off the **Item** (inspection anchor), not invent a new anchor; CMLs are Items with an `item_type`. R13 rules-of-credit must earn against **RSW/Item** scope caps (`progress` F6), not a quantity bill.
- **Scope-creep risk:** condition-monitoring (R1) is the one genuinely *new surface* — it risks drifting toward a full integrity-management suite (inspection strategies, API-581 probability modelling, degradation-mechanism libraries). **Bound it to: CMLs + readings + corrosion-rate + remaining-life flag → NCR.** Full RBI scoring is a later decision, not MVP.
- **Single-company doctrine:** benchmarking (R?) and portfolio KPIs are fine *within one company's projects*; do not import multi-tenant marketplace/entitlement machinery from the reference `03-extension-modules` (that is SaaS plumbing, see TD-007).
- **Effort realism:** several "S" items (R5, R10, R14, R15) are dashboard/picker reuse and genuinely small; R1 and R7 are the only real builds and should be phased.

## 7 · Open questions for the owner

1. **Is asset-integrity (condition monitoring / corrosion loops) in scope for v1, or a Phase-3 "integrity pack"?** This is the biggest strategic call — it defines whether KERP competes with RBI tools or just with Procore/Dalux. *(Propose new TD: "TD-011 — condition-monitoring/integrity scope line.")*
2. **How far does the instrument-calibration gate go** — block-on-expired only, or full traceability chain into the signed record and the MDR? (Ties to TD-002 / MODULE-SPEC-11 `forms` F10.)
3. **Do we build the TD-008 edge screens (constraint board, daily workface log) now?** R4/R5 depend on this being resourced. (Existing **TD-008**.)
4. **BIM depth:** confirm "plain tag + optional `bim_guid` string, no viewer, no FK" is the permanent ceiling (R16), so we stop re-litigating BIM.
5. **Benchmarking across projects** — acceptable within the single company, or a privacy concern for subcontractor-level PPC/defect comparison?

## 8 · Feeds-the-plan (lift directly into the merged plan)

- **P1 integrity borrow:** R1 condition-monitoring loops (bounded to CML+reading+rate+remaining-life→NCR), R2 finish calibration gate, R3 condition-based intervals — the niche-defining trio on `asset_management`/`inspections`/`forms`/`eligibility`. Raise **TD-011** for the integrity scope line.
- **P1 LPS borrow:** R4 constraint board + R5 PPC/RNC dashboard on `schedule_advanced`/`bi_dashboards` — folds into the **TD-008** edge-screen work.
- **P1 weld borrow:** R6 batch/heat-input fields + weld-map overlay on `wps`/`markups` (WeldEye parity).
- **P2 claims/doc borrows:** R7 rev-compare, R8 claim-readiness meter, R9 scope-ingest extractor (port from the as-built `scope_ingest`).
- **P2 quick wins:** R10 ball-in-court inbox, R11 meter/cal equipment gate, R12 safety leading indicators, R13 rules-of-credit.
- **P3 polish:** R14 shared spec picker, R15 client quality tile, R16 bounded `bim_guid`, R17 SCL-aware EOT + measured-mile.
- **Explicit refusals to record in the plan:** no pay applications, no takeoff/BOQ/estimating, no CMMS inventory valuation — all break the money/anchor boundary.

---

### Sources (products whose abilities were borrowed)
- Procore quality/safety, Specification Picker, defect-to-drawing, client view — [procore.com](https://www.procore.com/en-ca/quality-safety), [procore.com defect management](https://www.procore.com/en-ca/fc/defect-management)
- GE Vernova APM Risk-Based Inspection 580/581 (corrosion loops, thickness-monitoring integration) — [gevernova.com](https://gevernova.com/software/documentation/onpremises-apm/v522/help/pdf/Risk_Based_Inspection_580.pdf)
- DNV RBI + Credo asset-integrity pairing (CUI, API 581) — [dnv.com](https://www.dnv.com/cases/totalenergies-relies-on-dnv-s-software-solutions-for-risk-based-inspection-250228)
- IBM Maximo (meter-based PM, failure codes, Asset Health scoring) — [ibm.com Maximo](https://www.ibm.com/products/maximo/predictive-maintenance), [ibm.com asset work registry](https://www.ibm.com/products/maximo/asset-work-registry)
- Touchplan / LCI Last Planner (constraint log, PPC, variance report) — [touchplan.io](https://touchplan.io/), [leanconstruction.org](https://leanconstruction.org/wp-content/uploads/2023/10/2023_LCI_LeanConstructionPlannerStandard-Work.pdf)
- Kemppi WeldEye (weld traceability, heat input, batch, WPS/cert management) — [kemppi.com](https://kemppi.com/es/family/weldeye), [kemppi.com traceability blog](https://www.kemppi.com/ru/blogs/how-weldeye-sets-the-standard-for-welding-traceability)
- Dalux Field / Fieldwire (offline inspections, BIM-element issue linking) — [dalux.com](https://www.dalux.com/dalux-field/), [fieldwire.com](https://www.fieldwire.com/blog/bim-construction-management-software/)
- Autodesk Construction Cloud / Build + Takeoff (assets/handover, markup snap, pay apps — last two flagged as out-of-scope) — [help.autodesk.com Build](https://help.autodesk.com/cloudhelp/ENU/Build-About/files/What_is_Build.html), [autodesk.com releases](https://www.autodesk.com/blogs/construction/)
- Forensic delay practice (windows/TIA/fragnet, SCL Protocol, measured-mile) — [hka.com](https://www.hka.com/), [socotec.us](https://socotec.us/services/dispute-resolution/cpm-schedule-delay-analysis), [oberoimehak.substack.com](https://oberoimehak.substack.com/p/delay-analysis-methods)
- Inspection-ERP as-built reference (`scope_ingest`, `guest_portal`, `iso_workspace`) — `docs/spec/architecture-suite-reference/03-extension-modules.md`
