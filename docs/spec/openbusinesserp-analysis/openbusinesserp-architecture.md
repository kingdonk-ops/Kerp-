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
