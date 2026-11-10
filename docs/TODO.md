# KERP — TODO: decisions · spec gaps · missed · deferred · later

> **Generated** by `python scripts/tracker.py render` from `tracker/todo/*.md` — do not edit by hand.
> Add: `python scripts/tracker.py new-todo --kind <kind> --title "..." --task T-xxxx --source <agent>`

**Open:** 24 · **Closed:** 25

## decision (1)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-034](../tracker/todo/TD-034.md) | Port/rewrite assessment of remaining Inspection-ERP core packages (jobs, sync, forms, workflow, events, files, schema_engine/items/mapping, reporting) | T-0015 | reviewer |

## spec-gap (4)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-021](../tracker/todo/TD-021.md) | Outbox envelope: align frontend db field created_at_utc with master spec 1.3 timestamp_utc (decide in T-0126/T-0127 before the table is used; Dexie still at version 1) | T-0002 | reviewer |
| [TD-044](../tracker/todo/TD-044.md) | Spec the PWA-first client strategy: web-only PWA apps (supervisor, storeman, field report) vs the Android/tablet app, and re-check the frontend tech stack | T-0127 | user |
| [TD-045](../tracker/todo/TD-045.md) | Spec the Request for Inspection (inspection notification / booking) record; RFI means Request for Information | T-0115 | user |
| [TD-046](../tracker/todo/TD-046.md) | Admin side: where security and observability settings (headers/CSP, HSTS, API docs exposure, log retention) are shown or configured | T-0021 | user |

## deferred (5)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-011](../tracker/todo/TD-011.md) | forms F13 report-slot mapping + F14 AI import assistant (SPEC-11 §2) not in Phase 1 tasks | T-0110 | planner |
| [TD-032](../tracker/todo/TD-032.md) | tenant_id FK to the tenants directory table (spec §1.1) once the tenants table exists | T-0005 | implementer |
| [TD-033](../tracker/todo/TD-033.md) | Default soft-delete filtering: exclude deleted_at IS NOT NULL from ORM reads unless opted in | T-0005 | implementer |
| [TD-041](../tracker/todo/TD-041.md) | CEL parity gaps between celpy 0.5.0 and @marcbachmann/cel-js 8 (pinned known_gap fixtures) must be closed or accepted before T-0131 | T-0017, T-0018 | implementer |
| [TD-047](../tracker/todo/TD-047.md) | Automate refresh of backend/requirements*.lock (uv pip compile output): Dependabot pip does not find *.lock files or re-run uv; options: scheduled uv --upgrade workflow opening a PR, or Renovate pip-compile manager | T-0028 | implementer |

## later (12)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-016](../tracker/todo/TD-016.md) | Backend tests: move TestClient transport from httpx to httpx2 (Starlette 1.x deprecation warning) | T-0001 | implementer |
| [TD-018](../tracker/todo/TD-018.md) | Frontend: wire npm run gen:api (OpenAPI -> frontend/src/api/) once the backend exposes an OpenAPI schema | T-0002 | implementer |
| [TD-019](../tracker/todo/TD-019.md) | Frontend: PWA manifest + service worker (app-shell caching) for offline-first field use | T-0002 | implementer |
| [TD-020](../tracker/todo/TD-020.md) | Frontend: add ESLint (typescript-eslint, react-hooks) + lint gate in CLAUDE.md section 4 and CI | T-0002 | implementer |
| [TD-023](../tracker/todo/TD-023.md) | Distributed tracing / metrics (OpenTelemetry, Prometheus endpoint) for API and ARQ worker | T-0021 | implementer |
| [TD-025](../tracker/todo/TD-025.md) | Backend errors: request-context middleware renders unhandled 500s itself, so add_exception_handler(Exception) never runs; document or make the renderer pluggable before an error-format task; move _restore_logging autouse fixture to tests/conftest.py | T-0021 | reviewer |
| [TD-035](../tracker/todo/TD-035.md) | Optimistic locking on BaseRecordMixin.revision (version_id_col or explicit base_revision check) | T-0005 | reviewer |
| [TD-036](../tracker/todo/TD-036.md) | Guard against bare Depends(get_db) outside app/core/db.py (endpoints must use DbSession) | T-0005 | reviewer |
| [TD-038](../tracker/todo/TD-038.md) | Add reportlab (and openpyxl if the Excel renderer is ported) to the heavy-lib lazy-import lint when Inspection-ERP core/reporting is ported | T-0115, T-0119 | kerp-reviewer |
| [TD-043](../tracker/todo/TD-043.md) | Entra ID SSO for KAEFER staff behind the AuthProvider seam, before the first pilot | T-0008 | user |
| [TD-048](../tracker/todo/TD-048.md) | SHA-pin actions in promote.yml and review-gate.yml (persist-credentials:false where no push is needed) and digest-pin the CI service images (postgis, redis) | T-0028 | implementer |
| [TD-051](../tracker/todo/TD-051.md) | CI: backend-publish rebuilds the image instead of pushing the smoke-tested one; export/push the tested image by digest | T-0028 | reviewer |

## risk (2)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-013](../tracker/todo/TD-013.md) | review-gate patch-id ignores tracker/ — on planning PRs task content can change after approval without re-review | — | coordinator |
| [TD-024](../tracker/todo/TD-024.md) | Backend logging: httpx/httpcore INFO lines log full outbound URLs (incl. query-string tokens) via the root JSON handler; set them to WARNING before any integration makes outbound calls | T-0021 | reviewer |

## Closed

| | ID | Title | Resolution |
|---|---|---|---|
| ✅ | [TD-001](../tracker/todo/TD-001.md) | Port Inspection-ERP's proven core (BaseRecordMixin, RLS, auth, audit, loader, validation) or rewrite from the spec? | Port selectively per ADR 0001: port+adapt DB layer, RLS/project scope, auth deps + scrypt, module loader; rewrite audit (hash chain), JWT layer (RS256/EdDSA), login throttle; do not port validation engine/gates (EAV + Python gates vs JSONB + CEL run_checks). |
| ✅ | [TD-002](../tracker/todo/TD-002.md) | Adopt PROPOSAL-port-from-aip (one forms/item_types engine, eligibility service, inspection_requests)? | Adopted — PR #24 is part of the spec (user, 2026-11-08) |
| ✅ | [TD-003](../tracker/todo/TD-003.md) | Identity: local JWT only, or SSO (Entra ID / Windows) for staff + email/password for invited clients? | Local scrypt+JWT behind the AuthProvider seam now; Entra ID SSO for staff before the first pilot (TD-043) (user, 2026-11-09) |
| ✅ | [TD-004](../tracker/todo/TD-004.md) | Hosting target: Coolify/MinIO (ADR-001) vs AWS (S3, SES) — affects storage/email adapters and deploy | Dev/test on Coolify + MinIO; production on AWS (S3 storage, SES email). Storage adapter stays S3-compatible (user, 2026-11-09) |
| ⚫ | [TD-005](../tracker/todo/TD-005.md) | module_builder (runtime/no-code modules) has no buildable spec | module_builder culled per consolidation diff §1 (runtime no-code authoring out of scope). |
| ✅ | [TD-006](../tracker/todo/TD-006.md) | Offline sync listed in Phase 4 modules but plan §9 requires it from Phase 0/1 — moved core to Phase 1 (T-0125/T-0126) | Confirmed: offline sync core in Phase 1. Start PWA-first while the Android app is developed; web-only PWA apps (supervisor, storeman, field report) and a tech-stack review need spec (TD-044) (user, 2026-11-09) |
| ✅ | [TD-007](../tracker/todo/TD-007.md) | 'Single company' vs tenant_id + RLS everywhere — keep multi-tenant plumbing with one tenant per deploy? | Yes — keep tenant_id + project-scoped RLS everywhere, with 4 bypass contract tests (diff §4). |
| ✅ | [TD-008](../tracker/todo/TD-008.md) | Tier-1 edge screens (Gate Delivery, Look-Ahead Board, Daily Workface Log) and the app shell/design system have no UI specs in PRs 23/24 | Edge screens + app shell/design system now specified (KERP-EDGE §1, consolidation diff §6). |
| ✅ | [TD-009](../tracker/todo/TD-009.md) | Scope: ~94 modules. Define an MVP cut line (suggest: end of Phase 2 + documents) for a first pilot | M2 cut line = Phase 0+1+2 + Documents-Lite + P6 round-trip (~85–95 tasks), diff §7. |
| ⚫ | [TD-010](../tracker/todo/TD-010.md) | Architecture suite (Inspection-ERP PR #22) is still open/unmerged — imported from its branch as reference only | PR #22 is not a spec input; moved to docs/archive |
| ✅ | [TD-012](../tracker/todo/TD-012.md) | audit log (T-0013) has thin spec coverage — only DEVELOPMENT-PLAN principles 3/10/11 | Audit fully specified: append-only hash-chained ledger + REVOKE UPDATE/DELETE (diff §4). |
| ✅ | [TD-014](../tracker/todo/TD-014.md) | Relationship between inspection record kind='rfi' (T-0111) and the inspection_requests module (T-0115) is undefined — both model 'Request for Inspection' | RFI means Request for Information (its own module). Request for Inspection is an inspection notification/booking record in scheduling, stored with the RFI system; full spec pending in TD-045, T-0115 blocked on it (user, 2026-11-09) |
| ✅ | [TD-015](../tracker/todo/TD-015.md) | Phase 2 welder/NDE records reference the contacts directory (Phase 3): confirm interim nullable-linkage + backfill-FK default vs. landing a minimal contacts directory before Phase 2 | Confirmed default: nullable contact references in Phase 2; add the FK constraints when contacts (E-03-01) lands (user, 2026-11-09) |
| ✅ | [TD-017](../tracker/todo/TD-017.md) | Backend: add a dependency lock file (pinned versions) before T-0004 Dockerfile needs reproducible builds | Hashed runtime + build lock files (backend/requirements.lock, requirements-build.lock) with sync tests, added in T-0004 |
| ✅ | [TD-022](../tracker/todo/TD-022.md) | Sign off security header values and JSON log schema (T-0021 defaults) | T-0021 defaults accepted; no HSTS preload; /docs, /redoc, /openapi.json hidden in production (T-0030); log shipping on AWS decided with deploy; admin-side settings spec in TD-046 (user, 2026-11-09) |
| ✅ | [TD-026](../tracker/todo/TD-026.md) | Extend TID253 heavy-import ban list as heavy deps land (DXF/XER parsers, PDF compile libs) | Standing check added to the kerp-reviewer checklist: every new heavy dependency is added to banned-module-level-imports in the same PR (user, 2026-11-09) |
| ✅ | [TD-027](../tracker/todo/TD-027.md) | Worker queue: CLAUDE.md/plan say ARQ, consolidation spec says SAQ replaces ARQ | SAQ over Redis (interactive + batch queues), per the consolidated spec; CLAUDE.md §6 wording updated (user, 2026-11-09) |
| ✅ | [TD-028](../tracker/todo/TD-028.md) | Pin backend base image by digest and automate refresh of base image + requirements locks (Dependabot/Renovate) | T-0028: base image pinned by multi-arch index digest in both FROM lines; .github/dependabot.yml (Dependabot, no app install) refreshes the base image digest and the action SHAs. Lock-file refresh split out to TD-047. |
| ⚫ | [TD-029](../tracker/todo/TD-029.md) | Backend image is linux/amd64 only (locks target x86_64-manylinux_2_28); add arm64 if a deployment target needs it | Production on AWS x86_64 is enough; reopen if an arm64 (Graviton) target is chosen (user, 2026-11-09) |
| ✅ | [TD-030](../tracker/todo/TD-030.md) | CI: detect job is not in ci-gate.needs - if detect fails, backend/frontend/e2e/backend-image are skipped and ci-gate still passes; add detect to needs and fail on non-success | T-0028: detect is in ci-gate.needs; scripts/ci_gate.py fails the gate unless every needed job is success, or skipped only when its component is absent (detect output false) or it is the push-only publish job on a non-push event. Tested in scripts/tests/test_ci_gate.py. |
| ✅ | [TD-031](../tracker/todo/TD-031.md) | CI hardening: split GHCR publish into a push-only job holding packages:write, pin docker/* actions to commit SHAs, persist-credentials:false; declare packaging as backend dev dep; assert .dockerignore starts with * | T-0028: GHCR publish moved to the push-only backend-publish job, the only job with packages:write; all actions in ci.yml (incl. docker/*) pinned to commit SHAs; every checkout sets persist-credentials:false; packaging declared as a backend dev dependency; test asserts .dockerignore starts with *. |
| ✅ | [TD-037](../tracker/todo/TD-037.md) | Drop the empty 'postgres' extra from backend/pyproject.toml once CI's install line stops referencing it | T-0028: empty postgres extra removed from backend/pyproject.toml and ci.yml installs .[dev]. |
| ✅ | [TD-039](../tracker/todo/TD-039.md) | Bring back any PROPOSAL-port-from-aip extras dropped in spec consolidation (write-back, type inheritance, migration preview, test-fill, section library, report slots, auto_create_itp, competency assignment, programmes)? | Out of scope for Phase 1. Revisit auto_create_itp and asset write-back as Phase 3 candidates (user, 2026-11-09) |
| ✅ | [TD-040](../tracker/todo/TD-040.md) | CEL value encoding: plain JSON numbers are CEL double on both runtimes; ints/timestamps need explicit tags | Confirmed: plain JSON numbers are CEL double; int64/timestamp/uint/duration use $-tagged encoding. Binding for sync payloads and form templates (user, 2026-11-09) |
| ✅ | [TD-042](../tracker/todo/TD-042.md) | EDGE-SCREENS 2.1 hold-gating example uses count(list), which is not standard CEL | Use standard size(); EDGE-SCREENS §2.1 hold-gating example corrected (user, 2026-11-09) |
