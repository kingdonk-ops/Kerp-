# Advisory review — shared brief (read this first)

You are one of **nine independent advisors** reviewing the KERP specification before
it is broken down into build tasks. The repo owner wants each advisor's feedback as a
standalone, readable document. A coordinator will later synthesise all nine, the owner
will pick what to adopt, and the chosen items get merged into one plan.

**Your job:** produce *your* document only. Do not edit code, the tracker, or other
advisors' files. Read-only on the repo except for writing your own output file.

---

## What KERP is (in one paragraph)

KERP ("Field Operations Platform") is an **operational delivery / field-execution engine**
for industrial construction & asset work. It owns **field ground truth** — a recursive
`Site → Area → Asset (→ sub-Asset…) → RSW` hierarchy with typed **Items**, inspections/ITP/
NCR/punch, welding quality (WPS/welder-qual/welds/NDE), logistics, execution, documents/CDE,
claims and controls. It **owns no money ledger and no master CPM programme**: it imports from
Primavera P6 and the corporate ERP, captures reality on site, and exports **verified actuals**
back (physical % and dates to P6; goods-receipt/OS&D and signed daywork to the ERP). The owner
often calls it "the inspection management system" because inspections/ITP/NCR/weld quality are
its backbone. **Single company** (currency/date/locale/tax are Project Settings, not i18n).
Target scale: 30–80 concurrent field/office users, heavy files (CAD/BIM, 200-page MDRs).

## Stack & topology (ADR-001, authoritative)

- Backend: **FastAPI** (one app, modular monolith), 3 Uvicorn workers behind **Nginx** (HTTP/2, Brotli).
- Async: **Redis 7 + one ARQ worker** for all heavy jobs (PDF/MDR compile, OCR, CAD/BIM, embeddings, BI rollups, P6 parse, upload scan). Lazy-import heavy libs, enforced by lint.
- DB: **Postgres 16 + PostGIS** (relational + JSONB/GIN/GiST). **No DuckDB in the request path.**
- Storage: object store (MinIO/S3/R2), **presigned direct-to-bucket** uploads; API never buffers bytes.
- Frontend: **Vite + React 18 + TS strict, Tailwind, TanStack Query, router, i18n `t()`, Vitest**; tablet/phone-first edge screens; offline sync (IndexedDB + conflict replay).
- Deploy: images prebuilt in CI (GHCR), `pull && up`; target 2 vCPU/4 GB viable, 4 vCPU/8 GB comfortable.

## The authoritative documents (read the ones relevant to your lens)

Precedence when docs disagree: **DEVELOPMENT-PLAN + MODULE-SPECS → ADR-001 → MODULE-SPEC-08/09/10 → FINAL-PLAN → everything else.**

| Path | What it is |
|---|---|
| `docs/spec/README.md` | Index + precedence |
| `docs/spec/field-operations-platform/DEVELOPMENT-PLAN.md` | Scope, 12 architecture principles, anchor model, RBAC, verification engine, phases 0–7 |
| `docs/spec/field-operations-platform/MODULE-SPECS.md` | Per-module spec (identity/entities/endpoints/verification/features) for every in-scope module (~94) |
| `docs/spec/field-operations-platform/ADR-001-runtime-and-storage-architecture.md` | Runtime/storage decisions |
| `docs/spec/field-operations-platform/PROPOSAL-port-from-aip.md` | **Pending (TD-002):** collapse to ONE no-code forms/item_types engine + eligibility + inspection_requests |
| `docs/spec/field-operations-platform/spec/MODULE-SPEC-11-inspection-requests.md` | Build-ready spec for the AIP proposal |
| `docs/spec/field-operations-platform/spec/MODULE-SPEC-10-workpacks-awp.md` | `workpacks`, `awp`, VFS tables on `documents` |
| `docs/spec/openbusinesserp-analysis/FIELD-OPERATIONS-PLATFORM-FINAL-PLAN.md` | Module register (added/modified/kept/removed), resolved decisions D3/D5/D7/D8 |
| `docs/spec/openbusinesserp-analysis/spec/MODULE-SPEC-08-new-modules.md` | `asset_management`, `wps`, `cutting`, P6 round-trip |
| `docs/spec/openbusinesserp-analysis/spec/MODULE-SPEC-09-reanchored-and-portal.md` | re-anchored schedule/progress/costmodel/variations + phased portal |
| `docs/spec/architecture-suite-reference/01..04` | **Reference only** — the as-built "Inspection-ERP" we port patterns from (stack, base modules, extension modules, frontend/UX). Not KERP scope, but shows real screens/patterns. |
| `docs/DEVELOPMENT-PLAN.md`, `docs/TASKS.md`, `docs/TODO.md`, `docs/WORKFLOW.md`, `CLAUDE.md` | Build pipeline, task board, open decisions, agent rules |
| `tracker/tasks/*.md`, `tracker/todo/*.md` | Source task/epic files (65 tasks, 40 epics) and 10 open TODOs |

## Engineering hard rules (from CLAUDE.md — treat as constraints, flag anything that violates them)

1. **Modular monolith:** `backend/app/modules/<name>/` with `manifest.py, models.py, router.py, service.py, schemas.py, README.md`. **No cross-module table access** — call the other module's `service.py`.
2. Every table uses **`BaseRecordMixin`** (typed entity URI, `tenant_id`, content hash, provenance, revision) **+ an RLS policy**. Tenant comes from the JWT only; cross-tenant → 404.
3. **Anchor model is law:** lowest **work** anchor = **RSW** (`oe_asset_node level='rsw'`); lowest **inspection** anchor = **Item** (`oe_asset_item`); a weld is an Item. **No BOQ anchor anywhere.**
4. Systems of record referenced by `external_source`+`external_id`(+`synced_at`), **never FKs**. Money is a **read-only mirror**.
5. **Verification is configuration:** every verifiable transition calls `run_checks(context, rule_set)`; rule sets are data (DSL). Error findings block; warnings advise.
6. **AI-initiated mutations** carry `source="ai"` and need human confirmation before applying.
7. Heavy work runs in the **ARQ worker**; heavy libs imported lazily.
8. Additive migrations by default; destructive/core-table changes need an **ADR** in `docs/decisions/`.
9. Every user-visible string through i18n **`t()`**. Never hand-edit `frontend/src/api/` (generated).
10. No secrets in the repo; **synthetic fixtures only** (no real client data).
11. Sizes: **S** ≈ one PR < ~300 LOC; **M** ≈ one PR < ~800 LOC. Bigger → split.

## Open decisions already on the board (docs/TODO.md) — do not re-litigate settled ones blindly, but weigh in where your lens is relevant

- **TD-001** Port the proven Inspection-ERP core vs rewrite — default: **port & adapt**.
- **TD-002** (**blocking**) Adopt the AIP one-engine proposal (unify `forms`+`item_types`, `eligibility`, `inspection_requests`)? — *unresolved; this is the single biggest open fork.*
- **TD-003** Identity: local JWT only vs SSO (Entra ID) for staff + email/password for clients — default: local JWT behind an `AuthProvider` seam.
- **TD-004** Hosting: Coolify/MinIO vs AWS (S3/SES).
- **TD-005** `module_builder` (runtime no-code modules) has no buildable spec.
- **TD-006** Offline sync moved into Phase 1.
- **TD-007** Keep multi-tenant plumbing (tenant_id + RLS) in a single-company product?
- **TD-008** Tier-1 edge screens (Gate Delivery, Look-Ahead Board, Daily Workface Log) have no UI specs.
- **TD-009** No MVP cut line set (~94 modules) — recommend end of Phase 2 + documents.
- **TD-010** Architecture-suite docs imported from an unmerged branch (reference only).

---

## Output contract (every advisor follows this)

Write exactly one Markdown file at the path your prompt gives you, under `docs/reviews/`.
Structure it as:

1. **`# <Your role> — review`** and a 3–5 line **TL;DR** (your single most important recommendation first).
2. **Findings** — your substantive analysis, organised your way, but **KERP-specific**: cite module names (`asset_management`, `wps`, `inspection_requests`…), doc sections, and task/epic IDs. Generic best-practice advice with no tie to KERP's modules is not useful.
3. **Recommendations table** — each row: *Recommendation · Rationale · Effort (S/M/L) · Priority (P1/P2/P3) · Affects (modules/phases/TDs)*. This is what the owner will pick from, so make rows **atomic and decidable** (yes/no each).
4. **Conflicts & risks** — anything in your lens that contradicts the hard rules, the anchor model, the commercial boundary, or another part of the spec; and the top risks.
5. **Open questions for the owner** — the decisions only they can make (tie to existing TD-xxx where one fits, or propose a new one).
6. **Feeds-the-plan** — a short, concrete list of what should land in the final merged plan from your lens (so the coordinator can lift it directly).

Length: substantive but focused — roughly **250–600 lines**. Prefer tables and tight prose over padding.
Where market/library comparison helps (tech, security, features), use WebSearch and cite what you found (name the library/product + why). Keep money/commercial boundary, anchor model, and single-company doctrine intact unless you explicitly argue to change one (and mark it as such).

Be opinionated. The owner wants clear recommendations, not an on-the-one-hand survey.
