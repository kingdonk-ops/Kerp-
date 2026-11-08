# KERP — Development plan

**What we're building:** the Field Operations Platform — an operational delivery /
field-execution engine that owns field ground truth (asset/scope hierarchy, earned
progress, plant & labour presence, diaries, inspections/ITP/NCR, welds & NDE,
documents) and exports verified actuals back to P6 and the corporate ERP. Single
source of truth: [`docs/spec/`](spec/README.md) —
[`KERP-MASTER-SPEC.md`](spec/KERP-MASTER-SPEC.md) (data model, contracts, events) +
[`KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md`](spec/KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md)
(edge screens, offline outbox, CEL, calibration gates). The earlier Inspection-ERP
PR #23/#24 material and PR #22 suite are archived under
[`docs/archive-legacy-specs/`](archive-legacy-specs/README.md) — reference only.

**Live progress:** [`TASKS.md`](TASKS.md) · **Open questions / missed / deferred:** [`TODO.md`](TODO.md) · **How agents build it:** [`WORKFLOW.md`](WORKFLOW.md)

## Phases

| Phase | Scope | Planning depth | Exit criterion (from spec §7) |
|---|---|---|---|
| **0** Platform foundations | scaffold, DB + BaseRecordMixin, tenancy/RLS, auth, 3-tier RBAC, module loader, projects/teams/admin, audit, ARQ jobs, presigned uploads, `run_checks` shell, frontend shell | **27 tasks** (S/M) | tenant created, user authenticated & scoped, module toggled per project, rule set run — CI green |
| **1** Asset & Quality backbone (one-engine) | asset hierarchy, **unified field registry + CEL evaluator (one-engine, TD-002)**, item_types, forms engine + templates/revisions, TS-evaluator parity, Items, P6 import/export, Excel ingest, config packs, single inspection record + `eligibility`, ITP/holds/NCR/punch, **equipment calibration register + hard ingest gates (T-0134)**, signing, compliance, completion-gate rule sets, offline sync core, UI, **Phase 1 backbone-freeze contract gate (T-0133)** | **34 tasks** + 1 epic | hierarchy from P6; inspections raised/closed against Items/RSWs with CEL-enforced gates; expired tools blocked; completion blocked by open NCRs/holds; backbone contract frozen |
| **2** Welding quality | WPS, welder quals, welds as Items, NDE, R-WPS-1…4 | **8 tasks** | a weld can't be created/welded/accepted outside qualification/NDE rules |
| **3** Logistics | contacts, **procurement (pulled forward)**, deliveries + OS&D (Gate Delivery edge screen), inventory, cutting, equipment/fleet | 6 epics | delivery booked → gate receipt + OS&D exported; cutting plan confirmed into inventory |
| **4** Execution | schedule, Last Planner/PPC (Look-Ahead board), earned-qty progress, tasks, diaries + Daily Workface Log, field time, **resource-demand rollup (moved from Phase 3)**, reports, supervision/commissioning/closeout, safety | 11 epics | progress as earned qty (gated), actuals exported to P6, PPC computed |
| **5** Documents, collaboration & AI | CDE + VFS, file_*, markups, transmittals, RFIs/submittals, integrations, search/OCR, assistant | 10 epics | docket captured → OCR'd → searchable; assistant answers from live data and only proposes |
| **6** Defense, claims & controls | variations/MoC, claims evidence, physical EVM, reporting/BI, risk, ESG, cases + approval routes | 7 epics | measured variation exported + priced mirror back; physical EVM + claims binder |
| **6b** Work packaging | AWP constraint engine, field work packs + MDR compile | 2 epics | CWP → IWPs, offline stage sign-off, QC hold release, MDR PDF filed |
| **7** Client portal | v1 read-only → v2 co-sign → v3 visit scheduler + client NCR | 3 epics | per sub-phase |

**Rolling-wave planning.** Phases 0–2 are broken down to S/M tasks now. Phases 3–7
are epics (`needs-breakdown`); the planner agent breaks each phase down when the
previous phase's backbone contract is frozen — breaking them down today would
bake in guesses that Phases 0–2 will invalidate (spec §9: "freeze the backbone
contract before 3–6").

## Changes from the source spec (recorded, not silent)

1. **Offline sync core moved into Phase 1** (T-0125/T-0126) — spec §9 requires it be designed in from Phase 0/1, though its modules are listed under Phase 4. → TD-006.
2. **`module_builder`** has no buildable spec → a spec-writing task (T-0027) instead of code. → TD-005.
3. **Admin/User-Manager UI** added (T-0025) — the RBAC matrix (§4) needs a UI to assign personas and scope; no module spec covers it.
4. **PROPOSAL-port-from-aip + MODULE-SPEC-11 (PR #24) adopted** — one shared forms/item_types engine, single inspection record, `eligibility` service, new `inspection_requests` module (E-01-01). Shapes T-0102, T-0109, T-0110, T-0131, T-0132.
5. **Spec consolidated to `docs/spec/`** — `KERP-MASTER-SPEC.md` + `KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md` are now the single build target; the Inspection-ERP PR #23/#24 material and PR #22 suite moved to `docs/archive-legacy-specs/` (reference only). Every task `spec:` repointed to the two new files.
6. **Equipment calibration elevated to Phase 1** (T-0134) — register + OCR cert ingest + hard field gate (expired tool blocked), per the edge-screens addendum §3.
7. **Phase 1 backbone-freeze contract gate** (T-0133) added as the Phase-1 exit: schema-immutability tests on `oe_asset_node`/`oe_asset_item`, CEL evaluator JS/Python parity suite, and `run_checks`/`eligibility`/VFS seam hash-checks (spec addendum §7.1). The spec labels this gate `T-0131`; that id is already the forms-templates task, so the tracker uses the next free id (`T-0133`).
8. **Phase 3 re-ordered:** `procurement` pulled forward (after `contacts`); `resource_summary` deferred to Phase 4 (demand rollup belongs with execution/look-ahead data; `E-03-07` → `E-04-11`).
9. **`enterprise_workflows` retired** in favour of `approval_routes` (E-06-07).

## Decisions (defaults applied unless you override — see `TODO.md`)

| ID | Question | Default in use |
|---|---|---|
| TD-001 | Port Inspection-ERP core or rewrite | Port & adapt |
| TD-002 | Adopt the AIP one-engine dedup proposal | **Adopted** (PR #24 is spec) — resolved |
| TD-003 | SSO vs local auth | Local JWT behind an `AuthProvider` seam |
| TD-004 | Hosting (Coolify/MinIO vs AWS) | S3-compatible storage; deploy target TBD before T-0004 publishes |
| TD-007 | Multi-tenant plumbing in a single-company product | Keep tenant_id + RLS |
| TD-009 | MVP cut line (~94 modules) | None set — recommend end of Phase 2 + documents |
