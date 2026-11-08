<!-- GENERATED MASTER — assembled from the companion analysis docs in this folder. -->
# OpenBusinessERP — Full Redevelopment Plan (consolidated)

**Subject:** `kingdonk-ops/openbusinesserp` @ `a96d525` (v18.5, 191 modules) · **Assembled:** 2026-11-06
**Purpose:** a single structured plan sufficient to **redevelop the system from scratch** — architecture, data model, security, AI layer, a dependency-ordered phased build plan, a complete module index, and editable per-module specs — with every area that **still needs data or planning** flagged in the Gaps Register (§0.3).

> This master concatenates the companion documents (listed in §0.4) under one structure. Where sections overlap, read the earliest occurrence as canonical; later companion copies retain their own context. Nothing here is invented: claims are grounded in the source tree; inference is flagged.

---

## 0.1 How to use this document
- **To redevelop:** follow Part B (the phased plan) top-to-bottom; the build order is the module dependency graph.
- **To reshape scope:** edit Part D (per-module specs) — strike a `### module` to drop it (check its *Dependents*), strike a `[F#]` to drop a feature, copy the blank template to add one. Edits there reshape the phase order deterministically (phases follow `Depends on`).
- **To see what's missing:** §0.3 Gaps Register is the authoritative to-do before this is build-ready.

## 0.2 Grounding / status legend
- **⟦verified⟧** — implementation read this session (line-level): platform/loader, auth/RBAC, tenancy/RLS, vector/AI indexing, erp_chat, documents/file_search, module-builder, frontend shell/Settings/users/modules.
- **⟦structural⟧** — documented from manifests, `models.py` tables, routers, `depends`, and docstrings; internal service logic/state machines **not** read line-by-line. Most Phase 3–5 and Phase 7 modules.
- **⟦spec⟧** — lifted to editable spec depth (entities+fields, endpoints, discrete `[F#]` features, deps, removal impact): **Finance only** so far (Part D).

## 0.3 ⚠️ GAPS REGISTER — areas that still need DATA or PLANNING
Grouped; **[DATA]** = needs facts from the source/stakeholders, **[PLAN]** = needs a design decision. Priority: P1 blocks build, P2 blocks a domain, P3 polish.

### A. Specification completeness
- **G1 [DATA] P1 — 176 of 191 modules are not yet at ⟦spec⟧ depth.** Only the 15 Finance modules have entities+fields+feature-breakdown. Batches 2–7 (Sales/Commercial, Purchasing, Estimating/Cost, Delivery/Field, Documents/Collab, Platform/AI/Regional) are documented at ⟦structural⟧ depth only. **Action:** run the spec pass per domain (method proven in Part D) reading each module's `models.py`+`router.py`+`service.py`.
- **G2 [DATA] P2 — partial field capture in Finance.** Full entity fields captured for `gl`, `finance`, `banking`, `reconciliation`, `payment_clock`; `tax_withholding`, `fx`, `payroll`, `certified_payroll`, `labor_rates`, `allowances`, `funding`, `cost_recovery`, `postcalc` list tables + features but not every column. **Action:** enumerate remaining columns.
- **G3 [PLAN] P2 — state machines not formalised.** Lifecycle modules (sales, purchasing, contracts, variations, change orders, hse, commissioning, scheduler-like) have statuses in their tables but the transition rules/guards were not extracted. **Action:** read each `service.py` transition logic.

### B. Data model & platform contract
- **G4 [PLAN] P1 — base-record / provenance contract unconfirmed.** inspection-erp enforces a `BaseRecordMixin` (uuid, entity_uri, tenant_id, hash, provenance, revision). OBE uses `oe_*` tables with `tenant_id` + `metadata_` + `idempotency_key` in places, but a **uniform** record mixin / content-hash / audit contract was not confirmed across all 191 modules. **Action:** confirm whether to adopt a uniform base-record contract in the rebuild (recommended).
- **G5 [PLAN] P1 — multi-tenancy model.** Today tenant = JWT `sub` (single-tenant installs); no Tenant entity; PG RLS is **opt-in** (`OE_RLS_ENFORCE`). **Action:** decide target — keep single-tenant, or introduce a real Tenant entity + `tenant_id` claim + enforced RLS (the resolver is already forward-compatible).
- **G6 [DATA] P2 — full table inventory.** ~191 modules × several tables each; only the documented tables are catalogued. **Action:** generate the complete `oe_*` schema inventory + relationships (ERD) from `models.py`.
- **G7 [PLAN] P2 — migrations & seed strategy.** Alembic is used, but the rebuild's migration baseline, demo/seed data, and reference data (chart-of-accounts templates, tax regimes, country registries) loading strategy are unplanned.

### C. Cost/estimating external data
- **G8 [DATA] P2 — cost database & norm content.** `costs`/`assemblies`/`norm_expansion`/`price_index` are engines; the **actual cost/norm datasets** (CWICR-derived) are an external data dependency not covered here. **Action:** source/licence decision for the cost data.

### D. AI / search layer
- **G9 [PLAN] P2 — body-level semantic search not wired.** Only document **metadata** is embedded into `oe_documents`; the `file_search` full-text/OCR body is **not** vectorised. **Action:** decide whether to chunk `content_text` into a vector collection (design sketched in the AI-indexing companion).
- **G10 [DATA] P3 — erp_chat tool catalogue + action registry incomplete.** ~18 read/search tools documented; the full tool set and the `propose_*` action registry (the mutation surface) were not exhaustively enumerated. **Action:** list every tool + every proposable action.
- **G11 [PLAN] P2 — embedding model & vector backend.** Default `all-MiniLM-L6-v2`/multilingual + LanceDB; model choice, dimension, licensing, and Qdrant-vs-LanceDB sizing for production are undecided.

### E. Integrations, regional, non-functional
- **G12 [DATA] P2 — connector/integration specifics.** Teams/Slack/Telegram, calendar feeds, inbound email/webhook, billing/payment provider, e-invoice clearance endpoints per country — not detailed.
- **G13 [DATA] P2 — country packs are thin shells.** The per-pack tax/document/validation profiles (uk/dach/latam/asia_pac/china/india/mexico/middle_east/russia/sa/us/us_ca/us_tx) are not enumerated.
- **G14 [PLAN] P3 — i18n sourcing.** 44 languages incl. 4 RTL — the translation sourcing/QA workflow is unplanned.
- **G15 [PLAN] P1 — non-functional targets.** No agreed performance budgets, deployment topology (embedded-PG desktop vs server+Qdrant), scaling, backup/DR, per-module test-coverage targets, or a security-review plan. **Action:** set these before Phase 0 freeze.
- **G16 [PLAN] P3 — frontend parity.** The frontend was documented at shell/Settings/users/modules depth; the ~190 feature screens are not individually specced. **Action:** per-domain frontend spec pass (mirror the backend batches).

> **Bottom line:** Phases 0–2 and 6 are build-ready at spec depth; Phases 3–5 and 7 need the G1/G2/G3 spec pass and the G4–G8 data/platform decisions before they are buildable rather than merely planned.

## 0.4 Companion documents assembled below (in order)
1. Part B — Phased development plan (`openbusinesserp-development-plan.md`)
2. Part C1 — Architecture, RBAC & security (`openbusinesserp-architecture.md`)
3. Part C2 — Frontend, UX & Settings (`openbusinesserp-frontend-ux.md`)
4. Part C3 — User management & system/module settings (`openbusinesserp-users-modules.md`)
5. Part C4 — Module registry & module builder (`openbusinesserp-modules-registry-builder.md`)
6. Part C5 — Document ingest, AI indexing & erp_chat (`openbusinesserp-document-ai-indexing-and-chat.md`)
7. Part E — Module index & catalogue, Batches 1–7 (`openbusinesserp-modules-01..07-*.md`)
8. Part D — Editable module specs: format + Finance (`spec/MODULE-SPEC-00-template-and-batch-01-finance.md`)

---

# PART B — PHASED DEVELOPMENT PLAN

---

# OpenBusinessERP — Development Plan (phased rebuild)

> A forward build plan to construct OpenBusinessERP from scratch, reverse-engineered from `kingdonk-ops/openbusinesserp` @ `a96d525` (v18.5, 191 modules). It is **architecture- and sequencing-solid** (module contract, data model, RBAC, tenancy, AI/vector layer, build order from the real dependency graph). Per-module internals in Phases 3–7 are specified at **contract depth** (tables, endpoints, state, dependencies) from the module catalog; where a module's internal logic was not read line-by-line this session, it is marked **⟦structural⟧** — treat those as scope to confirm against the source, not invented detail. Subsystems read in depth this session (auth/RBAC, module loader, tenancy/RLS, vector/AI indexing, erp_chat, documents/file_search) are marked **⟦verified⟧**.

---

## 0. Principles & cross-cutting rules (hold in every phase)

1. **Modular monolith, async end-to-end.** One deployable; business features are modules under `app/modules/*`; platform services under `app/core/*`. ⟦verified⟧
2. **Module contract** (every feature module): `manifest.py` (`name`, `version`, `category∈{core,platform,business,department,regional,extension,controls,compliance,enterprise,integration,infra,…}`, `depends=[...]`), `models.py` (`oe_<module>_*` tables), `schemas.py`, `router.py` (mounted `/api/v1/<prefix>`), `service.py` (the only cross-module entry — **never touch another module's tables**), `permissions.py` (registers `{module}.{action}` → min-role), optional `events.py` (event-bus subscribers), optional `vector_adapter.py` (semantic indexing). The **loader** auto-discovers, toposorts by `depends`, mounts routers, and supports enable/disable/unregister at runtime. ⟦verified⟧
3. **AuthZ = three cooperating tiers:** hierarchical RBAC registry (`viewer<editor<manager<admin`, admin bypass, role aliases) + **business-role presets** (per-module `full/read` grants in `user.metadata_["business_role"]`) + **per-user module access** (`visible`+`none/view/edit/full`). JWT claims are **re-hydrated from the DB every request** (never trusted from the token); password-change watermark + per-session `sid` revocation; API keys *narrow* (intersection). ⟦verified⟧
4. **Multi-tenancy:** single resolver (`tenant_scope`) — tenant = JWT `tenant_id` claim if present, else `sub`; app-layer owner/project filtering is the enforced path; **PG-native RLS is opt-in** (`OE_RLS_ENFORCE`) with a non-BYPASSRLS role. Cross-tenant/forbidden → **404** (IDOR defence). ⟦verified⟧
5. **AI is augmented-human-confirmed:** AI never writes directly; mutations are `propose_*` actions committed to a confirmation card. ⟦verified⟧
6. **Money & quantities are Decimal/strings end-to-end** (never JS floats); double-entry must balance before posting. ⟦verified (gl)⟧
7. **i18n first:** 44 languages incl. 4 RTL; every user-visible string is an i18n key; terminology overrides resolve through the same layer; documents render in the customer's language/currency. ⟦verified (frontend)⟧
8. **Provenance & audit:** append-only audit log (`oe_core_audit_log`), editable permission-matrix changes audited with before/after.
9. **Fail-open infra, fail-closed security:** vector/AI unavailable → warn + no-op (never break CRUD); unknown permission / unsigned webhook / missing tenant → deny.
10. **Testing gate per phase** (see §9); CI green before merge.

**Tech stack** ⟦verified⟧: Python 3.12 · FastAPI (async) · SQLAlchemy 2.0 async · python-jose JWT · PostgreSQL (embedded option) + Alembic · LanceDB (embedded, default) / Qdrant vector store · sentence-transformers embeddings · React 18 + TypeScript 5.9 + Vite 6 + zustand + i18next + Tailwind (CSS-var "Datum" tokens) · Playwright E2E.

---

## Build order (from the real dependency graph)

Foundational sinks everything depends on, build first: **projects → users/teams/contacts → gl**. Then the order below follows `depends` so a module never precedes its dependency (the loader's toposort is the source of truth).

```
core platform ─▶ users/teams/contacts/projects ─▶ gl ─▶ fx/tax ─▶ items/inventory
 ─▶ sales/purchasing/banking/receivables ─▶ costs/assemblies/boq/costmodel/estimating
 ─▶ schedule/progress/field/quality ─▶ eac/evm/cvr ─▶ documents/file_* + vector/AI + erp_chat
 ─▶ marketplace/billing/module_builder/country packs/portals/compliance
```

---

## Phase 0 — Platform foundations ⟦verified core⟧
**Goal:** the substrate every module plugs into, with one trivial proof module.

- Repo scaffold: `backend/app` async FastAPI app factory (`main.py`) with startup **lifespan** (init/prime Postgres before socket bind), the full middleware stack (CORS w/ prod wildcard refusal, APIVersion, security headers, actor-context, request-id, slow-request log, accept-language, compression, max-body-size), `/api/v1` mount.
- `database.py`: async engine/session (`async_session_factory`, `get_session` auto commit/rollback), base model, `oe_*` naming.
- Embedded PostgreSQL option (`core/embedded_pg.py`) + Alembic (`core/postgres_migrator.py`); migrations discipline (additive-first).
- `config.py` (`Settings`): env-driven incl. `jwt_secret`, embedding model, `VECTOR_BACKEND`, `OE_RLS_ENFORCE`, vector-pool knobs.
- **Module loader** (`core/module_loader.py`): manifest discovery, toposort, cycle/unknown-dep detection, router mount, runtime enable/disable, `unregister_module_permissions`.
- **Permission registry** (`core/permissions.py`) + `dependencies.py` (`RequirePermission`, `RequireRole`, `RequirePermissionOrApiKey`, DB-rehydrated `get_current_user_payload`, password/session kill-switches, API-key principal).
- **Tenancy** (`core/tenant_scope.py` + optional `core/rls.py`), **audit** (`core/audit*`), **event bus** (`core/events.py`), **storage** (`core/storage.py`), **jobs** (`core/job_runner.py`, `modules/jobs`), **i18n foundation** (`modules/i18n_foundation`, `core/i18n*`), **notifications**.
- **Vector layer scaffold** (`core/vector.py` + `core/vector_index.py` + `core/embedding_pool.py`): `EmbeddingAdapter` protocol, collection constants, LanceDB default. ⟦verified⟧
- Frontend shell: `AppLayout` (fixed sidebar + header + desktop chrome + floating AI/queue), "Datum" token design system (`index.css`/`tailwind.config.js`), i18next (44-lang), zustand auth store, nav catalog, permission-gate mirror (`permissionGates.ts`, CI-checked vs backend).
- CI: ruff/mypy/pytest + vitest/tsc/build; the `system` proof module exercises the loader contract.

**Gate:** app boots; a trivial module mounts via the loader; RBAC gate + tenant resolver + audit write + one vector round-trip all pass; frontend shell renders with theme + i18n.

## Phase 1 — Identity, tenancy & governance depth ⟦verified⟧
**Modules:** `users`, `teams`, `contacts`, `projects`, `admin`, `onboarding`, `credentials`.

- `projects` (foundational; most modules `depend` on it): project entity, ownership, membership; `verify_project_access` / `accessible_project_ids` (404 IDOR). 
- `users` + `teams`: user CRUD, roles, invite (`POST /v1/users/`, strong-password), activate/deactivate, erase/anonymise (protect last admin / self), per-user `module-access`.
- `contacts`: shared party master (tenant_id = creator in single-tenant).
- `admin`: editable **permissions matrix** API (`GET matrix` manager+, `PATCH/{key}` + `POST preset/{name}` admin, lockout-protected, audited); business-role presets (`/v1/business-roles/`).
- Frontend: `/users` User Management, Settings → Team (members/job-roles/licence), Permissions Matrix, system/module settings.

**Gate:** full auth lifecycle; RBAC matrix edit + business-role presets + per-user module access all enforced and audited; cross-tenant 404 verified; (optional) RLS passes under a non-BYPASSRLS role.

## Phase 2 — Accounting core ⟦verified (gl) / structural (fx,tax)⟧
**Modules:** `gl`, `fx`, `tax_withholding`, (`payment_clock` regional later).

- `gl`: chart of accounts, fiscal year/period open-close, balanced journal entries (`build_lines`/`check_balanced`/`natural_balance`), trial balance, posting evidence attachments. **The posting sink for all later modules.**
- `fx`: ECB-rate ingest, cross-rate/convert-via-base, period-end revaluation lines → gl. ⟦structural⟧
- `tax_withholding`: banded withholding + reverse charge, party verification gating. ⟦structural⟧

**Gate:** debits=credits enforced; closed-period refusal; FX revaluation posts; SAF-T/DATEV export stub.

## Phase 3 — Order-to-cash & procure-to-pay
**Modules:** `items`, `inventory`, `sales`, `purchasing`, `procurement`, `banking`, `reconciliation`, `receivables`, `recurring`, `einvoice`, `einvoice_clearance`, `supplier_catalogs`, `rfq_bidding`.

- `items` (price lists, discounts) + `inventory` (warehouses, moving-average cost, stocktakes). ⟦structural⟧
- `sales` (quote→order→invoice→payment, posts to gl via account-map, numbering, send log, pay-links) ⟦structural⟧ and `purchasing` (PO→GR→bill→payment). 
- `banking` (camt.053/MT940/CSV import, matching, rules) → reconciliation postings; `reconciliation` (cross-record links); `receivables` (aging, **draft-only** dunning). ⟦structural⟧
- `einvoice` (EN 16931/UBL/CII/Peppol/ZUGFeRD) + `einvoice_clearance`; `recurring` invoices.
- `procurement`/`supplier_catalogs` (PR→PO→GR→invoice 3-way match, vendor KYC/scorecards); `rfq_bidding`.

**Gate:** the README "month" flow runs end-to-end: quote→invoice→**post (tax-split)**→bank import→reconcile→dunning draft; a compliant e-invoice validates.

## Phase 4 — Estimating & cost ⟦structural, large⟧
**Modules:** `costs`, `assemblies`, `norm_expansion`, `price_breakdown`, `price_index`, `waste_factors`, `boq`, `takeoff`, `dwg_takeoff`, `rebar_schedule`, `measurement`, `costmodel`, `cost_plan`, `rom_estimate`, `ai_estimator`, `estimate_basis`, `estimate_rollup`, `preliminaries`, `allowances`, `resources`, `resource_summary`, `labor_rates`, `methodology`, `tendering`, `bid_management`, `cost_match`.

- Cost base + unit-rate build-up (`costs`→`assemblies`/`norms`→`price_index`/`waste_factors`); quantities (`takeoff`/`dwg_takeoff`/`measurement`→`boq`); 5D `costmodel`; estimate assembly (`estimate_basis`+`estimate_rollup` over boq+preliminaries+allowances); AI/ROM estimating; `tendering`/`bid_management` sourcing.

**Gate:** takeoff→BOQ→priced estimate→tender comparison works; `labor_rates` feed rates; `cost_match` maps a foreign bill.

## Phase 5 — Project delivery & field ⟦structural⟧
**Modules:** `schedule`, `schedule_advanced`, `timeline`, `deadlines`, `progress`, `fieldreports`, `field_time`, `daily_diary`, `field_diary`, `site_prep`, `site_logistics`, `site_supervision`, `site_inventory`, `construction_control`, `inspections`, `ncr`, `punchlist`, `safety`, `hse_advanced`, `temporary_works`, `commissioning`, `closeout`, `defects_liability`, `formwork`, `equipment`, `assets`, `contracts`, `subcontractors`, `changeorders`, `variations`, `claims_evidence`, `cost_recovery`, `payment_clock`, `payroll`, `certified_payroll`, `eac`, `full_evm`, `cvr`, `postcalc`, `project_controls`, `value`.

- Schedule/Last-Planner + progress; field capture (hours/events → payroll + EVM); quality/safety registers; handover/commissioning; contracts/subcontractors + change/variations/claims; **earned value** (`eac`→`full_evm`, `cvr`); `postcalc` feeds norms back to Phase 4.

**Gate:** progress→EVM/EAC/CVR reconcile; a contract runs claims+retention; field hours flow to payroll; postcalc returns feedback factors.

## Phase 6 — Documents, collaboration & the AI layer ⟦verified⟧
**Modules:** `documents` + `file_*` (versions, approvals, comments, tags, search, saved-views, favorites, references, distribution, transmittals, trash), `rfi`, `submittals`, `transmittals`, `correspondence`, `meetings`, `markups`, `plan_room`, `collaboration`, `collaboration_locks`, `approval_routes`, `bim_hub`, `cad`; AI: `search`, `retrieval`, `ai`, `ai_agents`, `erp_chat`, `smart_views`, `saved_views`, `change_intelligence`, `project_intelligence`, `voice`.

- `documents` CDE + the `file_*` capability modules; controlled-doc workflows (rfi/submittals/transmittals/correspondence/meetings/markups); reusable `approval_routes`, generic `collaboration`.
- **Dual document indexing** (the traced pipeline): semantic — `documents/events.py` → `vector_adapter` embeds metadata → `vector_index.index_one` → LanceDB/Qdrant (tenant/project-scoped); full-text — `file_search/extractors` PyMuPDF + Tesseract OCR → `oe_file_search_index.content_text` → Postgres `tsvector`.
- **erp_chat** tool-calling agent (SSE streaming, Anthropic/OpenAI native tools, provider fallbacks, `MAX_TOOL_ROUNDS`): read tools (projects/boq/schedule/risk/cost…) + vector-backed `search_*` tools (`search_documents` → `search.service.unified_search_service` two-track vector+SQL over `oe_documents`); mutations via `propose_*` confirmation cards; chat self-memory in `COLLECTION_CHAT`.
- **Recommended enhancement (from the gap we found):** add body-level semantic search by chunking `file_search.content_text` and embedding via a new `EmbeddingAdapter` collection, then expose it to `erp_chat`.

**Gate:** upload→both indexes populate; `/files` keyword search + semantic "Similar items" work; erp_chat answers a document question via `search_documents` and routes a change through a confirmation card; AI-unavailable degrades gracefully.

## Phase 7 — SaaS, extensibility, compliance & regional ⟦verified (marketplace/builder) / structural (rest)⟧
**Modules:** `marketplace`, `billing`, `module_builder`, regional packs (`uk/dach/latam/asia_pac/china/india/mexico/middle_east/russia/sa/us/us_ca/us_tx`), `portal`, `portfolio`, `property_dev`, `connectors`, `integrations`, `webhook_leads`, `inbound_email`, `inbound_capture`, `phonelog`, `backup`, `data_import`, `pipelines`, `resumable_uploads`, `uploads`, `global_presence`, `enterprise_workflows`, `interface_management`, `project_route`, `compliance`(+`_ai`,`_docs`), `qms`, `requirements`, `validation`, `risk`, `cases`, `moc`, `review_authority`, `authority_submission`, `record_publishing`, `signing`, `source_data`, `carbon`, `esg`, `forms`, `appointments`, `accommodation`, `service`, `funding`, `crm`, `reporting`, `dashboard(s)`, `bi_dashboards`, `tasks`, `client_errors`.

- Entitlements/marketplace + billing→entitlement sync (where applicable); **module_builder** (runtime no-code/AI modules with preview→review-token→install, quarantine, data-retention-by-default); **regional packs** that switch on module bundles + tax/doc/validation profiles + branding + demo; portals (`portal`, `property_dev` buyer portal); integrations/inbound; compliance DSL + QMS + governance (risk/cases/moc/review/authority/signing); sustainability; reporting/dashboards.

**Gate:** install/uninstall a pack and a runtime-built module live (no restart); a webhook lead → CRM; QMS/compliance rule fires; a regional pack localises documents.

---

## 9. Testing & gates (every phase)
| Layer | Tool | Requirement |
|---|---|---|
| Service/unit | pytest + factories | new logic covered; per-tenant isolation |
| API | pytest httpx | each endpoint: happy path + authz (cross-tenant 404) + validation |
| RBAC/permission-mirror | pytest + `scripts/check_role_mirrors_match_the_backend.py` | frontend gate map == backend registry |
| RLS (if enabled) | pytest (non-BYPASSRLS role) | tenant isolation at DB layer |
| Vector/AI | pytest | index→search round-trip; fail-open when backend absent |
| Frontend | vitest + testing-library | components + module registration + a11y |
| E2E | Playwright | one flow per module min; the "month" flow; the chat flow |

**Rule:** full affected suites pass locally; CI green before merge; no skipped tests without a tracked task.

## 10. Risks & sequencing notes
- **gl + projects + users are hard prerequisites** — slipping them blocks everything downstream.
- **i18n/RTL and Decimal-money must be designed in from Phase 0**, not retrofitted (51-file migrations are costly — the codebase shows this).
- **Vector/AI is additive and fail-open** — can trail the data modules; never let it gate CRUD.
- **The 3 authZ tiers interact** — build registry first, presets second, per-user module access third; keep the frontend mirror CI-checked.
- **Phases 4–5 are the bulk** (construction estimating + delivery, ~80 modules) — parallelise by domain team once the Phase-0 contract is frozen.
- **Confidence:** Phases 0–2 and 6 are implementation-grounded ⟦verified⟧; Phases 3–5 and most of 7 are contract-grounded ⟦structural⟧ — before building those, read each module's `service.py`/`models.py` to lift ⟦structural⟧ items to spec depth.

*Grounded in `kingdonk-ops/openbusinesserp` @ `a96d525`. Companion docs: architecture/RBAC, frontend-UX, users+modules, registry+builder, per-module batches 01–07, document-AI-indexing+chat.*

---

# PART C1 — ARCHITECTURE, RBAC & SECURITY

# OpenBusinessERP — Architecture & Security Documentation

> **Repository:** `kingdonk-ops/openbusinesserp` (upstream: `datadrivenconstruction/OpenBusinessERP`), **v18.5.0**, AGPL-3.0.
> **What it is:** a self-hosted, open-source ERP — quotes, invoices, stock, purchasing, bank reconciliation and double-entry bookkeeping, e-invoicing, in 44 languages — with a large construction/project-controls heritage layered on top of a general business core.
> **Scale:** **192 backend modules** under `backend/app/modules/*` and ~160 platform-service files under `backend/app/core/*`; a ~5,600-line application factory (`backend/app/main.py`).
>
> **Scope of this document.** This is a proportionate, source-grounded architecture + security write-up. Because the module count is ~8× the inspection-erp suite, the business modules are presented as a **grouped catalog** (§6) rather than 192 individual Four-Pillars entries; the **security/RBAC layer is documented in depth** (§4–§5) because that is where the user-manager / permissions-management capability lives — the thing missing from inspection-erp. Deeper per-module write-ups can be produced in batches on request. Every claim cites a concrete `path:line`.

---

## 1. Context & relationship to Inspection-ERP

OpenBusinessERP is the **upstream reference** that Inspection-ERP was derived from (Inspection-ERP's README names "OpenConstructionERP ~160 modules" as its architecture reference). Many concepts recur — a module loader, per-module permissions, RLS scaffolding, audit logging — but OpenBusinessERP is **much larger and more mature**: async stack, an **editable RBAC matrix with an admin UI**, **business-role presets**, per-session token revocation, narrowing API keys, 44-language i18n, e-invoicing, and country packs.

Crucially for the question that prompted this: **the user-manager / permissions-management surface that does not exist in Inspection-ERP's frontend *does* exist here**, as `backend/app/modules/admin/permissions_router.py` plus `core/business_roles.py` and `core/permissions.py` (§4–§5).

---

## 2. Technology stack & runtime

### Backend
- **Language/runtime:** Python **3.12** (`backend/pyproject.toml`, `requires-python = ">=3.12"`).
- **Web framework:** FastAPI (Starlette) with a large async middleware stack (`main.py`).
- **ORM:** **SQLAlchemy 2.0 async** (`AsyncSession`, `async_session_factory`, `get_session` — `dependencies.py:54-62`).
- **Auth/crypto:** **python-jose** JWT (`from jose import jwt` — `dependencies.py:30`); SHA-256-hashed API keys (`dependencies.py:591`).
- **Database:** **PostgreSQL**, including an **embedded/managed Postgres** option (`core/embedded_pg.py`, `core/postgres_migrator.py`, `core/postgres_version.py`) so the product runs self-hosted/desktop; Alembic migrations (`backend/alembic/`).
- **Packaging:** installable server + **desktop** build (`backend/openbusinesserp-server.spec`, `desktop/`, PyInstaller lock).

### Frontend
- **React 18.3 + TypeScript 5.9 + Vite 6** (`frontend/package.json`).
- **State:** **zustand 5**; **i18next 26** (44 languages, 4 RTL).
- **E2E:** Playwright (multiple configs incl. a BOQ tour, captures, floating-chat).

### Infrastructure & tooling
- Multiple compose files (`docker-compose.yml`, `.prod.yml`, `.quickstart[.image].yml`), `deploy/`, `services/`, `desktop/`.
- Pre-commit, gitleaks (`.gitleaks.toml`), CI (`.github`), mkdocs site (`mkdocs.yml`, `docs/`), `MODULES.md`, `llms.txt`.
- Vector search support in core (`core/vector.py`, `vector_index.py`, `vector_routes.py`, `retrieval` module) — pgvector-style semantic retrieval.

---

## 3. High-level architecture

### Paradigm
A **modular monolith**, async end-to-end, single deployable, with:
- **~160 core platform services** (`backend/app/core/*`) — not business features: auth/RBAC, RLS, audit, jobs, i18n, money/tax/currency, fonts/PDF/XLSX branding, global search, vector, module loader, demo/seed tooling, country registries, etc.
- **192 business modules** (`backend/app/modules/*`) — the ERP + construction domains (§6).
- A **plugin/module system**: `core/module_loader.py`, `core/module_router.py`, `core/module_state.py`, `core/plugin_manager.py`, `core/marketplace.py`, and a `module_builder` module. Modules can be **installed, enabled/disabled, and even leave while the server runs** — the permission registry supports `unregister_module_permissions` for exactly that (`core/permissions.py:138-163`).

### Application factory & middleware pipeline
`backend/app/main.py` (~5,600 lines) builds the app in a `FastAPI(...)` factory (`main.py:1807`) behind a startup **lifespan** that initialises/primes Postgres before the socket binds (`main.py:513,598,1879`). The middleware stack (added outer→inner, `main.py:2214-2390`) includes:

`CORSMiddleware` (wildcard origin refused in production — `main.py:2214-2221`) · `APIVersionMiddleware` · `_RejectNonFiniteJSONMiddleware` · `DDCFingerprintMiddleware` · `SecurityHeadersMiddleware` · `ActorContextMiddleware` (audit IP/UA/request-id) · `RequestIDMiddleware` · `SlowRequestLoggerMiddleware` · `AcceptLanguageMiddleware` (per-request locale) · `CompressionMiddleware` · `MaxBodySizeMiddleware`.

Routers mount under `/api/v1` (`main.py:2528+`); modules contribute routers during the startup lifespan via the loader.

### Multi-tenancy & RLS (current state)
Documented candidly in source (`core/tenant_scope.py`): today these are **single-tenant installs** — there is no separate Tenant entity and **no `tenant_id` JWT claim yet**; the "tenant" resolves to the owning user's id (`sub`), matching columns like `Contact.tenant_id = created_by` (`tenant_scope.py:84-120`). The resolver **prefers an explicit `tenant_id` claim when present**, so real multi-tenancy is a future data change with no call-site edits. **PostgreSQL-native RLS exists but is opt-in** (`OE_RLS_ENFORCE`, off by default; `core/rls.py`, `rls_setup.py`; `rls_request_context` binds the tenant GUC per request — `dependencies.py:1226-1246`), with the usual caveat that superuser/BYPASSRLS roles bypass policies.

**App-layer isolation is the enforced path today:** project access is gated by ownership **or team membership**, returning **404 (not 403)** on deny to avoid UUID-existence oracles (`verify_project_access`, `accessible_project_ids` — `dependencies.py:887-1001`).

---

## 4. Security & Authentication (IAM)

All in `backend/app/dependencies.py` unless noted.

### Tokens
- **JWT** (python-jose), secret + algorithm from settings (`decode_access_token` — `dependencies.py:73`).
- **`type` claim enforced** (BUG-321/331): access vs refresh vs password-reset tokens are not interchangeable; legacy claimless tokens are treated as `access` only when `access` is requested (`dependencies.py:107-125`).
- **Error hygiene:** raw jose errors are never echoed to the client (they leak crypto internals); the caller gets a generic message + a correlation id (`dependencies.py:127-143`).

### Claims are re-hydrated from the DB on every request — never trusted from the token
`get_current_user_payload` (`dependencies.py:326-392`) decodes the JWT, then **overwrites `role`, `business_role`, and `permissions` from live DB state**. A forged/stale token claiming `role="admin"` therefore has no effect unless the DB actually grants it, and demotions/revocations take effect immediately. If the DB is unreachable it **fails closed** (503), never admin-bypass.

### Two independent session kill-switches
- **Password-change watermark:** any token with `iat` < `user.password_changed_at` is refused, enforced through one shared rule called from every door — HTTP, the three websockets, optional-auth, refresh (`reject_token_issued_before_password_change` — `dependencies.py:149-197`).
- **Per-session revocation (`sid`):** `reject_revoked_session` refuses a credential whose `UserSession` row is revoked, scoped by `user_id` so a `sid` can't probe another account; carefully reasoned fail-open only for pre-session-table tokens and post-restore gaps, with bounded logging (`dependencies.py:231-323`).

### API keys (additive, narrowing)
`X-API-Key` is SHA-256-hashed and looked up (`resolve_api_key_principal` — `dependencies.py:558-608`). A key's `permissions` list has **opposite polarity to a JWT's**: it can only **narrow** the owner's authority, never widen it. Effective authority = **intersection** of the owner's live role-permissions and the key's declared scopes; the narrowing check runs **before** the admin bypass so even an admin's key can be scoped down (`key_scopes_allow` + `RequirePermissionOrApiKey._authorize` — `dependencies.py:528-555, 777-819`).

### Other controls
- **AI rate limiting** per user (429 + Retry-After — `check_ai_rate_limit`, `dependencies.py:866`).
- **Locale resolution** per request for i18n'd error messages (`get_lang` — `dependencies.py:1043`).
- Security headers, body-size cap, non-finite-JSON rejection, slow-request logging (middleware, §3).

---

## 5. Authorization & RBAC — the permissions-management surface

This is the layer the user asked about. Three cooperating pieces: a **hierarchical permission registry**, **business-role presets**, and an **admin matrix API** that edits the registry live.

### 5.1 The permission registry (`core/permissions.py`)
- **Roles are a hierarchy:** `viewer(0) < editor(1) < manager(2) < admin(3)`; **admin bypasses all checks** (`role_has_permission` — `permissions.py:165-186`). Design-stage field roles (`site_inspector`, `site_foreman`, `field_worker`) exist with **negative ranks and intentionally empty permissions** until a pilot lands (`permissions.py:40-66, 314-346`).
- **Role aliases:** industry titles map to canonical roles (`estimator/qs → editor`, `owner/superuser → admin`, `guest/readonly → viewer` — `permissions.py:73-82`).
- **Permissions are `{module}.{action}`** with a `min_role`; modules **register their own** at startup and can **unregister** on unload (shared keys are kept so no endpoint checks an unknown permission — `permissions.py:113-163`). Unknown permission ⇒ **deny** for everyone below admin.
- **Core permissions** include `system.settings.*`, `system.modules.*`, `audit.view` (manager+), `audit.delete`/`audit.redact` (admin) — `permissions.py:349-374`.

### 5.2 Enforcement dependencies (`dependencies.py`)
- **`RequirePermission("x.y")`** — admin bypass; else checks the re-hydrated permission list, with a **live-registry fallback** so a stale JWT still honours the current mapping (issue #101); 403 on miss (`dependencies.py:645-715`).
- **`RequireRole("admin")`** — rank comparison for destructive endpoints ("a plain estimator holding `costs.update` must not truncate the cost DB" — `dependencies.py:822-860`).
- **`RequirePermissionOrApiKey`** — same bar for JWT or API-key callers, returning the caller id for attribution (`dependencies.py:718-819`).

### 5.3 Business-role presets (`core/business_roles.py`) — "the accountant posts journals but never touches the pipeline"
The pure hierarchy can't express per-domain scoping, so a **preset** pairs a base role with **per-module grants** (`full`/`read`) over the business modules (`sales, crm, items, inventory, purchasing, gl, banking, receivables, appointments, data_import`). Built-in presets: **`owner`** (admin), **`accountant`** (manager; full gl/banking/receivables/data_import, read sales/purchasing/items/inventory), **`sales`**, **`purchasing`**, **`readonly`** (viewer) (`business_roles.py:17-56`). The preset key lives in `user.metadata_["business_role"]` and is applied on **every request** when claims are re-hydrated (`dependencies.py:372-375`). `role_allows` / `effective_permissions` compute the intersection of base-role permissions and the preset's grants (`business_roles.py:64-88`); `business_matrix()` backs a **Settings → Team** UI (`business_roles.py:90-104`).

### 5.4 The admin permissions matrix API (`modules/admin/permissions_router.py`)
A view **and edit** surface for the live registry so the frontend renders a roles × modules matrix without re-implementing role logic:

| Endpoint | Gate | Behaviour |
|---|---|---|
| `GET /api/v1/admin/permissions/matrix` | `audit.view` (**manager+**) | Read-only snapshot: roles, hierarchy, modules→permissions→min_role, available presets (`permissions_router.py:95-150`). Operators who can read the audit log can *see* the matrix. |
| `PATCH /api/v1/admin/permissions/{key}` | **admin only** | Raise/lower a single permission's `min_role`; returns previous value for rollback; **admin-lockout protection** refuses to drop admin's own admin grant; **audit-logged** with before/after (`permissions_router.py:180-262`). |
| `POST /api/v1/admin/permissions/preset/{name}` | **admin only** | Rewrite the whole matrix to a baseline preset (`viewer-default`/`editor-default`/`manager-default`, derived from key *shape* so new modules are covered automatically — `permissions.py:277-311`); returns the full diff; audit-logged (`permissions_router.py:265-343`). |

So **permissions management is role-gated exactly as you described**: the matrix is visible to managers+ (via `audit.view`) and editable only by admins, with the frontend falling back to a read-only matrix when the caller lacks edit rights (`permissions_router.py:26-30`). The sibling `modules/admin/router.py` (507 lines) and `service.py` carry other admin operations (e.g. a triple-gated demo/QA reset — `admin/service.py`).

### 5.5 Audit trail
Every matrix mutation is written to the append-only audit log (`oe_core_audit_log`) via `core/audit` / `core/audit_log.py`, with actor, IP, and a replayable before/after diff (`permissions_router.py:236-248, 315-328`). The audit stack also has gates, pruning, redaction (GDPR right-to-erasure), and a router (`core/audit*.py`).

---

## 6. Business module catalog (192 modules, grouped)

Modules live under `backend/app/modules/*`, each following the loader contract (manifest + router + service + permissions). Grouped by domain:

- **General ERP core:** `sales`, `crm`, `contacts`, `items`, `inventory`, `site_inventory`, `catalog`, `supplier_catalogs`, `purchasing`, `procurement`, `rfq_bidding`, `subcontractors`, `appointments`, `tasks`, `teams`, `users`, `projects`, `portfolio`, `dashboard(s)`, `bi_dashboards`.
- **Finance & accounting:** `gl`, `finance`, `banking`, `reconciliation`, `receivables`, `payment_clock`, `tax_withholding`, `fx`, `payroll`, `certified_payroll`, `labor_rates`, `allowances`, `funding`, `cost_recovery`, `postcalc`.
- **E-invoicing & compliance:** `einvoice`, `einvoice_clearance`, `compliance`, `compliance_ai`, `compliance_docs`, `authority_submission`, `qms`, `signing`, `record_publishing`.
- **Country/regional packs:** `uk_pack`, `dach_pack`, `latam_pack`, `asia_pac_pack`, `china_pack`, `india_pack`, `mexico_pack`, `middle_east_pack`, `russia_pack`, `sa_pack`, `us_pack`, `us_ca_pack`, `us_tx_pack`, `global_presence`, `i18n_foundation`.
- **Estimating & cost (construction):** `boq`, `takeoff`, `dwg_takeoff`, `rebar_schedule`, `estimate_basis`, `estimate_rollup`, `rom_estimate`, `ai_estimator`, `costs`, `costmodel`, `cost_plan`, `cost_match`, `price_breakdown`, `price_index`, `norm_expansion`, `waste_factors`, `assemblies`, `prefab`, `measurement`, `value`, `eac`, `full_evm`, `cvr`, `project_controls`, `progress`, `resource_summary`, `resources`.
- **Project delivery / field:** `schedule`, `schedule_advanced`, `timeline`, `deadlines`, `recurring`, `daily_diary`, `field_diary`, `field_time`, `fieldreports`, `site_prep`, `site_logistics`, `site_supervision`, `construction_control`, `commissioning`, `closeout`, `defects_liability`, `punchlist`, `ncr`, `snagging`, `inspections`, `safety`, `hse_advanced`, `temporary_works`, `formwork`, `equipment`, `assets`, `methodology`.
- **Documents & collaboration:** `documents`, and a large file subsystem (`file_approvals`, `file_comments`, `file_distribution`, `file_favorites`, `file_references`, `file_saved_views`, `file_search`, `file_tags`, `file_transmittals`, `file_trash`, `file_versions`), `uploads`, `resumable_uploads`, `transmittals`, `submittals`, `rfi`, `correspondence`, `meetings`, `markups`, `plan_room`, `collaboration`, `collaboration_locks`, `review_authority`, `approval_routes`, `file_distribution`.
- **Contracts & commercial:** `contracts`, `changeorders`, `variations`, `claims_evidence`, `cases`, `cost_recovery`, `tendering`, `bid_management`, `moc`.
- **BIM / CAD / carbon:** `bim_hub`, `cad`, `carbon`, `esg`, `waste_factors`.
- **AI & intelligence:** `ai`, `ai_agents`, `ai_estimator`, `erp_chat`, `change_intelligence`, `project_intelligence`, `retrieval`, `smart_views`, `saved_views`, `search`.
- **Platform/admin & integration:** `admin`, `module_builder`, `connectors`, `integrations`, `webhook_leads`, `inbound_email`, `inbound_capture`, `phonelog`, `notifications`, `onboarding`, `backup`, `credentials`, `data_import`, `pipelines`, `client_errors`, `portal`, `voice`, `enterprise_workflows`, `interface_management`, `project_route`.

(Full list is in `MODULES.md`; `ls backend/app/modules` is authoritative.)

### Representative module: `admin`
- **What/Does:** platform administration — the RBAC matrix API (§5.4), plus admin operations and a safety-gated demo/QA reset.
- **How:** `permissions_router.py` (matrix view/edit), `router.py` (507 lines of admin ops), `service.py` (triple-gated `reset_demo_data`: `QA_RESET_ALLOWED` env + constant-time `confirm_token` + non-production hostname + a user-count ceiling — `admin/service.py:86-133`).
- **Interactions:** reads/writes `core/permissions.permission_registry`; audit-logs via `core/audit`; gated by `RequireRole`/`RequirePermission` from `dependencies.py`.

---

## 7. How this answers the original question

- **Inspection-ERP** (the four-part suite already delivered) has **no** user-manager / permission-management UI — enforcement exists (role/entitlement gating) but there is no screen to manage members or edit permissions.
- **OpenBusinessERP** (this document) **does** have it: an **editable RBAC matrix** (`admin/permissions_router.py`) that is **visible to managers+ and editable by admins**, backed by a live `PermissionRegistry`, **business-role presets** for job functions, and a **Settings → Team** matrix (`business_roles.business_matrix`). This matches "it's there under settings, only for those with the permission."

---

## 8. Suggested next steps (not yet done)

If you want the full inspection-erp-style treatment for OpenBusinessERP, the natural next deliverables are:
1. **Frontend/UX pass** — confirm the Settings → Team / permissions-matrix screens in `frontend/src` (not yet read in this pass) and document the actual UI, colours, menu, logo, profile.
2. **Per-module Four-Pillars** in batches (e.g. finance group, then estimating group, …) — 192 modules is large, so this is best done domain-by-domain.

Say which batch to start and I'll continue.

---

*Grounded in `kingdonk-ops/openbusinesserp` @ `a96d525` (shallow clone). Backend paths under `backend/app/`.*

---

# PART C2 — FRONTEND, UX & SETTINGS

# OpenBusinessERP — Frontend, UX & Settings Screens

> Companion to `openbusinesserp-architecture.md`. Documents the **website layout, design system/colours, menu/navigation, logo/branding, the Settings hub, the Settings → Team / Members / Job-roles surface, and the admin Permissions Matrix** — grounded in `frontend/src`. Every claim cites a file; paths are under `frontend/` unless noted.
>
> Stack recap: **React 18.3 + TypeScript 5.9 + Vite 6**, **zustand 5** state, **i18next 26** (44 languages, 4 RTL), Tailwind with CSS-variable tokens, Playwright e2e.

---

## 1. Design system & colours

Colours are **CSS custom properties** on `:root` in `src/index.css` (1,857 lines), mapped to Tailwind utilities in `tailwind.config.js` (341 lines) — so components use semantic class names (`bg-oe-blue`, `text-content-primary`, `border-border-light`, `bg-surface-secondary`) and theme automatically.

**Brand / accent**
- **Primary brand: `--oe-blue: #1747d6`** with `-hover #2155e6`, `-active #123bb5`, `-dark #0f319a`, `-subtle #eef2fd`, and an AA-tuned `-text #1239a8` (`index.css:16-37`). Used everywhere as `text-oe-blue` / `bg-oe-blue` and alpha forms (`bg-oe-blue/10`).
- Secondary accent: `--oe-purple #9333ea` (+subtle/text).

**Surfaces, text, borders, semantics** (`index.css`, `tailwind.config.js:45-85`)
- Surfaces: `--oe-bg #ffffff`, `-secondary #f6f7f9`, `-tertiary #fafbfc`, `-elevated` → Tailwind `surface.primary/secondary/tertiary/elevated`.
- Text hierarchy: `content-primary/secondary/tertiary/quaternary`.
- Borders: `border` `#d9dce3`, `border-light #e8eaef`, `border-focus`.
- Semantic palette: `semantic.error/warning/success/info` (+ subtle/text variants), used for badges and status pills.

**Theming mechanism**
- Each hex token also has a **channel-triplet var** (e.g. `--oe-blue-ch: 23 71 214`) so Tailwind alpha modifiers (`/10`, `/30`) compose correctly (`tailwind.config.js:10-33`).
- **Light/dark** is driven by overriding the vars (dark-mode values are AA-contrast tuned — the comments document a 51-file migration to a `-text` token that passes AA in both modes, `index.css:21-37`).
- **RTL**: four RTL languages; `dir="rtl"` on `<html>` flips the sidebar anchor and horizontal paddings via CSS rules (`AppLayout.tsx:132-153`).
- Radius, `content` max-width, and a large animation set (e.g. `pulse-border` for uninstalled converters) are tokenised too.

---

## 2. Website layout / shell

`src/app/layout/AppLayout.tsx` is the authenticated shell (mounted once per signed-in session by `App.tsx`'s `AppShell`). Structure:

```
┌───────────────────────────────────────────────────────────────┐
│ Sidebar (fixed desktop / slide-in mobile, RTL-aware, swipe)    │
│  → Sidebar.tsx                                                  │
├───────────────────────────────────────────────────────────────┤
│ Main column (lg:pl-sidebar)                                     │
│  DesktopToolbar  (desktop app only: back/fwd/reload/home/addr)  │
│  Header          (sticky; i18n page title; menu button)         │
│  <main>          page content (px-4 sm:px-7)                    │
└───────────────────────────────────────────────────────────────┘
Floating / global overlays mounted at shell level:
  DemoBanner · GlobalProgress · FloatingQueuePanel (bg tasks) ·
  DwgUploadIndicator · FloatingRecentButton ·
  FloatingChatButton + FloatingChatPanel (AI dock, Alt+A, push-mode) ·
  ReviewPromptCard · FeedbackDialog
```

Notable shell behaviours (all in `AppLayout.tsx`):
- **Mobile sidebar** with backdrop, body-scroll lock, and **swipe gestures** (RTL-aware edge-swipe to open/close — `:91-102`).
- **AI assistant dock** pushes the page aside on wide screens via `oe-ai-dock-shell` + `--oe-ai-dock-offset`; conversation survives navigation (`:104-120, 180-188`).
- **White-labelling**: `useBrandingStore.companyName` drives both the browser-tab suffix and the sidebar brand, falling back to **"OpenBusinessERP"** (`:52-75`); `CustomBranding.tsx` renders the logo in the sidebar.
- **Offline-first**: `useOfflineSync()` replays queued mutations on reconnect; a global-presence WebSocket reports route/idle status.
- **Desktop build**: `DesktopToolbar` renders window chrome (the product ships as a desktop app too); renders null on web.

---

## 3. Menu / navigation, logo & branding

`src/app/layout/Sidebar.tsx` (2,684 lines) + `navCatalog.ts` (1,089 lines) build a **grouped, workspace-aware** sidebar.

- **Logo/branding:** the sidebar header renders `CustomBranding` — the tenant's own company name/logo when white-labelled, else the OpenBusinessERP mark. There is a real branding store (`useBrandingStore`), unlike inspection-erp's generated monogram.
- **Grouped nav** (`navCatalog.ts`), each group an i18n `labelKey` with lucide icons (`routeIcons.ts`):
  - **Learn** — Video guides, Cases.
  - **Home** (`grp_overview`) — Dashboard (`/`), Projects, Documents/Files, Inbox, Timeline.
  - **Sales / Commercial** (`grp_commercial`) — CRM, Quotes & invoices (`/sales`), Products & services (`/items`), Inventory, Import data, Contacts, Contracts, Payment clock, Tax withholding, Tax rates, E-invoice clearance, Subcontractors.
  - **Purchasing** (`grp_procurement`) — Purchasing, Procurement, Bid management, RFQ bidding, Tendering, Supplier catalogs.
  - **Resources/Inventory** (`grp_resources`) — Equipment, … (plus the many construction/finance groups).
- **Simple vs Advanced mode:** items flagged `advancedOnly` hide in Simple mode; a **company workspace preset** (`workspaces.ts`, `PRESET_WORKSPACES`) can redefine Simple mode to show a curated set first. Pinned and Recent rows are tracked separately.
- **Terminology overrides:** `navCatalog` remaps a row's `labelKey` through a terminology layer (`terminology.ts`) so labels can be re-worded per deployment/industry.
- **Header** (`Header.tsx`, 2,108 lines): sticky top bar with the i18n'd page title (`resolvePageTitleKey`), the mobile menu button, and global actions (search, learn, subscribe/support, bug report, language).

---

## 4. Settings hub

`src/features/settings/SettingsPage.tsx` (2,579 lines) is a **tabbed hub**; the guided-tour catalog (`settingsGuide.ts`) enumerates the sections:

| Tab / section | Component(s) | What it does |
|---|---|---|
| **General** | `BusinessSettingsHub`, `CompanyDocumentsSettings`, `MailSettings`, `PaymentSettings`, `TextSizeSetting` | Company profile, mail server, payment details, UI text size. |
| **Regional** | `RegionalSettings` | Locale, number/date formats, currency, country/regional packs. |
| **AI** | the AI-provider panel in `SettingsPage.tsx` | Choose an LLM provider from a large catalog — Anthropic, OpenAI, Gemini, OpenRouter, Mistral, Groq, DeepSeek, Together, Fireworks, Perplexity, Cohere, AI21, xAI, Zhipu, Yandex, Baidu, Kimi, **Ollama (keyless/local)**, and an **OpenAI-compatible endpoint / vLLM** (base-URL, optional key) — with API-key entry (show/hide), tool-calling mode (auto/on/off) and timeout (`SettingsPage.tsx:122-276, 464-831`). |
| **Integrations** | `ModulesSettings`, `WebhookLeads`, connectors | Enable/disable modules; inbound webhooks; connectors. |
| **Advanced** | `BackupRestore`, DB setup wizard, `VectorStatusCard`, `ReferenceDataPanel`, `TranslationManager` | Backup & restore, database setup, vector-index status, reference data, translations. |
| **Account** | `DeleteAccountDialog`, profile | Account management incl. delete-account. |
| **Dashboard** | widget settings (`useWidgetSettingsStore`) | Reorder / show / hide personal dashboard sections (saved to this browser). |
| **Documents** | `DocumentDesignSettings`, `BusinessDocumentsSettings`, `DocumentTemplatesPanel` | Branded document design + templates for quotes/invoices. |
| **E-invoicing** | `EInvoiceSettings` | EN 16931 / Peppol / XRechnung / ZUGFeRD configuration. |
| **Team** | `SettingsTeamPanel` (→ §5) | Members, roles, job-role presets, licence. |

Settings is where the **profile, team and permissions** surfaces live — the part inspection-erp lacks.

---

## 5. Settings → Team / Members / Job-roles (the user-manager)

`src/features/settings/SettingsTeamPanel.tsx` (474 lines) + `SettingsRolePresets.tsx` (145 lines). It **surfaces** existing users/business-roles APIs rather than rebuilding auth, and is fully permission-gated.

**Gating** (`SettingsTeamPanel.tsx:99-138`)
- `canListMembers = role === 'admin' | 'manager'` (mirrors backend `users.list`).
- `isAdmin` unlocks the write affordances.
- A caller lacking `users.list` gets a **read-only "your membership" card** (built from `GET /v1/users/me/`) instead of controls that would 403 — exactly the "only for those with the permission" behaviour.

**Cards**
1. **Team & workspace** — member/active/admins/managers counts + role-breakdown chips (`:152-244`).
2. **Members** — list from `GET /v1/users/` with avatar, name/email, **role chip** (admin=Crown/red, manager=ShieldCheck/amber, editor=Edit3/blue, viewer=Eye/neutral), active/inactive badge, "You" marker. Admin-only **Invite User** and **Manage users** buttons navigate to `/users` (the full User Management page). Non-admins/managers see an honest "Member list is admin-only" panel (`:246-373`).
3. **Job roles** (`SettingsRolePresets`, shown to admin/manager) — the **business-role presets** surface:
   - A per-member **`<select>`** assigns a preset (Owner/Admin, Accountant, Sales, Purchasing & Warehouse, Read-only, or "Custom (role)") via `PUT /v1/business-roles/users/{id}/`; disabled unless `canEdit` (admin) (`SettingsRolePresets.tsx:76-106`).
   - A **preset × permission matrix** (from `GET /v1/business-roles/`) shows ✓/– for each business permission per preset (`:108-139`) — the UI for `core/business_roles.business_matrix()`.
4. **License & plan** — honest AGPL Community-Edition panel: version/env/modules-loaded from `GET /api/health`, AGPL-3.0 badge, and a **commercial-license request link** (no in-app payment flow, because none exists server-side) (`:377-471`).

---

## 6. Admin → Permissions Matrix

`src/features/admin/PermissionsMatrixPage.tsx` (1,490 lines) is the front end for the editable RBAC registry (backend documented in the architecture doc §5.4):

- **Data/gating:** `GET /api/v1/admin/permissions/matrix` (needs `audit.view` = manager+). Below that role the page renders a **read-only matrix**; admins get edit controls (`PermissionsMatrixPage.tsx:17-25, 531`).
- **Edits:** `PATCH /api/v1/admin/permissions/{key}` (per-permission `min_role`) and `POST /api/v1/admin/permissions/preset/{name}` (apply a baseline), both admin-only, with **optimistic cache updates + rollback** (`:602-616`), a per-role **edit mode**, a **preset-apply confirmation modal**, and **CSV export** of the snapshot (`:117, 547-564`).
- Renders roles × modules × permissions with per-cell toggles; permission keys shown in mono with human-cased action labels.

---

## 7. Permission gating in the UI (client mirror)

`src/shared/lib/permissionGates.ts` + `src/shared/auth/AdminOnly.tsx`:
- A **`PERMISSION_MIN_ROLE` map** mirrors the backend's minimum role per permission (e.g. `users.list: manager`, `payroll.read: manager`, `schedule.purge: admin`, `boq.delete: editor`). `useHasPermission()` reads the live role from `useAuthStore` and decides **whether the UI fires a call at all** — so a low-privilege role never triggers a 403 it can't pass (keeps pages usable and client-error reports honest). **The backend remains the sole authority** (`permissionGates.ts:3-22, 70-80`).
- A CI script (`scripts/check_role_mirrors_match_the_backend.py`) fails the build on drift between this mirror and `permission_registry`, so the client never silently diverges from server policy.
- `AdminOnly.tsx` conditionally renders admin-only affordances.

---

## 8. Status summary (asked-for items)

| Asked | Status in OpenBusinessERP | Where |
|---|---|---|
| Site layout | ✅ fixed sidebar + sticky header + desktop chrome + floating AI/queue/recent | `app/layout/AppLayout.tsx` |
| Colours / design | ✅ `--oe-*` CSS-var tokens, brand `#1747d6`, semantic palette, light/dark, RTL | `src/index.css`, `tailwind.config.js` |
| Menu / menus | ✅ grouped, workspace-aware, Simple/Advanced, terminology overrides | `navCatalog.ts`, `Sidebar.tsx` |
| Logo | ✅ real white-label branding (company name + logo), fallback "OpenBusinessERP" | `CustomBranding.tsx`, `useBrandingStore` |
| Settings | ✅ full tabbed hub (General/Regional/AI/Integrations/Advanced/Account/Dashboard/Documents/E-invoice/Team) | `settings/SettingsPage.tsx` |
| Profile / account | ✅ Account tab + delete-account; self-membership card | `settings/*`, `SettingsTeamPanel.tsx` |
| **User manager** | ✅ Members list + invite + Manage users (`/users`), admin/manager-gated | `SettingsTeamPanel.tsx`, `features/users` |
| **Permissions management** | ✅ **editable RBAC matrix** (admin) + **job-role presets** (preset×permission matrix) | `admin/PermissionsMatrixPage.tsx`, `settings/SettingsRolePresets.tsx` |

This is the capability you pointed to: **under Settings (Team → Job roles) and Admin → Permissions Matrix, visible to managers+ and editable by admins** — present in OpenBusinessERP, absent from inspection-erp.

---

## 9. Not covered here (offer)

Not yet read in depth: the full `/users` **User Management page** (`features/users`), the Header's global-search / command palette internals, and per-feature screens for the 192 modules. Say the word and I'll document any of those next.

*Grounded in `kingdonk-ops/openbusinesserp` @ `a96d525`. Frontend paths under `frontend/`.*

---

# PART C3 — USER MANAGEMENT & SYSTEM/MODULE SETTINGS

# OpenBusinessERP — User Management (`/users`) & System/Module Settings

> Third companion doc (with `openbusinesserp-architecture.md` and `openbusinesserp-frontend-ux.md`). Documents the full **`/users` User Management page** and the **system/module settings** surface, grounded in `frontend/src`. Paths under `frontend/`.

---

## 1. User Management page — `/users`

**File:** `src/features/users/UserManagementPage.tsx` (1,246 lines) · **API:** `src/features/users/api.ts`.
The admin panel for managing people, roles, status and per-user module access. It **surfaces the users API** (the backend stays authoritative); every control is role-gated.

### 1.1 Access gating
- `canListUsers = useHasPermission('users.list')` → **manager+**. Below that the page renders `<NoAccessState/>` instead of firing the list call (`:760, 926-935`).
- `isAdmin` (from `useAuthStore`) unlocks **Invite** (create), because `POST /v1/users/` is gated `users.create = admin`; managers can view but not invite (`:754-764, 953-962`).
- The list query is `enabled: canListUsers`, so a manager with no permission never triggers a 403.

### 1.2 API surface (`api.ts`)
| Call | Endpoint | Notes |
|---|---|---|
| `fetchUsers({is_active,limit,offset})` | `GET /v1/users/` | List; `users.list` (admin/manager). |
| `inviteUser(data)` | `POST /v1/users/` | Admin-create; **honours the chosen role** (unlike open `/auth/register`, which forces viewer). Strong password: **≥12 chars, ≥1 letter, ≥1 digit** (mirrored client-side). |
| `updateUser(id,{full_name,role,is_active,locale})` | `PATCH /v1/users/{id}` | Role change / activate-deactivate / rename. |
| `deleteUser(id)` | `DELETE /v1/users/{id}` | **Erase/anonymise in place** — personal fields stripped, account can't log in, but their projects/history keep resolving. Server refuses deleting the **last active admin (409)** and **self (400)**. |
| `getUserModuleAccess(id)` / `setUserModuleAccess(id,payload)` | `GET`/`PATCH /v1/users/{id}/module-access/` | Per-user module visibility + access level (§1.6). |

### 1.3 Layout
Breadcrumb → `PageHeader` (title + subtitle + ModuleGuide + admin-only **Invite User**) → `DismissibleInfo` intro (links to **Governance** and **Audit Log**) → **stats cards** (Total / Active / Admins / Managers) → **search + status filter** (All/Active/Inactive) → **users table** → **role legend**. Uses the Datum tokens (`bg-oe-blue`, `surface-*`, `content-*`) throughout.

### 1.4 Users table
Columns: avatar + Name · Email · **Role (dropdown)** · Status badge · Last login · Actions. Each row's actions: **Access** (opens the per-user module panel), **Deactivate/Activate** (lock/unlock icon), **Delete** (trash).
- **Roles:** `admin` Crown/red · `manager` ShieldCheck/amber · `editor` Edit3/blue · `viewer` Eye/neutral, ranked `admin3>manager2>editor1>viewer0` (`ROLE_CONFIG`, `ROLE_RANK`).
- **`RoleDropdown`** is a portal-anchored popover (escapes table overflow), re-positions on scroll/resize, Escape-closes.

### 1.5 Safety guards (self-lockout & destructive confirms)
All in the page's handlers (`:840-909`):
- **Self-protection:** a user **cannot change their own role, deactivate themselves, or delete themselves** from this list — those controls are disabled with explanatory tooltips, and the handlers early-return on `userId === currentUserId` (self id from `/v1/users/me/`).
- **Role downgrade** (new rank < current) → **warning confirm** ("they will immediately lose the higher role's access").
- **Deactivate** → **danger confirm**; activate is silent (non-destructive).
- **Delete** → **danger confirm** spelling out erase-but-keep-history semantics.
Errors/successes surface via the toast store; the list invalidates (`['users']`) after each mutation.

### 1.6 Invite modal
`InviteModal` (shared `WideModal`: focus-trap, Esc, backdrop dismiss, scroll-lock). Fields: full name, email, password (with live "min 12 + letter + digit" validation), and a **4-button role picker** (admin/manager/editor/viewer). Submits to `inviteUser` → `POST /v1/users/`.

### 1.7 Per-user Module Access panel (`ModuleAccessPanel`)
A second authorization layer **on top of** the org role — a per-user overlay of **which modules are visible and at what level**:
- Loads `GET /v1/users/{id}/module-access/`; edits tracked as `dirty`; saves via `PATCH …/module-access/` (toast on success).
- **Modules grouped** into 6 buckets: `core` (projects, boq, costs, assemblies, validation), `planning_finance` (schedule, costmodel, finance, procurement, changeorders), `communication` (tasks, meetings, rfi, correspondence, transmittals), `quality_safety` (inspections, ncr, safety, punchlist, submittals), `documents_bim` (documents, cde, bim_hub, fieldreports, contacts), `ai_analytics` (ai, takeoff, reporting, risk).
- Per module: a **visibility toggle** + an **access-level selector** (`none / view / edit / full`); setting a level ≠ none also flips visibility on.
- **Custom role name** free-text field (e.g. "Site Engineer") saved alongside.
- **Presets:** `all` (full on every module), `viewer` (view everywhere), `minimal` (only core modules at edit). Footer shows "N / total modules enabled".

> So OpenBusinessERP has **three authorization tiers** surfaced in the UI: (1) the org **role** (this page's dropdown), (2) **business-role presets** (Settings → Team → Job roles), and (3) this **per-user module visibility/access** overlay. All sit above the backend permission registry documented in `openbusinesserp-architecture.md` §5.

---

## 2. System / Module settings

**File:** `src/features/settings/ModulesSettings.tsx` (678 lines), surfaced in **Settings → Integrations**.

### 2.1 What it is (and the distinction it draws)
The panel's own docstring clarifies **two separate "module is on" systems**:
1. **Backend module loader** (`GET /v1/modules/`, `POST /v1/modules/{name}/enable|disable`) — decides whether a module's **routes and models are mounted at all**; **instance-wide, admin-only**. **This panel drives that.**
2. **`useModuleStore`** — a **per-user sidebar view preference**, edited from the sidebar's "Edit menu". Not this panel.

The panel states which it is and links to the other, to end the long-standing confusion.

### 2.2 Data model (`SettingsModule`, from `/v1/modules/`)
`name, version, display_name, display_name_i18n?, description, author, category, depends[], optional_depends[], has_router, loaded, enabled, is_core`.

### 2.3 Layout & behaviour
- **Installed modules** card: a **summary** ("Optional modules installed: X of Y"), a **search** (name/key/description), and **category-grouped, collapsible** lists. Manifest `category` is free text, so it's mapped to a **closed key set** (business, regional, extension, controls/project_controls, enterprise, integration, infra, estimation, developer_tools, compliance) + an **"Other"** fallback — so no snake_case English leaks into the 43 locales.
- **Each module row:** display name (i18n-resolved), **"Always on" badge** for core, version, description, a **"Required by: …"** warning when other enabled modules depend on it, an **"Installing this also installs: …"** hint, and a **toggle switch** (`role="switch"`, busy spinner).
- **Core modules** card: collapsed by default ("Show always-on modules (N)") because the locked core rows (the comment cites ~119 core vs ~69 optional of ~188 loaded) would otherwise bury the changeable ones. Core rows are permanently locked.
- **Pointers card:** links to the **Module registry** page (`/modules` — industry packs, the **module builder**, developer guide) and reminds users that tidying their *own* menu is the sidebar's "Edit menu", not this instance-wide panel.

### 2.4 Dependency-aware toggling (client-side, pre-empting the server)
- A **reverse-dependency index** (`dependents`) is computed from the single list response (rather than 188 calls to `/v1/modules/dependency-tree/{name}`).
- **Disable:** if any **enabled** module still `depends` on this one, the toggle is **blocked** with a warning toast (pre-empting the server's 400); otherwise a **confirm** ("its screens and API routes stop being served for everyone here; your data is kept") then `POST …/disable`.
- **Enable:** pulls in any **disabled dependencies**; the success toast names what was "Also installed" (the backend `enable_module` walks `depends`).
- A module is **locked** in the UI when it's core, the caller isn't admin, or it has blocking dependents.
- Non-admins see a read-only **"Only an administrator can install or remove modules"** hint; the toggles are disabled.
- Query key `['system-modules']` is **shared with the Modules registry page and the Sidebar** (which hides routes whose backing module is disabled), so one successful toggle refreshes all three.

### 2.5 Related system-settings surfaces (same Settings hub)
From `settingsGuide.ts` / `SettingsPage.tsx` (detailed in the frontend-ux doc §4): **General** (company profile, mail, payment), **Regional** (locale/format/currency/country packs), **AI** (LLM provider + key management), **Advanced** (backup & restore, DB setup wizard, vector status, translations), **Account**, **Dashboard** (personal widget layout), **Documents**, **E-invoicing**, **Team** (users/roles/presets/licence).

---

## 3. Status summary

| Asked | Status | Where |
|---|---|---|
| Full `/users` User Management page | ✅ documented — list, invite, role dropdown, activate/deactivate, erase, self-guards, per-user module-access matrix | `features/users/UserManagementPage.tsx`, `api.ts` |
| System / module settings | ✅ documented — instance-wide enable/disable via the module loader, category groups, dependency-aware toggles, core-locked list, registry link | `features/settings/ModulesSettings.tsx` |

Both are **admin-centric and permission-gated**: `/users` needs `users.list` to view and `users.create`/admin to mutate; module install/remove is admin-only and instance-wide.

*Grounded in `kingdonk-ops/openbusinesserp` @ `a96d525`.*

---

# PART C4 — MODULE REGISTRY & MODULE BUILDER

# OpenBusinessERP — Module Registry (`/modules`) & Module Builder

> Fourth companion doc. Documents the **`/modules` registry** and the **module-builder** (runtime, no-code/AI module creation), grounded in `frontend/src/features/modules` and `frontend/src/features/module-builder`. Paths under `frontend/`.

## Routes (from `src/app/App.tsx`)
| Route | Component | Purpose |
|---|---|---|
| `/modules` | `ModulesPage` (3,017 lines) | The **registry**: company profiles, packs, data packages, and system modules. |
| `/modules/developer-guide` | `ModuleDeveloperGuide` | In-product guide to building a module by code. |
| `/module-builder` | `ModuleBuilderPage` (431 lines) | **Runtime module builder**: register of modules built on this instance + the build wizard. |
| `/modules/:moduleKey` | `GeneratedModulePage` | Renders a generated module's list/form from its served `ui-spec`. |

Both the registry and the builder are **read-for-all, write-for-admin**, and the server enforces it on every call (the pages just hide controls).

---

## 1. `/modules` — the registry (`ModulesPage.tsx`)

Layout: `PageHeader` (with a **"Build a module — developer guide"** link to `/modules/developer-guide`) → dismissible intro (links to the developer guide) → `AdvancedModeNotice` → a **"Find a module"** search → a **4-tab** panel (`useTabKeyboardNav`, deep-linkable via `?tab=`):

| Tab (`MODULE_TAB_IDS`) | Label | What it holds |
|---|---|---|
| `profiles` | **Company Profiles** | Pre-shaped company profiles; `profileDelta`/`profileShapes` show what switching profile would add/remove. |
| `partner-packs` (alias `?tab=packs`) | **Packs** | **Country** and **Industry** packs (`PackType` badges: country=Globe/blue, industry=HardHat). Install via `PartnerPackApplyDialog` / remove via `PartnerPackDeactivateDialog`; `useInstallPack`/`useRescanPacks`/`useAppliedPack`; `canInstallPacks` gates it. A pack can enable/disable a bundle of modules, load a demo, apply branding, and add validation-rule packs & methodology. |
| `data-packages` | **Data Packages** | Reference/data bundles (regional registries etc.). |
| `system` | **System Modules** | The instance-wide module on/off list — the same data as **Settings → Integrations** (`/v1/modules/`), shown here too. |

- **Module display names** resolve through `resolveModuleDisplayName` + manifest i18n (`translateManifestText`), never raw snake_case.
- **"Request a custom module"** (`RequestCustomModuleDialog`, launched from the sidebar CTA): a full-viewport modal with two CTA cards routing to a hosted contact form on the marketing site (lead capture / CRM happens there — no in-app form), plus a link to the in-product developer guide. This is the commercial "we'll build it for you" path.
- The registry distinguishes **instance-wide module state** (the module loader) from a user's **personal sidebar** preferences (`useModuleStore`, "Edit menu"), same as the Settings panel.

---

## 2. `/module-builder` — runtime module builder (`ModuleBuilderPage.tsx`)

**What it is.** A no-code/AI builder that **creates a working module at runtime** — its own list, form, validation and API — that **appears in the menu immediately with no restart** and **survives platform upgrades** (it belongs to the instance, stored under a server `runtime_root`). Backend: `backend/app/modules/module_builder`, mounted at `/api/v1/module-builder`.

**The page** (register + launcher):
- `PageHeader` with an admin-only **"Build a module"** (`Wand2`) button that opens `ModuleBuilderWizard`.
- **AI-availability banner**, driven by the builder's own `vocabulary.assistant_available` (admin-only fetch): green "AI connected — draft from a sentence", or amber "No AI connected — start from a template or build by hand" with a link to **Settings → AI**. Deliberately tri-state (undefined = not answered, never shown as "off").
- A collapsible **how-it-works** (4 steps) and an optional `<details>` showing the server `runtime_root` (for admins taking backups).
- **Installed list** as cards: display name, `v{version}`, a `{entity} · N fields · M rules` summary, the module's `base_path` (mono, shown in full — it differs per instance), **Open the module** (`/modules/{key}`), **ExtendModuleButton** (upgrade), and an admin-only **remove** (trash).
- **Quarantine:** a module whose generated files the server could not vouch for is shown with a `QuarantineBadge`/`QuarantineMessage`; its routes are unmounted (its `base_path` answers nothing) but its records are kept and it can still be removed.
- **Removal** dialog (`WideModal`): removes the module immediately; **records are kept unless** the "Also delete everything recorded with it" box is ticked (then an irreversible warning). Reinstalling restores kept data.

---

## 3. The build wizard (`ModuleBuilderWizard.tsx` + `wizard/*`)

Three screens, each with one obvious next step (`STEPS = ['describe','proposal','create']`, then `'done'`; an **upgrade** path uses `['proposal','create']`):
1. **Describe** (`DescribeScreen`) — a sentence (when an AI provider is connected), a **template**, or build by hand (`MIN_DESCRIPTION` guard).
2. **Proposal** (`ProposalScreen`) — review the drafted entity/fields/rules; tick **suggestions** (link fields, features: status/due/export/comments) — nothing is applied until ticked; `FineTune` for detailed edits; a `RefusalNotice` surfaces a model/server refusal.
3. **Create** (`CreateScreen` → `DoneScreen`) — a plain summary with the files and address one click away, then **Create module** installs it.

Suggestions come from the assistant **or** from deterministic server rules (`suggestForSpec`), so templates and the by-hand path get the same proposals.

---

## 4. Builder API & generated-module runtime (`module-builder/api.ts`, 767 lines)

Two halves of one feature: describing/installing a module, and then using it. Base `'/v1/module-builder'`.

**Spec model** (mirrors `spec.py`): `ModuleSpec{ key, display_name, description, category(community|integration|regional), icon, version, author, entity, rules[], drafted_by(assistant|wizard), schema_version, features }`. An **entity** has typed **fields** (`text/long_text/integer/number/money/date/datetime/boolean/select/link`); `link` fields point at another module's records (`contract/contact/schedule_activity/document/user`). **Features:** `status` (2–8 states), `due` (deadline + reminder), `export`, `comments`. **Rules:** `required/positive/not_future/range/one_of/order` with severity error/warning.

**Build endpoints:**
| Fn | Endpoint | Notes |
|---|---|---|
| `fetchVocabulary` | `GET /vocabulary` | Allowed field types, rule kinds, reserved names/keys, `max_fields`, `assistant_available`, link targets, features, `upgrade`. The wizard reads capability from here, so the frontend can't offer what the server doesn't support (`extra="forbid"` → a stray key is a 422). |
| `draftSpec` | `POST /draft` | AI: sentence → spec (long-running). |
| `suggestForSpec` | `POST /suggest` | Deterministic, AI-free suggestions. |
| `previewModule` | `POST /preview` | Renders **every file** that would be written (none are), returning a **`review_token`**. |
| `installModule` | `POST /` | Writes + loads the module; **requires the `review_token`** — the review step is a server-enforced rule, not just a screen. Atomic: installed-and-serving, or nothing left behind. |
| `previewUpgrade` / `upgradeModule` | `POST /{key}/upgrade[/preview]` | Add fields/features to an installed module in place; existing records preserved (new field empty, new status at first stage). |
| `fetchInstalledModules` | `GET /` | `{items, total, runtime_root}`; readable by any signed-in user. |
| `uninstallModule` | `DELETE /{key}?drop_data=` | Records kept unless `drop_data=true`. |

**Money/quantities are strings end-to-end** (Decimal → JSON string) — never parsed to JS numbers (a contract sum must not be "approximately right"). `base_path` always comes from the server (`url_prefix_for` in `spec.py`), never rebuilt client-side.

**Generated-module runtime API** (mirrors the generated `router.py`): `GET {base_path}/ui-spec` (the screen description the renderer reads), list/get/create/update/delete records, and `GET {base_path}/export?format=csv|xlsx` when the `export` feature is on. A generated validator returns field-level findings on 422 (`findingsFromError`); upgrades/installs can return structured **refusals** (`upgrade_not_additive`, `layout_conflict`, `module_quarantined`, `links_switched_off`, …) parsed by `upgradeRefusalFrom`.

---

## 5. `/modules/:moduleKey` and `/modules/developer-guide`
- **`GeneratedModulePage`** renders a built module's list + record form entirely from its served `ui-spec` (no compiled screen ships for a runtime module); `RecordFormModal` edits rows; `LinkPicker` resolves `link` fields via `lookupRecords`/`lookupLabels`.
- **`ModuleDeveloperGuide`** (`moduleDeveloperGuide.md`) is the in-product guide for building a module by code — the hand-written alternative to the wizard.

---

## 6. Gating & safety summary
- **Read:** any signed-in user can see the registry and the installed-modules list.
- **Write (install/remove/upgrade/toggle, apply packs):** **admin-only**, enforced server-side; the UI hides the controls for non-admins and shows read-only hints.
- **Safety rails:** preview-before-install with a server-checked `review_token`; **quarantine** of unverifiable generated code (routes unmounted, data kept); **data-retention-by-default** on uninstall (opt-in drop); non-additive/layout-conflict **refusals** rather than silent data rewrites; AI only *suggests* (nothing applied until ticked, nothing created until "Create module").

| Asked | Status | Where |
|---|---|---|
| `/modules` registry | ✅ 4 tabs (Profiles/Packs/Data Packages/System), packs install/deactivate, request-custom, dev-guide link | `features/modules/ModulesPage.tsx` |
| Module builder page | ✅ runtime builder register + wizard + uninstall/drop-data + quarantine + AI banner | `features/module-builder/ModuleBuilderPage.tsx`, `ModuleBuilderWizard.tsx`, `api.ts` |

*Grounded in `kingdonk-ops/openbusinesserp` @ `a96d525`.*

---

# PART C5 — DOCUMENT INGEST, AI INDEXING & ERP-CHAT

# OpenBusinessERP — Document Ingest, AI Indexing & the ERP Chat Trace

> How a document gets from upload to being answerable by the AI assistant. Two decoupled index pipelines (semantic vector + full-text), and the `erp_chat` tool-calling agent that queries them. Grounded in `backend/app/` of `kingdonk-ops/openbusinesserp` @ `a96d525`.

---

## Part A — Ingest & AI indexing (two separate pipelines)

### A1. Semantic / vector index — the "AI index" (`oe_documents` collection)
Powers semantic search, the "Similar items" panel, and the chat's `search_documents` tool.

- **Trigger — the in-process event bus.** `modules/documents/events.py` subscribes to `documents.document.created / updated / deleted`. Create/update → `core/vector_index.index_one`; delete → `delete_one`. **Best-effort**: its own short-lived session, errors logged and swallowed, never breaks CRUD (`events.py:41-95`).
- **What is embedded — metadata, not the file body.** `modules/documents/vector_adapter.py` builds the canonical text from **name · description · category · tags · drawing_number · discipline · file_name** (joined with `|`) and a light payload for hit rendering (`vector_adapter.py:44-88`). So *"structural rebar schedule level 02"* matches by meaning regardless of filename. **The extracted page/OCR text is NOT in this vector** — that lives in the full-text index (A2).
- **Embedding layer** (`core/vector_index.py`) — a **multi-collection** abstraction: every module ships a `vector_adapter.py` implementing the `EmbeddingAdapter` protocol (collection name, `to_text`, `to_payload`, `project_id_of`), so BOQ, documents, tasks, risks, BIM, RFIs, submittals, correspondence, validation and chat share one API. Reads: `search_collection` / `find_similar` (encode query → `vector_search_collection` → decode payload → `VectorHit`). Writes: `index_one / index_many / delete_one / reindex_collection`.
- **Vector store** (`core/vector.py`) — **LanceDB embedded by default** (in-process like SQLite, data at `~/.openestimator/data/vectors/`); **Qdrant** via `VECTOR_BACKEND=qdrant` for servers. Every row: `id, vector, text, tenant_id, project_id, module, payload(JSON)` — so hits are **tenant- and project-scoped by filter**. Embeddings via sentence-transformers (legacy default `all-MiniLM-L6-v2`/384-dim; active model multilingual from `Settings.embedding_model_name`; Qdrant snapshots 3072-dim).
- **Performance** (`core/embedding_pool.py`) — optional warm pool: pre-load/compile at startup (`OE_VECTOR_PRELOAD=1`), thread-pool `encode_texts_async` (`OE_VECTOR_POOL_WORKERS`), process-local per uvicorn worker. Model install in `core/embedding_installer.py`.
- **Fail-open everywhere** — if LanceDB/Qdrant/model is unavailable, every helper warns and returns empty/no-op; callers never wrap in try/except.

### A2. Full-text content index — `file_search` (text extraction + OCR)
The *other* pipeline — this is where the file's actual contents are processed.

- **Extraction** (`modules/file_search/extractors.py`, `extract_text(payload, mime)`): **PyMuPDF (`fitz`)** pulls embedded PDF text (cheap path); if a PDF has `< 32` chars of embedded text (a scan) it **falls back to Tesseract OCR**, rasterising each page; images (jpg/png/tiff) → OCR; text/JSON/XML → UTF-8 decode. Hardened: content capped (~1–10 MB), OCR capped at 150 DPI / 40 MP per page / 200 pages (memory safety); any missing library → engine `"none"`, never raises.
- **Store & search** (`modules/file_search/service.py`, `models.py`): extracted text → `oe_file_search_index.content_text` (+ `page_count`, `ocr_engine`, `language`, `indexed_at`); re-index overwrites in place. Search = **Postgres `to_tsvector('simple', content_text) @@ plainto_tsquery` + `ts_rank`** (generated `tsv_vector` column + GIN index), SQLite `LIKE` fallback for dev.

### A3. The key distinction
| | Vector / AI index (`oe_documents`) | Full-text index (`file_search`) |
|---|---|---|
| Content | Document **metadata** (name/desc/tags/drawing/discipline) | **Extracted body text + OCR** |
| Engine | sentence-transformers → LanceDB/Qdrant (ANN) | PyMuPDF + Tesseract → Postgres tsvector |
| Trigger | `documents.document.*` events | file-bytes indexer |
| Used by | semantic search, "Similar items", **chat tools** | `/files` keyword search |

**Implication:** semantic search over a PDF's *body* isn't wired today — bodies are keyword-searchable via `file_search`; semantics match on title/description/tags/drawing metadata. To add body-level semantics, feed `file_search`'s `content_text` (chunked) into a vector collection through the same `EmbeddingAdapter` path.

---

## Part B — The ERP Chat (`erp_chat`) trace

`erp_chat` is a **tool-calling agent with SSE streaming** (`modules/erp_chat/service.py`, 1718 lines). It reads live ERP data through tools and **changes nothing directly** — every mutation is a human-confirmed `propose_*` proposal.

### B1. Request → agent loop
1. **Entry:** `ChatSessionStreamer.stream_response(user_id, request)` (`service.py:514`) — an async generator yielding SSE frames: `session_id`, `tool_start`, `tool_result`, `text`, `done`, with periodic keep-alive comment frames (the reply is not token-streamed, so comments keep the connection alive while a provider call is in flight).
2. **Prompt assembly** (`prompts.py`): `SYSTEM_PROMPT` (tool-capable) or `SYSTEM_PROMPT_NO_TOOLS` + a per-request **context block** (`build_context_block`) naming the active project, currency, interface language, and regional methodology (VOB/HOAI for DACH, NRM/RICS for UK, …). The no-tools prompt forbids fabricating project data.
3. **Provider call** (`call_ai`): **Anthropic / OpenAI native tool-calling**; other OpenAI-shaped providers get the tool schema too (widened per-provider on evidence — issue #424); self-hosted `ollama`/`vllm` tool mode is configurable (`ai_client.tool_calling_mode`); everything else falls back to plain text. Per-provider timeout via `chat_ai_timeout`.
4. **Tool loop:** `_extract_tool_calls(provider, result)` → run each handler → append `tool_result` → call the model again, up to `MAX_TOOL_ROUNDS` (infinite-loop guard). Tool results are truncated to `MAX_TOOL_RESULT_CHARS` (8000).

### B2. The tools (`tools.py`, 1405 lines)
- **Structured-data tools** (direct SQL/service reads): `get_all_projects`, `get_project_summary`, `get_boq_items`, `get_schedule`, `get_validation_results`, `get_risk_register`, `get_cost_model`, `compare_projects`, `run_validation`, `search_cwicr_database`.
- **Semantic-memory tools (vector-backed)** — the bridge to Part A: `search_documents`, `search_boq_positions`, `search_tasks`, `search_risks`, `search_bim_elements`, `search_rfis`, `search_submittals`, `search_correspondence` (each: free-text `query` + optional `project_id`; omit → cross-project "lessons learned").
- **Permission-gated:** `check_tool_permission` + `_require_project_access` / `_user_is_manager_or_above` enforce the caller's RBAC per tool; a tool the role can't run is refused, not executed.

### B3. How `search_documents` reaches the `oe_documents` vectors (the full loop)
```
model emits tool_call search_documents{query, project_id?}
  → handle_search_documents (tools.py:1234)
    → _generic_collection_search(COLLECTION_DOCUMENTS, …) (tools.py:1142)
      → modules.search.service.unified_search_service(...)         # two-track
          ├─ VECTOR track: core.vector_index.search_collection
          │     → core.vector.encode_texts_async(query)            # embedding_pool
          │     → core.vector.vector_search_collection             # ANN over LanceDB/Qdrant
          │        filtered by tenant_id + project_id              # the vectors A1 wrote at ingest
          └─ SQL track: ILIKE substring over the same collection's rows
        → merge + rank → hits
    → tool_result frame streamed to the client, fed back to the model
  → model composes the answer from the hits (never fabricates) → text frames → done
```
So the ingest side (A1: `documents.document.*` → `vector_adapter` → `index_one` → LanceDB/Qdrant) and the read side (B3: `search_documents` → `unified_search_service` → `search_collection` → same store) are the two ends of one semantic memory, joined by the `oe_documents` collection and scoped by tenant/project on both write and read.

### B4. Mutations are proposals, not writes
A change is never a direct tool: the model calls a `propose_*` tool from `actions/registry`, which **stores a proposal** (`ChatActionService` / `propose_tool_result`); `_commit_turn` commits it (in a savepoint) **before** its `tool_result` frame so the UI renders a confirmation **card** the human approves — the platform's "AI-augmented, human-confirmed" rule. `MAX_TOOL_ROUNDS` bounds the loop.

### B5. Chat self-memory
The chat also indexes **its own messages** into a `COLLECTION_CHAT` vector collection (`erp_chat/events.py` + `vector_adapter.py`), and `router.py` exposes a `find_similar` over `COLLECTION_CHAT` (surface similar past conversations) — the same `EmbeddingAdapter` pattern as documents.

---

## One-line summary
Upload → `documents.document.created` event → **metadata embedded** into the `oe_documents` vector store (LanceDB/Qdrant, tenant/project-scoped), while `file_search` separately extracts **full text/OCR** into a Postgres tsvector index. The **erp_chat agent** answers by calling the `search_documents` tool, which runs a two-track (vector + SQL) `unified_search_service` over that same `oe_documents` collection, feeds the scoped hits back to the LLM, streams the reply over SSE, and routes any change through a human-confirmed `propose_*` card.

*Grounded in `kingdonk-ops/openbusinesserp` @ `a96d525`. Files: `core/vector.py`, `core/vector_index.py`, `core/embedding_pool.py`, `modules/documents/{vector_adapter,events}.py`, `modules/file_search/{extractors,service,models}.py`, `modules/erp_chat/{service,tools,prompts,events,vector_adapter}.py`, `modules/search/service.py`.*

---

# PART E — MODULE INDEX & CATALOGUE (all 191 modules, Batches 1–7)

# OpenBusinessERP — Per-Module Docs · Batch 1: Finance & Accounting

> Part of the full per-domain module documentation (inspection-erp Part-3 style) for OpenBusinessERP's 192 modules. **Batch 1 of 7.** Each module gets the Four Pillars (What It Is · What It Does · How It Does It · Interactions/failure). Grounded in `backend/app/modules/<name>/`. Tables are `oe_*`; routes mount at `/api/v1/<prefix>`; dependencies are manifest `depends`.

**Modules in this batch (15):** `gl`, `finance`, `banking`, `reconciliation`, `receivables`, `payment_clock`, `tax_withholding`, `fx`, `payroll`, `certified_payroll`, `labor_rates`, `allowances`, `funding`, `cost_recovery`, `postcalc`.

The README's "month in the software" is the spine these implement: quote → order → delivery → **invoice** (post the journal, split by tax) → **bank import + reconciliation** → **dunning** → period close → **SAF-T / DATEV** export.

---

## 1. `gl` — General Ledger (double-entry bookkeeping)
- **What:** the accounting core — chart of accounts, fiscal calendar, and balanced journal entries. `category=core`, no deps (it's foundational; other modules post *into* it).
- **Does:** ~60 routes over `oe_gl_account`, `oe_gl_fiscal_year`, `oe_gl_period`, `oe_gl_journal_entry`, `oe_gl_journal_line`, `oe_gl_attachment` — chart-of-accounts CRUD, fiscal-year/period open-close, journal posting, trial balance / ledger views, and attachments as posting evidence.
- **How:** service primitives `build_lines`, `check_balanced`, `natural_balance`, `base_currency`, `money` enforce **debits = credits** before a journal entry posts; accounts carry a natural (debit/credit) balance side; periods gate posting (a closed period refuses entries). Money is Decimal throughout.
- **Interactions:** the sink for `finance` (invoice postings), `banking` (reconciliation postings), `fx` (revaluation lines), `tax_withholding`. **Failure:** if disabled, nothing can post — it's effectively always-on `core`.

## 2. `finance` — Invoices, payments, budgets, EVM, GAAP ledger
- **What:** the commercial finance layer — customer/supplier invoices, payments, budgets, earned-value, and a GAAP-oriented ledger view. `category=core`, `depends=[users, projects, contacts]`. Router prefix `/gaap`.
- **Does:** 74 routes over `oe_finance_invoice(_item)`, `oe_finance_payment`, `oe_finance_budget`, `oe_finance_evm_snapshot`, `oe_finance_ledger(_account)` — invoice lifecycle, payment application, budget vs actual, EVM snapshots, ledger reporting.
- **How:** `compute_payment_withholding` nets withholding on payments; `resolve_position_cost_lines` ties cost lines to positions; EVM snapshots capture PV/EV/AC over time.
- **Interactions:** posts to `gl`; consumes `contacts` (parties) and `projects`; feeds `receivables` (open invoices) and dashboards.

## 3. `banking` — Bank accounts, statement import, matching, reconciliation
- **What:** the bank side — import statements and reconcile each line against the open invoice/bill behind it. `category=core`, `depends=[gl, sales]`.
- **Does:** 44 routes over `oe_bank_account`, `oe_bank_statement`, `oe_bank_transaction`, `oe_bank_match`, `oe_bank_partner_iban`, `oe_bank_settings`, `oe_bank_rule` — import **camt.053/054, MT940, CSV**, propose matches, apply reconciliation, and auto-rules.
- **How:** `partner_key` normalises counterparties; rules auto-classify recurring lines; a matched transaction posts the reconciling journal entry into `gl`; `oe_bank_partner_iban` learns IBAN→partner mappings.
- **Interactions:** `gl` (postings), `sales`/`finance` (open items to match), `reconciliation` (cross-record links). **Failure:** disabled → no statement import/reconciliation; invoices still post manually.

## 4. `reconciliation` — Cross-record linking
- **What:** a generic "these two records are the same money/event" linker. `category=controls`, `depends=[users, projects]`.
- **Does:** 6 routes over `oe_record_link` — list/get/decide links and assemble evidence threads across modules.
- **How:** `canonical_pair`/`link_key`/`scored_link_key` build a stable key for a pair; `gather_candidates` + `assemble_thread`/`build_event_thread` surface likely matches; `decide_record_link` confirms/rejects. Module-agnostic (works on entity URIs, not foreign keys).
- **Interactions:** complements `banking` matching; used by controls/audit flows. **Failure:** disabled → no manual cross-links; module-native matching still works.

## 5. `receivables` — Aging & dunning (draft-only)
- **What:** chase overdue customers safely. `category=core`, `depends=[sales, banking]`.
- **Does:** 44 routes over `oe_dunning_level`, `oe_dunning_settings`, `oe_dunning_block`, `oe_dunning_run`, `oe_dunning_letter`, `oe_ar_followup` — aging buckets, dunning runs, block list, AR follow-ups, statements.
- **How:** `bucket_for` ages open items; `next_level` proposes the dunning level; `render` builds the letter. **Dunning is draft-only** — letters are never sent automatically (module docstring); a blocked customer is skipped.
- **Interactions:** reads open invoices from `sales`/`finance`, payment status from `banking`. **Failure:** disabled → no automated chasing.

## 6. `payment_clock` — Statutory payment deadlines (regional)
- **What:** construction payment-term regimes (application → notice → due/final-date clocks). `category=regional`, `depends=[projects, contracts, subcontractors]`.
- **Does:** 26 routes over `oe_payment_clock_regime`, `_application`, `_notice`, `_event` — model a jurisdiction's payment regime, log applications/notices, and compute the statutory clock.
- **How:** `regime_spec`/`ensure_regimes` seed jurisdiction rules; `build_schedule`/`recompute_schedule` compute due/payless dates; `clock_snapshot`, `notified_sum`, `record_clock_events`, `sync_clock_register` keep a live register; applications/notices are CRUD with specs.
- **Interactions:** `contracts`/`subcontractors` (the paying relationships), `projects`. Regional add-on. **Failure:** disabled → no statutory-clock tracking.

## 7. `tax_withholding` — Withholding tax / reverse charge (e.g. CIS)
- **What:** compute and track tax withheld on supplier payments and reverse-charge VAT. `category=core`, `depends=[projects, users]`.
- **Does:** 48 routes over `oe_tax_withholding_regime`, `_party`, `_deduction`, `_reverse_charge` — regimes, per-party status/verification, deductions, reverse-charge records.
- **How:** `compute_taxable_base` + `compute_tax_withheld` with banded rates (`bands_of`/`find_band`/`resolve_band`), Decimal `to_decimal`/`quantise`; `verification_is_current` gates the rate on a party's current verification status; `seed_regimes` + `apply_*_body` drive config. Pairs with `finance.compute_payment_withholding`.
- **Interactions:** `finance` (payment netting), `gl` (postings), `projects`. **Failure:** disabled → payments don't net withholding.

## 8. `fx` — Foreign exchange rates & revaluation
- **What:** multi-currency rates, policy, and period-end revaluation. `category=core`, no deps.
- **Does:** 28 routes over `oe_fx_rate_set`, `oe_fx_rate_quote`, `oe_fx_policy`, `oe_fx_rate`, `oe_ppp_factor` — rate sets/quotes, conversion policy, and PPP factors.
- **How:** `parse_ecb_xml` ingests ECB reference rates; `cross_rate`/`convert_via_base` convert through a base currency; `decompose_movement` + `as_revaluation_lines` generate the FX gain/loss journal lines for `gl`.
- **Interactions:** `gl` (revaluation postings); any module handling non-base-currency amounts. **Failure:** disabled → single-currency only.

## 9. `payroll` — Payroll batches
- **What:** payroll runs built from field time/resources. `category=core`, `depends=[projects, fieldreports, resources]`.
- **Does:** 24 routes over `oe_payroll_batch`, `oe_payroll_entry`, `oe_payroll_deduction` — assemble a pay batch, per-person entries and deductions.
- **How:** entries draw hours from `fieldreports`/`resources` against `projects`; deductions net per entry; batches post to `gl`.
- **Interactions:** upstream of `certified_payroll`; `fieldreports`/`resources` (hours). **Failure:** disabled → no payroll runs.

## 10. `certified_payroll` — Prevailing-wage certified payroll (compliance)
- **What:** US-style certified payroll (Davis-Bacon determinations, weekly WH-347-type reporting). `category=compliance`, `depends=[payroll, projects, resources]`.
- **Does:** 38 routes over `oe_certpay_determination`, `_classification`, `_assignment`, `_week`, `_line` — wage determinations, worker classifications, assignments, and weekly certified lines.
- **How:** classifications map workers to determination rates; weekly lines derive from `payroll` entries and are checked against the determination; produces the certified-payroll report.
- **Interactions:** builds on `payroll`; `projects`/`resources`. Compliance add-on. **Failure:** disabled → no prevailing-wage reporting.

## 11. `labor_rates` — Fully-burdened labour rate build-up
- **What:** compute all-in crew/labour rates from base pay + on-costs. `category=core`, `depends=[users]`.
- **Does:** 20 routes over `oe_labor_rates_template`, `_oncost`, `_crew_member` — rate templates, on-cost components, crew composition.
- **How:** `compute_breakdown` applies on-costs (tax, insurance, overhead) to base rates; `describe_template_references` reports what a template feeds.
- **Interactions:** feeds estimating/cost modules (Batch 4) and `payroll`. **Failure:** disabled → manual rates only.

## 12. `allowances` — Contract allowances & drawdowns
- **What:** track contractual allowance pots and draw against them. `category=core`, `depends=[projects, users]`.
- **Does:** 18 routes over `oe_allowances_allowance`, `oe_allowances_drawdown` — define an allowance, record drawdowns, show remaining.
- **How:** drawdowns decrement the allowance; balance = allowance − Σ drawdowns.
- **Interactions:** `projects`; relates to change orders/variations (Batch 5). **Failure:** disabled → no allowance tracking.

## 13. `funding` — Grants/funding programmes & proof-of-use
- **What:** manage funding programmes, applications, disbursements and obligations (grant compliance). `category=business`, `depends=[users, projects]`.
- **Does:** 60 routes over `oe_funding_programme`, `_application`, `_disbursement`, `_proof_of_use`, `_obligation`, `_cost_allocation` — programme setup, applications, disbursement tracking, proof-of-use, obligations and cost allocation.
- **How:** `render_detail` composes a programme view; obligations carry title keys (i18n) and `server_authored_fields_replaced` guards server-owned fields; cost allocation ties project costs to a funding source.
- **Interactions:** `projects` (allocated costs), `finance`. **Failure:** disabled → no grant tracking.

## 14. `cost_recovery` — Back-charges & apportionment (controls)
- **What:** recover costs from a responsible party and apportion shared costs. `category=controls`, `depends=[users, projects]`.
- **Does:** 18 routes over `oe_cost_recovery_back_charge`, `_apportionment` — raise back-charges, apportion, and report recovery performance.
- **How:** `create/update/list/get_back_charge`; `apportion_back_charge` + `build_recovery_ledger`; `build_recovery_performance` / `build_portfolio_recovery_performance` roll up; `pending_backcharges` lists open items.
- **Interactions:** `projects`; relates to `subcontractors`/`contracts`. **Failure:** disabled → no back-charge recovery.

## 15. `postcalc` — Post-calculation (actual vs estimate feedback)
- **What:** close the loop from actuals back to estimating norms. `category=business`, `depends=[boq, costmodel]`. (No tables of its own — it computes over `boq`/`costmodel` data.)
- **Does:** 8 routes — project post-calculation and productivity feedback.
- **How:** `consumed_material_cost`, `compute_line_productivity`, `aggregate_resources`, `build_feedback_factors`, `compute_project_postcalc` compare actual consumption/productivity against the estimate and emit feedback factors to tune future norms.
- **Interactions:** reads `boq`/`costmodel` (Batch 4); feeds estimating. **Failure:** disabled → no actual-vs-estimate feedback.

---

### Batch-1 interaction map (within finance)
```
sales/finance ──invoices──▶ receivables (dunning)
   │                           ▲
   └──postings──▶ gl ◀──reconcile── banking ◀──camt/MT940/CSV
                    ▲    ▲
          revaluation │   │ withholding
                 fx ──┘   └── tax_withholding ──nets──▶ finance payments
labor_rates ──rates──▶ payroll ──▶ certified_payroll
funding / allowances / cost_recovery ──▶ projects (cost allocation)
boq/costmodel ──actuals──▶ postcalc ──feedback──▶ estimating
payment_clock ──statutory clocks──▶ contracts/subcontractors
```
All post into **`gl`**, which is the always-on sink. Each add-on fails closed/independently: disabling one removes its surface and its routes 402/404, but `gl` and the invoice→post path keep working.

---

*Batch 1 of 7. Next in order: **Sales & Commercial** (sales, crm, contacts, items, inventory, contracts, quotes/invoicing, subcontractors). Grounded in `kingdonk-ops/openbusinesserp` @ `a96d525`.*
# OpenBusinessERP — Per-Module Docs · Batch 2: Sales & Commercial

> Batch 2 of 7. Four Pillars per module. Grounded in `backend/app/modules/<name>/`. Tables `oe_*`; routes at `/api/v1/<prefix>`; deps = manifest `depends`.

**Modules (10):** `sales`, `crm`, `contacts`, `items`, `inventory`, `site_inventory`, `catalog`, `supplier_catalogs`, `contracts`, `subcontractors`.

---

## 1. `sales` — Order-to-cash
- **What:** the sell side — quote → sales order → invoice → payment. `category=core`, `depends=[gl]`.
- **Does:** 68 routes over `oe_sales_document`, `oe_sales_line`, `oe_sales_payment`, `oe_sales_sequence`, `oe_sales_account_map`, `oe_sales_send_log`, `oe_sales_payment_link` — a single document that transitions quote→order→invoice, line items, payments, numbering sequences, GL account mapping, send log (emailed PDFs), and payment links (SEPA/QR).
- **How:** one polymorphic `oe_sales_document` carries the state machine; `oe_sales_sequence` issues human numbers; posting an invoice writes the journal into `gl` via `oe_sales_account_map` (line→account), split by tax rate; `oe_sales_send_log` records deliveries; `oe_sales_payment_link` mints pay-by-link references.
- **Interactions:** posts to `gl`; feeds `receivables` (open items) and `banking` (reconciliation targets); consumes `items` (products) and `contacts` (customers). **Failure:** the commercial spine — effectively always-on `core`.

## 2. `crm` — Sales pipeline
- **What:** leads, opportunities, accounts and forecasting. `category=business`, `depends=[users]`.
- **Does:** 94 routes over `oe_crm_account`, `oe_crm_lead`, `oe_crm_opportunity`(+`_stage_history`), `oe_crm_pipeline_stage`(+`_config`), `oe_crm_win_loss_reason`, `oe_crm_activity`, `oe_crm_forecast` — configurable pipeline stages, lead→opportunity conversion, stage history, win/loss reasons, activities, and forecast.
- **How:** opportunities move through configurable stages with a recorded `_stage_history`; `_forecast` aggregates weighted pipeline; win/loss reasons close the loop.
- **Interactions:** converts to `sales` documents; `contacts`/`accounts`. **Failure:** disabled → no pipeline; `sales` still works for direct quoting.

## 3. `contacts` — Party master
- **What:** the shared people/company directory (customers, suppliers, others). `category=core`, `depends=[users]`.
- **Does:** 30 routes over `oe_contacts_contact` — CRUD, search, and party classification. `Contact.tenant_id` backfills to `created_by` (the single-tenant convention — see architecture doc §3).
- **How:** one contact table serves every party role; other modules reference contacts by id.
- **Interactions:** consumed by `sales`, `crm`, `finance`, `subcontractors`, purchasing (Batch 3). **Failure:** core — parties are foundational.

## 4. `items` — Product/service master, price lists, discounts
- **What:** the catalogue of what you sell/buy and how it's priced. `category=core`, no deps.
- **Does:** 54 routes over `oe_items_category`, `oe_items_item`, `oe_items_price_list`(+`_item`), `oe_items_discount_rule` — items & categories, multiple price lists, per-item prices, and discount rules.
- **How:** price resolution walks the applicable price list then discount rules; items can be goods or services.
- **Interactions:** priced into `sales` lines, stocked by `inventory`, bought via purchasing. **Failure:** core — sales/inventory depend on it.

## 5. `inventory` — Warehouses, movements, moving-average cost, stocktakes
- **What:** stock control with valuation. `category=core`, `depends=[items]`.
- **Does:** 36 routes over `oe_inventory_warehouse`, `oe_inventory_movement`, `oe_inventory_item_cost`, `oe_inventory_stocktake`(+`_line`), `oe_inventory_settings` — warehouses, in/out movements, moving-average cost per item, and stocktakes.
- **How:** each movement adjusts on-hand qty and recomputes **moving-average cost** (`oe_inventory_item_cost`); stocktakes reconcile counted vs system and post the variance; a delivery out of stock is the movement that feeds a sales dispatch.
- **Interactions:** `items` (what's stocked); `sales` (dispatch) and purchasing (receipt). **Failure:** disabled → no stock tracking/valuation.

## 6. `site_inventory` — On-site material metering
- **What:** track materials held and consumed **on a project site** (distinct from warehouse stock). `category=business`, `depends=[projects, boq, procurement, progress, costs]`.
- **Does:** 28 routes over `oe_site_inventory_location`, `_item`, `_movement` — site locations, on-site item balances, and deliveries/consumption movements.
- **How:** movements meter material against `boq`/`progress`/`costs` so consumed quantity ties to measured progress and cost.
- **Interactions:** `procurement` (deliveries in), `boq`/`progress`/`costs` (consumption). **Failure:** disabled → no on-site metering; warehouse `inventory` unaffected.

## 7. `catalog` — Resource catalog (data layer)
- **What:** a resource catalogue table — **data layer only** (the loaders/UI were removed; the docstring says so). `category=core`, `depends=[costs]`.
- **Does:** `oe_catalog_resource` only; no router surface of its own.
- **How:** holds resource reference data other modules (costing/estimating) read.
- **Interactions:** read by `costs`/estimating. **Failure:** inert if unused.

## 8. `supplier_catalogs` — Vendor management & procure-to-pay (extension)
- **What:** a full vendor/P2P suite — the richest commercial add-on. `category=extension`, `depends=[projects, users, procurement]`.
- **Does:** 96 routes over 21 tables: vendors (`_vendor`, `_kyc_document`, `_scorecard`, `_tolerance_profile`), catalogues (`_item_category`, `_catalog_item`, `_price_list`, `_catalog_entry`, `_commodity_code`), and a complete P2P chain — **PR → PO → GR → Invoice** (`_pr`/`_pr_line`, `_po`/`_po_line`, `_gr`/`_gr_line`, `_invoice`/`_invoice_line`) with **3-way match** (`_match_record`) and vendor stock (`_warehouse`, `_stock_balance`, `_stock_movement`).
- **How:** requisition→order→goods-receipt→invoice with tolerance-profiled 3-way matching; KYC documents and scorecards gate/rank vendors.
- **Interactions:** `procurement` (Batch 3), `inventory`, `finance`. Extension add-on. **Failure:** disabled → vendor management + 3-way match unavailable; simpler `procurement` still works.

## 9. `contracts` — Contract Types Engine (largest commercial module)
- **What:** head/sub contracts across contract types (lump-sum, cost-plus, target/gainshare, etc.) with the full commercial machinery. `category=business`, `depends=[users, projects]`.
- **Does:** **260 routes** over 21 tables incl. `oe_contracts_contract`(+`_line`), `_type_configuration`, `_retention_schedule`/`_retention_release`, `_fee_structure`, `_gainshare_configuration`, `_ld_clause`, `_progress_claim`(+`_line`), `_final_account`, `_party`, `_security`, `_eot_claim`, `_milestone`, `_template`(+`_clause`), `_sov_adjustment`, `_stored_material`(+`_movement`) — contract setup by type, schedule-of-values, progress claims, retention, liquidated-damages clauses, extensions-of-time, securities/bonds, stored materials, and final account.
- **How:** a **type configuration** parametrises behaviour (fees, gainshare, retention, LDs) per contract type; progress claims draw against the SOV with retention schedules; EOT claims and LD clauses manage time/penalty; templates assemble clause sets.
- **Interactions:** `projects`; feeds `payment_clock`, `subcontractors`, `cost_recovery`, finance. **Failure:** disabled → no formal contract administration.

## 10. `subcontractors` — Subcontractor management
- **What:** manage subcontractors end-to-end: prequal, agreements, work packages, payments, retention, compliance. `category=business`, `depends=[users, projects, contacts, contracts]`.
- **Does:** 126 routes over `oe_subcontractors_subcontractor`(+`_contact`), `_prequalification`, `_certificate`, `_agreement`, `_work_package`, `_payment_application`(+`_line`), `_retention_ledger`, `_rating`, `_lien_waiver` — subbie register, prequalification, certificates (insurance/licences), agreements, work packages, payment applications with retention, lien waivers and performance ratings.
- **How:** payment applications draw on work packages/agreements, net retention into `_retention_ledger`, and require current certificates + lien waivers before release; ratings accrue performance history.
- **Interactions:** `contracts` (flow-down), `contacts` (party), `projects`, payment/finance. **Failure:** disabled → no subcontractor administration; `contracts` still handles the head contract.

---

### Batch-2 interaction map
```
crm ──convert──▶ sales ──post──▶ gl ; sales ──open items──▶ receivables/banking
items ──price──▶ sales lines ; items ──stocked──▶ inventory ──site──▶ site_inventory
contacts ──parties──▶ sales/crm/finance/subcontractors/purchasing
contracts ──flow-down──▶ subcontractors ; contracts ──▶ payment_clock / cost_recovery
supplier_catalogs (PR→PO→GR→Invoice, 3-way) ──▶ procurement/inventory/finance
```

*Batch 2 of 7. Next: **Purchasing & Supply** (purchasing, procurement, rfq_bidding, bid_management, tendering). Grounded @ `a96d525`.*
# OpenBusinessERP — Per-Module Docs · Batch 3: Purchasing & Supply

> Batch 3 of 7. Four Pillars per module. Grounded in `backend/app/modules/<name>/`.

**Modules (5):** `purchasing`, `procurement`, `rfq_bidding`, `bid_management`, `tendering`.

> Note: the platform has **two purchasing lineages** — a general-business **procure-to-pay** (`purchasing`, GL-posting, PO→GR→bill→payment) and a **construction procurement** chain (`procurement`, project/requisition-oriented) plus three **sourcing/competitive-bidding** modules (`rfq_bidding`, `bid_management`, `tendering`). `supplier_catalogs` (Batch 2) is the vendor-management/P2P superset.

---

## 1. `purchasing` — Business purchase-to-pay
- **What:** general-business buying: PO → goods receipt → supplier bill → payment, posting to GL. `category=core`, `depends=[gl]`.
- **Does:** 70 routes over `oe_purchasing_order`(+`_line`), `_receipt`(+`_line`), `_bill`(+`_line`), `_payment`, `_stock`, `_sequence`, `_account_map` — the full P2P document chain, numbering, GL mapping, and a stock view.
- **How:** mirrors `sales` in reverse — a PO is received (GR), matched to a supplier bill, then paid; posting writes journals into `gl` via `_account_map`; `_sequence` issues numbers.
- **Interactions:** posts to `gl`; feeds `banking` (bills to reconcile) and `inventory`/`_stock`. **Failure:** core — the buy-side spine.

## 2. `procurement` — Construction procurement (project-oriented)
- **What:** project requisitions, purchase orders and goods receipts with retainage. `category=core`, `depends=[users, projects, contacts]`.
- **Does:** 42 routes over `oe_procurement_requisition`(+`_req_item`), `_po`(+`_po_item`, `_po_retainage_release`), `_goods_receipt`(+`_gr_item`) — requisition→PO→GR with retainage release.
- **How:** a requisition becomes a PO against a project; goods receipts draw down PO items; retainage is held and released via `_po_retainage_release`.
- **Interactions:** `projects`/`contacts`; upstream of `site_inventory`, `supplier_catalogs`, `rfq_bidding`. **Failure:** disabled → no project procurement; `purchasing` still covers business P2P.

## 3. `rfq_bidding` — Request-for-quote & award (enterprise)
- **What:** solicit supplier quotes for procurement lines and award. `category=enterprise`, `depends=[users, projects, contacts, procurement]`.
- **Does:** 44 routes over `oe_rfq_rfq`(+`_line`), `_bid`(+`_bid_line`, `_bid_adjustment`), `_award` — issue an RFQ, collect bids, normalise with adjustments, award.
- **How:** bid lines are compared against RFQ lines; `_bid_adjustment` normalises (freight, terms) for apples-to-apples; `_award` records the winner and can feed a PO in `procurement`.
- **Interactions:** builds on `procurement`; `contacts` (bidders). Enterprise add-on. **Failure:** disabled → no competitive RFQ; direct POs still possible.

## 4. `bid_management` — Trade-package bid management (sell-side/estimating)
- **What:** manage bid packages, invitations, submissions, Q&A, leveling and award — the estimator's sourcing of trade bids. `category=business`, `depends=[users, projects]`.
- **Does:** 126 routes over `oe_bid_management_package`, `_line_item`, `_invitation`, `_bidder`, `_submission`(+`_line`), `_qa`, `_comparison`, `_leveling`, `_award`, `_rejection` — packages and line items, invite bidders, receive submissions, run Q&A, compare and **level** bids, award or reject.
- **How:** submissions are compared line-by-line; **leveling** normalises scope differences before comparison; award/rejection close the package.
- **Interactions:** `projects`; relates to estimating (Batch 4) and `tendering`. **Failure:** disabled → no structured trade-bid management.

## 5. `tendering` — Tender package management & comparison
- **What:** BOQ-based tender packages, invitations and bid comparison. `category=core`, `depends=[projects, boq]`.
- **Does:** 76 routes over `oe_tendering_package`, `_bid`, `_bid_invitation` — assemble a tender package from a BOQ, invite bidders, and compare returned bids.
- **How:** packages derive line items from the `boq`; returned bids price those lines for side-by-side comparison.
- **Interactions:** `boq` (Batch 4) as the source of scope; `projects`. **Failure:** disabled → no BOQ-driven tendering.

---

### Batch-3 note
`purchasing` (business P2P, posts to `gl`) and `procurement` (project requisitions/retainage) are parallel buy-side chains; `supplier_catalogs` is the vendor-management superset with 3-way match. Sourcing is split by audience: `rfq_bidding` (supplier quotes on procurement lines), `bid_management` (trade-package bids for estimating), `tendering` (BOQ-based tender comparison). Each is independently gated; disabling one leaves the others and the GL untouched.

*Batch 3 of 7. Next: **Estimating & Cost** (boq, takeoff, costs, costmodel, cost_plan, estimate_*, assemblies, eac/full_evm/cvr). Grounded @ `a96d525`.*
# OpenBusinessERP — Per-Module Docs · Batch 4: Estimating & Cost

> Batch 4 of 7 — the largest domain (construction estimating, cost databases, 5D cost modelling, earned value). Four Pillars per module (compact where the module is a library/thin). Grounded in `backend/app/modules/<name>/`. `category` / `depends` / route counts are from the manifests/routers.

**Modules (26):** `boq`, `takeoff`, `dwg_takeoff`, `rebar_schedule`, `estimate_basis`, `estimate_rollup`, `rom_estimate`, `ai_estimator`, `costs`, `costmodel`, `cost_plan`, `cost_match`, `price_breakdown`, `price_index`, `norm_expansion`, `waste_factors`, `assemblies`, `prefab`, `measurement`, `value`, `eac`, `full_evm`, `cvr`, `project_controls`, `progress`, `resource_summary`, `resources`.

The pipeline: **takeoff → BOQ → assemblies/norms/costs (unit rates) → cost model (5D) → estimate basis/rollup → tender** (Batch 3), then on delivery **progress → EAC/EVM/CVR → postcalc** (Batch 1) feeds norms back.

---

### Quantities & takeoff
- **`boq`** — *core, depends[projects], 220 routes.* **What:** the Bill of Quantities — the central measured-scope spine. **Does:** BOQ structures, sections, items, quantities, rates, totals. **How:** the item tree carries measured quantities and unit rates; most estimating/tendering/progress modules read or roll up the BOQ. **Interactions:** source for `tendering`, `costmodel`, `progress`, `estimate_*`, `postcalc`. Core.
- **`takeoff`** — *extension, depends[projects, cad], 106 routes.* **What/Does:** PDF upload + quantity extraction (measure areas/lengths/counts off drawings into quantities). **How:** works with `cad`; produces measured quantities that feed the BOQ. **Failure:** disabled → no PDF takeoff; manual BOQ entry remains.
- **`dwg_takeoff`** — *extension, depends[projects], 52 routes.* **What/Does:** takeoff from DWG/CAD vector drawings. **How:** reads DWG geometry to quantities. Peer of `takeoff` for native CAD.
- **`rebar_schedule`** — *business, depends[users, projects], 30 routes.* **What/Does:** reinforcement bar scheduling/bending schedules → steel quantities and weights.
- **`measurement`** — *library (no router).* Quantity-determination helpers shared by takeoff/BOQ.
- **`waste_factors`** — *core, 14 routes.* **What/Does:** net-to-gross quantity adjustment (apply waste % to measured net quantities) — a library + config surface used across estimating.

### Cost base & unit rates
- **`costs`** — *core, no deps, 123 routes.* **What:** the **Cost Database** — the reference library of resource/material/labour/plant costs. **Does:** cost records, hierarchies, rate libraries. **How:** the unit-cost source assemblies and cost models price against. Core, widely depended on.
- **`assemblies`** — *core, depends[costs, fx], 42 routes.* **What/Does:** composite unit rates built from cost-database resources (labour+material+plant → a unit rate), FX-aware. **How:** an assembly sums its component resources × quantities × rates into a unit price.
- **`norm_expansion`** — *core, 20 routes.* **What/Does:** expand a **production norm** (output per crew-hour) into resource quantities for a work item — the no-code "norms" engine the product markets.
- **`price_breakdown`** — *library (no router).* Structured unit-price analysis (break a rate into its components).
- **`price_index`** — *core, depends[costs], 40 routes.* **What/Does:** base-to-current cost-index adjustment (escalate historic rates to today). **How:** applies index series to cost-database rates.
- **`cost_match`** — *core, depends[users, projects, costs, dashboards, ai], 26 routes.* **What/Does:** match a foreign/supplier bill onto the internal cost base (AI-assisted line mapping). **How:** maps external line text → cost-base items.

### Cost modelling & estimate assembly
- **`costmodel`** — *core, depends[projects, boq], 66 routes.* **What:** the **5D cost model** — cost dimension layered on the BOQ (and schedule). **Does:** model costs across quantity/time, scenario costing. **How:** ties BOQ quantities × rates across time for 5D (3D+time+cost).
- **`cost_plan`** — *business, depends[boq, projects], 4 routes.* **What/Does:** elemental cost plan (early-stage cost plan by element).
- **`rom_estimate`** — *core, depends[projects, costmodel], 14 routes.* **What/Does:** conceptual / Rough-Order-of-Magnitude estimates for early stages.
- **`ai_estimator`** — *core, no deps, 66 routes.* **What/Does:** AI Estimate Builder — draft an estimate from description/scope with AI assistance (human-confirmed).
- **`estimate_basis`** — *business, depends[boq, projects, allowances, preliminaries, costs], 12 routes.* **What/Does:** the **Basis of Estimate** — assumptions, inclusions/exclusions, qualifications behind the number.
- **`estimate_rollup`** — *business, depends[boq, projects, preliminaries, allowances], 2 routes.* **What/Does:** roll the BOQ + preliminaries + allowances into the headline estimate total.
- **`prefab`** — *business, depends[users, projects], 30 routes.* **What/Does:** off-site / Prefab / DfMA planning (modular/offsite scope and its cost/logistics).

### Resources
- **`resources`** — *business, depends[users, projects], 108 routes.* **What/Does:** graphical resource planning — crews/plant/labour allocation over time. **How:** schedules resources against activities; feeds payroll/field time.
- **`resource_summary`** — *core, depends[boq], 10 routes.* **What/Does:** summarise resource demand rolled up from the BOQ.

### Delivery: earned value & reconciliation
- **`progress`** — *core, depends[projects, boq], 18 routes.* **What/Does:** site-execution measurement (% complete against BOQ). **How:** progress claimed against measured BOQ items → earned quantity. Feeds EVM/CVR/site_inventory.
- **`eac`** — *core, depends[users, projects], 58 routes.* **What:** **Estimate At Completion** platform (EAC v2). **Does:** forecast final cost from actuals + remaining. **How:** combines committed/actual cost with remaining work to project EAC.
- **`full_evm`** — *enterprise, depends[finance, eac, projects], 40 routes.* **What/Does:** full Earned Value Management (PV/EV/AC, CPI/SPI, variances). **How:** builds on `eac` + `finance` actuals.
- **`cvr`** — *business, depends[users, projects], 54 routes.* **What/Does:** Cost-Value Reconciliation & cashflow (the UK commercial-reporting view: value vs cost vs cash).
- **`value`** — *controls, library-ish, 16 routes.* **What/Does:** "Value Realized" — track realised value/benefits (controls/governance view).
- **`project_controls`** — *extension, depends[projects, users, bi_dashboards], 4 routes.* **What/Does:** executive cross-module controls dashboard aggregating cost/schedule/risk.

---

### Batch-4 pipeline map
```
takeoff / dwg_takeoff / rebar_schedule / measurement ──quantities──▶ boq
boq ──×rates── costs → assemblies / norm_expansion / price_breakdown / price_index / waste_factors
   │                                   │
   └──▶ costmodel (5D) ──▶ estimate_basis + estimate_rollup (+ preliminaries, allowances) ──▶ tender/bid
rom_estimate / ai_estimator / cost_plan ──early estimates──▶ (feed the above)
resources / resource_summary ──allocate──▶ payroll / field time
DELIVERY:  progress ──earned──▶ eac ──▶ full_evm ; cvr (cost/value/cash) ; value ; project_controls (exec rollup)
cost_match ──foreign bill → cost base ; postcalc (Batch 1) ──actuals──▶ norms
```
`boq` and `costs` are the always-on cores everything else reads; each estimating/EVM add-on gates independently (disable → its routes 402, the BOQ/cost base keep working).

*Batch 4 of 7. Next: **Project Delivery & Field** (schedule, daily_diary, inspections, ncr, safety, punchlist, commissioning, closeout, defects_liability, hse_advanced, temporary_works, formwork, equipment, assets, fieldreports, field_time, field_diary, site_*). Grounded @ `a96d525`.*
# OpenBusinessERP — Per-Module Docs · Batch 5: Project Delivery & Field

> Batch 5 of 7. Four Pillars per module (compact). Grounded in `backend/app/modules/<name>/`. `category` / `depends` / route counts from manifests & routers.

**Modules (25):** `schedule`, `schedule_advanced`, `timeline`, `deadlines`, `recurring`, `daily_diary`, `field_diary`, `field_time`, `fieldreports`, `site_prep`, `site_logistics`, `site_supervision`, `construction_control`, `commissioning`, `closeout`, `defects_liability`, `punchlist`, `ncr`, `inspections`, `safety`, `hse_advanced`, `temporary_works`, `formwork`, `equipment`, `assets`, `methodology`.

---

### Scheduling & time
- **`schedule`** — *core, depends[projects], 110 routes.* **What:** the 4D schedule (activities, WBS, dependencies, dates linked to 3D). **Does:** activities, logic links, baselines, critical path. **How:** CPM scheduling tied to BOQ/model for 4D; the backbone for progress/EVM. Core.
- **`schedule_advanced`** — *business, depends[users, projects], 208 routes.* **What/Does:** **Last Planner System** (lookahead, weekly work plans, constraints, PPC, make-ready). **How:** collaborative pull-planning layered over `schedule`.
- **`timeline`** — *core, 4 routes.* Lightweight project timeline view.
- **`deadlines`** — *core, depends[notifications], 4 routes.* Deadline register that raises notifications.
- **`recurring`** — *core, depends[sales], 22 routes.* **What/Does:** recurring-invoice templates that materialise into ordinary `sales` invoices each period. **How:** a template + schedule emits a sales invoice per cycle. (Finance-adjacent but delivery-cadence in nature.)

### Field capture
- **`fieldreports`** — *core, depends[projects], 58 routes.* **What/Does:** field reports (daily logs, labour/plant/weather) — the hours source for payroll/EVM.
- **`daily_diary`** — *business, depends[users, projects], 108 routes.* **What/Does:** the full Daily Site Diary (weather, labour, plant, events, photos, sign-off).
- **`field_diary`** — *core, depends[projects, users], 44 routes.* **What/Does:** the field-worker Daily Diary MVP (the mobile/low-trust persona surface, Epic F) — lighter sibling of `daily_diary`.
- **`field_time`** — *business, 38 routes.* **What/Does:** field time capture (clock-in/out, timesheets) feeding payroll.
- **`site_prep`** — *business, depends[projects], 20 routes.* Pre-construction mobilisation / site-setup readiness checklist.
- **`site_logistics`** — *business, depends[projects, users, boq], 48 routes.* Site logistics & delivery scheduling (gate bookings, lay-down, crane/hoist windows).
- **`site_supervision`** — *core, depends[users, projects], 38 routes.* Design-side site supervision (site visits, instructions, observations).

### Quality, safety & control
- **`construction_control`** — *core, depends[users, projects, ncr], 100 routes.* **What/Does:** construction control — site quality/conformance control plans tied to NCRs.
- **`inspections`** — *core, depends[users, projects], 18 routes.* Inspection records (QA/QC checks). (Namesake of inspection-erp's core, but a lighter module here.)
- **`ncr`** — *core, depends[users, projects], 14 routes.* Non-Conformance Reports (raise → disposition → close).
- **`punchlist`** — *core, depends[projects], 30 routes.* Punch/snag list items to closeout.
- **`safety`** — *core, depends[users, projects], 34 routes.* Core safety (observations, toolbox talks, incidents).
- **`hse_advanced`** — *business, depends[users, projects], 162 routes.* **What/Does:** the full HSE suite — incidents, investigations, permits-to-work, audits, actions — "sister to `safety`" with the enterprise depth.
- **`temporary_works`** — *business, depends[projects], 20 routes.* Safety-critical temporary-works governance register (TWC sign-off, design checks).
- **`defects_liability`** — *business, depends[projects], 22 routes.* Post-handover warranty / Defects-Liability-Period register.

### Handover & commissioning
- **`commissioning`** — *business, depends[projects, users], 46 routes.* Commissioning (Cx) — systems, checksheets, witness tests.
- **`closeout`** — *core, depends[users, projects, documents], 26 routes.* Handover & closeout pack assembly (O&M, as-builts, certificates).

### Formwork, equipment, assets, methodology
- **`formwork`** — *estimation, depends[projects, boq], 50 routes.* **What/Does:** temporary-mould catalogue, priced assignments and pour cycles (formwork planning + cost).
- **`equipment`** — *business, depends[users, projects], 96 routes.* Equipment & fleet management (register, allocation, utilisation, maintenance).
- **`assets`** — *core, depends[users, projects], 10 routes.* Asset operations register (owned assets lifecycle).
- **`methodology`** — *core, depends[projects, boq], 42 routes.* Estimating/construction methodology statements tied to BOQ scope.

---

### Batch-5 map
```
schedule (4D) ◀──lastplanner── schedule_advanced ; timeline/deadlines (views/alerts)
fieldreports / field_time / daily_diary / field_diary ──hours & events──▶ payroll, EVM, progress
site_prep → site_logistics → site_supervision ──delivery/ops──▶ projects
construction_control ──ties──▶ ncr ; punchlist / inspections / commissioning / closeout → handover
safety ◀──sister── hse_advanced ; temporary_works (governance) ; defects_liability (post-handover)
formwork / equipment / assets / methodology ──plant, moulds, methods──▶ cost & delivery
recurring ──period──▶ sales invoices
```
`schedule` is the delivery backbone; the quality/safety/handover modules are mostly independent registers that gate individually (disable → routes 402). Field-capture modules feed payroll (Batch 1) and EVM/progress (Batch 4).

*Batch 5 of 7. Next: **Documents & Collaboration** (documents + file_* subsystem, rfi, submittals, transmittals, correspondence, meetings, markups, plan_room, collaboration, approval_routes, bim_hub, cad, dwg_takeoff viewer). Grounded @ `a96d525`.*
# OpenBusinessERP — Per-Module Docs · Batch 6: Documents & Collaboration

> Batch 6 of 7. Four Pillars per module (compact). Grounded in `backend/app/modules/<name>/`.

**Modules (24):** `documents` + the `file_*` subsystem (11), `rfi`, `submittals`, `transmittals`, `correspondence`, `meetings`, `markups`, `plan_room`, `collaboration`, `collaboration_locks`, `approval_routes`, `bim_hub`, `cad`.

---

### Document management core
- **`documents`** — *core, depends[projects], 84 routes.* **What:** the Document Management core / CDE (the `/files` surface). **Does:** folders, files, metadata, download. **How:** files go through the core storage service (never raw paths); the `file_*` modules below each add one capability on top. Core — the hub the whole file subsystem orbits.

### The `file_*` subsystem (each adds one capability to `/files`)
All `category=core`, depend on `[projects, users]` (and `documents` where noted):
- **`file_versions`** (8 r) — version history per file.
- **`file_approvals`** (18 r) — approval workflows + stamps (Wave W8).
- **`file_comments`** (12 r) — per-file comment threads.
- **`file_tags`** (16 r) — taggable files (W4).
- **`file_search`** (8 r, depends documents) — full-text search over files (W3).
- **`file_saved_views`** (12 r) — Smart Folders / saved filters for `/files`.
- **`file_favorites`** (6 r) — pins/favourites.
- **`file_references`** (16 r) — link files to other records.
- **`file_distribution`** (20 r, depends documents) — cross-project search + distribution lists.
- **`file_transmittals`** (18 r) — formal file-issue transmittals (W7).
- **`file_trash`** (12 r) — recycle bin / soft-delete + restore.

> **How, collectively:** each is a thin, independently-gated capability so the document core stays small and features can be enabled per deployment; they share the `documents` file ids. **Failure:** disabling one removes just that capability (e.g. no versions, or no trash), leaving the rest of `/files` working.

### Controlled-document workflows (construction correspondence)
- **`rfi`** — *core, 36 routes.* Requests for Information (raise → respond → close, with ball-in-court).
- **`submittals`** — *core, 30 routes.* Submittal register (shop drawings/material approvals, review cycles).
- **`transmittals`** — *core, depends[contacts], 20 routes.* Document-issue tracking with recipients (who got what, when).
- **`correspondence`** — *core, depends[contacts], 14 routes.* General project correspondence log.
- **`meetings`** — *core, 58 routes.* Meetings & minutes (agenda, attendees, action items).
- **`markups`** — *core, depends[projects], 38 routes.* Markups & annotations on drawings/documents.

### Collaboration & approvals
- **`collaboration`** — *core, depends[users], 14 routes.* Threaded comments + viewpoints attachable to **any** entity (generic, entity-URI based).
- **`collaboration_locks`** — *core, depends[users], 12 routes.* Edit locks — "layer 1 of the real-time collaboration plan" (prevent concurrent-edit clobber).
- **`approval_routes`** — *core, depends[projects, users], 40 routes.* Configurable multi-step approval routes reusable across modules.
- **`plan_room`** — *business, depends[projects, users, documents], 6 routes.* Plan room — a controlled set of current drawings for distribution/viewing.

### BIM & CAD
- **`bim_hub`** — *core, depends[users, projects, boq].* BIM model hub (host/view models, link elements to BOQ for 4D/5D). Model/viewer-heavy; thin REST surface.
- **`cad`** — *extension, depends[projects].* CAD/DWG handling that backs `takeoff`/`dwg_takeoff` and the CAD explorer. Underpins drawing-based takeoff (Batch 4).

---

### Batch-6 map
```
documents (/files, CDE)
  └─ file_versions · file_approvals(+stamps) · file_comments · file_tags · file_search
     file_saved_views · file_favorites · file_references · file_distribution
     file_transmittals · file_trash        (each = one capability, independently gated)
rfi / submittals / transmittals / correspondence / meetings / markups  ──controlled docs──▶ projects
collaboration (any entity) · collaboration_locks (edit locks) · approval_routes (reusable approvals)
bim_hub ──elements──▶ boq (4D/5D) ; cad ──drawings──▶ takeoff / dwg_takeoff / plan_room / markups
```
`documents` is the file hub; the `file_*` capabilities and the controlled-document registers gate independently. `approval_routes` and `collaboration` are cross-cutting services other modules reuse.

*Batch 6 of 7. Next (final): **Country packs, AI & Platform/Integration** — regional packs (uk/dach/latam/asia_pac/china/india/mexico/middle_east/russia/sa/us/us_ca/us_tx), AI (ai, ai_agents, erp_chat, change_intelligence, project_intelligence, retrieval, smart_views, saved_views, search), and platform/integration (connectors, integrations, webhook_leads, inbound_email, inbound_capture, phonelog, notifications, onboarding, backup, credentials, data_import, pipelines, client_errors, portal, voice, enterprise_workflows, interface_management, project_route, module_builder, admin, i18n_foundation, einvoice, qms, carbon/esg, compliance*, bi_dashboards/dashboards, cases, correspondence, risk, etc.). Grounded @ `a96d525`.*
# OpenBusinessERP — Per-Module Docs · Batch 7 (final): Platform, AI, Compliance & Regional

> Batch 7 of 7 — the remaining modules, completing coverage of **all 191** `app/modules/*`. Compact Four-Pillars entries grouped by theme. Grounded in `backend/app/modules/<name>/` (`category` + route counts from manifests/routers; purpose from each module docstring).

---

## A. AI & intelligence
- **`ai`** *(core, 28 r)* — AI Estimation service: the shared LLM-estimation surface.
- **`ai_agents`** *(core, 56 r)* — AI Agents framework (multi-step agent orchestration over ERP data).
- **`erp_chat`** *(core, 16 r)* — the in-app assistant (the floating chat dock / `/chat`) that answers over the ERP.
- **`retrieval`** *(core, 12 r)* — retrieval/RAG layer feeding chat & agents (pairs with `core/vector`).
- **`search`** *(core, 6 r)* — unified cross-collection **semantic** search.
- **`smart_views`** *(core, 22 r)* — rule-based, re-evaluating BIM viewer presets.
- **`saved_views`** *(core, 24 r)* — saved filters/views across modules.
- **`match_elements`** *(core, library)* — element-matching helpers (used by cost/BIM matching).
- **`change_intelligence`** *(controls, 34 r)* — detects/analyses change across project data.
- **`project_intelligence`** *(core, 26 r)* — project-level insights/analytics.
- **`voice`** *(core, 4 r)* — turn a spoken/typed site note into a structured, human-confirmed draft.
> **How/Interactions:** all are AI-augmented-human-confirmed (platform rule: AI mutations are drafts). They read across modules via services; `retrieval`+`search` sit on `core/vector`. **Failure:** disable → assistant/search degrade; data modules unaffected.

## B. Reporting, dashboards & portfolio
- **`reporting`** *(core, 42 r)* — reports & dashboards generation (branded output via `core/reporting`).
- **`dashboard`** *(core, 12 r)* — single rollup endpoint returning all dashboard sections.
- **`dashboards`** *(core, 54 r)* — analytical layer over snapshots (`Snapshot.tenant_id`).
- **`bi_dashboards`** *(extension, 68 r)* — BI dashboards & reporting (richer analytics, Wave 4).
- **`portfolio`** *(business, 20 r)* — multi-project portfolio rollups.
- **`teams`** *(core, 72 r)* — team visibility / project membership (the project-member access layer `dependencies.verify_project_access` consults).
- **`tasks`** *(core, 32 r)* — generic task tracking.

## C. Compliance, quality & governance
- **`compliance`** *(core, 14 r)* — compliance **DSL**: user-authored validation rules.
- **`compliance_ai`** *(core, 4 r)* — DSL engine + natural-language rule builder.
- **`compliance_docs`** *(core, 14 r)* — tracker for insurance/permits/bonds/certifications (expiry).
- **`qms`** *(extension, 118 r)* — full Quality Management System (ISO-style: procedures, audits, CAPA, NCR linkage).
- **`requirements`** *(core, 68 r)* — requirements & quality gates register.
- **`validation`** *(core, 32 r)* — the shared validation engine/rules surface (platform validation).
- **`risk`** *(core, 30 r)* — risk register (likelihood/impact, mitigation).
- **`cases`** *(core, 20 r)* — issue/case management.
- **`claims_evidence`** *(controls, 8 r)* — claims/dispute evidence-pack assembly.
- **`review_authority`** *(core, 30 r)* — external-review-authority (expertise/review cycle).
- **`moc`** *(project_controls, 26 r)* — Management of Change register.
- **`source_data`** *(core, 30 r)* — prerequisite-documents register (source data a task needs before it can start).
- **`authority_submission`** *(core, 24 r)* — XML document factory for authority submissions.
- **`record_publishing`** *(core, 8 r)* — one-tap publish-a-record-and-distribute.
- **`signing`** *(core, 22 r)* — e-signature workflow + attestation registry.
- **`phonelog`** *(core, 14 r)* — capture phone-call/voice-note verbal instructions as dispute-ready records.

## D. Commercial change & e-invoicing (remaining)
- **`changeorders`** *(core, 38 r)* — change orders (client-side change control).
- **`variations`** *(business, 160 r)* — Variations & Site Measurements (the large variation-valuation module).
- **`preliminaries`** *(core, 14 r)* — preliminaries / general conditions (feeds estimate rollup).
- **`einvoice`** *(core, 6 r)* — international electronic-invoicing (EN 16931 / UBL / CII / Peppol / ZUGFeRD generation).
- **`einvoice_clearance`** *(core, 32 r)* — e-invoice clearance (country clearance-model submission, e.g. real-time tax authority).
- **`service`** *(business, 104 r)* — Service & Maintenance (service jobs, maintenance contracts, scheduling).
- **`appointments`** *(business, 74 r)* — services, bookable resources, bookings, online booking + reminders.
- **`accommodation`** *(business, 38 r)* — worker camps / rental apartments / hotels management.
- **`property_dev`** *(business, 460 r)* — **Property Development & Buyer Portal** (the single largest module: units, sales, buyer portal, handover — a whole sub-product).
- **`portal`** *(business, 72 r)* — customer & partner portal (external-stakeholder access surface).
- **`webhook_leads`** *(business, 24 r)* — inbound web-form/webhook lead capture → CRM.
- **`forms`** *(business, 30 r)* — forms & checklists (configurable data-capture forms).
- **`carbon`** *(business, 122 r)* — carbon & sustainability accounting.
- **`esg`** *(business, 20 r)* — ESG site-performance metrics.
- **`interface_management`** *(business, 20 r)* — multi-package interface coordination register.
- **`enterprise_workflows`** *(enterprise, 22 r)* — configurable enterprise workflow automation.
- **`projects`** *(core, 122 r)* — **the project entity itself** (projects, ownership, membership) — foundational; nearly everything `depends=[projects]`.

## E. Platform & integration services
- **`onboarding`** *(core, 8 r)* — non-blocking first-run provisioning (presets, starter data).
- **`module_builder`** *(core, 22 r)* — the runtime module-builder backend (documented in the registry/builder doc).
- **`notifications`** *(core, 30 r)* — in-app notifications with i18n keys + per-user preferences.
- **`i18n_foundation`** *(core, 46 r)* — the internationalisation foundation (44 languages, RTL, terminology).
- **`integrations`** *(core, 26 r)* — chat connectors (Teams/Slack/Telegram), webhooks, calendar feeds.
- **`connectors`** *(core, 8 r)* — connector registry/config.
- **`inbound_capture`** *(integration, 6 r)* — inbound capture gateway.
- **`inbound_email`** *(controls, 2 r)* — inbound email intake.
- **`data_import`** *(core, 22 r)* — CSV/XLSX import wizard (map → validate → dry-run → commit → undo).
- **`pipelines`** *(core, 18 r)* — pipeline builder (data/processing pipelines).
- **`uploads`** *(core, 2 r)* / **`resumable_uploads`** *(core, 12 r)* — upload intake + chunked/resumable uploads (GB-scale files).
- **`backup`** *(core, 6 r)* — export/import user-data backups.
- **`credentials`** *(core, 46 r)* — professional-credentials registry (people's licences/certs).
- **`global_presence`** *(core, 2 r)* — who's online and where (the presence WebSocket).
- **`jobs`** *(core, 8 r)* — background-job surface (over `core/job_runner`).
- **`project_route`** *(core, 20 r)* — work-type classifier gate ("delivery / permit route").
- **`client_errors`** *(infra, 2 r)* — client-side error sink (frontend error reporting).

## F. Regional / country packs
All `category=regional`. Most are thin activation shells (≈2 routes) that, when installed (via the `/modules` Packs tab), **switch on a bundle of modules, load demo data, apply branding, and add validation-rule packs / tax & document profiles** for that jurisdiction; a few carry their own tables/logic:
- **Thin packs (~2 r):** `uk_pack`, `dach_pack`, `latam_pack`, `asia_pac_pack`, `china_pack`, `india_pack`, `middle_east_pack`, `russia_pack`, `us_pack`.
- **Heavier packs (own routes/tables):** `mexico_pack` (20 r), `sa_pack` (South Africa, 12 r), `us_ca_pack` (6 r), `us_tx_pack` (6 r) — jurisdiction-specific tax/compliance specifics.
> **How/Interactions:** packs are applied through the registry's pack engine (`partnerPacks`, Batch "registry" doc) and compose other modules + `tax`/`einvoice`/`compliance` configuration. **Failure:** deactivating a pack restores the prior module set (the deactivate dialog lists `restored_modules`).

---

## Completion summary — all 191 modules documented

| Batch | Domain | Modules | File |
|---|---|---|---|
| 1 | Finance & Accounting | 15 | `…-01-finance.md` |
| 2 | Sales & Commercial | 10 | `…-02-sales-commercial.md` |
| 3 | Purchasing & Supply | 5 | `…-03-purchasing-supply.md` |
| 4 | Estimating & Cost | 26 | `…-04-estimating-cost.md` |
| 5 | Project Delivery & Field | 25 | `…-05-delivery-field.md` |
| 6 | Documents & Collaboration | 24 | `…-06-documents-collaboration.md` |
| 7 | Platform / AI / Compliance / Regional | 86 (incl. 13 regional packs) | this file |
| (deep-dives) | admin, users | 2 | earlier OBE docs |

That accounts for **191 module directories** under `backend/app/modules/` (the loader shows ~188 loadable; the difference is library-only modules such as `catalog`, `measurement`, `price_breakdown`, and always-on vs optional split). Every module directory now has at least a grounded Four-Pillars-compact entry; the architecturally load-bearing ones (`gl`, `boq`, `costs`, `schedule`, `documents`, `projects`, `contracts`, `property_dev`, `qms`, `admin`, `users`, module-builder) have fuller treatment in their batch or a dedicated doc.

### The full OpenBusinessERP documentation set (scratchpad)
1. `openbusinesserp-architecture.md` — stack, architecture, RBAC/security, module catalog
2. `openbusinesserp-frontend-ux.md` — layout, design tokens, menu, Settings, Team/permissions
3. `openbusinesserp-users-modules.md` — `/users` page + system/module settings
4. `openbusinesserp-modules-registry-builder.md` — `/modules` registry + module builder
5. `openbusinesserp-modules-01…07` — **per-module Four-Pillars for all 191 modules** (this batch set)

*Grounded in `kingdonk-ops/openbusinesserp` @ `a96d525`.*

---

# PART D — EDITABLE MODULE SPECIFICATIONS (format + Finance; batches 2–7 pending per Gap G1)

# OpenBusinessERP — Module Specifications (editable)
## Spec format + Batch 1: Finance & Accounting

> Purpose: a per-module spec you can **edit to shape the build plan** — strike whole modules, strike individual features, or add new ones. This file defines the **spec format** (so all 7 batches are consistent) and contains **Batch 1 (Finance, 15 modules)** as the worked sample. Grounded in `backend/app/modules/<name>/{models,router,service}.py` of `kingdonk-ops/openbusinesserp` @ `a96d525`.
>
> **Please confirm this format before I spec the remaining ~176 modules** — the shape (esp. the discrete `[F#]` features and removal-impact) is what makes it editable, so it's worth locking first.

---

## Spec format (per module)

```
### <name>  — <one-line title>
- Identity: category=<…> · optional?/always-on · prefix=/api/v1/<…> · routes≈N
- Depends on: [modules]      Dependents: [modules that import this]   ← removal blast radius
- Purpose: 1–2 lines
- Entities (oe_<module>_*): table → key fields
- Endpoints: grouped
- Key logic / state: service functions, state machines, invariants
- Events: emitted / consumed (if any)
- Features [F1..Fn]: discrete, individually removable — tick/strike to edit scope
- Removal impact: what breaks / who must change if this module is dropped
- Edit notes: (left blank for you)
```

### Blank template for a NEW module (copy to add one)
```
### <new_name> — <title>
- Identity: category=<core|business|department|regional|extension|…> · optional · prefix=/<…>
- Depends on: []              Dependents: []
- Purpose:
- Entities: oe_<name>_<entity> → (fields…)
- Endpoints:
- Key logic / state:
- Events:
- Features: [F1] …
- Removal impact:
- Edit notes:
```

**How to edit:** strike a `### module` to remove it (note its Dependents — they must change); strike a `[F#]` line to drop just that feature; copy the blank template to add one and wire `Depends on`. The build plan's phase/order follows the `Depends on` graph, so edits here reshape the plan deterministically.

---

## Batch 1 — Finance & Accounting (15 modules)

### gl — General Ledger (double-entry)
- **Identity:** category=core · always-on · prefix=`/gl` · routes≈60
- **Depends on:** [] · **Dependents:** finance, banking, sales, purchasing, fx, tax_withholding, payroll (everything that posts)
- **Purpose:** the accounting core; chart of accounts, fiscal calendar, balanced journals. The posting sink for the whole system.
- **Entities:**
  - `oe_gl_account` → code, name, account_type, parent_id, currency, is_postable, active, template
  - `oe_gl_fiscal_year` → name, start_date, end_date, status, last_number, periods
  - `oe_gl_period` → fiscal_year_id, name, start_date, end_date, status
  - `oe_gl_journal_entry` → number, seq, entry_date, period_id, fiscal_year_id, currency, base_currency, fx_rate, status, source_type, source_id, memo, reversal_of_id, reversed_by_id, created_by
  - `oe_gl_journal_line` → entry_id, line_no, account_id, description, debit, credit, base_debit, base_credit, tax_code, cost_center, project_id
  - `oe_gl_attachment` → entry_id, filename, content_type, size, data, uploaded_by
- **Endpoints:** accounts CRUD · fiscal-year/period open-close · journal post/list/get · trial balance / ledger · attachments.
- **Key logic:** `build_lines`, `check_balanced` (debits=credits before post), `natural_balance`, `base_currency`, `money` (Decimal). Closed period refuses posting. Reversal via `reversal_of_id`/`reversed_by_id`.
- **Features:** [F1] chart of accounts · [F2] fiscal calendar + period close · [F3] balanced journal posting · [F4] multi-currency base/fx on entries · [F5] journal attachments (evidence) · [F6] reversals · [F7] cost-center/project tagging on lines.
- **Removal impact:** **catastrophic** — no module can post; do not remove. (Strike features instead, e.g. drop [F5] attachments or [F7] cost-center.)
- **Edit notes:**

### finance — Invoices, payments, budgets, EVM, GAAP ledger
- **Identity:** category=core · prefix=`/gaap` · routes≈74 · **Depends on:** [users, projects, contacts] · **Dependents:** receivables, dashboards
- **Purpose:** commercial finance — AR/AP invoices, payments, budgets, earned value, a project GAAP ledger.
- **Entities:**
  - `oe_finance_invoice` → project_id, contact_id, invoice_direction, source_claim_id, purchase_order_id, invoice_number, invoice_date, due_date, currency_code, amount_subtotal, tax_amount, retention_amount, amount_total, tax_config_id, status, payment_terms_days, notes, metadata_
  - `oe_finance_invoice_item` → invoice_id, description, quantity, unit, unit_rate, amount, wbs_id, cost_category, vat_rate, vat_category, cost_line_id, sort_order
  - `oe_finance_payment` → invoice_id, payment_date, amount, currency_code, exchange_rate_snapshot, reference, idempotency_key, is_refund, withholding_amount, source_claim_id, withholding_release_date, metadata_
  - `oe_finance_budget` → project_id, wbs_id, category, currency_code, original_budget, revised_budget, committed, actual, forecast_final
  - `oe_finance_evm_snapshot` → project_id, snapshot_date, bac, pv, ev, ac, sv, cv, spi, cpi, eac, vac, etc, tcpi
  - `oe_finance_ledger` → project_id, transaction_ref, account_code, description, debit_amount, credit_amount, currency_code, posted_at, source_type, source_id, is_reversal, reversal_of_id, idempotency_key
  - `oe_finance_ledger_account` → project_id, account_code, name, account_type, normal_balance, parent_id, statement_section, is_cash, is_active, currency_code
- **Endpoints:** invoice lifecycle · payment application · budgets · EVM snapshots · ledger reporting.
- **Key logic:** `compute_payment_withholding` (nets withholding), `resolve_position_cost_lines`; idempotency keys on payments/ledger; EVM metrics.
- **Features:** [F1] AR/AP invoices + items · [F2] payments (incl. refunds, FX snapshot, idempotency) · [F3] withholding netting (needs tax_withholding) · [F4] project budgets (orig/revised/committed/actual/forecast) · [F5] EVM snapshots · [F6] project GAAP ledger + accounts · [F7] retention on invoices.
- **Removal impact:** receivables & finance dashboards lose their data; a lighter `sales`/`gl`-only AR path would need building. 
- **Edit notes:**

### banking — Accounts, statement import, matching, reconciliation
- **Identity:** category=core · prefix=`/banking` · routes≈44 · **Depends on:** [gl, sales] · **Dependents:** receivables, reconciliation
- **Entities:** `oe_bank_account`(name, iban, bic, currency, gl_account_code, active, opening_balance[_date]) · `oe_bank_statement`(account_id, filename, format, imported_count, duplicate_count) · `oe_bank_transaction`(…) · `oe_bank_match` · `oe_bank_partner_iban` · `oe_bank_settings` · `oe_bank_rule`
- **Endpoints:** accounts · statement import · transactions · matches · rules · settings.
- **Key logic:** import **camt.053/054, MT940, CSV**; `partner_key` normalise; rules auto-classify; match → post reconciling journal to gl; IBAN→partner learning.
- **Features:** [F1] bank accounts · [F2] statement import camt · [F3] import MT940 · [F4] import CSV · [F5] auto-matching · [F6] auto-rules · [F7] IBAN→partner learning · [F8] reconciliation posting to gl.
- **Removal impact:** no statement import/reconciliation; invoices still post manually. (Strippable: drop [F3]/[F4] formats or [F6] rules independently.)
- **Edit notes:**

### reconciliation — Cross-record linking
- **Identity:** category=controls · prefix (controls) · routes≈6 · **Depends on:** [users, projects] · **Dependents:** (banking-adjacent)
- **Entities:** `oe_record_link` → project_id, left_type, left_id, right_type, right_id, relation, confidence, status, created_by, metadata_
- **Key logic:** `canonical_pair`, `link_key`, `scored_link_key`, `gather_candidates`, `assemble_thread`, `decide_record_link` — entity-URI based, module-agnostic.
- **Features:** [F1] manual record links · [F2] candidate suggestion/scoring · [F3] evidence threads.
- **Removal impact:** no cross-module manual links; native matching (banking) unaffected.
- **Edit notes:**

### receivables — Aging & dunning (draft-only)
- **Identity:** category=core · prefix=`/receivables`? · routes≈44 · **Depends on:** [sales, banking] · **Dependents:** —
- **Entities:** `oe_dunning_level`, `oe_dunning_settings`, `oe_dunning_block`, `oe_dunning_run`, `oe_dunning_letter`, `oe_ar_followup`
- **Key logic:** `bucket_for` (aging), `next_level`, `render`; **letters are draft-only, never auto-sent**; block list skips customers.
- **Features:** [F1] aging buckets · [F2] dunning levels/settings · [F3] dunning runs + letters (draft) · [F4] block list · [F5] AR follow-ups · [F6] statements.
- **Removal impact:** no automated chasing; manual AR only.
- **Edit notes:**

### payment_clock — Statutory payment deadlines (regional)
- **Identity:** category=regional · prefix=`/payment-clock`? · routes≈26 · **Depends on:** [projects, contracts, subcontractors]
- **Entities:** `oe_payment_clock_regime`(code, jurisdiction, country_code, statute, due/notice/final/pay_less bases+days, interest_*) · `oe_payment_clock_application`(project_id, regime_id, source, application_date, period, applied_amount, due/notice/payless/final dates, status, paid_*) · `oe_payment_clock_notice`(application_id, notice_type, issued_at, notified_amount, basis) · `oe_payment_clock_event`
- **Key logic:** `regime_spec`/`ensure_regimes`, `build_schedule`/`recompute_schedule`, `clock_snapshot`, `record_clock_events`, `sync_clock_register`.
- **Features:** [F1] jurisdiction regimes · [F2] payment applications + clocks · [F3] notices (payment/payless) · [F4] interest computation · [F5] clock register sync.
- **Removal impact:** no statutory-clock tracking (UK Construction Act etc.).
- **Edit notes:**

### tax_withholding — Withholding / reverse charge
- **Identity:** category=core · routes≈48 · **Depends on:** [projects, users] · **Dependents:** finance (payment netting)
- **Entities:** `oe_tax_withholding_regime`, `oe_tax_withholding_party`, `oe_tax_withholding_deduction`, `oe_tax_withholding_reverse_charge`
- **Key logic:** `compute_taxable_base`, `compute_tax_withheld` (banded: `bands_of`/`find_band`/`resolve_band`), `verification_is_current` gates rate, `seed_regimes`, Decimal quantise.
- **Features:** [F1] withholding regimes · [F2] per-party status + verification gating · [F3] banded deduction computation · [F4] reverse-charge records · [F5] determinations.
- **Removal impact:** payments don't net withholding (finance [F3] disabled).
- **Edit notes:**

### fx — FX rates & revaluation
- **Identity:** category=core · routes≈28 · **Depends on:** [] · **Dependents:** gl (revaluation), assemblies, any multi-currency
- **Entities:** `oe_fx_rate_set`, `oe_fx_rate_quote`, `oe_fx_policy`, `oe_fx_rate`, `oe_ppp_factor`
- **Key logic:** `parse_ecb_xml`, `cross_rate`, `convert_via_base`, `decompose_movement`, `as_revaluation_lines`.
- **Features:** [F1] rate sets/quotes · [F2] ECB import · [F3] conversion policy · [F4] period-end revaluation → gl · [F5] PPP factors.
- **Removal impact:** single-currency only.
- **Edit notes:**

### payroll — Payroll batches
- **Identity:** category=core · routes≈24 · **Depends on:** [projects, fieldreports, resources] · **Dependents:** certified_payroll
- **Entities:** `oe_payroll_batch`, `oe_payroll_entry`, `oe_payroll_deduction`
- **Features:** [F1] pay batches · [F2] entries from field hours · [F3] deductions · [F4] post to gl.
- **Removal impact:** no payroll; certified_payroll loses its source.
- **Edit notes:**

### certified_payroll — Prevailing-wage reporting (compliance)
- **Identity:** category=compliance · routes≈38 · **Depends on:** [payroll, projects, resources]
- **Entities:** `oe_certpay_determination`, `_classification`, `_assignment`, `_week`, `_line`
- **Features:** [F1] wage determinations · [F2] worker classifications · [F3] assignments · [F4] weekly certified lines (WH-347-style) · [F5] report output.
- **Removal impact:** no US prevailing-wage certified payroll.
- **Edit notes:**

### labor_rates — Burdened labour rates
- **Identity:** category=core · routes≈20 · **Depends on:** [users] · **Dependents:** estimating, payroll
- **Entities:** `oe_labor_rates_template`, `_oncost`, `_crew_member`
- **Key logic:** `compute_breakdown` (base + on-costs), `describe_template_references`.
- **Features:** [F1] rate templates · [F2] on-cost components · [F3] crew composition · [F4] all-in rate build-up.
- **Removal impact:** manual rates only.
- **Edit notes:**

### allowances — Contract allowances & drawdowns
- **Identity:** category=core · routes≈18 · **Depends on:** [projects, users]
- **Entities:** `oe_allowances_allowance`, `oe_allowances_drawdown`
- **Features:** [F1] allowance pots · [F2] drawdowns · [F3] remaining balance.
- **Removal impact:** no allowance tracking (estimate_rollup loses an input).
- **Edit notes:**

### funding — Grants/funding & proof-of-use
- **Identity:** category=business · routes≈60 · **Depends on:** [users, projects]
- **Entities:** `oe_funding_programme`, `_application`, `_disbursement`, `_proof_of_use`, `_obligation`, `_cost_allocation`
- **Features:** [F1] programmes · [F2] applications · [F3] disbursements · [F4] proof-of-use · [F5] obligations · [F6] cost allocation to funding source.
- **Removal impact:** no grant/funding compliance.
- **Edit notes:**

### cost_recovery — Back-charges & apportionment (controls)
- **Identity:** category=controls · routes≈18 · **Depends on:** [users, projects]
- **Entities:** `oe_cost_recovery_back_charge`, `oe_cost_recovery_apportionment`
- **Key logic:** `create/update/list/get_back_charge`, `apportion_back_charge`, `build_recovery_ledger`, `build_recovery_performance`, `pending_backcharges`.
- **Features:** [F1] back-charges · [F2] apportionment · [F3] recovery ledger · [F4] recovery performance (project + portfolio).
- **Removal impact:** no back-charge recovery.
- **Edit notes:**

### postcalc — Post-calculation feedback
- **Identity:** category=business · routes≈8 · no tables · **Depends on:** [boq, costmodel] · **Dependents:** estimating norms
- **Key logic:** `consumed_material_cost`, `compute_line_productivity`, `aggregate_resources`, `build_feedback_factors`, `compute_project_postcalc`.
- **Features:** [F1] actual-vs-estimate comparison · [F2] productivity calc · [F3] feedback factors to norms.
- **Removal impact:** no actual→norm feedback loop.
- **Edit notes:**

---

*Batch 1 sample of 7. On your confirmation of this format I'll produce Batches 2–7 the same way (entities+fields, endpoints, discrete features, deps-in/out, removal impact) and push them to this branch. Grounded @ `a96d525`.*

---

*End of consolidated master. See §0.3 Gaps Register for outstanding data/planning items.*
