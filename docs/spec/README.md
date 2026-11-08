# KERP specification — index (single source of truth)

Agents build **strictly** against the two documents below. They are the unified,
consolidated output of the architectural review suite and supersede every legacy
document (now in [`../archive-legacy-specs/`](../archive-legacy-specs/README.md),
including the Inspection-ERP PR #23/#24 material that previously lived here).

## Authoritative (build to these)

| Document | Role |
|---|---|
| [KERP-MASTER-SPEC.md](KERP-MASTER-SPEC.md) | Single source of truth: database schemas (DDL), 3-tier topology, module contracts (`service.py`), state machines, the cross-module interaction matrix, domain-event bus payloads, and the automated MDR collation engine. |
| [KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md](KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md) | Operational addendum: the three Tier-1 offline edge screens (Gate Delivery/OS&D, Look-Ahead Constraint Board, Daily Workface Log), the IndexedDB offline mutation outbox, the CEL rule engine (dual-runtime JS/Python), the equipment-calibration hard quality gates, the persona access matrix, project-scoped RLS, and the phase roadmap. |

### How the two relate
`KERP-MASTER-SPEC.md` owns the data model, contracts and events.
`KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md` owns the field UI/UX, the offline sync outbox,
CEL, and the calibration/quality gates (the "one-engine" decisions: TD-002). A task
that touches edge screens, CEL/the unified field engine, offline sync, or calibration
references **both**; everything else references the master spec. On disagreement, the
master spec's data contracts win; raise a `spec-gap` TODO.

## Resolved architectural decisions now baked in
- **TD-002 — One-Engine Architecture (adopted):** one dynamic schema/field registry, one
  inspection-record lifecycle, one centralized `eligibility` verification service, shared
  by forms, item types, ITP templates and inspection requests.
- **CEL (Common Expression Language)** is the sandboxed, dual-runtime (JS client / Python
  server) rule and dynamic-expression engine behind `run_checks`.
- **Equipment calibration hard gates** block expired/invalid tools from executing field
  inspections (Phase 1 backbone).
- **Project-isolated tenancy:** PostgreSQL RLS keyed to `tenant_id` bound to each `project_id`.

## Note on numbering
The spec roadmap (§7.2 of the edge-screens addendum) is an illustrative sequence. Where its
`T-xxxx` numbers collide with ids already in the tracker (e.g. the Phase-1 freeze gate it
labels `T-0131`), the tracker keeps its existing ids and the gate/calibration tasks take the
next free ids — see `docs/TASKS.md`.

## Reference only (do NOT build to these)
Everything under [`../archive-legacy-specs/`](../archive-legacy-specs/README.md) — the
pre-consolidation analysis, module notes, and port templates (incl. SPEC-11, MODULE-SPECS,
ADR-001, the OpenBusinessERP analysis, and the PR #22 architecture suite). Kept for obscure
calculation/field detail only.

## Review reports
The `00`–`09` review suite that produced this consolidation lives in
[`../reviews/`](../reviews/README.md) (reference, not a build target).
