# KERP — Development plan

**What we're building:** the Field Operations Platform — an operational delivery /
field-execution engine that owns field ground truth (asset/scope hierarchy, earned
progress, plant & labour presence, diaries, inspections/ITP/NCR, welds & NDE,
documents) and exports verified actuals back to P6 and the corporate ERP. Full
spec: [`docs/spec/`](spec/README.md). Authoritative scope:
[`DEVELOPMENT-PLAN.md`](spec/field-operations-platform/DEVELOPMENT-PLAN.md) +
[`MODULE-SPECS.md`](spec/field-operations-platform/MODULE-SPECS.md).

**Live progress:** [`TASKS.md`](TASKS.md) · **Open questions / missed / deferred:** [`TODO.md`](TODO.md) · **How agents build it:** [`WORKFLOW.md`](WORKFLOW.md)

## Phases

| Phase | Scope | Planning depth | Exit criterion (from spec §7) |
|---|---|---|---|
| **0** Platform foundations | scaffold, DB + BaseRecordMixin, tenancy/RLS, auth, 3-tier RBAC, module loader, projects/teams/admin, audit, ARQ jobs, presigned uploads, `run_checks` shell, frontend shell | **27 tasks** (S/M) | tenant created, user authenticated & scoped, module toggled per project, rule set run — CI green |
| **1** Asset & Quality backbone | asset hierarchy, dynamic schema, Items, P6 import/export, Excel ingest, config packs, forms engine, inspections/ITP/holds/NCR/punch, signing, compliance, completion-gate rule sets, offline sync core, UI | **30 tasks** + 1 epic | hierarchy from P6, inspections raised/closed against Items/RSWs with enforced gates, completion blocked by open NCRs/holds |
| **2** Welding quality | WPS, welder quals, welds as Items, NDE, R-WPS-1…4 | **8 tasks** | a weld can't be created/welded/accepted outside qualification/NDE rules |
| **3** Logistics | contacts, deliveries + OS&D, inventory, cutting, procurement (basic), equipment, resource summary | 7 epics | delivery booked → gate receipt + OS&D exported; cutting plan confirmed into inventory |
| **4** Execution | schedule, Last Planner/PPC, earned-qty progress, tasks, diaries, field time, reports, supervision/commissioning/closeout, safety | 10 epics | progress as earned qty (gated), actuals exported to P6, PPC computed |
| **5** Documents, collaboration & AI | CDE + VFS, file_*, markups, transmittals, RFIs/submittals, integrations, search/OCR, assistant | 10 epics | docket captured → OCR'd → searchable; assistant answers from live data and only proposes |
| **6** Defense, claims & controls | variations/MoC, claims evidence, physical EVM, reporting/BI, risk, ESG, workflows | 7 epics | measured variation exported + priced mirror back; physical EVM + claims binder |
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
4. **PROPOSAL-port-from-aip + MODULE-SPEC-11 (PR #24) adopted** — one shared forms/item_types engine, single inspection record, `eligibility` service, new `inspection_requests` module (E-01-01). Shapes T-0102, T-0109, T-0110.

## Decisions (defaults applied unless you override — see `TODO.md`)

| ID | Question | Default in use |
|---|---|---|
| TD-001 | Port Inspection-ERP core or rewrite | Port & adapt |
| TD-002 | Adopt the AIP one-engine dedup proposal | **Adopted** (PR #24 is spec) — resolved |
| TD-003 | SSO vs local auth | Local JWT behind an `AuthProvider` seam |
| TD-004 | Hosting (Coolify/MinIO vs AWS) | S3-compatible storage; deploy target TBD before T-0004 publishes |
| TD-007 | Multi-tenant plumbing in a single-company product | Keep tenant_id + RLS |
| TD-009 | MVP cut line (~94 modules) | None set — recommend end of Phase 2 + documents |
