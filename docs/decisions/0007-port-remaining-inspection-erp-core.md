# ADR 0007 — Port or rewrite the remaining Inspection-ERP core packages (resolves TD-034)

- **Status:** accepted
- **Date:** 2026-11-11
- **Resolves:** TD-034 (approved by the user 2026-11-09; that approval named this record
  "ADR 0003", but 0003 went to the configurable-breakdown decision, so it is 0007)
- **Follows:** [ADR 0001](0001-port-inspection-erp-core.md) (same method, per component) and
  [ADR 0002](0002-port-one-engine.md) (already decides `forms/evaluation.py`, `reporting/*`,
  `workflow/engine.py`)
- **Affects:** T-0015, T-0016, T-0108, T-0110, T-0126, T-0133, E-05-07

## Context

ADR 0001 decided only the six components TD-001 named. Its "Out of scope" table listed the
rest of `kingdonk-ops/Inspection-ERP` `backend/app/core/` with first observations and no
verdict. TD-034 asked for a verdict per package before the tasks that overlap them start,
T-0015 (jobs worker) first.

All packages were read at the source repo's HEAD on 2026-11-11, with their tests. Facts
that apply to every package:

- **Everything is synchronous** (`sqlalchemy.orm.Session`, `def` handlers). KERP is async.
- Models use `TenantRecordMixin`: no `project_id`, `deleted_at` or `custom_fields`,
  `modified_at` instead of `updated_at`, plus a per-row `hash` that ADR 0001 drops.
- Tooling is looser than KERP's: non-strict mypy, no ruff `ANN`, 80% coverage floor,
  Python 3.11. Bare `dict`/`tuple` generics are common.
- Services raise `HTTPException` directly, so the web layer leaks into the service layer.
- Routes unpack the old auth context as an untyped `(user, org, membership)` tuple. KERP
  uses the typed context from T-0008.

Inspection-ERP's own ADRs are cited below as `Inspection-ERP ADR nnnn` so they never
collide with KERP's numbers.

## Decision

Same rule as ADR 0001: port a component only when it is complete, matches the KERP spec's
shape and is cheaper to adapt than to rewrite. "Reference" means the implementer reads it
for shape and test cases but writes the code fresh.

| Package (Inspection-ERP `backend/app/core/`) | Verdict | Why | Lands in |
|---|---|---|---|
| `jobs/runner.py`: `ThreadPoolExecutor` runner, `register_job`, `submit_job` | **Rewrite** | In-process threads lose jobs when the process dies and cannot run in the SAQ worker (TD-027). Inspection-ERP ADR 0002 itself called it "v0, broker later". | T-0015 |
| `jobs/models.py` + `router.py`: `JobRecord` (`kind`, `status`, `progress`, `message`, `payload`, `result`, `error`), `GET /jobs/{id}` | **Reference** | Keep the contract: the Postgres row is the source of truth and clients poll `GET /jobs/{id}`; handlers get an `update(progress, message)` callback; the traceback is logged server-side and only a sanitised, truncated error is stored (Inspection-ERP fix H-16). The table becomes `BaseRecordMixin` with `queue`, `saq_job_key`, `attempts`, `started_at`, `finished_at`. | T-0015 |
| `sync/service.py` + `models.py` + `router.py`: `op_id` ledger, savepoint per op, batch `POST /sync` | **Port and adapt** the skeleton; **rewrite** the apply logic | The idempotency ledger (unique `(tenant_id, client_op_id)`, stored result replayed as `duplicate`), one savepoint per op so a bad op fails only itself, audit on apply and the batch endpoint shape all carry over. The conflict rule does not: it rejects the whole record on `base_revision != revision` and returns server state, which is the record-level option ADR 0005 rejected. The handler contract is rewritten around ADR 0005's per-path `changes` with base hashes, field ownership, the checkout lease and the conflict log. | T-0126, T-0133 |
| `files/storage.py` helpers: `clean_filename`, `content_disposition` (RFC 5987), served-type allowlist (fix H-10), magic-byte allowlist, `LocalStorage` traversal guard, `Storage` Protocol | **Port** | Small, typed, tested security helpers (`test_files`, `test_hardening` H-10). | T-0016 |
| `files/router.py` + `s3.py` + `models.py`: multipart upload through the API, blocking boto3, `{tenant}/{uuid}.ext` keys | **Rewrite** | KERP uploads go direct to MinIO/S3 by presigned PUT (`presign` → `complete` with `head_object`, size and magic-byte check), downloads by presigned GET, keys `{tenant}/{project}/{module}/{hash}`. The S3 backend has no presign support and no tests. | T-0016 |
| `events.py`: in-process synchronous `subscribe`/`emit` | **Do not port** | Nothing in Inspection-ERP calls it. It fires before commit, inside the request, and never reaches the worker or other instances. KERP domain events go through a **transactional outbox** (row written in the domain transaction, relayed by the SAQ worker, at-least-once, idempotent handlers). Synchronous checks such as `run_checks` are called directly, not through a bus. | E-05-07 |
| `forms/` (`models`, `service`, `router`; `evaluation.py` already decided by ADR 0002) | **Do not port** | Spec overwritten in place on `version += 1` (no revision freeze), single-`equals` visibility, project row overlays the instance row (ADR 0004 copies, it does not overlay). AIP's form template is the base (ADR 0002). Reference only: the `response_type` vocabulary and the `_validate_sections` checks as lint codes. | T-0110 (reference) |
| `workflow/engine.py` | **Do not port** (agrees with ADR 0002) | KERP's lifecycle is a fixed enum and gates are CEL rule sets through `run_checks`. Reference: the order a transition runs in (resolve → build server-derived context → `run_checks` → block on errors → transition log → audit), and the `gates.py` catalogue as the starter list of default rule sets. | — |
| `schema_engine/` | **Do not port** | Attribute-definition rows plus a live instance-to-project overlay. KERP uses level definitions with work-anchor roles (ADR 0003), JSONSchema `custom_fields` and copy-on-create (ADR 0004). Reference: `bundle.py` export/import keyed by `key`, never by id, for client-pack apply; `numbering.py`'s row-locked per-scope counter. **Do not** reuse its `str.format` on a tenant-supplied pattern: it reads attributes (`{seq.__class__}`) and 500s on `AttributeError`. Use a whitelisted placeholder parser. | T-0108 (reference) |
| `items/` | **Do not port** | Adjacency list, not ltree. `set_attribute` skips validation. Replaced by `oe_asset_node` / `oe_asset_item`. | — |
| `mapping/` (Inspectivity importer, Xero/MYOB CSV profiles) | **Do not port** | The importer writes attribute-definition rows. KERP's import mapping profiles (ADR 0003 §5, T-0138) are a different feature. Finance push exports conflict with money being a read-only mirror. Its test fixture looks client-derived and must not be copied (CLAUDE.md §6). | — |
| `reporting/` | **Port and adapt** (as decided by ADR 0002) | Add to the record: move the reportlab/openpyxl imports inside the render functions (TD-038), call them only from the worker, route labels through `t()`, embed a Unicode TTF font, render images and the logo in the PDF, wrap table cells, make `Block` a typed union. | T-0119, T-0115 |

### Rules for the jobs worker (T-0015)

These follow from the verdicts above and bind T-0015:

1. **The jobs table keeps RLS.** Inspection-ERP put `ie_core_job` in `RLS_EXEMPT` because
   its worker loaded the row before it knew the tenant. KERP passes `tenant_id` and
   `project_id` in the SAQ job kwargs, so the worker binds the RLS context before it
   reads the row. No `BYPASSRLS` role and no exemption.
2. **Enqueue after commit.** A job row is committed before the SAQ job is enqueued, so a
   rolled-back request never leaves a queued job. A row stuck in `queued` without an SAQ
   job is re-enqueued by a sweep.
3. **Two queues** (`interactive`, `batch`), per TD-027. A job's kind declares its queue.
4. **Handlers are async**, registered by module (a manifest entry or a `register_job`
   decorator the loader picks up), and import heavy libraries inside the handler (T-0022).
5. **Only a sanitised error is stored** (no traceback, bounded length). The full traceback
   goes to the structured log with the request id.

## Consequences

- TD-034 is resolved. T-0015 is no longer waiting on an ADR.
- T-0015, T-0016, T-0126, T-0133 and T-0108 notes name their Inspection-ERP source files.
  The source repo stays a reference: nothing is vendored, and every ported file passes
  `mypy --strict`, ruff `ANN` and the 90% floor, keeping the one-line
  `Adapted from Inspection-ERP …` credit (ADR 0001).
- Ported sync and files code becomes async and takes typed contexts and Pydantic models
  instead of bare `dict`. Services raise domain errors, not `HTTPException`.
- The transactional outbox is the event mechanism. E-05-07 builds it. T-0015 only needs
  enqueue-after-commit, not the outbox.
- Inspection-ERP has gaps KERP must not inherit: the unhandled `IntegrityError` race when
  two identical sync batches arrive together (treat it as `duplicate`), and file-size checks
  that happen after the upload is already spooled (presigned PUT with a content-length
  condition fixes this).
