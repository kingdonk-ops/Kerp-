# Field Operations Platform — Development Plan

> **Authoritative, self-contained plan.** This document and its companion `MODULE-SPECS.md` are the complete specification: a phased build plan plus a per-module spec for every in-scope module. Everything here describes the target platform only — nothing out of scope is referenced.

---

## 0. What this platform is

An **operational delivery / field-execution engine**. It owns **field ground truth** — the physical state of delivery: the asset/scope hierarchy, installed quantities and earned progress, plant and labour presence, delay and diary logs, quality/weld/inspection records, documents and photos — and feeds **verified actuals** back to the organisation's systems of record.

**It deliberately owns no corporate financial ledger and no master programme.** Money (invoicing, GL, payroll, pricing) lives in the corporate ERP; the master CPM programme lives in Primavera P6. The platform **imports** from those systems, captures reality against them on site, reconciles the difference, and **exports verified actuals back** (physical progress and dates to P6; receipt/OS&D and signed daywork/labour to the corporate ERP). Values that would otherwise be computed in money are held as **physical metrics** (earned quantity, crew hours, PPC); where a money figure must be shown it is a **read-only mirror** synced from the system of record.

**Single company.** Currency, date, locale and tax are **Project Settings**, not an internationalisation subsystem.

### The four pillars (+ backbone)

| Pillar | Owns |
|---|---|
| **1 · Logistics** | Getting the right materials/plant to the right place: deliveries, gate receipt & OS&D, site inventory, cutting/nesting, basic procurement & vendor/subcontractor records. |
| **2 · Execution** | Doing and recording the work: field programme (P6-synced), look-ahead/Last-Planner, physical progress, tasks, diaries, field time, reports, safety. |
| **3 · Defense & Claims** | Protecting entitlement: variations & signed site measurement, daywork, change/MoC, disruption & EOT evidence, claims binders. |
| **4 · Controls & Insights** | Seeing the truth: physical EVM & productivity, dashboards/BI, risk, reporting, the AI assistant. |
| **Backbone** | **Asset & Quality** — the recursive Site→Area→Asset→RSW hierarchy with typed Items, the WPS/weld register, inspections/ITP/NCR, and the central verification engine that every gate runs through. |
| **Platform** | Projects, identity/RBAC, documents/CDE, collaboration, integrations (incl. P6), extensibility, ingestion. |

---

## 1. Architecture principles (hold in every phase)

1. **Modular monolith.** One FastAPI app; each capability is a self-contained module under `backend/app/modules/<name>/` with a `manifest.py`, `models.py`, `router.py`, `service.py`, `schemas.py`, `README.md`. A module loader registers enabled modules per project.
2. **Module contract.** No cross-module table access — call the other module's `service.py`. New capability = new module; changing a core table needs an ADR.
3. **Every table uses `BaseRecordMixin`** (typed entity URI, `tenant_id`, content hash, provenance, revision). A metadata-contract test enforces it.
4. **Tenancy & RLS.** Every table gets a row-level-security policy; tenant comes from the JWT, never from request params; cross-tenant access returns 404.
5. **Three-tier authorisation.** Hierarchical capability (viewer < editor < manager < admin) × business-role presets (the 13 personas, §4) × per-user module/project/site access. Claims are rehydrated from the DB on each request, not trusted from the token body.
6. **The anchor model is law** (§3). The lowest **work** anchor is the **RSW**; the lowest **inspection** anchor is the **Item** (a weld is an Item). Everything physical hangs off the Asset hierarchy; there is no bill-of-quantities anchor anywhere.
7. **External integration keys, not hard FKs, across the boundary.** Anything owned by a system of record is referenced by `external_source` + `external_id` (+ `synced_at`), never a foreign key into a module we do not own. Money fields crossing the boundary are read-only mirrors.
8. **Verification is configuration, not code** (§5). Every verifiable transition calls `run_checks(context, rule_set)`; rule sets are data (DSL), so a new check is a config change, not a release.
9. **Offline-first on the edge.** Field capture (gate delivery, look-ahead board, daily workface log, diaries, inspections) works offline via an idempotent sync ledger (client op id → result), with conflict replay. Tier-1 edge screens are tablet/phone first.
10. **AI mutations are proposals.** Any AI-initiated change carries `source="ai"` and requires human confirmation before it applies; the assistant proposes, a person commits, and every applied action is revertible and audited.
11. **Append-only operational ledgers.** Where the as-is used double-entry money posting, use append-only physical ledgers (movements, hours, earned quantity) — auditable, never destructive.
12. **Additive migrations by default**; destructive migrations need an ADR. Testing gate per phase (§8); CI green before merge.

---

## 2. Data & integration boundaries

**Inbound (we consume):**
- **Primavera P6** `.xer` / Primavera XML → builds/updates the Asset hierarchy (WBS→Site/Area, activity groups→Assets, discipline bands→RSWs) and the field programme; WBS codes and Activity IDs are retained as permanent external keys.
- **Corporate ERP** → purchase-order lines (to reconcile gate receipts against), vendor/contact master.
- **Corporate safety** → safety KPI snapshots.

**On site (we own):** installed/earned quantity, crew hours, PPC, deliveries & OS&D, diaries, inspections/ITP/NCR, welds & NDE, signed site measurement & daywork, photos/dockets.

**Outbound (we export, verified):**
- **To P6:** physical percent-complete, earned units, actual/forecast dates — a P6-importable file keyed on Activity ID (progress actuals only, so re-import is non-destructive). Not a live sync — file-based both ways.
- **To corporate ERP:** goods-receipt confirmations and OS&D deltas with docket images (for AP three-way match), and signed daywork/plant/labour logs (for claim verification and payroll).

**Ingestion surface** lives in `integrations`: idempotent batch/webhook endpoints (`POST /integrations/p6/import`, `GET /integrations/p6/export`, `POST /integrations/purchasing/po-lines`, safety KPI sync) with a staging area (P6 raw/parsed, PO-line mirrors, KPI snapshots).

## 2a. Runtime & storage topology (see `ADR-001`)

Sized for 30–80 concurrent field/office users with bursty heavy files (CAD/BIM, 200-page MDRs). The rule is **keep heavy, spiky work out of the web loop and out of the DB connection**.

```
[ Nginx — HTTP/2, TLS, static Brotli/gzip ]
   ├── /api/*  → [ FastAPI · 3 Uvicorn workers ]  ──enqueue──► [ Redis 7 ] ──► [ ARQ worker container ]
   └── /*      → [ static SPA ]                                                 (PDF/QR compile, CAD/BIM,
                     │                                                           OCR, embeddings, BI rollups,
                     ▼                                                           P6 parse, upload scan)
          [ Postgres 16 + PostGIS ]        [ Object store: MinIO / S3 / R2 ]
          (relational + JSONB/GIN/GiST)    (flat UUID keys; presigned direct upload)
```

- **Prebuilt images in CI (GHCR); deploy is `pull && up`** — no host compilation (the cause of build 504s). Multi-stage Docker, `pip --only-binary`; 4 GB swap only as a fallback.
- **One ARQ + Redis worker** isolates every CPU/RAM-heavy job; if it OOMs, the API serving the other users stays up. Jobs are enqueued and polled through the existing `jobs` module.
- **Postgres 16 + PostGIS, no DuckDB in the request path** — the workload is OLTP; the analytics that needed DuckDB were removed in the pivot. BI/KPI rollups run in the ARQ worker into materialised/summary tables.
- **Presigned direct-to-object-store uploads** (extends `resumable_uploads`): the browser PUTs the file to the bucket; the API never buffers the bytes. On-complete scan + checksum + registration run as an ARQ job.
- **Lazy-import heavy libs** (`fitz`/PyMuPDF, `trimesh`, `cv2`, `pyarrow`) inside handlers/tasks, enforced by lint — ~30–50% smaller idle workers, instant startup.
- **Dynamic inspection/asset data = relational columns + JSONB + versioned JSONSchema**, GIN-indexed (same pattern across `asset_management`, `workpacks`).

Resulting footprint: web ~150–200 MB, Postgres ~150–250 MB → a **2 vCPU / 4 GB** box is viable, **4 vCPU / 8 GB** comfortable.

---

## 3. Asset & Quality backbone

### 3.1 Hierarchy (recursive)
`Site → Area → Asset (→ sub-Asset …)* → RSW` — an Asset may parent **sub-Assets** to any depth, and Assets/sub-Assets may parent **RSWs** (Required-State-of-Work: discipline work packages — Civil / Structural / Mechanical / Electrical / Piping / Instrumentation). Tasks, look-aheads, constraints and crews lodge at **RSW**.

### 3.2 Items & inspection anchoring
- Assets/sub-Assets carry **Items** (`oe_asset_item`: valves, welds, structural members, instruments…) built on the dynamic type/field schema; Items are **inspectable**.
- **Inspections / ITPs anchor at Asset · sub-Asset · RSW · Item.** The global register filters across all levels with batch PDF/Excel export; the Asset-Details Inspections tab aggregates the asset and its descendants.

### 3.3 WPS, welder qualification & welds
A weld is an Item of type `weld`. Every weld **must** reference an **approved** WPS and be assigned a welder holding a **valid** qualification **for that WPS covering the weld's range**, with **continuity** unbroken; a weld cannot be **accepted** until required **NDE** results are accept and ITP hold/witness points are released. These are enforced engine rules (R-WPS-1…4), not UI conventions — an unqualified assignment is blocked with a logged finding. (Full entities and endpoints in `MODULE-SPECS.md` → `wps`.)

### 3.4 Anchoring rules (applied to every module)
Three rules make the platform's data model consistent:
1. **Everything physical anchors to the Asset hierarchy.** Work anchors to an **RSW** (`oe_asset_node level='rsw'`); anything inspectable anchors to an **Item** (`oe_asset_item`). There is no separate bill-of-quantities anchor — scope lives in the hierarchy. Modules that carry a work/inspection anchor (`progress`, `schedule`, `site_inventory`, `site_logistics`, `variations`, `resource_summary`, `inspections`, `punchlist`, `requirements`, `submittals`) use `rsw_node_id` / `asset_item_id`.
2. **Anything owned by a system of record is an external reference**, not a foreign key — `external_source` + `external_id` (+ `synced_at`) — e.g. a client contract, a corporate PO, a P6 activity.
3. **Values that would be money are held as physical metrics** — planned / earned / installed **quantity**, crew hours, PPC — feeding physical EVM; any money figure shown is a read-only mirror from the system of record.

---

## 4. RBAC — persona → visibility matrix

Implemented as business-role presets over the capability tiers, plus per-user project/site scope. `cases` seeds a walkthrough playbook per persona. "Site-only" resolves to an Asset-hierarchy **Site** node.

| Persona | Scope |
|---|---|
| Operations Management | **All projects** + all-projects dashboard |
| Purchasing & Procurement · Health & Safety Manager · HR · Quality Manager | All projects (overview, read) |
| Project Manager | All projects **they manage** |
| Site Superintendent | Everything for **their project** |
| Supervisor · Planner · QA/QC Lead · Admin · Stores Person | **Their project only** |
| Inspector | **Their site only** (Asset `Site` scope) |

---

## 5. The central verification engine

Every verifiable action routes through **`run_checks(context, rule_set)`**, built on the `validation` module + the repurposed `eac` rules/blocks engine + the completion-gate pattern + the `compliance` / `compliance_ai` DSL. **Error** findings block the transition (surfaced in the UI); **warnings** advise. Rules are **data**, so a client's extra check is configuration.

Rule sets per verifiable entity: `weld.accept`, `welder.assign`, `inspection.close`, `rsw.complete`, `goods.receive`, `cutting.confirm`, `asset.status_change`, `asset.p6_fields`, `schedule.p6_import`, `schedule.p6_export`, `report.cosign`, `itp.cosign`, `ncr.signoff`, `variation.measure`, `document.route`, `document.share`, `document.immutable`, `workpack.issue`, `workpack.stage.signoff`, `cwp.release`, `iwp.generate`, `tq.apply`. Every finding is logged and auditable.

---

## 6. Client portal — phased (v1 → v2 → v3)

Clients reach the platform through a scoped magic-link, read-only by default; writes are added in phases, each reusing existing modules (`signing`, `ncr`, `review_authority`, `meetings`) rather than forking them.

- **v1 — read-only:** view the Asset hierarchy and all registers (inspections, ITP, NCR, punch, weld, documents).
- **v2 — co-sign:** counter-sign completed reports and ITPs via `signing`, gated by `report.cosign` / `itp.cosign` (target must be internally complete first; a decline blocks "client-accepted").
- **v3 — scheduler + client NCR:** request/organise hold-point-witness and final-sign-off visits (feeding `review_authority` gates and `meetings`), and raise / sign off NCRs (through `ncr`, gated by `ncr.signoff`). Completing a witness visit does **not** itself release the gate — `review_authority` + `inspection.close` do.

(Full entities, endpoints and state machines in `MODULE-SPECS.md` → `portal`.)

---

## 7. Build order & phases

Sequencing follows the real dependency graph: the platform contract and the Asset backbone are frozen first because almost everything anchors to them.

### Phase 0 — Platform foundations
**Modules:** `projects`, `users`, `admin`, `teams`, `module_builder`, `onboarding`, `jobs`, `uploads`, `resumable_uploads`, `backup`, `client_errors`, `global_presence`, `notifications`, `saved_views`, `validation`.
- App skeleton, module loader + manifest contract, `BaseRecordMixin`, tenancy + RLS, auth + three-tier RBAC with DB-rehydrated claims, Project Settings (currency/date/locale/tax, single company), background job runner, chunked uploads, the `validation` engine shell (foundation of `run_checks`).
- **Exit:** a tenant can be created, a user authenticated and scoped, a module enabled/disabled per project, and a validation rule set run over a target. CI green.

### Phase 1 — Asset & Quality backbone
**Modules:** `asset_management`, `inspections`, `qms`, `construction_control`, `ncr`, `punchlist`, `requirements`, `review_authority`, `project_route`, `credentials`, `signing`, `source_data`, `compliance`, `compliance_ai`, `compliance_docs`, `forms`.
- The recursive hierarchy, typed Items, dynamic schema + page/layout designer, default config packs, **P6 import** and the first **P6 export** round-trip, Excel/CSV + manual ingestion.
- ITP/inspection/NCR/hold-gate registers anchored to Asset/RSW/Item; the completion-gate pattern wired into `run_checks` (`asset.status_change`, `inspection.close`, `rsw.complete`).
- **Exit:** a project's hierarchy can be built from a P6 file, inspections raised and closed against Items/RSWs with enforced gates, and progress to "complete" blocked by open NCRs/holds.

### Phase 2 — Welding quality
**Modules:** `wps`.
- WPS register, welder qualifications (range/expiry/continuity), welds as Items, NDE results, the weld state machine, and rules R-WPS-1…4 in the engine.
- **Exit:** a weld cannot be created, welded or accepted outside the qualification/NDE rules; the weld register and continuity watch report. CI green.

### Phase 3 — Logistics
**Modules:** `site_logistics`, `site_inventory`, `equipment`, `cutting`, `procurement` (basic), `supplier_catalogs` (basic), `subcontractors` (basic), `resource_summary`, `contacts` (party master, with the people/company directory folded in).
- Deliveries + gate receipt + **OS&D** against ERP-mirrored PO lines; site inventory movements; `cutting` nesting that writes verified site-inventory movements (`cutting.confirm`); basic vendor/subcontractor records (external contract reference, physical retention); resource-demand rollup from tasks/look-ahead with required-by dates.
- **Exit:** a delivery can be booked, received at the gate with OS&D captured and exported for three-way match, and a cutting plan confirmed into site inventory.

### Phase 4 — Execution
**Modules:** `schedule`, `schedule_advanced`, `progress`, `tasks`, `daily_diary`, `field_diary`, `field_time`, `fieldreports`, `voice`, `phonelog`, `site_prep`, `site_supervision`, `commissioning`, `closeout`, `defects_liability`, `temporary_works`, `timeline`, `deadlines`, `prefab`, `resources`, `safety`, `hse_advanced`.
- Field programme (P6-synced, RSW-linked), Last-Planner look-ahead → PPC, **physical progress by earned quantity** (feeding P6 export), offline edge capture, diaries/reports/time (hours exported to corporate payroll), safety.
- **Exit:** progress booked as earned quantity against RSW/Item (capped at scope, gated by inspection/weld acceptance), actuals exported back to P6, PPC computed from commitments.

### Phase 5 — Documents, collaboration & AI
**Modules:** `documents` (incl. the **Virtual File System**) + the `file_*` subsystem, `markups`, `plan_room`, `transmittals`, `correspondence`, `meetings`, `rfi`, `submittals`, `collaboration`, `collaboration_locks`, `connectors`, `inbound_email`, `inbound_capture`, `data_import`, `pipelines`, `integrations`, `record_publishing`, `authority_submission`, `interface_management`, `ai_agents`, `search`, `retrieval`, `erp_chat`.
- The **Virtual File System is part of `documents`** (not a separate module): a logical folder tree + pre-set `/Site/Asset/RSW` scaffold, the routing resolver (module → folder + standard name), polymorphic entity links, orphan engine, personal lockers/shares, and the per-folder versioning toggle (over `file_versions`) — rename-safe (DB-only), with bytes on flat UUID object-store keys.
- CDE with CDE states/revisions, the file capabilities, OCR of dockets/daywork/receipts into the full-text index, vector indexing of document metadata, and the assistant with **operational** tools (`get_open_constraints`, `get_po_delivery_status`, `check_ppc_reliability`, `summarize_claims_timeline`) — all mutations proposed and human-confirmed.
- **Exit:** a docket can be captured, OCR'd and found by search; the assistant answers a delivery/PPC question from live data and proposes (never applies) a change.

### Phase 6 — Defense, claims & controls
**Modules:** `variations`, `changeorders`, `moc`, `change_intelligence`, `claims_evidence`, `reconciliation`, `cost_recovery`, `costmodel` (physical), `eac` (physical), `full_evm` (physical), `project_controls`, `reporting`, `dashboard`, `dashboards`, `bi_dashboards`, `project_intelligence`, `risk`, `carbon`, `esg`, `service`, `cases`, `enterprise_workflows`, `approval_routes`.
- Signed site measurement & daywork on RSW/Item (owned) with priced mirror from the corporate system; disruption/EOT evidence assembled from physical productivity + delay windows into claims binders; **physical EVM** (SPI, physical-CPI as earned-qty-per-crew-hour, PPC) and executive controls; BI/reporting with physical KPIs.
- **Exit:** a variation captured and measured on site exports to the commercial system and pulls back a priced mirror; physical EVM and a claims binder render from owned data.

### Phase 6b — Work packaging (AWP) & field work packs
**Modules:** `workpacks`, `awp`. *(Depends on the backbone (1–2), execution/schedule (4) and `documents` incl. its VFS (5); build after them.)*
- **`awp`** — the CII/COAA Advanced Work Packaging sequence as a constraint engine: EWP (engineering, approved drawings/specs/ITP refs) → PWP (procurement, PO/batch/MTC status over `procurement`/`site_inventory`) → CWP (construction envelope) → **IWP** (field pack), with staggered gates (`cwp.release`, `iwp.generate`) that refuse to release field work until engineering is Rev 0 and materials are received with certs. Technical Queries / concessions (scope levels project/asset/work-pack/product) reuse `moc` for the lifecycle and auto-stamp warning banners.
- **`workpacks`** — the live field dossier (IWP): clones `qms` ITP stages into execution stages, links approved drawings (`documents`) + `markups` overlays, pulls batch MTCs from `site_inventory`, runs the offline-first tablet sign-off (reusing the `field_diary` sync ledger) with supervisor→QC hold-point gating via `construction_control` + `notifications`, and compiles the QR-coded MDR/pack PDF in the ARQ worker; the QR auto-filer returns signed scans into the pre-set `documents` VFS folder.
- **Exit:** a constraint-free CWP decomposes into field packs that inherit locked batches + approved drawings; a supervisor completes a stage offline, the QC inspector is notified and releases the hold point, and the signed pack compiles to a filed MDR PDF.

### Phase 7 — Client portal
**Modules:** `portal` (v1 → v2 → v3 per §6).
- **Exit (per sub-phase):** v1 clients read their scoped hierarchy/registers; v2 co-sign reports/ITPs with gates; v3 book witness/sign-off visits and raise/sign off NCRs.

---

## 8. Testing & gates (every phase)
- **Backend:** `ruff check .` + `mypy app` + full `pytest` suite (not just new tests).
- **Frontend:** `npm run typecheck` + `npm run test`, regenerate the API client if the API changed and commit it.
- **E2E:** affected Playwright suites for user-facing changes.
- **Contract tests:** metadata/`BaseRecordMixin`, RLS tenant isolation (cross-tenant → 404), module-manifest validity, and **`run_checks` rule-set tests** per verifiable entity.
- **All green or no commit.** No skipped tests without a linked task entry. A phase is done only when code + tests + docs (module `README.md`, ADRs for significant decisions) are complete.

## 9. Risks & sequencing notes
- **The Asset backbone is the critical path** — freeze its contract (hierarchy, Items, dynamic schema, external keys, `run_checks`) before building Phases 3–6, which all anchor to it.
- **Offline sync and the verification engine must be designed in from Phase 0/1**, not retrofitted — they cut across every edge screen and every gate.
- **P6 round-trip is the main integration risk:** WBS→Asset matching is never perfect; surface unmatched rows for manual linking, keep import idempotent on Activity ID, and export progress actuals only so the client's P6 stays authoritative on logic.
- **Phases 3–6 are the bulk** (logistics + execution + claims/controls); parallelise by pillar once the Phase-0/1 contract is frozen.
- **Keep the commercial boundary honest:** resist re-growing money ownership inside the platform — every money figure is a mirror unless a deliberate ADR says otherwise.

---

*Companion: `MODULE-SPECS.md` (complete per-module spec for every in-scope module). Both documents describe the target platform only.*
