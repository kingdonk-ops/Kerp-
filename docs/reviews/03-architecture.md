# Architecture & Code Structure Advisor — review

**TL;DR.** The modular-monolith doctrine is sound and the as-built Inspection-ERP reference (`architecture-suite-reference/01–02`) is a *working* implementation of almost exactly what KERP's CLAUDE.md §6 mandates — so **port its skeleton verbatim in Phase 0 and freeze it as the module contract** rather than re-deriving it. My single most important recommendation: **adopt TD-002 (the one-engine form/item_types/eligibility collapse) now, before T-0102/T-0109/T-0110 are built**, because four half-engines for inspection schema is the biggest structural liability in the spec and the cost of unifying rises every phase. Beyond that: (1) split the mandated module skeleton into a `core/` platform layer vs `modules/` feature layer that CLAUDE.md's flat `modules/<name>/` list does not yet name; (2) make the five cross-cutting seams (`run_checks`, VFS resolver, `eligibility`, field-type evaluator, sync ledger) **services with frozen contracts**, each owned by exactly one module; (3) formalise a 3-tier dependency layering (platform → backbone → pillars) and enforce it with an import-lint gate so the ~94-module graph cannot grow cycles; (4) write the module template as a scaffolded generator in T-0009 so all 94 modules are structurally identical. This review is KERP-specific and assumes the greenfield repo (no `backend/`/`frontend/` on disk yet) is built from these docs.

---

## 1. Findings

### 1.1 The module contract needs a two-layer split that CLAUDE.md §6 does not yet state

CLAUDE.md §6 and DEVELOPMENT-PLAN §1 both describe *one* shape: `backend/app/modules/<name>/` with `manifest.py, models.py, router.py, service.py, schemas.py, README.md`. That is correct for the ~94 **business** modules, but it is **missing the platform layer**. The as-built reference proves the distinction is load-bearing: `app/core/*` (tenancy, auth, audit, schema engine, jobs, files, validation, sync, reporting) is mounted unconditionally and is *never* a `modules/<name>/` folder, while `app/modules/*` is discovered, toposorted and gated by the loader (ref 01 §2.2, 02 §"What counts as a base module"). KERP's own Phase-0 task list already implies this split without naming it: T-0005 (BaseRecordMixin), T-0006 (tenancy/RLS), T-0013 (audit), T-0014 (jobs/ARQ), T-0015 (storage/presign), T-0016 (`run_checks`), T-0125 (sync) are all **platform substrate**, not modules — yet the spec lists `validation`, `jobs`, `uploads`, `notifications` as "modules". 

**The confusion is real and will cause mis-placed code.** `validation` is listed as a Phase-0 *module* (MODULE-SPECS §1) but is simultaneously "the substrate of `run_checks`" (plan §5) that every gated module calls — i.e. it is a core engine. Resolve it: **`run_checks`/rule-set evaluation lives in `app/core/verification/` (engine), `validation` the module is a thin router/register over it.** Same pattern the reference uses for `core/forms/` (engine) vs a forms feature.

Recommended backend tree (greenfield, built by T-0001/T-0005/T-0009):

```
backend/
├── pyproject.toml            # deps, ruff(E,F,I,UP,B,SIM), mypy(strict), pytest(--cov-fail-under), py3.12
├── alembic.ini
├── app/
│   ├── main.py               # create_app() factory — composition root (ref 01 §2.3)
│   ├── config.py             # pydantic-settings; KERP_* env; check_production() refuses unsafe secret
│   ├── database.py           # engine/session, Base, GUID/JSONType, BaseRecordMixin, hash/revision before_flush
│   ├── all_models.py         # imports every models.py so Base.metadata is complete before create_all/RLS
│   ├── cli.py                # `python -m app.cli migrate` — Alembic + app-role grant + RLS (prod entrypoint)
│   ├── core/                 # PLATFORM — changes here need an ADR (docs/decisions/)
│   │   ├── tenancy/          # org/project models, rls.py, context.py (ContextVars), scope.py, references.py
│   │   ├── auth/             # JWT access/refresh, AuthProvider seam (TD-003), password hash, RBAC deps, throttle
│   │   ├── audit/            # append-only ie log + content-hash/revision contract (T-0013)
│   │   ├── verification/     # run_checks(context, rule_set); DSL parse/eval; findings log (T-0016) ← was "validation"
│   │   ├── fields/           # shared field-type REGISTRY + sandboxed evaluator (TS parity)  ← TD-002 foundation
│   │   ├── jobs/             # ARQ enqueue/status over Redis; one worker container (T-0014)
│   │   ├── storage/          # presign/complete, sha256 verify, scan-on-complete, orphan sweep (T-0015)
│   │   ├── sync/             # idempotent op-ledger (client_op_id→result) + conflict replay (T-0125)
│   │   ├── module_loader.py  # manifest discovery, toposort, mount /api/v1, per-project enable (T-0009)
│   │   ├── events.py         # in-process event bus (wire emitters this time — ref 02 flags it unused)
│   │   ├── reporting/        # ReportLab PDF / openpyxl — library, lazy-imported
│   │   ├── observability.py  # request-id/timing middleware, structured logs (T-0022)
│   │   └── security_headers.py
│   └── modules/              # BUSINESS features — one folder per module (the canonical template, §1.2)
│       ├── projects/  users/  admin/  teams/  onboarding/  notifications/  saved_views/   # Phase 0
│       ├── asset_management/  inspections/  qms/  construction_control/  ncr/  forms/  item_types/  eligibility/  wps/ …
│       └── …(≈94 total)
├── migrations/               # Alembic versions — additive by default; destructive ⇒ ADR
├── rule_sets/                # run_checks DSL as DATA: weld.accept.yaml, inspection.close.yaml, … (§5 list)
├── config_packs/             # asset taxonomies/ITP seeds/starter packs as versioned JSON (unified w/ form seeds)
├── seeds/                    # synthetic fixtures only (no real client data — hard rule 10)
└── tests/
    ├── contract/             # BaseRecordMixin metadata, RLS tenant-isolation, manifest validity, run_checks sets
    └── modules/<name>/       # per-module unit/integration
```

Rule-sets, config-packs and i18n live as **repo data read at runtime**, not code — `rule_sets/*.yaml` is what makes "verification is configuration" (hard rule 5) true, and it keeps a client's extra check a PR to a data file, not a release. i18n `t()` catalogs live under `frontend/src/app/locales/` (single company ⇒ catalogs are for terminology/renaming, per TD-002 §2.6 `terms`, not localisation).

### 1.2 The canonical module template (scaffold this in T-0009)

Every one of the ~94 modules must be byte-structurally identical so one change touches one place and a manifest contract test can police all of them. T-0009 should ship a `scripts/new_module.py` generator that emits this skeleton; the reviewer agent rejects any module that deviates.

**`manifest.py`** — the contract fields (ported from ref 01 §2.5, extended for per-project enablement + pillar layering):

```python
# app/modules/inspections/manifest.py
from app.core.module_loader import ModuleManifest

manifest = ModuleManifest(
    name="inspections",              # MUST equal the folder name (loader asserts)
    version="1.0.0",
    category="backbone",             # platform | backbone | logistics | execution | defense | controls | docs | portal
    layer=1,                         # 0 platform, 1 backbone, 2 pillars — loader forbids depending on a higher layer
    depends=["asset_management", "forms", "eligibility"],   # resolved by toposort; unknown/cycle ⇒ RuntimeError
    provides_rule_sets=["inspection.close", "inspection.signoff"],   # declared so run_checks can validate coverage
    anchors=["rsw_node_id", "asset_item_id"],   # declares which anchor cols it carries (hard rule 3/6 check)
    default_enabled=True,            # per-project toggle lives in projects._project_module
    tables_prefix="oe_inspections_",
)
```

**`models.py`** — every table inherits `BaseRecordMixin` and is registered for RLS:

```python
# app/modules/inspections/models.py
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, BaseRecordMixin   # entity_uri, tenant_id, created/modified, revision, source, hash
from app.core.tenancy.rls import tenant_table    # marks the table for ENABLE+FORCE RLS policy generation

class Inspection(Base, BaseRecordMixin):
    __tablename__ = "oe_inspections_inspection"   # oe_<module>_* prefix (hard rule / MODULE-SPECS §5)
    __table_args__ = (tenant_table(), )           # RLS policy auto-applied by apply_rls_policies()
    project_id:    Mapped[UUID] = mapped_column(index=True)      # ORM project-scope guard keys on this
    rsw_node_id:   Mapped[UUID | None]                          # anchor to RSW (work) …
    asset_item_id: Mapped[UUID | None]                          # … or Item (inspection) — never a BOQ anchor
    template_revision_id: Mapped[UUID]        # pins the frozen forms revision (TD-002 immutability)
    status:        Mapped[str]
    # external systems of record ⇒ external_source/external_id/synced_at, NEVER an FK (hard rule 4/7)
```

**`schemas.py`** — Pydantic v2 request/response DTOs (the only shapes the router speaks; never expose ORM rows).
**`service.py`** — *the* module's public surface. Cross-module calls land here and nowhere else:

```python
# app/modules/inspections/service.py  — the ONLY door other modules may knock on
def close_inspection(db, ctx, inspection_id, payload):
    insp = _get_or_404(db, ctx, inspection_id)                       # tenant+project scoped; cross-tenant ⇒ 404
    findings = run_checks(ctx, "inspection.close", target=insp)      # verification is configuration (hard rule 5)
    if findings.has_errors:                                          # errors block, warnings advise
        raise GateBlocked(findings)
    # needs welder/instrument validity? ASK eligibility — do NOT read wps/credentials/qms tables (hard rule 1)
    elig = eligibility_service.check(ctx, subject_kind="person", subject_id=insp.inspector_id, context="inspection.close")
    ...
    write_audit(db, entity_uri=insp.entity_uri, action="inspection.close")
```

**`router.py`** — FastAPI `APIRouter(prefix="/inspections")`; endpoints call `service.py` only; `Depends(get_current_context)` binds tenant/scope; no business logic.
**`README.md`** — identity, entities, endpoints, the rule-sets it owns, verification points, removal impact (MODULE-SPECS already drafts this per module — lift it verbatim).
**`permissions.py`** (add to the mandated six) — the role→action map the ref ships per module (ref 01 §3.2b); without it RBAC lives nowhere consistent.

### 1.3 Frontend layout mirrors the backend one-to-one

The ref (01 §2.2) and T-0024/T-0002 already fix the stack. The structure that keeps "one change, one place":

```
frontend/src/
├── app/            # shell: App.tsx, Layout.tsx, auth.tsx, i18n.ts, navCatalog.ts (4-pillar nav), theme tokens, router
├── api/            # GENERATED openapi-typescript (schema.d.ts) + typed surface — never hand-edit (CI fails on drift)
├── lib/            # http.ts (single-flight 401 refresh), offline/ (IndexedDB queue + useOfflineSync), queryClient
├── components/     # shared design-system primitives — NO business logic (Tailwind + shadcn-style)
└── modules/<name>/ # one folder per backend module; _registry.ts self-registers routes/nav; _types.ts local contract
```

Four decisions that matter for KERP specifically:
- **`navCatalog` is data, driven by `projects._project_module`** — the 4-pillar sidebar (T-0024) shows a module only if it is enabled for the project *and* in the user's scope. One catalog file; modules register into it via `_registry.ts`. Do not hard-code the nav.
- **`useOfflineSync` (T-0126) is a single hook over `lib/offline/`**, consumed by every Tier-1 edge screen (Gate Delivery E-03-02, Look-Ahead Board E-04-02, Daily Workface Log E-04-05). It must be built in Phase 1 (TD-006) because retrofitting offline onto 3 edge screens later means 3 divergent implementations.
- **i18n `t()` everywhere (hard rule 9)** even single-company — because the `terms` dictionary (TD-002 §2.6) renames RSW/Punch/Snag per client *through the same mechanism*. Wire `t()` now or pay to retrofit.
- **The generated `api/` client is the contract test** between front and back: `npm run gen:api` after any API change, committed in the same PR (CLAUDE.md §4).

### 1.4 Cross-cutting seams — each lives in exactly one place, consumed by service call

This is the heart of "one change touches one place." Five seams cut across every phase; name their owner and their consumption rule:

| Seam | Lives in | How modules consume it (no cross-module tables) | Built by |
|---|---|---|---|
| **Verification engine** `run_checks(ctx, rule_set, target)` | `core/verification/` + `rule_sets/*.yaml` (data) | import `run_checks`; declare owned rule-sets in `manifest.provides_rule_sets`; errors block, warnings advise | T-0016; sets in T-0124, T-0205 |
| **VFS routing resolver** `documents.route_and_store(module, target, bytes)` | `documents` module `service.py` (VFS folded in, **not** a separate module) | call the resolver to file into `/Site/Asset/RSW`; get a `document_id`; never touch object-store keys or `_folder` tables | E-05-01 |
| **Eligibility gate** `eligibility.check(subject, context)` | `eligibility` module — *reads* `credentials`/`wps`/`qms` calibration/`compliance_docs`, owns no table | one call returns `{eligible, failures[]}`; feeds `run_checks` predicates + the inline inspection banner | TD-002 (E-01-01) |
| **Field-type registry + evaluator** | `core/fields/` — one TS package + Python port tested on **shared fixtures** | both `forms` and `item_types` import it; the single palette for every template and asset schema | T-0110 (unblock via TD-002) |
| **Offline sync ledger** `sync.apply(client_op_id, op)` | `core/sync/` (idempotent op→result) | edge screens push ops; `field_diary` ledger is the *same* engine, not a second one (MODULE-SPECS §4/14 reuse it) | T-0125 |

Two more that are smaller but must be single-owner: **notifications** (`core/`-adjacent module; webhook targets + in-app) and **audit/provenance** (`core/audit/` + the `before_flush` hash/revision listener — "no model can forget it", ref 01 §3.3). The provenance middleware is *not* per-module code; it is one ORM event listener in `database.py`.

The anti-pattern to forbid in review: a module importing another module's `models.py` to "just read one column." Every such read goes through the owning `service.py`. The ref enforces this as a hard rule (01 §2.1); KERP's reviewer-gate (CLAUDE.md §3.4) must grep for cross-module model imports and auto-`CHANGES_REQUESTED`.

### 1.5 Dependency graph & layering — freeze the backbone, lint the layers

The ~94 modules form a clean 3-layer DAG if you enforce it; they rot into a ball of mud if you don't. Layers (encode as `manifest.layer`, loader forbids depending upward):

- **Layer 0 · platform** (`core/*` + `projects, users, admin, teams, onboarding, notifications, saved_views`). Frozen first (Phase 0). Everything is `project_id`-scoped to `projects`; `projects` removal is "catastrophic" (MODULE-SPECS §1).
- **Layer 1 · backbone** (`asset_management, forms, item_types, inspections, qms, construction_control, ncr, eligibility, wps, validation/verification, compliance*`). **This is the critical path** (plan §9). `asset_management` is the highest-fan-in node in the whole system — dependents listed: inspections, qms, construction_control, tasks, schedule, progress, documents, file_*, moc, wps, cutting (MODULE-SPECS §2). Its contract (hierarchy, Items, dynamic schema, external keys, `run_checks`) **must be frozen at the end of Phase 2 before Phases 3–6 are broken down** — exactly what the rolling-wave plan already says, and the reason E-03..E-07 are still epics.
- **Layer 2 · pillars** (logistics, execution, defense, controls, docs, portal, workpacks/awp). These anchor *onto* layer 1 via `rsw_node_id`/`asset_item_id` and consume seams; they should never depend on each other except through declared service calls.

**Hotspots / high-fan-in nodes to watch** (flag these for extra contract tests and a frozen public `service.py`):
1. `asset_management` — the anchor for everything. Any change ripples to ~11 modules.
2. `documents` (incl. VFS) — dependents: file_*, closeout, plan_room, workpacks, awp, AI index, "every module that files a document" (MODULE-SPECS §5). The VFS resolver is the choke point; keep it a stable 2-function surface.
3. `validation`/`verification` — every gated transition calls it. If its signature drifts, every module breaks.
4. `schedule` + `progress` — the P6 round-trip and earned-quantity feed; `variations`, `costmodel`, `full_evm`, `portal` all read them.
5. `contacts` — party master; procurement, subcontractors, supplier_catalogs, wps, variations all reference it (MODULE-SPECS §3).

**Cycle risks the spec already contains:** `wps` depends on `inspections`+`qms`+`construction_control`, and those will want weld rules from `wps` — resolve by making `wps` own `weld.accept` as a rule-set that `inspections` *runs* (data), not imports (code). `workpacks`/`awp` depend on a huge fan (13+ modules each, MODULE-SPECS §14) — these are explicitly **orchestration layers**; keep them strictly layer-2 consumers that call services, never add tables other modules read. `approval_routes` vs `enterprise_workflows` is a **declared duplicate** ("pick one engine in the rebuild" — MODULE-SPECS §1); pick `approval_routes`, drop `enterprise_workflows`, before Phase 6.

Enforce layering with an **import-lint rule** (sibling to the T-0023 lazy-import lint): a module may import `core/*`, its own package, and `schemas` of declared `depends` — nothing else. This is the structural guarantee that the graph stays a DAG.

### 1.6 TD-002 (the dedup) — a clear structural YES

From a pure structure standpoint, adopting the AIP one-engine model is the highest-leverage decision in the spec, and the arguments are concrete, not aesthetic:

**Today's surface area (what the spec admits is duplicated):**
- Two schema designers: `forms` (`oe_forms_template`) **and** `asset_management` (`oe_asset_field_def`/`oe_asset_layout`) — two field palettes, two evaluators, two sets of validation code. MODULE-SPECS §2 itself says `oe_asset_layout` is a "Form/Inspection-Designer output," i.e. they already overlap.
- Inspection logic spread across **four** modules: `inspections` (thin register) + `qms` (ITP/templates) + `construction_control` (hold gates/criteria) + `workpacks` (execution stages). MODULE-SPECS §2 twice writes "resolve the dedup in the rebuild."
- Four independent "is it valid?" checks: `credentials`, `wps` welder-qual, `compliance_docs`, `qms` calibration — each consumer re-implements the validity query (PROPOSAL §1).

**After the collapse (MODULE-SPEC-11):**
- One `core/fields/` registry + one evaluator (TS+Python, shared fixtures) drives **both** templates and asset schema. One place to add a field type.
- One inspection **record** with `kind ∈ {inspection, itp, rfi}`; templates come from `forms`; gates run through `run_checks`; MDR via `workpacks`. `qms` keeps the audit register, `construction_control` keeps the hold-gate engine — their *overlap* is removed, their distinct value kept.
- One `eligibility` service answers validity for person/instrument/material over the existing registers; it owns no new table (PROPOSAL §1, MODULE-SPEC-11 §5).

**Structural verdict: adopt it.** It genuinely reduces coupling (every gate consumes one eligibility seam instead of reaching into three registers), reduces surface area (one designer, one evaluator), and the proposal is careful to keep it reuse-first and inside KERP doctrine (BaseRecordMixin, RLS, `run_checks`, external-mirror all preserved — MODULE-SPEC-11 preamble). The **cost of delay is the real argument**: T-0102 (dynamic schema), T-0109 (layout designer) and T-0110 (form engine) are the exact tasks that build the *duplicated* designers. Build them once, unified, or build them twice and pay to merge later. The blocked tasks (T-0102/T-0109/T-0110/E-01-01, blocked by TD-002 per the board) are correctly gated — **unblock them by deciding TD-002 before Phase 1 implementation starts.** The one caution: the shared TS/Python evaluator with fixture parity is non-trivial engineering (PROPOSAL §4 sequences it first, correctly); budget it as the foundation task, not a sub-bullet.

### 1.7 Testing structure — contract tests are the architecture's immune system

Four contract-test families belong in `tests/contract/` and run in `ci-gate` on every PR (CLAUDE.md §4, plan §8):

1. **BaseRecordMixin metadata test** — iterate `Base.metadata.tables`; assert every table has `entity_uri, tenant_id, revision, source, hash`. The ref enforces exactly this ("no model can forget it" — 01 §3.3). One test guards 94 modules.
2. **RLS tenant-isolation test** — run the suite twice, once on SQLite (app-layer 404), once against **real PG16 as a NOSUPERUSER NOBYPASSRLS role** so RLS is exercised, not assumed (ref 01 §1.4). Cross-tenant read ⇒ 404, cross-tenant write ⇒ rejected by `WITH CHECK`. This is the single most important test in the repo for a multi-tenant product (TD-007).
3. **Manifest validity test** — every `modules/<name>/` has a `manifest.py`; folder name == `manifest.name`; `depends` resolve; no cycles; `layer` never depends upward; declared `provides_rule_sets` all exist as `rule_sets/*.yaml`.
4. **`run_checks` rule-set tests** — one fixture suite per verifiable entity (the §5 list: `weld.accept`, `inspection.close`, `rsw.complete`, …). Each asserts error-findings block and warnings advise. These are per-module (`tests/modules/wps/test_rules.py`).

Keep the ref's `--cov-fail-under` floor (it used 80) and CLAUDE.md's rule: never weaken a test/lint/coverage floor to go green (auto-`CHANGES_REQUESTED`).

### 1.8 Conventions to fix in writing before T-0001

- **Table naming:** `oe_<module>_*` (MODULE-SPECS §5). Keep the `oe_` prefix even though it is inherited from OpenBusinessERP — it is harmless and already assumed across every entity spec. Unique constraints are **per-tenant** (`(tenant_id, code)`), never global (ADR-001 §5 flags the `work_pack_number` bug).
- **API versioning:** mount everything under `/api/v1` (ref 01 §2.3). One version prefix; the generated client pins to it.
- **Errors:** RFC 7807 `problem+json`; cross-tenant/not-found ⇒ **404 never 403** (avoid existence leaks — ref 01 §2.4).
- **Module enable/disable:** per-project via `projects._project_module`, read by the loader (MODULE-SPECS §1 F5). Drop the as-built *entitlement/billing* gate (ref 01 §2.5) — KERP is single-company (TD-007), so the gate is "enabled for this project," not "entitled tenant." This is a deliberate simplification to record.
- **ADR process:** `docs/decisions/` (CLAUDE.md §6 rule 8); destructive migrations and any `core/*` or core-table change need an ADR. Additive migrations are the default. The ref carries 15 ADRs; KERP should open ADR-0001 = this runtime/storage decision (already written) and ADR-0002 = the TD-002 one-engine decision once made.
- **Auth seam (TD-003):** put JWT behind an `AuthProvider` interface in `core/auth/` now so SSO/Entra can slot in later without touching modules. Cheap insurance; the ref already localises all auth in `core/auth/`.

---

## 2. Recommendations

| # | Recommendation | Rationale | Effort | Priority | Affects |
|---|---|---|---|---|---|
| R1 | **Adopt TD-002** — one `core/fields/` registry + evaluator, `forms`+`item_types` unified, `inspections`/`qms`/`construction_control` de-overlapped, `eligibility` service | Removes the largest duplication in the spec; cost of merging rises each phase; blocked tasks are already gated on it | L | P1 | TD-002; forms, item_types, inspections, qms, construction_control, eligibility; T-0102/T-0109/T-0110/E-01-01 |
| R2 | **Split the module contract into `core/` (platform) vs `modules/` (business)** and say so in CLAUDE.md §6 | The flat "everything is a module" wording mis-places `validation`/`jobs`/`sync`/`run_checks`, which are engines, not features | S | P1 | CLAUDE.md §6; Phase 0 (T-0005/06/13/14/15/16/125) |
| R3 | **Ship a module scaffold generator in T-0009** (`scripts/new_module.py`) emitting the 7-file template + manifest contract | 94 structurally identical modules; the manifest test polices drift; one change touches one place | S | P1 | T-0009; every module |
| R4 | **Add `layer`, `provides_rule_sets`, `anchors`, `permissions.py` to the module contract** and enforce with an import-lint + manifest test | Keeps the 94-node graph an acyclic 3-layer DAG; makes anchor-model and rule-set coverage machine-checkable | M | P1 | module_loader (T-0009); verification (T-0016); all modules |
| R5 | **Make the five seams single-owner services with frozen signatures** (`run_checks`, VFS resolver, `eligibility`, field evaluator, sync ledger) | "One change, one place"; prevents cross-module table reads; each is high-fan-in | M | P1 | T-0016, E-05-01, TD-002, T-0110, T-0125 |
| R6 | **Rename the `validation` *module* role → `core/verification/` engine; keep a thin `validation` register** | It is listed as both a Phase-0 module and the `run_checks` substrate; the dual role confuses placement | S | P2 | validation; T-0016; T-0124 |
| R7 | **Freeze `asset_management` + `documents`(VFS) + `verification` public `service.py` as contracts at end of Phase 2** before breaking down E-03..E-07 | They are the top-3 fan-in hotspots; the plan already says freeze the backbone — make it an explicit sign-off gate | M | P1 | plan §9; asset_management, documents, verification; Phases 3–6 |
| R8 | **Pick `approval_routes`, drop `enterprise_workflows`** before Phase 6 | The spec declares them duplicates ("pick one engine"); two approval engines is a maintenance tax | S | P2 | enterprise_workflows, approval_routes; E-06-07 |
| R9 | **Run the CI suite twice (SQLite + real PG16 as NOSUPERUSER NOBYPASSRLS)** and add the 4 contract-test families to `ci-gate` | RLS isolation must be exercised not assumed; one metadata test guards all 94 modules | M | P1 | CI; plan §8; TD-007 |
| R10 | **Put JWT behind an `AuthProvider` seam in `core/auth/` (TD-003 default)** | SSO/Entra slots in later without touching modules; auth already localised | S | P2 | TD-003; core/auth; T-0007 |
| R11 | **Keep `oe_` table prefix + per-tenant unique constraints; i18n `t()` wired from T-0024** | Consistency with every entity spec; `terms` renaming rides the i18n mechanism even single-company | S | P2 | all modules; T-0024; TD-002 §2.6 |
| R12 | **Drop the as-built entitlement/billing loader gate; replace with per-project enablement** | Single-company (TD-007) — modules are enabled per project, not entitled per tenant | S | P2 | module_loader (T-0009); TD-007 |

---

## 3. Conflicts & risks

- **`validation` is specced as both a module and the verification substrate** (MODULE-SPECS §1 vs plan §5). Not a hard-rule violation but a structural ambiguity that will scatter `run_checks` logic. Resolve per R6 before T-0016.
- **Two no-code designers violate the spirit of "one change, one place"** even though neither breaks a hard rule. TD-002 is the fix; leaving it unresolved *is* the risk (and the board already blocks T-0102/T-0109/T-0110/E-01-01 on it, so Phase 1 cannot complete cleanly without a decision).
- **`workpacks`/`awp` fan-in (13+ deps each)** is the cycle-risk zone. They are orchestration layers by design (MODULE-SPECS §14) — the risk is a future agent adding a table that another module reads, turning orchestration into coupling. Mitigate with the import-lint (R4) and a "no tables other modules read" rule on layer-2 modules.
- **`wps` ↔ `inspections`/`qms`/`construction_control` mutual need** (weld rules vs inspection record) is a latent cycle. Resolve by rule-set-as-data (`weld.accept` owned by `wps`, *run* by inspections) — do **not** let them import each other's models.
- **Entitlement gate removal (R12)** is a deliberate departure from the as-built reference. Record it as an ADR so a later agent doesn't "restore" SaaS billing that KERP (single-company) doesn't want.
- **Shared TS/Python evaluator (TD-002 foundation)** is real engineering with a parity-test burden. If under-budgeted it becomes the Phase-1 bottleneck. Sequence it first (PROPOSAL §4) and treat it as a foundation task, not a line item.
- **Anchor-model drift** is the quiet risk across 94 modules: a module adding a BOQ-style anchor or an FK into a system-of-record table. Make `manifest.anchors` + an anchor-contract test machine-check it (R4), because prose rules (hard rules 3/4/6/7) are not self-enforcing at this scale.

---

## 4. Open questions for the owner

1. **TD-002 (blocking):** Adopt the one-engine collapse now? My structural recommendation is **yes, before Phase 1 implementation** (R1). This is the decision that unblocks T-0102/T-0109/T-0110/E-01-01 and determines whether you build one designer or two. — *existing TD-002*
2. **Should CLAUDE.md §6 be amended to name the `core/` platform layer** explicitly (R2), or do you want `core/*` to stay implicit? I recommend naming it — the Phase-0 tasks already build it. — *propose new TD (module-contract two-layer split)*
3. **Keep `oe_` table prefix** inherited from OpenBusinessERP, or adopt a KERP-native prefix (`kerp_`)? Cosmetic but one-way; the specs all assume `oe_`. I'd keep `oe_` to avoid churning every entity spec. — *propose new TD if you want to change it*
4. **TD-003 auth:** confirm the `AuthProvider` seam (local JWT now, SSO later) is acceptable as the frozen seam, so modules never see auth internals. — *existing TD-003*
5. **`enterprise_workflows` vs `approval_routes`:** confirm dropping `enterprise_workflows` (R8) — the spec says pick one but doesn't say which. — *propose new TD*

---

## 5. Feeds-the-plan

Concrete items the coordinator can lift directly:

1. **Backend tree + two-layer contract (§1.1) and the 7-file module template (§1.2)** become the normative scaffold in T-0009; add `permissions.py` to the mandated file list and `layer`/`provides_rule_sets`/`anchors` to the manifest.
2. **`scripts/new_module.py` generator** (T-0009 deliverable) emitting the skeleton; reviewer-gate rejects deviations.
3. **Five single-owner seam services** with frozen signatures: `run_checks` (T-0016, `core/verification/`), VFS `route_and_store` (E-05-01, `documents`), `eligibility.check` (TD-002), `core/fields/` registry+evaluator (T-0110), `sync.apply` (T-0125). Each gets a "no cross-module table reads" review rule.
4. **Import-lint gate** (sibling to T-0023 lazy-import lint): a module may import `core/*`, itself, and declared `depends` schemas only — the structural DAG guarantee.
5. **Four contract-test families** in `tests/contract/` wired into `ci-gate` (R9): BaseRecordMixin metadata, RLS double-run (SQLite + PG16 NOSUPERUSER), manifest validity, run_checks rule-sets.
6. **Rule-sets as data** under `rule_sets/*.yaml`, config/starter-packs under `config_packs/` — the mechanism that makes hard-rule-5 real.
7. **Backbone-freeze sign-off gate** at end of Phase 2 over `asset_management`/`documents`/`verification` public `service.py` (R7) before E-03..E-07 breakdown.
8. **Adopt TD-002** and open **ADR-0002 (one-engine)**; drop the entitlement gate (ADR note, R12); confirm `AuthProvider` seam (R10).

*Written against the greenfield repo (no `backend/`/`frontend/` on disk) from the authoritative docs; the as-built Inspection-ERP suite (`architecture-suite-reference/01–02`) is treated as the pattern source to port, not KERP scope.*
