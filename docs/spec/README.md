# KERP specification — index (single source of truth)

Agents build **strictly** against the documents below — the merged, consolidated spec.
They supersede every legacy document (archived under
[`../archive-legacy-specs/`](../archive-legacy-specs/README.md), reference only).

## Authoritative (build to these)

| Document | Role | Precedence |
|---|---|---|
| [KERP-CONSOLIDATION-AND-DIFF.md](KERP-CONSOLIDATION-AND-DIFF.md) | **Binding** scope culls, data-model overhauls, engine/infra, security, calibration, frontend, phasing — the merge of old + new | **1 (wins on conflict)** |
| [KERP-MASTER-SPEC.md](KERP-MASTER-SPEC.md) | 12-domain architecture catalog, core DDL, module contracts, state machines, event bus, MDR engine | 2 |
| [KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md](KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md) | 3 Tier-1 offline edge screens, IndexedDB/Dexie outbox, CEL engine, calibration gates, persona matrix, project-RLS | 2 |
| [modules/](modules/) | Preserved KAEFER domain detail (quality/ITP/eligibility, welding/NDE, coatings/scaffolding, asset/ltree/JSONB, materials/heat, documents/MDR, commercial/claims, shift handover, P6 import/RSW matching/field-checkout merge, planner/work plans, client plan review, scaffold management, change of scope, register views) with the overhauls applied | 3 |

**Precedence:** consolidation diff → master + edge specs → module detail → (archive, reference only).
Disagreements found while building → `spec-gap` TODO.

## What the merge changed (summary — full detail in the consolidation diff)
- **Culled:** GL/accounting, HR/payroll, e-commerce/POS/CRM, `enterprise_workflows`, `module_builder`, 3D CAD/BIM viewers & `.dwg` parsers. Money is a read-only external mirror.
- **One-engine (TD-002):** one field-type registry (`core/fields`), one inspection record (`oe_inspections_inspection`, kind inspection/itp — TD-045), stateless `eligibility`, new `inspection_requests`. ITP = plan, Request for Inspection = trigger, ITR = evidence: see [modules/quality-and-inspection.md](modules/quality-and-inspection.md).
- **2026-11-13 decisions:** access model (one hidden tenant per deployment, System admins appoint Project Admins, lobby + access requests, project picker), 12-hour sessions, hybrid device PIN, devices, deployment settings and region (diff §4.1–§4.4); nav mapping, Android SQLCipher client storage contract, NFR budgets (diff §6.1–§6.3); Pilot-0 milestone (diff §7); localised template strings (edge §2.2); ADR 0004 Addendum F. Pilot-0 slice spec with planner defaults (Q5a): sync scope and download for offline (diff §6.4), review/return, answer comments, planning fields, photo annotation, inspection PDF and dashboard tiles (`modules/quality-and-inspection.md`), Item scan identifiers (`modules/asset-and-tagging.md`).
- **Overhauls:** ltree hierarchy; JSONB attributes (EAV deleted); **CEL** (not JSONLogic); **SAQ** (not ARQ); **FFD + rectpack** cutting; project-scoped RLS + 4 bypass tests; **RS256/EdDSA + JWKS**; hash-chain audit → pyHanko PAdES at turnover; calibration hard gate; 15 personas; 8-group IA; Dexie offline outbox.

## Reference only (do NOT build to these)
Everything under [`../archive-legacy-specs/`](../archive-legacy-specs/README.md) — the
pre-consolidation OpenBusinessERP analysis, field-ops module specs (SPEC-08/09/10/11,
MODULE-SPECS, ADR-001) and the PR #22 architecture suite. Kept for obscure calculation /
field detail only, superseded wherever the four authoritative sources speak.

## Review reports
The `00`–`09` review suite that produced this consolidation lives in
[`../reviews/`](../reviews/README.md) (reference, not a build target).
