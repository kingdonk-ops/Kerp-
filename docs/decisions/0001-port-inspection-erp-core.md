# ADR 0001 — Port Inspection-ERP's core selectively (resolves TD-001)

- **Status:** accepted
- **Date:** 2026-11-09
- **Resolves:** TD-001
- **Affects:** T-0005, T-0006, T-0007, T-0008, T-0009, T-0010, T-0011, T-0018

## Context

`kingdonk-ops/Inspection-ERP` (`backend/app/core/`, `backend/app/database.py`) already
runs the doctrine KERP builds on: a modular FastAPI monolith on SQLAlchemy 2, record
mixins, tenant RLS, an append-only audit log, JWT auth and a module loader. It has a
test suite (SQLite and PostgreSQL) that covers it. TD-001 asked whether to port that
core or rewrite it from the KERP spec.

KERP's spec differs from Inspection-ERP in several places:

- RLS is project-scoped (`app.current_tenant_id` and `app.current_project_id`) and needs
  four RLS-bypass contract tests (diff §4).
- The audit ledger is hash-chained (`seq`, `chain_key`, `prev_hash`, `content_hash`,
  `payload`) and append-only at the database level with `REVOKE`.
- JWTs are asymmetric (RS256 or EdDSA) and published on a JWKS endpoint. Access tokens
  last 15 minutes, refresh tokens rotate and revocation is by `jti` in Redis.
- Verification is configured by CEL rule sets run through `run_checks(context, rule_set)`.
  Attributes live in `custom_fields` JSONB, and the EAV tables are deleted.
- The backend uses Python 3.12, `mypy --strict`, a 90% coverage floor and the `KERP_`
  settings prefix.

## Decision

Port **per component**. A component is ported only when it is complete, matches the
KERP spec's shape and would take less time to adapt than to rewrite.

| Component (Inspection-ERP source) | Verdict | Why | Lands in |
|---|---|---|---|
| `database.py`: `GUID`, `JSONType`, naming convention, `Base`, engine and session factory, `before_flush` revision maintenance | **Port and adapt** | Complete and tested. Dialect-portable types let the fast suite run on SQLite. The mixin needs KERP's columns: `project_id`, `updated_at`, `deleted_at`, `custom_fields`. The `source` column is kept because AI mutations must carry `source="ai"` (CLAUDE.md §6). | T-0005 |
| `tests/test_contract.py`: metadata contract test | **Port and adapt** | The same enforcement idea. Retargeted to `BaseRecordMixin` and KERP's exemption lists. | T-0005 |
| `alembic/env.py`: metadata-driven env, owner URL for migrations | **Port and adapt** | Small and correct. Migrations run as the schema owner, so the app role stays `NOBYPASSRLS`. | T-0005 |
| `core/tenancy/rls.py`: policy helper, `FORCE RLS`, `NULLIF` empty-setting guard (applies to both the tenant and the project setting), `after_begin` re-bind, pool-checkin reset, `rls_enforcement_problems()` | **Port and adapt** | Each of these fixes a real failure Inspection-ERP hit in production hardening, such as the pooled-connection tenant leak and the superuser bypass. Extend it to the project dimension, rename the setting to `app.current_tenant_id`, and add the four bypass tests. | T-0007 |
| `core/tenancy/scope.py`: ORM project scope (`with_loader_criteria`, write guard, 404 on out-of-scope) | **Port and adapt** | App-layer complement to project RLS. It already returns 404, never 403. | T-0007 |
| `core/tenancy/context.py`: tenant/user `ContextVar`s | **Port** | Trivial. Add `current_project_id`. | T-0007 |
| `core/auth/security.py`: scrypt password hashing | **Port** | Stdlib-only and constant-time. | T-0008 |
| `core/auth/security.py`: HS256 token issue/verify | **Rewrite** | The spec mandates RS256/EdDSA + JWKS + `jti`. The token layer is the part that changes. | T-0008 |
| `core/auth/deps.py`: credential → tenant → RLS bind → project scope dependency | **Port and adapt** | Right shape, and tenant comes only from the credential. Swap in the new token verifier and 15-persona roles. | T-0008 / T-0010 |
| `core/auth/ratelimit.py`: in-memory login throttle | **Rewrite** | KERP needs Redis-backed progressive lockout. The in-memory version is per-worker. | T-0009 |
| `core/audit/*`: flat `AuditLog` table | **Rewrite** | No hash chain, no `seq`, no DB-level `REVOKE`. Little code to save. | T-0006 |
| `core/module_loader.py`: manifest discovery, topological sort, fail-fast on unknown/cyclic deps | **Port and adapt** | Complete and tested. Replace marketplace entitlement gating with per-project enable/disable. | T-0011 |
| `core/validation/engine.py`: attribute validation | **Do not port** | Bound to `AttributeDefinition` EAV rows, which KERP deletes. KERP validates `custom_fields` with JSONSchema. | — |
| `core/validation/gates.py`: Python-registered completion gates | **Do not port** | Gate logic is inspection-specific Python. KERP's verification is CEL rule sets via `run_checks`. Only the `Finding(key, severity, message)` result shape is worth reusing as a reference. | — |
| `observability.py`, `security_headers.py` | **Not needed** | Already built independently in T-0021. | — |

### Out of scope of this ADR

TD-001 named six components: `BaseRecordMixin`, RLS, auth, audit, the loader and
validation. `backend/app/core/` also contains the packages below. This ADR gives them
**no verdict**. A port/rewrite assessment is tracked as decision TODO
TD-034, raised with this ADR. First observations, to be confirmed by that assessment:

| Package | First observation |
|---|---|
| `jobs/` (`runner.py`) | Thread-pool runner. Not portable as-is, because KERP runs heavy work in a Redis-backed worker queue (ARQ/SAQ, see TD-027). The job record and progress-callback shape may still be reusable. |
| `sync/` | Server half of offline sync: idempotent `op_id`, `base_revision` conflicts. Overlaps the Phase 1 offline sync core, and the most promising candidate. |
| `forms/` | Conditional sections and a restricted-AST formula evaluator. KERP's one-engine forms use CEL, so the evaluator is likely a rewrite. |
| `workflow/` | JSON-defined FSM with role-guarded transitions and gates. Must be reconciled with `run_checks` rule sets. |
| `events.py` | In-process event bus. KERP needs to decide between an outbox and an in-process bus. |
| `files/` | Storage-key abstraction with local and S3 backends. A candidate for the presigned-upload task, adapted to the `{tenant}/{project}/{module}/{hash}` key layout. |
| `schema_engine/`, `items/`, `mapping/` | Built on the EAV attribute model that KERP deletes. Likely not portable. |
| `reporting/` | reportlab PDF and openpyxl Excel output. Reassess when MDR/report tasks are planned. |

## Consequences

- T-0005 ships the ported DB layer: `app/core/db.py`, the contract test and Alembic.
- Each later task's notes name its Inspection-ERP source file, so the implementer ports
  from it instead of starting cold. The source repo is a reference only. Nothing is
  vendored wholesale, and every ported file must pass KERP's stricter gates
  (`mypy --strict`, ruff `ANN`, 90% coverage).
- Inspection-ERP's `entity_uri` and per-record content `hash` are **not** carried over.
  KERP's tamper evidence lives in the hash-chained ledger (T-0006), not on every row.
- Ported docstrings that cite Inspection-ERP's own ADRs (e.g. its "ADR 0001" on scrypt) are
  rewritten as `Inspection-ERP ADR nnnn`, so they never collide with KERP's ADR numbers.
- Ported code keeps a one-line module docstring credit (`Adapted from Inspection-ERP
  …`) so reviewers can diff against the origin.
