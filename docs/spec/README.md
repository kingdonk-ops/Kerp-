# KERP specification — index

Everything in this folder came from `kingdonk-ops/Inspection-ERP`:

| Source PR | What it delivered | Where it is here |
|---|---|---|
| **#23** (merged) — OpenBusinessERP architecture & per-module analysis | Field Operations Platform plan, module specs, ADR-001, MODULE-SPEC-08/09/10, final plan + the original analysis | `field-operations-platform/` (except the two PR #24 files below) · `openbusinesserp-analysis/` |
| **#24** (merged) — port the strongest Project-AIP capabilities | One-engine dedup proposal, MODULE-SPEC-11, MODULE-SPECS §15 pointer | `field-operations-platform/PROPOSAL-port-from-aip.md` · `field-operations-platform/spec/MODULE-SPEC-11-inspection-requests.md` · `field-operations-platform/MODULE-SPECS.md` (§15) |

PR #22 (architecture suite of the as-built Inspection-ERP) is **not** part of the spec; it is parked in `docs/archive/` for reference only.

## Authoritative (build to these)

| Document | Role |
|---|---|
| [field-operations-platform/DEVELOPMENT-PLAN.md](field-operations-platform/DEVELOPMENT-PLAN.md) | Scope, principles, boundaries, RBAC, verification engine, phases & exit criteria |
| [field-operations-platform/MODULE-SPECS.md](field-operations-platform/MODULE-SPECS.md) | Per-module spec for every in-scope module (incl. §15 → SPEC-11) |
| [field-operations-platform/ADR-001-runtime-and-storage-architecture.md](field-operations-platform/ADR-001-runtime-and-storage-architecture.md) | Runtime topology: Nginx, 3 Uvicorn, ARQ+Redis, Postgres16+PostGIS, presigned uploads |
| [field-operations-platform/PROPOSAL-port-from-aip.md](field-operations-platform/PROPOSAL-port-from-aip.md) | **Adopted** (PR #24): one forms/item_types engine, single inspection record, `eligibility` service, `inspection_requests` |
| [field-operations-platform/spec/MODULE-SPEC-11-inspection-requests.md](field-operations-platform/spec/MODULE-SPEC-11-inspection-requests.md) | `inspection_requests`, `forms`/`item_types`, `inspections` + `eligibility` + `rfi`/`submittals` extensions |
| [field-operations-platform/spec/MODULE-SPEC-10-workpacks-awp.md](field-operations-platform/spec/MODULE-SPEC-10-workpacks-awp.md) | `workpacks`, `awp`, documents VFS |
| [openbusinesserp-analysis/spec/MODULE-SPEC-08-new-modules.md](openbusinesserp-analysis/spec/MODULE-SPEC-08-new-modules.md) | `asset_management`, `wps`, `cutting` (+ P6 round-trip §2.5) |
| [openbusinesserp-analysis/spec/MODULE-SPEC-09-reanchored-and-portal.md](openbusinesserp-analysis/spec/MODULE-SPEC-09-reanchored-and-portal.md) | Re-anchored schedule/progress/costmodel/variations + phased portal |
| [openbusinesserp-analysis/FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md](openbusinesserp-analysis/FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md) | Module register (added/modified/kept/removed) + resolved decisions D3/D5/D7/D8 |

## Reference only (do not build to these directly)

- `openbusinesserp-analysis/MASTER-REDEVELOPMENT-PLAN.md`, `openbusinesserp-*.md`, `spec/MODULE-SPEC-00…07b` — the original full-ERP analysis; many modules were **removed** in the field-ops pivot (FINAL-PLAN §1.4). Use only for entity/field detail on modules still in scope.
- `REVISED-PLAN-*.md` — superseded by FINAL-PLAN.

## Precedence when documents disagree
1. **For the modules SPEC-11 covers** — `forms`, `item_types` / `asset_management` schema & layout, `inspections`, `eligibility`, `inspection_requests`, `rfi` / `submittals` — **MODULE-SPEC-11 + PROPOSAL-port-from-aip override** MODULE-SPECS and MODULE-SPEC-08 (e.g. the thin `forms` and the standalone `oe_asset_layout` designer are superseded). PROPOSAL's own "Status: Proposal for review" header is superseded by TD-002 (adopted); the file is kept byte-identical to its source.
2. **Everywhere else:** DEVELOPMENT-PLAN + MODULE-SPECS → ADR-001 → MODULE-SPEC-08/09/10 → FINAL-PLAN → everything else.

Disagreements found while building → `spec-gap` TODO.
