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
