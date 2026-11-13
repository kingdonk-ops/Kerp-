# KERP — Development plan (merged)

**What we're building:** KERP, an industrial Field Operations & Commercial Execution
platform for heavy construction / fabrication / remediation / turnaround (KAEFER-style).
It owns field ground truth — asset/scope hierarchy, earned progress, plant & labour
presence, diaries, inspections/ITP/NCR, welds & NDE, materials & heat traceability,
documents/MDR — and exports verified actuals to P6 and the corporate ERP. It is **not**
back-office ERP and **not** ongoing plant maintenance.

**Single source of truth:** [`docs/spec/`](spec/README.md) —
[`KERP-MASTER-SPEC.md`](spec/KERP-MASTER-SPEC.md) (12-domain architecture, core DDL,
events) · [`KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md`](spec/KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md)
(edge screens, offline outbox, CEL, calibration) · [`KERP-CONSOLIDATION-AND-DIFF.md`](spec/KERP-CONSOLIDATION-AND-DIFF.md)
(**binding** scope culls + overhauls) · [`spec/modules/`](spec/modules/) (preserved
KAEFER domain detail). Legacy analysis is archived under
[`archive-legacy-specs/`](archive-legacy-specs/README.md) — reference only.

**Live progress:** [`TASKS.md`](TASKS.md) · **Open questions / decisions:** [`TODO.md`](TODO.md) · **Pipeline:** [`WORKFLOW.md`](WORKFLOW.md)

## Scope (from the consolidation diff)

**In:** 12 domains (MOD-01…12) — identity/RBAC, project master & engineering tagging,
estimating/WBS-CBS, commercial/SoR/claims, AWP, field execution, materials/heat trace,
QC/ITP, NCR/punch, asset integrity/preservation/hydro, documents/MDR, dynamic
fields/integrations — plus the edge/quality layer (CEL, calibration gates, offline).

**Out (culled):** General Ledger / double-entry / bank rec / depreciation / tax / direct
invoicing / AR-AP · HR / payroll / time-billing · e-commerce / POS / CRM pipelines ·
`enterprise_workflows` (→ `approval_routes`) · `module_builder` (runtime no-code) ·
interactive 3D CAD/BIM viewers & native `.dwg` parsers (→ PDF viewer + markups +
pluggable parser adapter). Money is a **read-only external mirror** only.

## Phases

| Phase | Scope | Depth | Exit criterion |
|---|---|---|---|
| **0** Platform substrate & security core | scaffold, DB + BaseRecordMixin, **project-scoped RLS** (+4 bypass tests), **RS256/EdDSA JWT + JWKS**, field-worker magic-link+PIN, 15-persona RBAC, module loader, **SAQ** jobs, presigned uploads, **CEL runtime substrate**, `run_checks` shell, hash-chain audit, frontend shell (8-group IA) + reusable shells; access model (System admin → Project Admins, lobby, project picker), 12 h sessions, hybrid PIN, device registry, deployment settings (2026-11-13) | **48 tasks** | tenant/project created, user authed & scoped, module toggled, rule set run — CI green |
| **1** Asset backbone, one-engine & quality core | **ltree** hierarchy, **JSONB** field registry (EAV deleted), Items + CML stubs, P6 import/export (idempotent + unmatched-row linking), **one-engine** forms+CEL, **unified inspection record** + **eligibility**, **equipment calibration** register + hard gate, qms/construction_control/inspection_requests, NCR/punch, signing, completion-gate + P6 rule sets, offline sync core (Dexie), UI, **M1 freeze gate (T-0131)**; Capacitor Android build + SQLCipher store, FCM push, Pilot-0 gate (2026-11-13); Pilot-0 slice: sync scope + download, review/return, scan-to-locate, photo annotation, inspection PDF, dashboard tiles | **59 tasks** | P6→hierarchy; inspections gated by CEL; expired tool blocked; completion blocked by open NCR; backbone contract frozen |
| **2** Welding quality & traceability | WPS, welder quals/continuity, welds as Items, NDE, heat/MTR trace, R-WPS rules | **9 tasks** | a weld can't be accepted outside qualification/NDE rules |
| **3** Logistics & supply chain | **contacts**, **procurement (front)**, materials/MRIR+heat, inventory, **cutting (FFD + rectpack)**, Gate Delivery edge screen, equipment/fleet | 7 epics | delivery → gate receipt + OS&D; cutting plan → inventory |
| **4** Execution & LPS | schedule, Look-Ahead board, earned-qty progress, Daily Workface Log + CBS burn, field time, estimating/EVM, tasks, **resource rollup (moved here)**, safety/PTW, supervision/closeout, **shift/swing handover register** | 11 epics | progress as earned qty (gated), actuals → P6, PPC computed |
| **5** Documents, CDE & MDR | VFS/CDE + parser adapter, markups/plan room, transmittals, TQ/submittals, **MDR collation engine**, search/OCR, integrations/event bus, assistant | 8 epics | docket → OCR → searchable; MDR dossier compiled; assistant proposes only |
| **6** Defense, variations & claims | commercial/SoR, progress claims (G702/G703) + `claim.approved`, variations + delay attribution + TIA, physical EVM, reporting/BI, risk, cases + `approval_routes`, ESG | 8 epics | measured variation → priced mirror back; physical EVM + claims binder |
| **6b** Asset integrity & work packaging | preservation + hydrotest packs, AWP constraint engine (7 gates), field work packs + MDR compile | 3 epics | CWP→IWPs, offline stage sign-off, hydro gating, MDR filed |
| **7** Client portal | v1 read-only → v2 co-sign (pyHanko) → v3 visit scheduler + client NCR | 3 epics | per sub-phase |

**Rolling-wave planning.** Phases 0–2 are broken to S/M tasks now; Phases 3–7 are epics
(`needs-breakdown`) the planner splits once the **M1 freeze gate (T-0131)** is green —
breaking them down earlier would bake in guesses Phases 0–2 will invalidate.

## Milestones

- **M1 — Phase 1 Freeze Gate (`T-0131`):** contract test freezing `oe_asset_node`,
  `oe_asset_item`, the mixins and `run_checks` signatures; CEL JS/Python parity; seam
  hashes. Post-freeze changes require a formal ADR in `docs/decisions/`.
- **Pilot-0 — first pilot (user, 2026-11-13; TD-173):** 8 inspectors, one project, **Android
  only**. Usable when an inspector can download work, inspect offline, sign and sync, and a
  reviewer can approve or return the inspection and get a PDF. No P6 import/export and no
  Phase 2. Gate: **T-0146** (NFR budgets become hard CI gates, checklist below complete).
  - **Phase 0** as planned, including the 2026-11-13 additions: T-0037 (12 h sessions, hidden
    tenant), T-0038 (hybrid PIN), T-0039–T-0042 (Project Admins, lobby, access requests,
    picker), T-0043 + T-0048 (device registry, revoke/wipe), T-0044 (deployment settings,
    offline window), T-0045–T-0047 (NFR harness, glove/high-contrast tokens, localised template
    strings); plus the AWS production deploy in `ap-southeast-2` (TD-004, TD-171).
  - **Daily re-login dependency:** sessions end 12 h after sign-in, so the pilot inspectors
    sign in every day. Entra ID SSO (TD-043) or magic-link e-mail delivery (TD-084) must be
    live in the pilot deployment (checked by T-0146).
  - **Phase 1 subset:** T-0101, T-0103 (hierarchy + Items), T-0107 (equipment import), T-0110
    (field registry + CEL), T-0111 (inspection record), T-0116/T-0117 (defects, TD-167), T-0119
    (signing), T-0126/T-0127 (offline sync), T-0133 (field checkout), T-0141–T-0145 (Android
    build, SQLCipher store, device session + offline unlock, FCM push, sync budget).
  - **Pilot-0 slice** (cut 2026-11-13, user Q5a; spec with planner defaults in diff §6.4,
    `quality-and-inspection.md` and `asset-and-tagging.md`):
    - sync scope and download for offline (TD-162): T-0147 (picks, candidates with size, paged
      scoped pull), T-0148 (client UUIDv7 ids, answer conflict flag), T-0149 (download screen,
      local purge);
    - review/return (TD-165): T-0150 (`SUBMITTED` / `RETURNED`, approve/return, submit waits for
      attachments), T-0151 (comments on single answers), T-0152 (assignee, planned start,
      duration, overdue), T-0153 (review and returned-work screens);
    - scan-to-locate (TD-163): T-0154 (Item scan identifiers + lookup), T-0155 (camera/NFC scan,
      offline lookup);
    - photo annotation (TD-166): T-0156 (capture time, location, annotation JSON), T-0157
      (capture + annotation editor);
    - inspection PDF (TD-169): T-0158 (per inspection, annotations burned in), T-0159 (batch
      export);
    - dashboard tiles: T-0160 (progress, overdue, open defects);
    - end-to-end: T-0161 (Pilot-0 journey).
  - **Also in the gate through dependencies** (T-0146 depends on them transitively): T-0136
    (terminology layer, via the PDF T-0158), T-0140 (inspection date, self-approval, via T-0150)
    and T-0104 (P6 `.xer` import, via T-0133 → T-0147; to be decided in TD-214).
  - **Not in the pilot:** inspection calendar, general recurrence and work orders (TD-211); video,
    audio, barcode/QR answer fields and reference images (TD-212).
  - **Open before the gate:** client co-sign on the inspector's device (TD-213); capture screen,
    ad-hoc inspection UI and T-0133's dependency on the deferred P6 import (TD-214).
  - **Deferred for the pilot:** P6 import/export (T-0104–T-0106, T-0125, T-0129), Phase 2,
    Phases 3–7. The client signs on the inspector's device with a PIN instead of the portal.
- **M2 — MVP cut line (next milestone after Pilot-0):** Phase 0 + 1 + 2 + **Documents-Lite**
  (VFS tree, presigned uploads, batch inspection PDF export) + **P6 schedule round-trip**
  (~85–95 tasks).

## Key engineering decisions (baked into the spec — see consolidation diff)

| Area | Decision |
|---|---|
| Rule engine | **CEL** (celpy server / JS client), CI parity fixtures — replaces JSONLogic/ad-hoc JS |
| Queue | **SAQ** over Redis (interactive + batch) — replaces ARQ |
| Hierarchy | **ltree** `tree_path` (GiST) + `parent_id` source-of-truth + `display_path` |
| Attributes | **`custom_fields` JSONB** only (EAV `oe_asset_field_value` deleted); `oe_asset_field_def` = JSONSchema registry |
| Inspection | **one record** `oe_inspections_inspection` (kind inspection/itp/rfi) + stateless **eligibility** |
| Cutting | in-house **FFD** (1D) + **rectpack** (2D); `libnest2d`/`nest2D` banned (GPL) |
| Security | **project-scoped RLS** + 4 bypass tests; **RS256/EdDSA + JWKS**; hash-chain audit; pyHanko PAdES at turnover |
| Calibration | lifecycle + SAQ OCR + supersede-lock + hard CEL gate (expired tool blocked) |
| Frontend | 8-group IA on 4 pillars (nav mapping diff §6.1); RegisterPage/DetailPage/Dynamic Form Renderer first; **Dexie** offline outbox for web PWA apps |
| Access (2026-11-13) | One hidden tenant per deployment; System admins appoint Project Admins (`create_projects`); creator gets PER-09; lobby + access requests; project picker; one URL; admin roles never confer hold/sign-off (diff §4.1) |
| Sessions & devices (2026-11-13) | 15 min access, 12 h refresh from sign-in (daily re-login); hybrid device PIN, offline unlock independent of the server session; offline window 7 days default, 14 max; device registry + remote revoke/wipe (diff §4.2–§4.4) |
| Field client (2026-11-13) | Capacitor Android 11+, SQLCipher SQLite + file-system media, managed Play/MDM; one client storage contract and a client-agnostic sync contract (diff §6.2); NFR budgets warn now, hard gate before Pilot-0 (diff §6.3) |
| Region (2026-11-13) | Per-deployment setting: AWS `ap-southeast-2` for Australian deployments (diff §4.4) |

## Open decisions (see `TODO.md`)
TD-003 (SSO vs local JWT), TD-004 (hosting target), TD-072 (ADR 0004 rule-set defaults per project), TD-073 (handover first-shift trigger),
TD-013 (review-gate ignores tracker), TD-039 (dropped AIP-proposal extras). TD-002/005/007/008/009/012 are resolved by this merge; TD-001 by ADR 0001 (`docs/decisions/`). TD-002 is carried into code by ADR 0002 (`docs/decisions/0002-port-one-engine.md`). TD-034 (remaining Inspection-ERP core packages) is resolved by ADR 0007.
