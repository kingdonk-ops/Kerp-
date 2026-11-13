# KERP — Task board

> **Generated** by `python scripts/tracker.py render` from `tracker/tasks/*.md` — do not edit by hand.
> ⬜ todo · ▶ ready to start · 🟦 in progress · 🟪 in review · ✅ done · 🟥 blocked · 🧩 epic awaiting breakdown · ⚫ dropped

**Overall:** 21/139 tasks done (15%) · **ready now:** 27 · **epics awaiting breakdown:** 47

| Phase | Tasks done | Progress |
|---|---|---|
| 0 · Platform foundations | 21/55 | `███░░░░░░░` 38% |
| 1 · Asset & Quality backbone | 0/75 (+1 epics) | `░░░░░░░░░░` 0% |
| 2 · Welding quality | 0/9 | `░░░░░░░░░░` 0% |
| 3 · Logistics | 0/0 (+7 epics) | `░░░░░░░░░░` 0% |
| 4 · Execution | 0/0 (+14 epics) | `░░░░░░░░░░` 0% |
| 5 · Documents, collaboration & AI | 0/0 (+10 epics) | `░░░░░░░░░░` 0% |
| 6 · Defense, claims & controls | 0/0 (+8 epics) | `░░░░░░░░░░` 0% |
| 6b · Work packaging (AWP) | 0/0 (+3 epics) | `░░░░░░░░░░` 0% |
| 7 · Client portal | 0/0 (+4 epics) | `░░░░░░░░░░` 0% |

## Phase 0 — Platform foundations

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| ✅ | [T-0001](../tracker/tasks/T-0001.md) | Backend scaffold: FastAPI app factory, settings, /health | platform | S | — | #7 |
| ✅ | [T-0002](../tracker/tasks/T-0002.md) | Frontend scaffold: Vite + React 18 + TS strict, Tailwind, TanStack Query, Dexie, router, i18n, Vitest | platform | S | — | #8 |
| ✅ | [T-0003](../tracker/tasks/T-0003.md) | Dev infra: docker-compose (PG16+PostGIS+ltree, Redis7, MinIO), Makefile, .env.example | platform | S | T-0001 | #33 |
| ✅ | [T-0004](../tracker/tasks/T-0004.md) | Backend Dockerfile (multi-stage, --only-binary) + CI image build to GHCR | platform | S | T-0001 | #11 |
| ✅ | [T-0005](../tracker/tasks/T-0005.md) | Database layer + BaseRecordMixin + Alembic + metadata contract test | platform | M | T-0001 | #13 |
| ✅ | [T-0006](../tracker/tasks/T-0006.md) | Hash-chain audit ledger: append-only (seq, chain_key, prev_hash, content_hash, payload) + REVOKE UPDATE/DELETE | audit | M | T-0005 | #20 |
| ✅ | [T-0007](../tracker/tasks/T-0007.md) | Project-scoped RLS: project_id context, policy helper, cross-project→404, 4 RLS-bypass contract tests | tenancy | M | T-0005 | #19 |
| ✅ | [T-0008](../tracker/tasks/T-0008.md) | Auth: users, RS256/EdDSA JWT + JWKS endpoint, 15-min access + rotating refresh, jti revocation in Redis | identity | M | T-0007 | #31 |
| ▶ | [T-0037](../tracker/tasks/T-0037.md) | Auth sessions: hidden deployment tenant at login, 12-hour refresh from sign-in (daily re-login) | identity | S | T-0009 |  |
| ✅ | [T-0009](../tracker/tasks/T-0009.md) | Field-worker auth: magic-link + 6-digit PIN local unlock + progressive lockout | identity | M | T-0008 | #35 |
| ⬜ | [T-0038](../tracker/tasks/T-0038.md) | Field PIN hybrid rework: PIN per enrolled device, server PIN only on renewal after idle, lockout counter per device | identity | M | T-0043 |  |
| ⬜ | [T-0043](../tracker/tasks/T-0043.md) | Device registry: enrolment, remote revoke with wipe signal, device_id on sessions and in the audit ledger (Pilot-0) | identity | M | T-0037, T-0014 |  |
| ✅ | [T-0010](../tracker/tasks/T-0010.md) | Three-tier RBAC/ABAC: capability tiers × 15 personas × project/site scope (Asset-Site filter) + explicit multi-project read scope | identity | M | T-0008 | #39 |
| ✅ | [T-0011](../tracker/tasks/T-0011.md) | Module loader + manifest contract + per-project enable/disable | platform | M | T-0005 | #32 |
| ✅ | [T-0012](../tracker/tasks/T-0012.md) | projects module: CRUD + settings (IANA timezone, spatial_bounds, currency/locale) | projects | M | T-0010, T-0011 | #43 |
| ▶ | [T-0013](../tracker/tasks/T-0013.md) | teams module: teams, membership, entity visibility | teams | S | T-0012 |  |
| ▶ | [T-0014](../tracker/tasks/T-0014.md) | admin module: user + project-membership administration API (PER-09 on its projects), admin grants table, membership-based RLS on projects and tenant-level tables | admin | M | T-0010, T-0011, T-0012 |  |
| ⬜ | [T-0039](../tracker/tasks/T-0039.md) | System admin appoints Project Admins: system_admin/create_projects grant API, project creation gated on create_projects, creator gets PER-09 | admin | S | T-0014 |  |
| ⬜ | [T-0044](../tracker/tasks/T-0044.md) | Deployment (tenant) settings: offline window (default 7, max 14 days) and new-project defaults (status, timezone, currency, locale) | admin | S | T-0039 |  |
| ✅ | [T-0015](../tracker/tasks/T-0015.md) | SAQ worker + Redis + jobs module (enqueue/status/poll), interactive+batch priority queues | jobs | M | T-0003, T-0011 | #40 |
| ✅ | [T-0016](../tracker/tasks/T-0016.md) | Storage service + presigned uploads (presign/complete, sha256 verify, scan stub, orphan sweep) | uploads | M | T-0015 | #49 |
| ✅ | [T-0017](../tracker/tasks/T-0017.md) | CEL runtime substrate: celpy (server) + JS CEL (client) + shared conformance_fixtures.json harness | platform | M | T-0001, T-0002 | #15 |
| ✅ | [T-0018](../tracker/tasks/T-0018.md) | Verification engine shell + single rule store: run_checks(context, rule_set), oe_validation_rule_set/rule (kinds, engines), compile-on-save, findings log | validation | M | T-0006, T-0017 | #36 |
| ▶ | [T-0034](../tracker/tasks/T-0034.md) | Rule store layers: system defaults → tenant seed → project copy, project edit/disable/delete/add/reset, locked rules + two-person unlock | validation | M | T-0018, T-0010, T-0011, T-0012 |  |
| ⬜ | [T-0036](../tracker/tasks/T-0036.md) | Rule store: default-upgrade offers with per-rule diff, tenant drift report, 'Try it' evaluation | validation | S | T-0034 |  |
| ▶ | [T-0019](../tracker/tasks/T-0019.md) | notifications module (in-app) behind a pluggable channel interface | notifications | S | T-0012 |  |
| ⬜ | [T-0035](../tracker/tasks/T-0035.md) | Per-record override of a blocked check: reasoned request, routed approver (never self), notifications, audit, bound to the checked values | validation | M | T-0018, T-0010, T-0019 |  |
| ⬜ | [T-0040](../tracker/tasks/T-0040.md) | Lobby and access requests API: visible-in-lobby flag, lobby listing, project-code lookup, request/approve/reject by the project's admins | admin | M | T-0014, T-0019 |  |
| ▶ | [T-0020](../tracker/tasks/T-0020.md) | saved_views module | saved_views | S | T-0012 |  |
| ✅ | [T-0021](../tracker/tasks/T-0021.md) | Observability + security headers (request id, structured logs, CSP/HSTS) | platform | S | T-0001 | #9 |
| ✅ | [T-0022](../tracker/tasks/T-0022.md) | Lint rule: heavy libs (fitz, cv2, rectpack, celpy, pyhanko) lazily imported | platform | S | T-0001 | #10 |
| ⬜ | [T-0023](../tracker/tasks/T-0023.md) | onboarding: first-run provisioning (the deployment's hidden tenant, first System admin, deployment settings) | onboarding | S | T-0012, T-0014, T-0044 |  |
| ✅ | [T-0024](../tracker/tasks/T-0024.md) | Frontend shell: auth, design tokens, 8-group nav (4 pillars + Backbone/Overview/Documents/Admin), project switcher + All-Projects, generated API client | frontend | M | T-0002, T-0008 | #38 |
| ▶ | [T-0046](../tracker/tasks/T-0046.md) | Field ergonomics tokens: 48 dp glove touch targets and an outdoor high-contrast theme (WCAG 2.1 AA) | frontend | S | T-0024 |  |
| ✅ | [T-0025](../tracker/tasks/T-0025.md) | Reusable UI shells: RegisterPage, DetailPage (gate banner, tabs, audit), Dynamic Form Renderer | frontend | M | T-0024, T-0017 | #42 |
| ▶ | [T-0045](../tracker/tasks/T-0045.md) | NFR budget harness: warn-only CI performance job + 300-question form-open budget (< 1.5 s) | frontend | S | T-0025 |  |
| ▶ | [T-0047](../tracker/tasks/T-0047.md) | Dynamic Form Renderer: localised template strings (plain string or per-locale map, fallback to the template default language) | frontend | S | T-0025 |  |
| ⬜ | [T-0026](../tracker/tasks/T-0026.md) | Admin UI: System admin grants (appoint Project Admins), user manager, project creation, persona + site scope assignment with SoD warning | frontend | M | T-0024, T-0014, T-0039 |  |
| ⬜ | [T-0041](../tracker/tasks/T-0041.md) | Lobby and access-request screens: lobby list, project-code entry, request status; admin access-request inbox | frontend | M | T-0040, T-0042 |  |
| ⬜ | [T-0042](../tracker/tasks/T-0042.md) | Top-bar project picker wired to memberships + post-login landing (one project -> in, several -> last used, none -> lobby) | frontend | S | T-0014, T-0024 |  |
| ⬜ | [T-0048](../tracker/tasks/T-0048.md) | Devices screen: list enrolled devices, revoke with wipe warning (admin and own devices) | frontend | S | T-0043, T-0024 |  |
| ⬜ | [T-0027](../tracker/tasks/T-0027.md) | Playwright setup + Phase 0 exit journey | e2e | S | T-0023, T-0024, T-0018 |  |
| ✅ | [T-0028](../tracker/tasks/T-0028.md) | CI hardening: detect in ci-gate needs, least-privilege GHCR publish, SHA-pinned actions, base image digest + Renovate, drop empty postgres extra | platform | S | T-0004 | #18 |
| ▶ | [T-0032](../tracker/tasks/T-0032.md) | CI hardening 2: weekly uv lock refresh PR, SHA-pinned promote/review-gate actions, digest-pinned service images, publish the smoke-tested image by digest | platform | S | T-0028 |  |
| ▶ | [T-0033](../tracker/tasks/T-0033.md) | Migrations: inline frozen RLS/ledger DDL per revision + test that the latest revision matches the live helpers | platform | S | T-0006, T-0007 |  |
| ▶ | [T-0029](../tracker/tasks/T-0029.md) | review-gate: include tracker/ in the patch-id (normalise bookkeeping fields, exclude generated boards) | platform | S | — |  |
| ▶ | [T-0030](../tracker/tasks/T-0030.md) | Backend hardening: httpx/httpcore loggers at WARNING; hide /docs, /redoc, /openapi.json in production | platform | S | T-0021 |  |
| ▶ | [T-0031](../tracker/tasks/T-0031.md) | Frontend ESLint (typescript-eslint, react-hooks) + npm run lint in the CLAUDE.md §4 gate and CI | platform | S | T-0002 |  |
| ▶ | [T-0060](../tracker/tasks/T-0060.md) | Request size bounds: app-wide body limit, total-position cap on spatial_bounds, jobs payload cap, list-safe job result + jobs scope tests | platform | S | T-0012, T-0015 |  |
| ▶ | [T-0061](../tracker/tasks/T-0061.md) | Auth: reject tokens issued in the same second as a password change (sub-second iat vs password_changed_at) | identity | S | T-0008 |  |
| ▶ | [T-0062](../tracker/tasks/T-0062.md) | UI shells hardening: hidden-all-columns guard, encoded storage keys, required_if a11y, tab keyboard/aria, reserved audit tab id check | frontend | S | T-0025 |  |
| ▶ | [T-0063](../tracker/tasks/T-0063.md) | Projects follow-ups: constraint-name duplicate detection, missing authorisation tests, GeoJSON precision, DB-side defaults | projects | S | T-0012 |  |
| ▶ | [T-0064](../tracker/tasks/T-0064.md) | Logging and config safety: ASCII-safe JSON logs, client disconnect not logged as 500, refuse debug in production | platform | S | T-0021 |  |
| ▶ | [T-0065](../tracker/tasks/T-0065.md) | Worker RLS binding test uses the real asyncio/SAQ worker path instead of threading.Thread | jobs | S | T-0015 |  |
| ▶ | [T-0066](../tracker/tasks/T-0066.md) | tracker.py set --note: do not double the date when the note already starts with one; clean existing doubled notes | platform | S | — |  |

## Phase 1 — Asset & Quality backbone

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| ▶ | [T-0101](../tracker/tasks/T-0101.md) | Asset hierarchy: oe_asset_level_def (roles) + oe_asset_node ltree tree (GiST, parent_id source of truth, display_path, PostGIS), per-path work-anchor invariant, code uniqueness, subtree move, tree API | asset_management | M | T-0012, T-0018 |  |
| ⬜ | [T-0134](../tracker/tasks/T-0134.md) | Asset restructure with lineage: split / merge / retire, invariant re-checks, roll-up through superseded_by / derived_from | asset_management | M | T-0101 |  |
| ⬜ | [T-0135](../tracker/tasks/T-0135.md) | Collections: job packs, shutdown windows, test packs, work plans linked to nodes many-to-many | asset_management | S | T-0101 |  |
| ⬜ | [T-0136](../tracker/tasks/T-0136.md) | Terminology layer: per-locale, plural-aware terminology packs (project → client pack → default) + frontend t() resolver | asset_management | M | T-0101, T-0024 |  |
| ⬜ | [T-0102](../tracker/tasks/T-0102.md) | Dynamic field registry: oe_asset_field_def (JSONSchema registry) + custom_fields JSONB (EAV deleted), GIN jsonb_path_ops + functional B-tree | asset_management | M | T-0101, T-0110 |  |
| ⬜ | [T-0103](../tracker/tasks/T-0103.md) | Items oe_asset_item: typed inspectable items + work_anchor_id + reserved CML stub fields (no calc engine) | asset_management | M | T-0102 |  |
| ⬜ | [T-0104](../tracker/tasks/T-0104.md) | P6 .xer import: SAQ parser → staging → hierarchy mapping via p6_grammar / p6_eligibility rules, unique (project_id, external_activity_id) | asset_management | M | T-0101, T-0015, T-0034 |  |
| ⬜ | [T-0105](../tracker/tasks/T-0105.md) | P6 Primavera XML variant + Unmatched-Row Linking Screen (backend) | asset_management | S | T-0104 |  |
| ⬜ | [T-0106](../tracker/tasks/T-0106.md) | P6 export: progress actuals keyed on Activity ID (non-destructive re-import) | asset_management | M | T-0104 |  |
| ⬜ | [T-0138](../tracker/tasks/T-0138.md) | Import mapping profiles: versioned per-project header→target maps, tree modes, update-by-ID, value parsers | asset_management | S | T-0102 |  |
| ⬜ | [T-0107](../tracker/tasks/T-0107.md) | Excel/CSV/IFC tag ingestion wizard: mapping profiles, text-safe identifiers, multi-value split, dry run, dedup, Rev A→0 revision tracking | asset_management | M | T-0103, T-0138 |  |
| ⬜ | [T-0108](../tracker/tasks/T-0108.md) | Client packs provisioned at project creation: levels + roles, terminology, item types, templates, numbering, register types, mapping profiles, rule sets | asset_management | M | T-0102, T-0034, T-0136, T-0138 |  |
| ⬜ | [T-0137](../tracker/tasks/T-0137.md) | Client-pack upgrade offers: derived_from links, 'changes available' diff, accept per item, compare view | asset_management | S | T-0108 |  |
| ▶ | [T-0110](../tracker/tasks/T-0110.md) | One-engine: shared field-type registry (core/fields) + CEL evaluator (dual-runtime) | forms | M | T-0017, T-0018 |  |
| ⬜ | [T-0111](../tracker/tasks/T-0111.md) | Unified inspection record oe_inspections_inspection (kind inspection/itp) + lifecycle + anchors by role + template header binding | inspections | M | T-0103, T-0110, T-0134 |  |
| ⬜ | [T-0140](../tracker/tasks/T-0140.md) | inspections: inspection date vs entry time, backdating reason + report flag, configurable self-approval (default off), soft-delete only | inspections | S | T-0111 |  |
| ⬜ | [T-0112](../tracker/tasks/T-0112.md) | Equipment calibration register + lifecycle + SAQ OCR cert ingest + QA-review-on-discrepancy + supersede lock + hard gate (as of inspection date) | equipment | M | T-0015, T-0016, T-0111, T-0139 |  |
| ⬜ | [T-0113](../tracker/tasks/T-0113.md) | qms: master ITP template authoring + audit registers + calibration records | qms | M | T-0111 |  |
| ⬜ | [T-0114](../tracker/tasks/T-0114.md) | construction_control: conformance criteria + hold/witness evaluation via run_checks (no own inspection table) | construction_control | M | T-0113 |  |
| 🟥 | [T-0115](../tracker/tasks/T-0115.md) | inspection_requests: RFI/call-out workflow (draft→requested→confirmed→witnessed\|waived\|no_show\|failed) + client delay attribution *(blocked by TD-045)* | inspection_requests | M | T-0111 |  |
| ⬜ | [T-0116](../tracker/tasks/T-0116.md) | ncr: Non-Conformance Reports + dispositions (scrap/rework/repair/use-as-is) anchored to any node or Item (anchor_role recorded) | ncr | S | T-0111 |  |
| ⬜ | [T-0117](../tracker/tasks/T-0117.md) | punchlist: Cat A/B/C punch items + spatial pins on drawings | punchlist | S | T-0111 |  |
| ▶ | [T-0118](../tracker/tasks/T-0118.md) | credentials: qualification-type catalogue + credential records with certificate/VOC uploads + expiry (feeds eligibility) | credentials | M | T-0012, T-0016 |  |
| ⬜ | [T-0139](../tracker/tasks/T-0139.md) | eligibility: stateless as-of-date validity service over credentials, calibration, WPS and materials registers (fail closed) | eligibility | M | T-0118 |  |
| ▶ | [T-0119](../tracker/tasks/T-0119.md) | signing: append-only hash-chained sign-off ledger (internal now; pyHanko PAdES seam later) | signing | M | T-0006 |  |
| ⬜ | [T-0120](../tracker/tasks/T-0120.md) | requirements / source_data: requirements & prerequisite-documents register | requirements | S | T-0101 |  |
| ⬜ | [T-0121](../tracker/tasks/T-0121.md) | review_authority / project_route: external review cycles + work-type routing gate | review_authority | S | T-0111 |  |
| ▶ | [T-0122](../tracker/tasks/T-0122.md) | compliance DSL wired into run_checks (CEL rule sets) | compliance | M | T-0018 |  |
| ⬜ | [T-0124](../tracker/tasks/T-0124.md) | Rule sets A — completion gates: inspection.close, work_anchor.complete, asset.status_change (open Cat A/NCR blocks) as locked defaults | validation | M | T-0111, T-0114, T-0116, T-0034, T-0139 |  |
| ⬜ | [T-0125](../tracker/tasks/T-0125.md) | Rule sets B — P6 sync: asset.p6_fields, schedule.p6_import/export | validation | S | T-0104, T-0106 |  |
| ⬜ | [T-0126](../tracker/tasks/T-0126.md) | Offline sync core: outbox_mutations, client_op_id idempotency, vector clock, conflict resolution (immutable approvals, per-field apply per ADR 0005) | sync | M | T-0006, T-0110 |  |
| ⬜ | [T-0127](../tracker/tasks/T-0127.md) | Frontend: client storage contract + Dexie (web PWA) adapter, useOfflineSync tier-1 outbox + offline indicator | frontend | M | T-0126, T-0024 |  |
| ▶ | [T-0141](../tracker/tasks/T-0141.md) | Capacitor Android build: Android 11+ (API 30) shell, signed release for managed Google Play / MDM, CI build job | frontend | M | T-0024 |  |
| ⬜ | [T-0142](../tracker/tasks/T-0142.md) | Android storage adapter: SQLCipher SQLite records + outbox and file-system media behind the client storage contract | frontend | M | T-0141, T-0127 |  |
| ⬜ | [T-0143](../tracker/tasks/T-0143.md) | Field device session: enrolment, offline PIN unlock against the keystore, offline-window enforcement, wipe on DEVICE_REVOKED | frontend | M | T-0142, T-0038, T-0043, T-0044 |  |
| ⬜ | [T-0144](../tracker/tasks/T-0144.md) | FCM push channel for the Android build: device push tokens, assignment/return pushes through the pluggable channel interface | notifications | S | T-0019, T-0043, T-0141 |  |
| ⬜ | [T-0145](../tracker/tasks/T-0145.md) | Delta-sync budget: 1,000 records pulled and applied in < 30 s on simulated 4G (warn-only in the NFR job) | sync | S | T-0126, T-0045 |  |
| ⬜ | [T-0172](../tracker/tasks/T-0172.md) | Client clock: per-action device_wall_time + monotonic_ms + clock_session_id on every outbox mutation, time anchors from /api/v1/time, Android elapsedRealtime plugin | frontend | M | T-0127, T-0141, T-0147 |  |
| ⬜ | [T-0128](../tracker/tasks/T-0128.md) | Frontend: asset tree + Asset-Details (attributes, items, inspections over descendants) | frontend | M | T-0101, T-0103, T-0025 |  |
| ⬜ | [T-0147](../tracker/tasks/T-0147.md) | Sync scope: download picks as checkouts, download candidates with state and size, device/project-scoped pull-deltas with has_more paging and remove deltas | sync | M | T-0126, T-0133, T-0111, T-0016, T-0043, T-0044 |  |
| ⬜ | [T-0148](../tracker/tasks/T-0148.md) | Sync: client-generated UUIDv7 ids on offline create + answer conflict flag from the per-field apply | sync | S | T-0126, T-0111 |  |
| ⬜ | [T-0149](../tracker/tasks/T-0149.md) | Frontend: download-for-offline screen (candidates with state and size), paged pull into the storage contract, local purge | frontend | M | T-0147, T-0127, T-0172 |  |
| ⬜ | [T-0150](../tracker/tasks/T-0150.md) | inspections: review and return — SUBMITTED and RETURNED states, signed submit with attachments-complete check, approve, return with reason, review rounds | inspections | M | T-0111, T-0119, T-0140, T-0016, T-0019, T-0147, T-0148, T-0177 |  |
| ⬜ | [T-0151](../tracker/tasks/T-0151.md) | inspections: comments on a single answer (question key + repeat index), flat, append-only, offline-capable, resolvable | inspections | S | T-0111, T-0148 |  |
| ⬜ | [T-0152](../tracker/tasks/T-0152.md) | inspections: planning fields — assignee, planned start, duration, due date and overdue flag (no calendar or recurrence) | inspections | S | T-0111, T-0019, T-0177 |  |
| ⬜ | [T-0153](../tracker/tasks/T-0153.md) | Frontend: review screen — reviewer queue, answers with comments and conflict flags, approve/return; inspector's returned list and resubmit; PDF download | frontend | M | T-0150, T-0151, T-0158, T-0025, T-0127, T-0174 |  |
| ⬜ | [T-0154](../tracker/tasks/T-0154.md) | asset_management: Item scan identifiers (barcode / QR / NFC UID) + lookup endpoint | asset_management | S | T-0103 |  |
| ⬜ | [T-0155](../tracker/tasks/T-0155.md) | Frontend: scan-to-locate — camera barcode/QR and Android NFC, offline lookup over downloaded Items, online fallback, manual tag search | frontend | M | T-0154, T-0141, T-0149 |  |
| ⬜ | [T-0156](../tracker/tasks/T-0156.md) | forms: photo answer value — capture time, location and versioned annotation JSON, validated server-side, read-only after submit | forms | S | T-0110, T-0016 |  |
| ⬜ | [T-0157](../tracker/tasks/T-0157.md) | Frontend: photo capture with location and time + annotation editor (arrow, ellipse, rectangle, freehand, text), offline | frontend | M | T-0156, T-0127, T-0046 |  |
| ⬜ | [T-0158](../tracker/tasks/T-0158.md) | inspections: inspection PDF report (SAQ batch job, PyMuPDF lazy) with annotations burned in, signatures and review history | inspections | M | T-0150, T-0156, T-0116, T-0117, T-0016, T-0136, T-0167 |  |
| ⬜ | [T-0159](../tracker/tasks/T-0159.md) | inspections: batch inspection PDF export (ZIP + index.csv, max 500) + export action on the review queue | inspections | S | T-0158, T-0153 |  |
| ⬜ | [T-0160](../tracker/tasks/T-0160.md) | reporting: Pilot-0 dashboard tiles — progress, overdue, open defects (API + Overview screen) | reporting | M | T-0150, T-0152, T-0116, T-0117, T-0024, T-0025 |  |
| ⬜ | [T-0162](../tracker/tasks/T-0162.md) | identity: client online signing PIN — own table, server-side per-signer rate limit and lockout, single-use co-sign token | identity | S | T-0010, T-0038 |  |
| ⬜ | [T-0163](../tracker/tasks/T-0163.md) | signing: client co-signature ledger entries — online_pin and client_device_signed (QR assertion verified at sync), record-version binding, requires_client_signature check | signing | M | T-0119, T-0150, T-0158, T-0162, T-0173, T-0177 |  |
| ⬜ | [T-0164](../tracker/tasks/T-0164.md) | Frontend: hand to client on the inspector's tablet — online PIN, challenge/assertion QR exchange with offline verify, provisional wet signature; PIN-gated hand-back | frontend | M | T-0162, T-0163, T-0174, T-0143, T-0165 |  |
| ⬜ | [T-0165](../tracker/tasks/T-0165.md) | Frontend: inspector capture screen — open a downloaded inspection, answer with autosave, resume after crash, sign and submit; start an ad-hoc inspection on a downloaded Item or anchor | frontend | M | T-0149, T-0148, T-0150, T-0130, T-0047, T-0025, T-0127 |  |
| ⬜ | [T-0167](../tracker/tasks/T-0167.md) | forms: video, audio and barcode field types — value schemas, limits, CEL support, shared fixtures | forms | S | T-0110, T-0156 |  |
| ⬜ | [T-0168](../tracker/tasks/T-0168.md) | forms: reference images on template questions — upload, caption, frozen with the template revision, delivered in the pull scope | forms | S | T-0110, T-0016, T-0147 |  |
| ⬜ | [T-0169](../tracker/tasks/T-0169.md) | uploads: SAQ media probe for video/audio (container, codec, duration, size), rejected-with-reason, attachment complete only after the probe | uploads | S | T-0016, T-0167 |  |
| ⬜ | [T-0170](../tracker/tasks/T-0170.md) | Frontend: video and audio note capture and playback (limits enforced on device, poster frame, offline) | frontend | M | T-0167, T-0157, T-0127 |  |
| ⬜ | [T-0171](../tracker/tasks/T-0171.md) | Frontend: barcode/QR answer field (scanner adapter, manual entry) + reference images shown on questions offline | frontend | S | T-0167, T-0168, T-0155, T-0047 |  |
| ⬜ | [T-0173](../tracker/tasks/T-0173.md) | identity: client device signing keys — ES256 key enrolment per PER-15 device, revoke with the device, public keys in the pull scope | identity | S | T-0043, T-0147 |  |
| ⬜ | [T-0174](../tracker/tasks/T-0174.md) | signing: provisional wet co-signature — stored provisional, client confirm or reject in their own account, approval block, reminders | signing | S | T-0163, T-0019 |  |
| ⬜ | [T-0175](../tracker/tasks/T-0175.md) | Frontend: client app co-sign — enrol a device signing key, scan the challenge QR, approve, show the signed assertion QR (offline) | frontend | M | T-0173, T-0163, T-0172, T-0141, T-0155 |  |
| ⬜ | [T-0176](../tracker/tasks/T-0176.md) | Frontend: client confirmations — PER-15 list of provisional co-signatures, read-only record with the wet image, confirm or reject with reason | frontend | S | T-0174, T-0025 |  |
| ⬜ | [T-0177](../tracker/tasks/T-0177.md) | sync: offline action time reconstruction — occurred_at + time_confidence from monotonic/anchor/skew, evidence columns on answers and comments, audit payload | sync | M | T-0126, T-0147, T-0111 |  |
| ⬜ | [T-0129](../tracker/tasks/T-0129.md) | Frontend: P6 import wizard UI (upload, map, unmatched-row linking, commit) | frontend | M | T-0104, T-0128 |  |
| ⬜ | [T-0130](../tracker/tasks/T-0130.md) | Frontend: inspections register + raise/close + calibration-gated tool pick + gate findings | frontend | M | T-0111, T-0112, T-0124, T-0128 |  |
| ⬜ | [T-0132](../tracker/tasks/T-0132.md) | Phase 1 exit journey: P6→hierarchy→inspection gates→completion blocked by open NCR; expired tool blocked | e2e | S | T-0129, T-0130 |  |
| ⬜ | [T-0133](../tracker/tasks/T-0133.md) | Field checkout lease + per-field sync-back contract: office edits blocked during checkout, expired-lease and force-release paths (P6 re-import tests in T-0166) | sync | M | T-0126 |  |
| ⬜ | [T-0166](../tracker/tasks/T-0166.md) | sync: P6 re-import during a field checkout — tests 10 and 12 and checked-out records on the change-impact report (split from T-0133) | sync | S | T-0133, T-0104 |  |
| 🧩 | [E-01-01](../tracker/tasks/E-01-01.md) | register views: column catalogue (level/item-type fields, ancestors, rollups, cross-module providers), filter AST, shared saved views, grouped totals, bulk actions | saved_views | L | T-0020, T-0025, T-0102 |  |
| ⬜ | [T-0161](../tracker/tasks/T-0161.md) | e2e: Pilot-0 journey — download, inspect offline with annotated photo, sign and sync, return, resubmit, approve, PDF; scan-to-locate | e2e | S | T-0149, T-0153, T-0155, T-0157, T-0159, T-0160, T-0143, T-0027, T-0164, T-0165, T-0170, T-0171, T-0172, T-0175, T-0176 |  |
| ⬜ | [T-0146](../tracker/tasks/T-0146.md) | [Pilot-0] readiness gate: NFR budgets become hard CI gates; pilot scope checklist green | platform | S | T-0045, T-0145, T-0046, T-0142, T-0143, T-0144, T-0111, T-0119, T-0126, T-0127, T-0048, T-0037, T-0038, T-0039, T-0040, T-0041, T-0042, T-0043, T-0044, T-0047, T-0147, T-0148, T-0149, T-0150, T-0151, T-0152, T-0153, T-0154, T-0155, T-0156, T-0157, T-0158, T-0159, T-0160, T-0161, T-0130, T-0162, T-0163, T-0164, T-0165, T-0167, T-0168, T-0169, T-0170, T-0171, T-0172, T-0107, T-0173, T-0174, T-0175, T-0176, T-0177 |  |
| ⬜ | [T-0131](../tracker/tasks/T-0131.md) | [M1] Phase 1 backbone-freeze contract gate: freeze core tables, level-def shape + role names, rule-store shape, override record, field ownership, seam sigs; CEL parity | platform | M | T-0005, T-0016, T-0018, T-0101, T-0103, T-0110, T-0111, T-0112, T-0124, T-0132, T-0035, T-0139 |  |

## Phase 2 — Welding quality

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| ⬜ | [T-0201](../tracker/tasks/T-0201.md) | WPS register (oe_wps_record) + approval status + document link | wps | M | T-0103, T-0016 |  |
| ⬜ | [T-0202](../tracker/tasks/T-0202.md) | Welders + welder qualifications (range, expiry, continuity) → eligibility | wps | M | T-0201, T-0118 |  |
| ⬜ | [T-0203](../tracker/tasks/T-0203.md) | Welds as Items (kind weld) + weld state machine | wps | M | T-0202, T-0103 |  |
| ⬜ | [T-0204](../tracker/tasks/T-0204.md) | NDE results (oe_weld_nde_result): RT/UT/MT/PT | wps | S | T-0203, T-0016 |  |
| ⬜ | [T-0205](../tracker/tasks/T-0205.md) | Heat/MTR traceability: heat_number + MTR OCR (SAQ) → weld joint properties | materials | M | T-0204, T-0015, T-0016, T-0116 |  |
| ⬜ | [T-0206](../tracker/tasks/T-0206.md) | Rules weld.accept / welder.assign (R-WPS-1..4) via run_checks + logged findings | wps | M | T-0204, T-0124 |  |
| ⬜ | [T-0207](../tracker/tasks/T-0207.md) | Continuity watch report + expiry notifications (SAQ cron) | wps | S | T-0206, T-0019, T-0015 |  |
| ⬜ | [T-0208](../tracker/tasks/T-0208.md) | Frontend: WPS register, welder quals, weld register | frontend | M | T-0206, T-0128 |  |
| ⬜ | [T-0209](../tracker/tasks/T-0209.md) | Phase 2 exit journey: weld cannot be accepted outside qualification/NDE rules | e2e | S | T-0208 |  |

## Phase 3 — Logistics

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| 🧩 | [E-03-01](../tracker/tasks/E-03-01.md) | contacts: unified party directory (Client/Contractor/Subcontractor/Supplier/Authority) | contacts | L | T-0012 |  |
| 🧩 | [E-03-02](../tracker/tasks/E-03-02.md) | procurement: PR/PO + supplier_catalogs + subcontractors (external commercial refs) | procurement | L | E-03-01 |  |
| 🧩 | [E-03-03](../tracker/tasks/E-03-03.md) | materials: MRIR receiving + heat/MTR traceability + PMI | materials | L | T-0103, T-0205 |  |
| 🧩 | [E-03-04](../tracker/tasks/E-03-04.md) | site_inventory: append-only movements ledger + reorder | site_inventory | L | E-03-03 |  |
| 🧩 | [E-03-05](../tracker/tasks/E-03-05.md) | cutting: 1D FFD + 2D rectpack nesting, yield/waste, offcut reuse, cutting.confirm → inventory | cutting | L | E-03-04 |  |
| 🧩 | [E-03-06](../tracker/tasks/E-03-06.md) | site_logistics: Gate Delivery edge screen + OS&D vs ERP-mirrored PO lines | site_logistics | L | T-0127, E-03-02 |  |
| 🧩 | [E-03-07](../tracker/tasks/E-03-07.md) | equipment & fleet register (extends calibration core) | equipment | L | T-0112 |  |

## Phase 4 — Execution

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| 🧩 | [E-04-01](../tracker/tasks/E-04-01.md) | schedule: field programme (P6-synced, RSW-linked) | schedule | L | T-0106 |  |
| 🧩 | [E-04-02](../tracker/tasks/E-04-02.md) | schedule_advanced: Look-Ahead Constraint Board edge screen → PPC + RNC Pareto | schedule_advanced | L | E-04-01, T-0127 |  |
| 🧩 | [E-04-03](../tracker/tasks/E-04-03.md) | progress: physical earned quantity vs RSW/Item (gated) → P6 export | progress | L | E-04-01 |  |
| 🧩 | [E-04-04](../tracker/tasks/E-04-04.md) | field_execution: Daily Workface Log edge screen + shift logs + CBS burn ingest | field_execution | L | T-0127 |  |
| 🧩 | [E-04-05](../tracker/tasks/E-04-05.md) | field_time: labour + plant hours capture → CBS cost codes + payroll export | field_time | L | E-04-04 |  |
| 🧩 | [E-04-06](../tracker/tasks/E-04-06.md) | estimating: WBS/CBS + norm libraries + EVM (CPI/SPI) | estimating | L | E-04-03 |  |
| 🧩 | [E-04-07](../tracker/tasks/E-04-07.md) | tasks: generic tasks lodged at RSW | tasks | L | T-0101 |  |
| 🧩 | [E-04-08](../tracker/tasks/E-04-08.md) | resource_summary: demand rollup from look-ahead/tasks (moved from Phase 3) | resource_summary | L | E-04-02 |  |
| 🧩 | [E-04-09](../tracker/tasks/E-04-09.md) | safety / hse: SWMS/PTW permits + incident + KPI ingestion | safety | L | T-0012 |  |
| 🧩 | [E-04-10](../tracker/tasks/E-04-10.md) | site_supervision / commissioning / closeout / defects_liability | site_supervision | L | E-04-03 |  |
| 🧩 | [E-04-11](../tracker/tasks/E-04-11.md) | handover: shift/swing handover register - free-form sections, record links + hover preview, activity suggestions (rule set), first-shift delivery + acknowledgement | handover | L | T-0006, T-0015, T-0018, T-0019, T-0025, T-0101 |  |
| 🧩 | [E-04-12](../tracker/tasks/E-04-12.md) | planner: work plans (shutdown/break-in/campaign) in overlay or interrupt mode, resource pool (groups, people, crews, rosters), allocation grid by day/shift, capacity check, overlay Gantt, commit into programme | planner | L | E-04-01, E-04-07, T-0101, T-0018, T-0019, T-0118 |  |
| 🧩 | [E-04-13](../tracker/tasks/E-04-13.md) | scaffolding: scaffold register (location, build/approve/tag data, files, photos), lifecycle, tagging on one-engine inspections, automatic re-inspection tasks + overdue gate, build/modify/dismantle requests, QR card | scaffolding | L | T-0101, T-0103, T-0111, T-0118, T-0016, T-0019, E-04-07 |  |
| 🧩 | [E-04-14](../tracker/tasks/E-04-14.md) | scope_changes: change-of-scope register anchored to RSW/node (current-scope snapshot, proposed lines, photos, walk-down, party responses), agree then scope revision via asset service, portal exposure, variation link | scope_changes | L | T-0101, T-0102, T-0016, T-0018 |  |

## Phase 5 — Documents, collaboration & AI

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| 🧩 | [E-05-01](../tracker/tasks/E-05-01.md) | documents: CDE + VFS (folders, routing, versions, links, lockers) + pluggable parser adapter | documents | L | T-0016 |  |
| 🧩 | [E-05-02](../tracker/tasks/E-05-02.md) | markups + plan_room: PDF/drawing viewer + vector markup overlays | markups | L | E-05-01 |  |
| 🧩 | [E-05-03](../tracker/tasks/E-05-03.md) | transmittals + correspondence | transmittals | L | E-05-01 |  |
| 🧩 | [E-05-04](../tracker/tasks/E-05-04.md) | rfi / submittals / meetings / interface_management | rfi | L | E-05-01 |  |
| 🧩 | [E-05-05](../tracker/tasks/E-05-05.md) | MDR collation engine: SAQ streaming PDF merge, bookmarks, TOC, pagination, SHA-256, transmittal | mdr | L | E-05-01, T-0111 |  |
| 🧩 | [E-05-06](../tracker/tasks/E-05-06.md) | search / retrieval: docket OCR → full-text + vector index (SAQ) | search | L | E-05-01 |  |
| 🧩 | [E-05-07](../tracker/tasks/E-05-07.md) | integrations: outbound event bus + webhooks (HMAC) + ERP sync bridge (SAP/Xero/P6) | integrations | L | T-0015 |  |
| 🧩 | [E-05-08](../tracker/tasks/E-05-08.md) | erp_chat / ai_agents: operational assistant (proposals only, human-confirmed) | erp_chat | L | E-05-06 |  |
| 🧩 | [E-05-09](../tracker/tasks/E-05-09.md) | RSW document ingestion: bulk/ZIP upload, combined-PDF splitting, template recognition, OCR extraction with source traceability | documents | L | T-0104, T-0105 |  |
| 🧩 | [E-05-10](../tracker/tasks/E-05-10.md) | RSW-P6 matching & reconciliation engine: candidates, versioned weighted scoring, CEL hard/soft rules, verification queue, findings | asset_management | L | T-0104, T-0105, E-05-09 |  |

## Phase 6 — Defense, claims & controls

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| 🧩 | [E-06-01](../tracker/tasks/E-06-01.md) | commercial: contracts + SoR catalogs + claim milestones | commercial | L | T-0111 |  |
| 🧩 | [E-06-02](../tracker/tasks/E-06-02.md) | progress_claims: Pay Apps (AIA G702/G703), retention, claim.approved webhook → ERP | claims | L | E-06-01, E-04-03 |  |
| 🧩 | [E-06-03](../tracker/tasks/E-06-03.md) | variations: VR/VN/VO register + delay event attribution + time-impact analysis | variations | L | E-06-01 |  |
| 🧩 | [E-06-04](../tracker/tasks/E-06-04.md) | costmodel: physical EVM / EAC / project_controls | costmodel | L | E-04-06 |  |
| 🧩 | [E-06-05](../tracker/tasks/E-06-05.md) | reporting / dashboards / BI (SAQ rollups → summary tables) | reporting | L | E-06-04 |  |
| 🧩 | [E-06-06](../tracker/tasks/E-06-06.md) | risk / project_intelligence | risk | L | E-06-05 |  |
| 🧩 | [E-06-07](../tracker/tasks/E-06-07.md) | cases (persona walkthroughs) + approval_routes (quorum/delegation/SLA) | approval_routes | L | T-0018 |  |
| 🧩 | [E-06-08](../tracker/tasks/E-06-08.md) | carbon / esg | esg | L | T-0012 |  |

## Phase 6b — Work packaging (AWP)

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| 🧩 | [E-06b-01](../tracker/tasks/E-06b-01.md) | asset_integrity: preservation routines + hydrotest/pneumatic test packs (blinds, test exec, reinstatement) | asset_integrity | L | T-0101, T-0117 |  |
| 🧩 | [E-06b-02](../tracker/tasks/E-06b-02.md) | awp: EWP→CWP→IWP constraint engine (7 gates incl. scaffolding green-tag) + Technical Queries | awp | L | E-05-01, E-04-01, E-03-04 |  |
| 🧩 | [E-06b-03](../tracker/tasks/E-06b-03.md) | workpacks: live field dossier, offline stage sign-off, QR MDR compile | workpacks | L | E-06b-02 |  |

## Phase 7 — Client portal

| | ID | Title | Module | Size | Depends on | PR |
|---|---|---|---|---|---|---|
| 🧩 | [E-07-01](../tracker/tasks/E-07-01.md) | portal v1: client read-only hierarchy + registers (magic link) | portal | L | T-0131 |  |
| 🧩 | [E-07-02](../tracker/tasks/E-07-02.md) | portal v2: client co-sign reports & ITPs (report.cosign / itp.cosign, pyHanko) | portal | L | E-07-01, T-0119 |  |
| 🧩 | [E-07-03](../tracker/tasks/E-07-03.md) | portal v3: visit scheduler (witness/final) + client NCR input/sign-off | portal | L | E-07-02 |  |
| 🧩 | [E-07-04](../tracker/tasks/E-07-04.md) | portal v4: plan review - layouts per client role, distribution matrix, send with PDF + magic link, anchored comment threads, multi-session review with attendance, revision-bound approvals + absent-approver override, commit on approval | portal | L | E-07-01, E-04-12, E-06-07, T-0009, T-0019, T-0119 |  |
