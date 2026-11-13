# jobs — job record, SAQ queues and the worker (T-0015)

Heavy work (PDF, OCR, CAD, P6 parse, rollups) runs in the SAQ worker over Redis, never in a
request (CLAUDE.md §6). The Postgres row is the source of truth; clients poll it. Rewritten
on SAQ; Inspection-ERP `core/jobs/{models,router}.py` was a reference for the record, the poll
contract, `update(progress, message)` and the sanitised error (ADR 0007).

| File | What |
|---|---|
| `models.py` | `oe_jobs_job` (`BaseRecordMixin`, project RLS, no exemption; migration `0007`) |
| `registry.py` | `register_job(kind, queue=...)`, `JobKind`, `JobContext`, `JobRegistry`, `JOB_REGISTRY` |
| `broker.py` | `JobBroker` seam, `SaqBroker` (two SAQ `RedisQueue`s), `LoopDispatcher` (post-commit enqueue) |
| `service.py` | `create_job`, `get_job`, `start_job`/`report_progress`/`finish_job`, `sanitise_error`, `sweep` |
| `worker.py` | `run_job` (the one SAQ function), `build_workers`, `serve`; `python -m app.modules.jobs.worker` |
| `router.py`, `schemas.py` | `POST /api/v1/jobs`, `GET /api/v1/jobs/{id}` |

## Writing a job (ADR 0007 rules 3, 4)

Put handlers in `app/modules/<name>/jobs.py`. The module loader imports that file for every
loaded module, in the API (to enqueue) and in the worker (to run).

```python
from app.modules.jobs.service import JobError, register_job
from app.modules.jobs.registry import JobContext


@register_job("documents.ocr_page", queue="interactive")
async def ocr_page(job: JobContext) -> dict[str, object]:
    import pytesseract  # heavy: inside the handler (T-0022 lint)

    await job.update(10, "Reading page")
    if not job.payload.get("file_id"):
        raise JobError("No file to read")  # stored as-is (first line, <= 500 chars)
    ...
    return {"text_id": "..."}  # stored as the job's result
```

- **Kind** `<module>.<name>`; **queue** `interactive` (short, user waiting: OCR, barcode) or
  `batch` (long: MDR compile, P6 import). `api=True` lets clients enqueue it via `POST /jobs`.
- Enqueue from a service: `create_job(session, JOB_REGISTRY.get(kind), payload)` inside the
  caller's unit of work. The SAQ job is enqueued **after the commit** (`after_commit` hook);
  a rollback discards it (rule 2).
- `job.db` is the worker's `Database`: open short units of work with
  `job.db.unit_of_work(job.tenancy)` and keep slow I/O (storage, HTTP) outside them.
- Any other exception is stored as `Internal error (<ClassName>)`; the traceback goes to the
  JSON log with the request id of the request that enqueued the job (rule 5).
- A handler returns a JSON object or `None`. Anything else (a list, a string) fails the job
  with `Internal error (TypeError)`, so `GET /jobs/{id}` never sees a malformed `result`.
- The `payload` names what to work on (ids, options), not the data: `POST /jobs` refuses a
  payload above 64 KiB as compact JSON with 422 (`MAX_PAYLOAD_BYTES`). Scope never comes from
  the body or the payload: a `tenant_id` in either is ignored or refused (T-0060).

## Worker (rule 1)

`run_job` gets `job_id`, `tenant_id`, `project_id`, `request_id` as SAQ kwargs and opens a unit
of work bound to that tenant/project **before** reading the row, so `oe_jobs_job` stays under
RLS and the worker role needs no `BYPASSRLS`. Each queue has its own SAQ worker and
concurrency (`KERP_JOBS_INTERACTIVE_CONCURRENCY`, default 4; `KERP_JOBS_BATCH_CONCURRENCY`,
default 1), so batch work never delays an interactive job. The compose service `worker`
runs it (`make worker`); liveness probe and re-queue on shutdown: TD-157.

## Sweep

`sweep(db, broker, scopes)` re-enqueues rows still `queued` after `older_than` that SAQ does
not know, one bound unit of work per scope. Scheduling it needs a list of tenant/project
scopes, which no module provides yet (TD-155).

## Tests

`tests/test_jobs.py` (SQLite and PostgreSQL, no Redis: a recording broker) and
`tests/test_jobs_saq.py` (real SAQ on Redis; needs `KERP_REDIS_URL`, set in CI's PostgreSQL step).
`tests/test_rls_bypass.py` runs jobs through the real SAQ workers under PostgreSQL RLS and
proves the handler is bound to the job's own scope and cannot read or write another's.
