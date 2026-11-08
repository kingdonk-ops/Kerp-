# KERP — Task board

> **Generated** by `python scripts/tracker.py render` from `tracker/tasks/*.md` — do not edit by hand.
> ⬜ todo · ▶ ready to start · 🟦 in progress · 🟪 in review · ✅ done · 🟥 blocked · 🧩 epic awaiting breakdown · ⚫ dropped

**Overall:** 0/66 tasks done (0%) · **ready now:** 2 · **epics awaiting breakdown:** 40

| Phase | Tasks done | Progress |
|---|---|---|
| 0 · Platform foundations | 0/27 | `░░░░░░░░░░` 0% |
| 1 · Asset & Quality backbone | 0/31 (+1 epics) | `░░░░░░░░░░` 0% |
| 2 · Welding quality | 0/8 | `░░░░░░░░░░` 0% |
| 3 · Logistics | 0/0 (+7 epics) | `░░░░░░░░░░` 0% |
| 4 · Execution | 0/0 (+10 epics) | `░░░░░░░░░░` 0% |
| 5 · Documents, collaboration & AI | 0/0 (+10 epics) | `░░░░░░░░░░` 0% |
| 6 · Defense, claims & controls | 0/0 (+7 epics) | `░░░░░░░░░░` 0% |
| 6b · Work packaging (AWP) | 0/0 (+2 epics) | `░░░░░░░░░░` 0% |
| 7 · Client portal | 0/0 (+3 epics) | `░░░░░░░░░░` 0% |

## Phase 0 — Platform foundations

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| ▶ | [T-0001](../tracker/tasks/T-0001.md) | Backend scaffold: FastAPI app factory, settings, /health | platform | S | — |  |
| ▶ | [T-0002](../tracker/tasks/T-0002.md) | Frontend scaffold: Vite + React 18 + TS strict, Tailwind, TanStack Query, router, i18n, Vitest | platform | S | — |  |
| ⬜ | [T-0003](../tracker/tasks/T-0003.md) | Dev infrastructure: docker-compose (Postgres16+PostGIS, Redis7, MinIO), Makefile, .env.example | platform | S | T-0001 |  |
| ⬜ | [T-0004](../tracker/tasks/T-0004.md) | Backend Dockerfile (multi-stage, --only-binary) + CI image build to GHCR | platform | S | T-0001 |  |
| ⬜ | [T-0005](../tracker/tasks/T-0005.md) | Database layer + BaseRecordMixin + Alembic + metadata contract test | platform | M | T-0001 |  |
| ⬜ | [T-0006](../tracker/tasks/T-0006.md) | Tenancy + RLS: tenant context from JWT, RLS policy helper, cross-tenant→404 contract test | tenancy | M | T-0005 |  |
| ⬜ | [T-0007](../tracker/tasks/T-0007.md) | Auth: users, password hashing, JWT access/refresh, login/logout; claims rehydrated from DB | users | M | T-0006 |  |
| ⬜ | [T-0008](../tracker/tasks/T-0008.md) | Three-tier RBAC: capability tiers × 13 persona presets × per-user project/site scope | users | M | T-0007 |  |
| ⬜ | [T-0009](../tracker/tasks/T-0009.md) | Module loader + manifest contract + per-project enable/disable | platform | M | T-0005 |  |
| ⬜ | [T-0010](../tracker/tasks/T-0010.md) | projects module: project CRUD + Project Settings (currency/date/locale/tax) | projects | S | T-0008, T-0009 |  |
| ⬜ | [T-0011](../tracker/tasks/T-0011.md) | teams module: teams, membership, entity visibility | teams | S | T-0010 |  |
| ⬜ | [T-0012](../tracker/tasks/T-0012.md) | admin module: tenant/user/role administration API | admin | S | T-0008, T-0009 |  |
| ⬜ | [T-0013](../tracker/tasks/T-0013.md) | Audit log + record hash chain + provenance middleware | audit | M | T-0006 |  |
| ⬜ | [T-0014](../tracker/tasks/T-0014.md) | Jobs: ARQ worker + Redis, jobs module (enqueue/status/poll), worker container | jobs | M | T-0003, T-0009 |  |
| ⬜ | [T-0015](../tracker/tasks/T-0015.md) | Storage service + presigned uploads (presign/complete, sha256 verify, scan stub, orphan sweep) | uploads | M | T-0014 |  |
| ⬜ | [T-0016](../tracker/tasks/T-0016.md) | Verification engine shell: run_checks(context, rule_set), rule sets as data, findings log | validation | M | T-0013 |  |
| ⬜ | [T-0017](../tracker/tasks/T-0017.md) | notifications module (in-app) | notifications | S | T-0010 |  |
| ⬜ | [T-0018](../tracker/tasks/T-0018.md) | saved_views module | saved_views | S | T-0010 |  |
| ⬜ | [T-0019](../tracker/tasks/T-0019.md) | client_errors + global_presence modules | platform | S | T-0010 |  |
| ⬜ | [T-0020](../tracker/tasks/T-0020.md) | backup module: tenant export/restore skeleton | backup | M | T-0015 |  |
| ⬜ | [T-0021](../tracker/tasks/T-0021.md) | onboarding: first-run provisioning (tenant, admin, default project) | onboarding | S | T-0010, T-0012 |  |
| ⬜ | [T-0022](../tracker/tasks/T-0022.md) | Observability + security headers (request id, structured logs, CSP/HSTS) | platform | S | T-0001 |  |
| ⬜ | [T-0023](../tracker/tasks/T-0023.md) | Lint rule: heavy libs (fitz, trimesh, cv2, pyarrow) must be lazily imported | platform | S | T-0001 |  |
| ⬜ | [T-0024](../tracker/tasks/T-0024.md) | Frontend shell: auth flow, design tokens, sidebar (4 pillars), top bar, generated API client | frontend | M | T-0002, T-0007 |  |
| ⬜ | [T-0025](../tracker/tasks/T-0025.md) | Admin UI: user manager, roles/personas, project/site scope assignment | frontend | M | T-0024, T-0012 |  |
| ⬜ | [T-0026](../tracker/tasks/T-0026.md) | Playwright setup + Phase 0 exit journey | e2e | S | T-0021, T-0024, T-0016 |  |
| 🟥 | [T-0027](../tracker/tasks/T-0027.md) | module_builder: write a buildable spec (spec-gap) — no code *(blocked by TD-005)* | module_builder | S | T-0009 |  |

## Phase 1 — Asset & Quality backbone

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| ⬜ | [T-0101](../tracker/tasks/T-0101.md) | Asset hierarchy: oe_asset_node (Site→Area→Asset→sub-Asset*→RSW), tree API | asset_management | M | T-0010, T-0016 |  |
| ⬜ | [T-0102](../tracker/tasks/T-0102.md) | item_types: asset types on the shared registry — inheritance, attribute registry, status sets | asset_management | M | T-0101, T-0110 |  |
| ⬜ | [T-0103](../tracker/tasks/T-0103.md) | Items (oe_asset_item): typed inspectable items on assets | asset_management | M | T-0102 |  |
| ⬜ | [T-0104](../tracker/tasks/T-0104.md) | P6 .xer import: parser in ARQ → staging → hierarchy mapping (idempotent on Activity ID) | asset_management | M | T-0101, T-0014 |  |
| ⬜ | [T-0105](../tracker/tasks/T-0105.md) | P6 Primavera XML import variant | asset_management | S | T-0104 |  |
| ⬜ | [T-0106](../tracker/tasks/T-0106.md) | P6 export: progress actuals file keyed on Activity ID (non-destructive re-import) | asset_management | M | T-0104 |  |
| ⬜ | [T-0107](../tracker/tasks/T-0107.md) | Excel/CSV bulk import wizard + manual ingest | asset_management | M | T-0103 |  |
| ⬜ | [T-0108](../tracker/tasks/T-0108.md) | Default configuration packs provisioned at project creation | asset_management | M | T-0102 |  |
| ⬜ | [T-0109](../tracker/tasks/T-0109.md) | item_types: versions, publish with migration preview (impact counts), migrate; starter packs unified with form seeds | asset_management | M | T-0102, T-0131 |  |
| ⬜ | [T-0111](../tracker/tasks/T-0111.md) | Inspection record anchored at Asset/sub-Asset/RSW/Item + lifecycle | inspections | M | T-0103, T-0131 |  |
| ⬜ | [T-0112](../tracker/tasks/T-0112.md) | Global inspections register: cross-level filters + batch PDF/Excel export (ARQ) | inspections | M | T-0111 |  |
| ⬜ | [T-0113](../tracker/tasks/T-0113.md) | qms: ITP plans/templates + audit register | qms | M | T-0111 |  |
| ⬜ | [T-0114](../tracker/tasks/T-0114.md) | construction_control: hold/witness gates + conformance criteria | construction_control | M | T-0113 |  |
| ⬜ | [T-0115](../tracker/tasks/T-0115.md) | ncr: Non-Conformance Reports anchored to Asset/RSW/Item | ncr | S | T-0111 |  |
| ⬜ | [T-0116](../tracker/tasks/T-0116.md) | punchlist: punch/snag items | punchlist | S | T-0111 |  |
| ⬜ | [T-0117](../tracker/tasks/T-0117.md) | requirements: requirements & quality gates | requirements | S | T-0016, T-0101 |  |
| ⬜ | [T-0118](../tracker/tasks/T-0118.md) | review_authority: external review cycles | review_authority | S | T-0111 |  |
| ⬜ | [T-0119](../tracker/tasks/T-0119.md) | project_route: work-type routing gate | project_route | S | T-0016 |  |
| ⬜ | [T-0120](../tracker/tasks/T-0120.md) | credentials: professional credentials register + expiry | credentials | S | T-0010 |  |
| ⬜ | [T-0121](../tracker/tasks/T-0121.md) | signing: e-signature abstraction (internal sign-off now, provider seam later) | signing | M | T-0013 |  |
| ⬜ | [T-0122](../tracker/tasks/T-0122.md) | source_data: prerequisite-documents register | source_data | S | T-0101 |  |
| ⬜ | [T-0123](../tracker/tasks/T-0123.md) | compliance / compliance_ai / compliance_docs DSL wired into run_checks | compliance | M | T-0016 |  |
| ⬜ | [T-0124](../tracker/tasks/T-0124.md) | Rule sets: asset.status_change, inspection.close, rsw.complete, asset.p6_fields, schedule.p6_import/export | validation | M | T-0111, T-0114, T-0115, T-0104 |  |
| ⬜ | [T-0125](../tracker/tasks/T-0125.md) | Offline sync core: idempotent sync ledger (client op id → result) + conflict replay | sync | M | T-0013 |  |
| ⬜ | [T-0126](../tracker/tasks/T-0126.md) | Frontend: useOfflineSync tier-1 (IndexedDB queue) + offline indicator | frontend | M | T-0125, T-0024 |  |
| ⬜ | [T-0127](../tracker/tasks/T-0127.md) | Frontend: asset tree + Asset-Details (attributes, items, inspections tab over descendants) | frontend | M | T-0101, T-0103, T-0024 |  |
| ⬜ | [T-0128](../tracker/tasks/T-0128.md) | Frontend: P6 import wizard UI (upload, map, unmatched rows, commit) | frontend | M | T-0104, T-0127 |  |
| ⬜ | [T-0129](../tracker/tasks/T-0129.md) | Frontend: inspections register + raise/close flow with gate findings surfaced | frontend | M | T-0112, T-0124, T-0127 |  |
| ⬜ | [T-0130](../tracker/tasks/T-0130.md) | Phase 1 exit journey: P6 → hierarchy → inspection gates → completion blocked by open NCR | e2e | S | T-0128, T-0129 |  |
| 🧩 | [E-01-01](../tracker/tasks/E-01-01.md) | Request-for-Inspection module + ported AIP capabilities | inspection_requests | L | T-0111 |  |
| ⬜ | [T-0110](../tracker/tasks/T-0110.md) | forms engine core: shared field-type registry + sandboxed evaluator (Python, TS-parity fixtures) | forms | M | T-0016 |  |
| ⬜ | [T-0131](../tracker/tasks/T-0131.md) | forms templates + revisions: kinds, draft→approved→retired, publish freezes schema (schema_hash + trigger), forms.publish lint | forms | M | T-0110 |  |

## Phase 2 — Welding quality

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| ⬜ | [T-0201](../tracker/tasks/T-0201.md) | WPS register (oe_wps_record) + approval status + document link | wps | M | T-0103, T-0015 |  |
| ⬜ | [T-0202](../tracker/tasks/T-0202.md) | Welders + welder qualifications (range, expiry, continuity) | wps | M | T-0201, T-0120 |  |
| ⬜ | [T-0203](../tracker/tasks/T-0203.md) | Welds as Items (type weld) + weld state machine | wps | M | T-0202 |  |
| ⬜ | [T-0204](../tracker/tasks/T-0204.md) | NDE results (oe_weld_nde_result) | wps | S | T-0203 |  |
| ⬜ | [T-0205](../tracker/tasks/T-0205.md) | Rules R-WPS-1..4 as rule sets weld.accept / welder.assign + logged findings | wps | M | T-0204, T-0124 |  |
| ⬜ | [T-0206](../tracker/tasks/T-0206.md) | Continuity watch report + expiry notifications (ARQ cron) | wps | S | T-0205, T-0017 |  |
| ⬜ | [T-0207](../tracker/tasks/T-0207.md) | Frontend: WPS register, welder quals, weld register | frontend | M | T-0205, T-0127 |  |
| ⬜ | [T-0208](../tracker/tasks/T-0208.md) | Phase 2 exit journey: weld cannot be accepted outside qualification/NDE rules | e2e | S | T-0207 |  |

## Phase 3 — Logistics

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| 🧩 | [E-03-01](../tracker/tasks/E-03-01.md) | contacts: party master + people/company directory (crm folded in) | contacts | L | T-0010 |  |
| 🧩 | [E-03-02](../tracker/tasks/E-03-02.md) | site_logistics: deliveries, gate receipt, OS&D vs ERP-mirrored PO lines (+ Gate Delivery edge screen) | site_logistics | L | T-0101, T-0126 |  |
| 🧩 | [E-03-03](../tracker/tasks/E-03-03.md) | site_inventory: append-only movements ledger | site_inventory | L | T-0101 |  |
| 🧩 | [E-03-04](../tracker/tasks/E-03-04.md) | cutting: bar/sheet nesting, yield/waste, offcut reuse, cutting.confirm → inventory | cutting | L | E-03-03 |  |
| 🧩 | [E-03-05](../tracker/tasks/E-03-05.md) | procurement / supplier_catalogs / subcontractors (basic, external contract refs) | procurement | L | E-03-01 |  |
| 🧩 | [E-03-06](../tracker/tasks/E-03-06.md) | equipment & fleet register | equipment | L | T-0101 |  |
| 🧩 | [E-03-07](../tracker/tasks/E-03-07.md) | resource_summary: demand rollup from tasks/look-ahead with required-by dates | resource_summary | L | E-04-04 |  |

## Phase 4 — Execution

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| 🧩 | [E-04-01](../tracker/tasks/E-04-01.md) | schedule: field programme (P6-synced, RSW-linked) | schedule | L | T-0106 |  |
| 🧩 | [E-04-02](../tracker/tasks/E-04-02.md) | schedule_advanced: Last Planner look-ahead → PPC + Look-Ahead Board | schedule_advanced | L | E-04-01 |  |
| 🧩 | [E-04-03](../tracker/tasks/E-04-03.md) | progress: physical earned quantity against RSW/Item, gated, feeding P6 export | progress | L | E-04-01, T-0205 |  |
| 🧩 | [E-04-04](../tracker/tasks/E-04-04.md) | tasks: generic tasks lodged at RSW | tasks | L | T-0101 |  |
| 🧩 | [E-04-05](../tracker/tasks/E-04-05.md) | daily_diary + field_diary + Daily Workface Log (offline) | daily_diary | L | T-0126 |  |
| 🧩 | [E-04-06](../tracker/tasks/E-04-06.md) | field_time: hours capture + export to corporate payroll | field_time | L | T-0126 |  |
| 🧩 | [E-04-07](../tracker/tasks/E-04-07.md) | fieldreports / voice / phonelog | fieldreports | L | T-0015 |  |
| 🧩 | [E-04-08](../tracker/tasks/E-04-08.md) | site_prep / site_supervision / commissioning / closeout / defects_liability / temporary_works | site_supervision | L | T-0124 |  |
| 🧩 | [E-04-09](../tracker/tasks/E-04-09.md) | timeline / deadlines / prefab / resources | timeline | L | E-04-01 |  |
| 🧩 | [E-04-10](../tracker/tasks/E-04-10.md) | safety + hse_advanced (+ safety KPI ingestion) | safety | L | T-0010 |  |

## Phase 5 — Documents, collaboration & AI

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| 🧩 | [E-05-01](../tracker/tasks/E-05-01.md) | documents: CDE + Virtual File System (folders, routing, links, orphans, lockers) | documents | L | T-0015 |  |
| 🧩 | [E-05-02](../tracker/tasks/E-05-02.md) | file_* subsystem (versions, approvals, comments, distribution, tags, trash…) | documents | L | E-05-01 |  |
| 🧩 | [E-05-03](../tracker/tasks/E-05-03.md) | markups + plan_room | markups | L | E-05-01 |  |
| 🧩 | [E-05-04](../tracker/tasks/E-05-04.md) | transmittals + correspondence | transmittals | L | E-05-01 |  |
| 🧩 | [E-05-05](../tracker/tasks/E-05-05.md) | meetings / rfi / submittals / interface_management | rfi | L | E-05-01 |  |
| 🧩 | [E-05-06](../tracker/tasks/E-05-06.md) | collaboration + collaboration_locks | collaboration | L | E-05-01 |  |
| 🧩 | [E-05-07](../tracker/tasks/E-05-07.md) | integrations ingestion surface (P6/PO-lines/KPI webhooks) + connectors / inbound_email / inbound_capture / data_import / pipelines | integrations | L | T-0106 |  |
| 🧩 | [E-05-08](../tracker/tasks/E-05-08.md) | record_publishing + authority_submission | record_publishing | L | E-05-01 |  |
| 🧩 | [E-05-09](../tracker/tasks/E-05-09.md) | search / retrieval: OCR of dockets → full-text, vector index (ARQ) | search | L | E-05-01 |  |
| 🧩 | [E-05-10](../tracker/tasks/E-05-10.md) | ai_agents + erp_chat operational tools (proposals only, human-confirmed) | erp_chat | L | E-05-09 |  |

## Phase 6 — Defense, claims & controls

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| 🧩 | [E-06-01](../tracker/tasks/E-06-01.md) | variations / changeorders / moc / change_intelligence (re-anchored, external commercial mirror) | variations | L | E-04-03 |  |
| 🧩 | [E-06-02](../tracker/tasks/E-06-02.md) | claims_evidence / reconciliation / cost_recovery (physical) | claims_evidence | L | E-06-01 |  |
| 🧩 | [E-06-03](../tracker/tasks/E-06-03.md) | physical EVM: costmodel / eac / full_evm / project_controls | costmodel | L | E-04-03 |  |
| 🧩 | [E-06-04](../tracker/tasks/E-06-04.md) | reporting / dashboard(s) / bi_dashboards (rollups in ARQ → summary tables) | reporting | L | E-06-03 |  |
| 🧩 | [E-06-05](../tracker/tasks/E-06-05.md) | project_intelligence + risk | risk | L | E-06-04 |  |
| 🧩 | [E-06-06](../tracker/tasks/E-06-06.md) | carbon / esg / service | carbon | L | T-0101 |  |
| 🧩 | [E-06-07](../tracker/tasks/E-06-07.md) | cases (persona walkthroughs) / enterprise_workflows / approval_routes | cases | L | T-0016 |  |

## Phase 6b — Work packaging (AWP)

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| 🧩 | [E-06b-01](../tracker/tasks/E-06b-01.md) | awp: EWP→PWP→CWP→IWP constraint engine + Technical Queries | awp | L | E-05-01, E-04-01, E-03-03 |  |
| 🧩 | [E-06b-02](../tracker/tasks/E-06b-02.md) | workpacks: live field dossier, offline stage sign-off, QR MDR PDF compile | workpacks | L | E-06b-01 |  |

## Phase 7 — Client portal

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| 🧩 | [E-07-01](../tracker/tasks/E-07-01.md) | portal v1: client read-only hierarchy + registers (magic link) | portal | L | T-0130 |  |
| 🧩 | [E-07-02](../tracker/tasks/E-07-02.md) | portal v2: client co-sign reports & ITPs (report.cosign / itp.cosign) | portal | L | E-07-01, T-0121 |  |
| 🧩 | [E-07-03](../tracker/tasks/E-07-03.md) | portal v3: visit scheduler (hold-point witness/final) + client NCR input/sign-off | portal | L | E-07-02 |  |
