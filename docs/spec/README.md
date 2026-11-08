# KERP specification — index

Imported 2026-11-08 from `kingdonk-ops/Inspection-ERP` PR #23 (merged) and PR #22 (open, branch `docs/architecture-suite`), plus later additions on Inspection-ERP `main` (PROPOSAL-port-from-aip, MODULE-SPEC-11).

## Authoritative (build to these)

| Document | Role |
|---|---|
| [field-operations-platform/DEVELOPMENT-PLAN.md](field-operations-platform/DEVELOPMENT-PLAN.md) | Scope, principles, boundaries, RBAC, verification engine, phases & exit criteria |
| [field-operations-platform/MODULE-SPECS.md](field-operations-platform/MODULE-SPECS.md) | Per-module spec for every in-scope module |
| [field-operations-platform/ADR-001-runtime-and-storage-architecture.md](field-operations-platform/ADR-001-runtime-and-storage-architecture.md) | Runtime topology: Nginx, 3 Uvicorn, ARQ+Redis, Postgres16+PostGIS, presigned uploads |
| [openbusinesserp-analysis/spec/MODULE-SPEC-08-new-modules.md](openbusinesserp-analysis/spec/MODULE-SPEC-08-new-modules.md) | `asset_management`, `wps`, `cutting` (+ P6 round-trip §2.5) |
| [openbusinesserp-analysis/spec/MODULE-SPEC-09-reanchored-and-portal.md](openbusinesserp-analysis/spec/MODULE-SPEC-09-reanchored-and-portal.md) | Re-anchored schedule/progress/costmodel/variations + phased portal |
| [field-operations-platform/spec/MODULE-SPEC-10-workpacks-awp.md](field-operations-platform/spec/MODULE-SPEC-10-workpacks-awp.md) | `workpacks`, `awp`, documents VFS |
| [openbusinesserp-analysis/FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md](openbusinesserp-analysis/FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md) | Module register (added/modified/kept/removed) + resolved decisions D3/D5/D7/D8 |

## Pending decision

| Document | Status |
|---|---|
| [field-operations-platform/PROPOSAL-port-from-aip.md](field-operations-platform/PROPOSAL-port-from-aip.md) + [spec/MODULE-SPEC-11-inspection-requests.md](field-operations-platform/spec/MODULE-SPEC-11-inspection-requests.md) | "Proposal for review" → **TD-002** |

## Reference only (do not build to these directly)

- `openbusinesserp-analysis/MASTER-REDEVELOPMENT-PLAN.md`, `openbusinesserp-*.md`, `spec/MODULE-SPEC-00…07b` — the original full-ERP analysis; many modules were **removed** in the field-ops pivot (see FINAL-PLAN §1.4). Use only for entity/field detail on modules still in scope.
- `REVISED-PLAN-*.md` — superseded by FINAL-PLAN.
- `architecture-suite-reference/01…04` — reverse-engineered description of the **as-built Inspection-ERP** (stack, base modules, extension modules, frontend/UX). The template for patterns we port (TD-001), not KERP's scope.

## Precedence when documents disagree
DEVELOPMENT-PLAN + MODULE-SPECS → ADR-001 → MODULE-SPEC-08/09/10 → FINAL-PLAN → everything else. Disagreements found while building → `spec-gap` TODO.
