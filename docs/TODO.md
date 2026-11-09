# KERP — TODO: decisions · spec gaps · missed · deferred · later

> **Generated** by `python scripts/tracker.py render` from `tracker/todo/*.md` — do not edit by hand.
> Add: `python scripts/tracker.py new-todo --kind <kind> --title "..." --task T-xxxx --source <agent>`

**Open:** 25 · **Closed:** 9

## decision (5)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-003](../tracker/todo/TD-003.md) | Identity: local JWT only, or SSO (Entra ID / Windows) for staff + email/password for invited clients? | T-0008 | spec-review |
| [TD-004](../tracker/todo/TD-004.md) | Hosting target: Coolify/MinIO (ADR-001) vs AWS (S3, SES) — affects storage/email adapters and deploy | T-0016 | spec-review |
| [TD-015](../tracker/todo/TD-015.md) | Phase 2 welder/NDE records reference the contacts directory (Phase 3): confirm interim nullable-linkage + backfill-FK default vs. landing a minimal contacts directory before Phase 2 | T-0202 | kerp-planner |
| [TD-034](../tracker/todo/TD-034.md) | Port/rewrite assessment of remaining Inspection-ERP core packages (jobs, sync, forms, workflow, events, files, schema_engine/items/mapping, reporting) | T-0015 | reviewer |
| [TD-037](../tracker/todo/TD-037.md) | Bring back any PROPOSAL-port-from-aip extras dropped in spec consolidation (write-back, type inheritance, migration preview, test-fill, section library, report slots, auto_create_itp, competency assignment, programmes)? | T-0102, T-0110, T-0111, T-0113 | kerp-planner |

## spec-gap (5)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-006](../tracker/todo/TD-006.md) | Offline sync listed in Phase 4 modules but plan §9 requires it from Phase 0/1 — moved core to Phase 1 (T-0125/T-0126) | T-0126 | spec-review |
| [TD-014](../tracker/todo/TD-014.md) | Relationship between inspection record kind='rfi' (T-0111) and the inspection_requests module (T-0115) is undefined — both model 'Request for Inspection' | T-0115 | kerp-planner |
| [TD-021](../tracker/todo/TD-021.md) | Outbox envelope: align frontend db field created_at_utc with master spec 1.3 timestamp_utc (decide in T-0126/T-0127 before the table is used; Dexie still at version 1) | T-0002 | reviewer |
| [TD-022](../tracker/todo/TD-022.md) | Sign off security header values and JSON log schema (T-0021 defaults) | T-0021 | implementer |
| [TD-027](../tracker/todo/TD-027.md) | Worker queue: CLAUDE.md/plan say ARQ, consolidation spec says SAQ replaces ARQ | T-0022 | implementer |

## deferred (1)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-011](../tracker/todo/TD-011.md) | forms F13 report-slot mapping + F14 AI import assistant (SPEC-11 §2) not in Phase 1 tasks | T-0110 | planner |

## later (11)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-016](../tracker/todo/TD-016.md) | Backend tests: move TestClient transport from httpx to httpx2 (Starlette 1.x deprecation warning) | T-0001 | implementer |
| [TD-018](../tracker/todo/TD-018.md) | Frontend: wire npm run gen:api (OpenAPI -> frontend/src/api/) once the backend exposes an OpenAPI schema | T-0002 | implementer |
| [TD-019](../tracker/todo/TD-019.md) | Frontend: PWA manifest + service worker (app-shell caching) for offline-first field use | T-0002 | implementer |
| [TD-020](../tracker/todo/TD-020.md) | Frontend: add ESLint (typescript-eslint, react-hooks) + lint gate in CLAUDE.md section 4 and CI | T-0002 | implementer |
| [TD-023](../tracker/todo/TD-023.md) | Distributed tracing / metrics (OpenTelemetry, Prometheus endpoint) for API and ARQ worker | T-0021 | implementer |
| [TD-025](../tracker/todo/TD-025.md) | Backend errors: request-context middleware renders unhandled 500s itself, so add_exception_handler(Exception) never runs; document or make the renderer pluggable before an error-format task; move _restore_logging autouse fixture to tests/conftest.py | T-0021 | reviewer |
| [TD-026](../tracker/todo/TD-026.md) | Extend TID253 heavy-import ban list as heavy deps land (DXF/XER parsers, PDF compile libs) | T-0022 | implementer |
| [TD-028](../tracker/todo/TD-028.md) | Pin backend base image by digest and automate refresh of base image + requirements locks (Dependabot/Renovate) | T-0004 | implementer |
| [TD-029](../tracker/todo/TD-029.md) | Backend image is linux/amd64 only (locks target x86_64-manylinux_2_28); add arm64 if a deployment target needs it | T-0004 | implementer |
| [TD-031](../tracker/todo/TD-031.md) | CI hardening: split GHCR publish into a push-only job holding packages:write, pin docker/* actions to commit SHAs, persist-credentials:false; declare packaging as backend dev dep; assert .dockerignore starts with * | T-0004 | reviewer |
| [TD-036](../tracker/todo/TD-036.md) | Add reportlab (and openpyxl if the Excel renderer is ported) to the heavy-lib lazy-import lint when Inspection-ERP core/reporting is ported | T-0115, T-0119 | kerp-reviewer |

## risk (3)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-013](../tracker/todo/TD-013.md) | review-gate patch-id ignores tracker/ — on planning PRs task content can change after approval without re-review | — | coordinator |
| [TD-024](../tracker/todo/TD-024.md) | Backend logging: httpx/httpcore INFO lines log full outbound URLs (incl. query-string tokens) via the root JSON handler; set them to WARNING before any integration makes outbound calls | T-0021 | reviewer |
| [TD-030](../tracker/todo/TD-030.md) | CI: detect job is not in ci-gate.needs - if detect fails, backend/frontend/e2e/backend-image are skipped and ci-gate still passes; add detect to needs and fail on non-success | T-0004 | reviewer |

## Closed

| | ID | Title | Resolution |
|---|---|---|---|
| ✅ | [TD-001](../tracker/todo/TD-001.md) | Port Inspection-ERP's proven core (BaseRecordMixin, RLS, auth, audit, loader, validation) or rewrite from the spec? | Port selectively per ADR 0001: port+adapt DB layer, RLS/project scope, auth deps + scrypt, module loader; rewrite audit (hash chain), JWT layer (RS256/EdDSA), login throttle; do not port validation engine/gates (EAV + Python gates vs JSONB + CEL run_checks). |
| ✅ | [TD-002](../tracker/todo/TD-002.md) | Adopt PROPOSAL-port-from-aip (one forms/item_types engine, eligibility service, inspection_requests)? | Adopted — PR #24 is part of the spec (user, 2026-11-08) |
| ⚫ | [TD-005](../tracker/todo/TD-005.md) | module_builder (runtime/no-code modules) has no buildable spec | module_builder culled per consolidation diff §1 (runtime no-code authoring out of scope). |
| ✅ | [TD-007](../tracker/todo/TD-007.md) | 'Single company' vs tenant_id + RLS everywhere — keep multi-tenant plumbing with one tenant per deploy? | Yes — keep tenant_id + project-scoped RLS everywhere, with 4 bypass contract tests (diff §4). |
| ✅ | [TD-008](../tracker/todo/TD-008.md) | Tier-1 edge screens (Gate Delivery, Look-Ahead Board, Daily Workface Log) and the app shell/design system have no UI specs in PRs 23/24 | Edge screens + app shell/design system now specified (KERP-EDGE §1, consolidation diff §6). |
| ✅ | [TD-009](../tracker/todo/TD-009.md) | Scope: ~94 modules. Define an MVP cut line (suggest: end of Phase 2 + documents) for a first pilot | M2 cut line = Phase 0+1+2 + Documents-Lite + P6 round-trip (~85–95 tasks), diff §7. |
| ⚫ | [TD-010](../tracker/todo/TD-010.md) | Architecture suite (Inspection-ERP PR #22) is still open/unmerged — imported from its branch as reference only | PR #22 is not a spec input; moved to docs/archive |
| ✅ | [TD-012](../tracker/todo/TD-012.md) | audit log (T-0013) has thin spec coverage — only DEVELOPMENT-PLAN principles 3/10/11 | Audit fully specified: append-only hash-chained ledger + REVOKE UPDATE/DELETE (diff §4). |
| ✅ | [TD-017](../tracker/todo/TD-017.md) | Backend: add a dependency lock file (pinned versions) before T-0004 Dockerfile needs reproducible builds | Hashed runtime + build lock files (backend/requirements.lock, requirements-build.lock) with sync tests, added in T-0004 |
