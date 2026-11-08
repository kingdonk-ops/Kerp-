# Proposal — Port the strongest Project-AIP capabilities into the Field Operations Platform

> **Status:** Proposal for review. **Scope:** a deep-dive comparison of the Project-AIP blueprint against this platform's specs (`DEVELOPMENT-PLAN.md`, `MODULE-SPECS.md`), the **dedup** of overlapping inspection modules, and a prioritised list of AIP capabilities worth replicating here. Companion build-ready specs are in [`spec/MODULE-SPEC-11-inspection-requests.md`](spec/MODULE-SPEC-11-inspection-requests.md). Everything below stays inside this platform's doctrine: single company; `BaseRecordMixin` (entity URI, `tenant_id`, hash, provenance, revision) + an RLS policy on every table; heavy work in the ARQ worker (ADR-001); verification is configuration via `run_checks(context, rule_set)`; anything owned by a system of record is an external **mirror**; AI mutations carry `source="ai"` and need human confirmation.

---

## 0. The one thesis

**Project-AIP runs one no-code engine everywhere; our spec runs three or four half-engines.**

In AIP, `forms` (the template designer) and `item_types` (the asset-attribute designer) **share a single field-type registry and a single sandboxed evaluator** (a TypeScript package with a Python port tested on the same fixtures). The identical builder produces:

- the **asset's own attribute schema** (Pipe → Insulated Pipe, Valve, Vehicle, Equipment, Consumable, RSW…), and
- every **template**, by `kind`: `inspection` (coating/blasting ITR, UTT survey, weld inspection, **vehicle pre-start / CB deck / daily pre-start / Take 5 / SWMS**), `itp`, `rfi`, `permit`, `test_report`.

In our `MODULE-SPECS.md`, the template designer (`forms`, thin) and the asset schema (`asset_management` `oe_asset_field_def`/`oe_asset_layout`) are **separate mechanisms**, and inspection/ITP logic is spread across `inspections` + `qms` + `construction_control` + `workpacks` (our own spec already says to "resolve the dedup in the rebuild"). **Unifying on AIP's one-engine model is the dedup, and it is the single highest-value change in this proposal.** Everything else hangs off it.

---

## 1. Dedup — the collapses we should make

| Collapse (today, our spec) | Into (proposed) |
|---|---|
| `forms` designer **+** `asset_management` `oe_asset_field_def`/`oe_asset_layout` (two designers) | **One engine**: `forms` (templates, all kinds) **+** `item_types` (asset schema) sharing one field-type registry + one shared TS/Python evaluator. Spec: [MODULE-SPEC-11 §2–§3](spec/MODULE-SPEC-11-inspection-requests.md). |
| `inspections` (thin register) **+** `qms` (ITP plans/templates) **+** `construction_control` (hold gates/criteria) — overlapping inspection halves | **One inspection record** (`kind` = inspection / itp / rfi); **templates** come from the `forms` engine; **gates** run through `run_checks`; **ITP dossier/MDR** via `workpacks`. `qms` keeps the audit register; `construction_control` keeps the conformance/hold-gate engine. |
| `rfi` **+** a would-be inspection-scheduling object (naming collision: Request for **Information** vs Request for **Inspection**) | Keep `rfi` = *Request for Information*. Add the new **`inspection_requests`** module = *Request for Inspection* (Section E / MODULE-SPEC-11 §1), with a disambiguating prefix in cross-module lists (AIP's resolution). |
| `credentials` **+** `wps` welder-qual **+** `compliance_docs` **+** `qms` calibration — four "is it valid?" checks | One **`eligibility`** gate service answering *"is this person/instrument/material valid right now for this step?"*, consumed by `run_checks` and surfaced inline on the inspection. Registers stay where they are. |

None of these remove capability — they remove duplicated surfaces and give one place to build and test.

---

## 2. What Project-AIP has that we don't (the gap list)

Grouped by the components you called out as primary — **inspections, ITPs, templates, scheduling** — then the rest. "Port" = replicate; "Adapt" = replicate but fit our single-company/physical doctrine; "Optional" = valuable but lower priority.

### 2.1 Template / form engine — **Port (P1, highest value)**

| AIP capability | What it buys | Our spec today | Action |
|---|---|---|---|
| **One shared field-type registry** (text, number, decimal, date, **measurement value+unit**, dropdown/multiselect, repeating table, photo/video/audio, **signature**, GPS, QR/barcode, asset/user/document reference, **instrument/calibration reference**, **calculated**) used by forms *and* asset types | One palette for every template and every asset detail | Split between `forms` and `oe_asset_field_def` | **Port** |
| **Template kinds** `inspection\|itp\|rfi\|permit\|test_report` on one model | Vehicle check, Take 5, SWMS, ITR, ITP, permit — one object, a `kind` field | `forms` kinds exist but logic lives in 3 modules | **Port** |
| **Conditional logic** `visible_if`/`required_if` + **calculated fields** (restricted formula, function allow-list — corrosion rate, DFT average) | Real engineering inspections, not flat checklists | Not specced | **Port** |
| **Revision immutability**: freeze schema JSONB at publish, `schema_hash` (sha256), DB trigger blocks editing a non-draft, **in-flight inspections keep their revision** | Legally defensible — the form can't change under a completed record | Snapshots only | **Port** |
| **Shared evaluator, TS + Python, same fixtures** | Identical logic on web and offline device | Not specified | **Port** |
| **Instrument-reference field + live calibration gate** (generic, any reading) | A reading can't be taken on an out-of-cal gauge; gauge enters the traceability chain | We gate welds via `wps` only | **Port** |
| **Test-fill + publish checks + definition lint** (run formulas on sample answers, block publish on broken refs/cycles) | Templates can't ship broken | Not specced | **Port** |
| **Reusable section/question library**, versioned (DFT readings, environmental conditions, weld details) | Build the 50th ITR in minutes | Not specced | **Port** |
| **Spreadsheet/PDF template import assistant** (AI field detection, human review) | Onboard existing paper forms fast | `data_import` is generic | **Adapt** |
| **Print-accurate report-slot mapping** (form field → designed PDF slot) | The digital form and its PDF are one design | `reporting` renders, no slot designer | **Port** |

### 2.2 Asset-detail engine (`item_types`) — **Port (P1)**

| AIP capability | What it buys | Our spec today | Action |
|---|---|---|---|
| **Same field-type registry** drives asset attributes | "The same engine does asset details" | Separate `oe_asset_field_def` | **Port** |
| **`auto_create_itp` / `auto_create_inspection`** with default templates per item type | Defining "Insulated Pipe" spins up its CUI ITP | Not specced | **Port** |
| **Schema versioning + migration preview with impact counts** before an attribute type changes; assets pin `item_type_version` | Safe schema evolution across live assets + completed inspections | `asset_management` has dynamic schema, no migration preview | **Port** |
| **Type inheritance** (base Pipe → Insulated Pipe; add-and-tighten only) | No duplicated schemas | Not specced | **Port** |
| **Template field → asset attribute write-back on approval** (e.g. measured wall thickness updates the asset) | The asset's live state is fed by its inspections | Not specced | **Port** |
| **Units & tolerances** on measurements with out-of-range flags → rules | Automatic out-of-tolerance detection | Partial | **Adapt** |
| **Custom status sets mapped to a neutral lifecycle enum** (draft/scoped/planned/in progress/on hold/completed/cancelled/decommissioned) | Rename statuses per site; gates rely on stable codes | Partial | **Port** |
| **Starter packs** (NDT/CUI, welding, insulation, coating) as versioned JSON | Fast onboarding of domain expertise | `oe_asset_config_pack` partial | **Port** (unify with form seeds) |

### 2.3 Inspection record integrity — **Port (P1)**

| AIP capability | What it buys | Our spec today | Action |
|---|---|---|---|
| **Append-only, hash-chained multi-party sign-off** (`auth_strength` password/mfa/pin/pades, `content_hash`+`prev_hash` chain, `eligibility_result` snapshot, `REVOKE UPDATE/DELETE`) | Record-level tamper evidence — the Digital-Trust evidence you want | Sign via central `signing`; weaker record chain | **Port** |
| **Append-only responses** with `client_id` + `sync_version` (idempotent offline push); corrections are superseding entries, never edits | Clean offline sync + an immutable answer trail | `field_diary` ledger is general | **Port** |
| **Inline eligibility banner** before each step (signer creds, instrument cal, material use-by) | The gate is visible where the work happens | Logic in `credentials`/`wps`, not surfaced inline | **Port** |
| **Competency-aware assignment** (suggest valid+available; record `competency_ok` at assignment) | Right inspector, provably | Not specced | **Port** |
| **Programmes / recurrence** (triggers calendar / **cert-expiry** / **issue** / ad-hoc; computed `next_due`; `asset_path_scope` ltree) | Re-inspection & re-coating campaigns drive themselves | Recurrence sits in `service`, not at inspection level | **Port** |

### 2.4 Scheduling — Request for Inspection — **Build (P1, net-new; Section E)**

Neither spec has the flow you described (seamless in-system request that still produces a PDF record for the MDR, with approved/delayed/no-show + reasons and client-vs-us delay attribution). AIP supplies the raw material (`inspection_bookings`, `booking_attendees`); our `portal` v3 visit scheduler + `review_authority` + `meetings` supply the rest. Composed into a full module in **[MODULE-SPEC-11 §1](spec/MODULE-SPEC-11-inspection-requests.md)**.

### 2.5 RFI & submittals — **Adapt (P2)**

| AIP capability | Our spec today | Action |
|---|---|---|
| **Submittal-as-gate rule** (a required submittal — e.g. approved NDT procedure — blocks an inspection/RSW task until an accepted review code) | `submittals` thin | **Adapt** |
| **Inspection-paperwork submittal types** (weld/NDT procedures, personnel quals, calibration records, **ITP approvals**) | Not called out | **Adapt** |
| **Convert RFI → variation candidate** (pre-filled evidence trail) | `rfi` links `change_order_id` only | **Adapt** |
| **Response-time analytics** (by party/discipline/area) + **resubmission comparison** + tenant-configurable review codes | Not specced | **Adapt** |

### 2.6 The rest — **Optional / lower priority**

| AIP capability | Note | Action |
|---|---|---|
| **Terminology dictionary** (`terms`) — every label a key with layered overrides | We drop i18n (single company), but a dictionary still earns its place for renaming (RSW, Punch/Snag/Deficiency) without code | **Optional (P3)** |
| **AI governance** (`ai_gov`) — single gateway + AI data-flow register | We have the "AI mutations are proposals" principle; a data-flow register helps client security reviews (mining/IRAP-type) even single-company | **Optional (P3)** |
| **Traceability as a searchable surface** (`components`) — "what has this welder worked on / where was this batch used / which instrument measured this" | We hold the data (`wps` + asset Items + `file_search`); AIP exposes it as queries | **Adapt (P2)** — a query surface over `search`/`retrieval`, no new store |
| **"My Work" + cross-module deadline escalation** (`tasks`) | Our `tasks`/`deadlines` are thinner | **Optional (P3)** |
| **Security & compliance programme** (`security`) | Largely a multi-tenant/SaaS posture; mostly out of scope here | **Skip** (revisit if sold as SaaS) |

---

## 3. Build-ready specs in this batch

[`spec/MODULE-SPEC-11-inspection-requests.md`](spec/MODULE-SPEC-11-inspection-requests.md) contains, in the house format:

1. **`inspection_requests`** — the full new module (Section E): raise → notice → confirm → witness/no-show/waive/fail, an append-only hash-chained what/why/who event ledger, reason codes with cause-party attribution, a PDF Request/Confirmation/Outcome notice filed into the MDR, auto delay-events on client no-show/decline, one-click change-notice/EOT, and a client-vs-contractor delay dashboard.
2. **`forms` (extension)** — unify to AIP's engine (§2.1).
3. **`item_types` / `asset_management` (extension)** — AIP's asset-schema engine (§2.2).
4. **`inspections` (extension)** — record-integrity (§2.3).
5. **`eligibility` (new service)** — the unified gate (§1).
6. **`rfi` / `submittals` (extension)** — gate rule, paperwork types, convert-to-variation, analytics (§2.5).

## 4. Suggested sequencing

1. **Shared field-type registry + shared TS/Python evaluator** — the foundation; nothing else lands cleanly first.
2. **`forms` engine** (kinds, conditional/calculated, revision immutability + `schema_hash`, instrument-cal field, test-fill/lint, section library, report-slot mapping).
3. **`item_types`** (shared registry, auto-create ITP/inspection, versioning + migration preview, inheritance, write-back, starter packs).
4. **`inspections`** record-integrity (hash-chained sign-off, idempotent responses, inline eligibility, competency assignment, programmes) + the **`eligibility`** gate.
5. **`inspection_requests`** (Section E).
6. **`rfi`/`submittals`** enrichment; then the optional **`terms`**, **`ai_gov`**, **traceability query surface**.
