# KERP — Consolidation & Merge Decisions (authoritative)

This document is the **binding reconciliation** of the legacy specs (now in
`../archive-legacy-specs/`: `architecture-suite-reference/`, `openbusinesserp-analysis/`,
`field-operations-platform/`) with the new architecture (`KERP-MASTER-SPEC.md` +
`KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md`). Where any legacy text disagrees with a
decision here, **this document wins**; where this document is silent, the master spec
and edge spec win, then the preserved legacy detail (see §8).

Build target is **KAEFER-style industrial field operations** (construction, fabrication,
remediation, turnaround) — **not** ongoing plant maintenance and **not** back-office ERP.

---

## 1. Hard scope exclusions — culled modules & domains

These are **removed** from scope. Do not build them; delete their influence from any
ported legacy text; raise no tasks for them.

| Culled | Retained replacement |
|---|---|
| **Accounting / General Ledger** — chart of accounts, double-entry journals, bank reconciliation, depreciation schedules, fiscal-year close, tax/BAS, direct invoicing, AR/AP | External-ERP **reference links** (`external_source`, `external_id`, `synced_at`) + **read-only** money mirrors (`amount_mirror`, `currency_code`). Money is never authored in KERP. |
| **HR / Payroll / time-billing** — employee contracts, PAYG/tax withholding, wage/salary calc, deductions, leave entitlements | **Physical labour logging only** — `field_time._line` (worker, equipment, RSW code, crew hours); export to corporate payroll. |
| **Generic e-commerce / Retail POS / CRM pipelines** — carts, storefront catalogs, POS registers, lead scoring, opportunity funnels, marketing automation | **Unified Party Directory** (`contacts`): Client, Contractor, Subcontractor, Supplier, Authority. |
| **Duplicated workflow engines** — `enterprise_workflows` + its redundant state tables | **Single** `approval_routes` engine (multi-party quorum, delegation, SLA timers). |
| **Runtime no-code module authoring** — `module_builder` / runtime dynamic-table generator | None — out of scope for a single-company contractor tool. Extensibility is custom fields/forms on fixed modules (MOD-12), not runtime table generation. |
| **Interactive 3D CAD/BIM viewers & native `.dwg` parsers** — WebGL IFC/BIM renderers, AutoCAD binary parsers, automated 3D takeoff | **PDF/drawing plan viewer** + vector **markups** overlay, plus a **pluggable background parser adapter** (`app/core/integrations/parsers.py`) for neutral formats (`.dxf`, `.pdf`, `.xer`). |

---

## 2. Data-model overhauls

### 2.1 One-engine inspection & schema consolidation (TD-002 — adopted)
Replaces the duplicated designers spread across `forms`, `asset_management`
(`oe_asset_layout`/`oe_asset_field_def`), `inspections` (thin), `qms`, and
`construction_control`.

- **Single field-type registry** in `core/fields/` — a shared palette: `numeric`, `text`,
  unit-bound `measurement`, `photo`, `signature`, **instrument reference**, `calculated`.
- **Unified inspection record** `oe_inspections_inspection` — the single store across all
  kinds: `kind IN ('inspection','itp','rfi')`.
- **`qms`** → strictly **master ITP template authoring**, audit registers, and calibration
  records. No parallel inspection store.
- **`construction_control`** → conformance criteria + hold/witness point evaluation via
  `run_checks`. **Drop `construction_control._inspection` entirely.**
- **`eligibility`** (new) → **stateless** service answering "is this person / instrument /
  material valid right now?" across WPS, credentials and calibration registers. **Owns no
  tables.**
- **`inspection_requests`** (new) → RFI / call-out workflow:
  `draft → requested → confirmed → witnessed | waived | no_show | failed`, with automatic
  **client delay attribution**.

### 2.2 Asset hierarchy storage (`oe_asset_node`)
- **Drop** string `path` querying and integer `depth`.
- **Add** PostgreSQL-native `tree_path ltree NOT NULL` with a **GiST** index (`CREATE
  EXTENSION ltree`).
- `parent_id` remains the **relational source of truth**; a move updates the whole subtree
  in a single `ltree` statement.
- **Add** read-only `display_path text` strictly for breadcrumb UI.

### 2.3 Dynamic asset attributes
- **Delete** the EAV table `oe_asset_field_value`.
- Attribute values live **exclusively** in `custom_fields jsonb` on `oe_asset_node` and
  `oe_asset_item`.
- Index with **GIN `jsonb_path_ops`** plus **functional B-tree** expression indexes on hot
  query attributes.
- `oe_asset_field_def` is **purely the JSONSchema definition registry**.

### 2.4 Reserved CML stubs (`oe_asset_item`)
- **Cull** the condition-monitoring calculation engine, wall-thickness trending, API 580/581
  degradation, and automatic re-inspection intervals (construction/remediation focus, not
  ongoing maintenance).
- **Reserve** fields only, in the Item JSONSchema: `cml_tag`, `nominal_thickness_mm`,
  `minimum_structural_thickness_mm`, `design_corrosion_allowance_mm`,
  `active_corrosion_rate_mm_yr`, `last_measured_thickness_mm`, `last_inspection_date`.

---

## 3. Engine, infrastructure & stack

- **Rule evaluator — CEL (Common Expression Language):** standardized for all dynamic form
  logic (`visible_if`, `required_if`, dynamic ranges, calculations). **Replaces JSONLogic /
  ad-hoc JS.**
  - Client/device: **JavaScript CEL** evaluator, offline in browser/IndexedDB.
  - Server: **Python CEL (`celpy`)** validating sync payloads.
  - CI parity against a shared fixture file `tests/contract/conformance_fixtures.json`.
- **Background queue — SAQ (Simple Async Queue) over Redis.** **Replaces ARQ.** Separate
  priority queues: `interactive` (real-time docket OCR, barcode lookups) and `batch`
  (200-page MDR PDF compile, P6 imports).
- **Cutting & nesting:** **ban `libnest2d` / `nest2D`** (GPL copyleft). 1D structural
  steel/pipe cutting via an **in-house First-Fit-Decreasing (FFD)** algorithm; 2D sheet/plate
  nesting via **`rectpack`** (MIT).
- **Primavera P6 ingest idempotency:** unique composite `(project_id,
  external_activity_id)`; mandatory **Unmatched-Row Linking Screen** in the P6 Import Wizard
  so schedule updates never sever existing inspection/RSW ties.

---

## 4. Security, isolation & identity

- **Project-scoped RLS:** keep `tenant_id` + PostgreSQL RLS on **every** table (TD-007 = yes),
  with `tenant_id` **scoped to the individual Project ID** — no cross-project leakage for
  external clients/subcontractors. **Four RLS-bypass contract tests** in CI cover: background
  SAQ workers, cross-module service calls, raw-SQL aggregations, and DB migrations.
- **Cryptographic signing (phased):**
  - Phase 0/1: **append-only hash-chained** event ledgers (`seq`, `chain_key`, `prev_hash`,
    `content_hash`, `payload`) with DB `REVOKE UPDATE, DELETE FROM app_role`.
    Ledger design (TD-057, accepted 2026-11-10 from the T-0006 implementation; detail in
    `backend/app/core/audit/README.md`): a chain is `(tenant, scope, chain_key)` where scope is
    the session's project or the tenant level (`scope_id` = `project_id` or the nil UUID;
    unique `(tenant_id, scope_id, chain_key, seq)`; keys embed the entity id, e.g.
    `inspection:<uuid>`). `content_hash` = SHA-256 lower-case hex over
    `b"kerp-audit-ledger\n"` + canonical JSON (sorted keys, compact separators, UTF-8; floats and
    NUL rejected) of `{v, tenant_id, project_id, chain_key, seq, prev_hash, created_at,
    created_by, source, payload}`; genesis `prev_hash` = 64 zeros; `seq` contiguous from 1.
    Verification checks contiguous `seq`, `prev_hash` links and the recomputed hash. Enforced
    by ORM guard, `REVOKE UPDATE, DELETE, TRUNCATE`, row/statement triggers and CHECKs; appends
    to one chain serialise on an advisory lock. Changing the hash format needs a new `v`.
  - Final turnover: MDRs and commercial Delay Notices signed with **pyHanko** →
    **ETSI PAdES-B-LTA** + **RFC 3161** trusted timestamp tokens.
- **Auth hardening:** **RS256 / EdDSA** JWTs via a **JWKS** endpoint (replaces symmetric
  HS256). 15-minute access tokens, rotating refresh tokens, server-side `jti` revocation
  blacklist in Redis. **Field-worker auth:** magic-link + **6-digit PIN** as a *local session
  unlock only*, with progressive lockout throttling.
- **Persona matrix — 15 roles** (base 13 + **Field Worker** (magic-link) + **Client Portal
  User**), plus an **Asset-Site scoping** filter for Site Inspectors (visibility limited to
  their physical site within a multi-site project).

---

## 5. Equipment calibration lifecycle & hard quality gate

- **`oe_equipment_asset` statuses:** `active`, `out_of_calibration`, `sent_for_calibration`,
  `quarantined`, `decommissioned`, `out_of_service`.
- **Immutable `oe_equipment_calibration_cert`** ledger, linked to presigned PDF certificate
  uploads in the VFS.
- Upload triggers a **SAQ OCR** job (cert number, lab accreditation, calibration date,
  expiry). **OCR-vs-user-entry discrepancy → QA review task.**
- A new verified cert sets `is_active = true` and **permanently supersedes** the prior cert
  (locked against edits).
- **Hard inspection gate:** instruments requiring calibration (hydro gauges, DFT meters,
  surface-profile gauges) that are expired **cannot be selected**. If forced offline, the CEL
  rule fails on sync:
  `equipment.calibration_due_date >= context.inspection_date && equipment.status == 'active'`
- Active cert PDFs + calibration details **auto-compile into the project MDR**.

---

## 6. Front-end architecture & edge screens

- **Global IA:** an **8-group** navigation on the **4 industrial pillars** — **Logistics,
  Execution, Defense & Claims, Controls & Insights** — plus **Backbone, Overview, Documents,
  Admin**. Top-bar **Project Switcher** including an **All-Projects** portfolio scope for
  corporate QA/HSE/Operations.
- **Reusable domain shells (build first):** `RegisterPage` (filter, column visibility,
  CSV/PDF export, saved views), `DetailPage` (gate-status banner, tab routing, audit log),
  and the **Dynamic Form Renderer** — before any individual module page.
- **Three Tier-1 edge screens** (wireframed 360–412px mobile + tablet): **Gate Delivery &
  OS&D Capture**, **Look-Ahead & Constraint Board**, **Daily Workface Log** (see
  `KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md` §1).
- **Offline outbox:** **Dexie.js (IndexedDB)** transactional outbox keyed on unique
  `client_op_id`; optimistic UI, idempotent drain/replay on reconnect. (TanStack Query alone
  is no longer the write path.)

---

## 7. Task tracker & phasing

- **Milestones:**
  - **M1 — Phase 1 Freeze Gate (`T-0131`):** contract test freezing `oe_asset_node`,
    `oe_asset_item`, the mixins, and `run_checks` signatures. Post-freeze changes require a
    formal ADR.
  - **M2 — MVP cut line:** Phase 0 + 1 + 2 + **Documents-Lite** (VFS tree, presigned uploads,
    batch inspection PDF export) + **P6 schedule round-trip** (~85–95 tasks).
- **Re-ordering:** `resource_summary` → **Phase 4** (depends on look-ahead + weekly
  commitments); **`procurement` pulled to the front of Phase 3** (before `site_inventory` and
  `site_logistics`); overloaded `T-0124` **split into 2–3** tasks (completion gates vs P6
  sync).

---

## 8. Preserved legacy detail (do NOT discard)

Granular, KAEFER-domain field-level definitions are **retained** and folded into
`docs/spec/modules/` with the overhauls above applied:

- **Welding (`wps`)** and **NDE** — procedures (WPS), welder qualification/continuity, weld
  state machine, RT/UT/MT/PT results, heat/MTR traceability.
- **Coatings / blasting** — ambient (temp, RH, dew point), surface profile, WFT/DFT, holiday
  testing; the dew-point CEL guard.
- **Scaffolding** — erect/inspect/green-tag lifecycle as an AWP constraint.

For exhaustive original text, the archive remains available; it is reference only and
superseded wherever this document or the two new specs speak.
