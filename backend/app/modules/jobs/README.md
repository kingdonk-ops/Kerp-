# jobs — job record, SAQ queues and the worker (T-0015)

Heavy work (PDF, OCR, CAD, P6 parse, rollups) runs in the SAQ worker over Redis, never in a
request (CLAUDE.md §6). The Postgres row is the source of truth; clients poll it. Rewritten
on SAQ; Inspection-ERP `core/jobs/{models,router}.py` was a reference for the record, the poll
contract, `update(progress, message)` and the sanitised error (ADR 0007).

| File | What |
|---|---|
| `models.py` | `oe_jobs_job` (`BaseRecordMixin`, project RLS, no exemption; migration `0007`) |
| `registry.py` | `register_job(kind, queue=...)`, `JobKind`, `JobContext`, `JobRegistry`, `JOB_REGISTRY`; `register_startup_check(fn)`: a module's precondition the worker runs before it connects (raise to refuse start-up; T-0213) |
| `broker.py` | `JobBroker` seam, `QueueMonitor` seam, `SaqBroker` (two SAQ `RedisQueue`s; both seams), `LoopDispatcher` (post-commit enqueue) |
| `service.py` | `create_job`, `get_job`, `start_job`/`report_progress`/`finish_job`, `requeue_for_retry`, `sanitise_error`, `sweep`; the queue monitor (`queue_statuses`, `cron_statuses`, `failed_counts`, `list_failed_jobs`, `retry_failed_job`, `abort_job`, `set_queue_monitor`; T-0218); tenant-level single runs (`create_single_run_job`, `pending_job`, `last_succeeded_job`) and the stats provider `deployment_stats` (job rows per status; T-0219) |
| `scheduler.py` | `deployment_scopes`, `sweep_cron` (SAQ cron, every 5 min, batch worker; T-0214), `hourly_project_jobs` (queues every `schedule_hourly_per_project` kind in every live project at :17; T-0215), `expire_mail_cron` (hourly at :41: fails mail still `queued` an hour past its 24 h payload lifetime with `payload_expired`; T-0346, TD-336), `tracked` (records each cron run for the queue monitor; T-0218). The cron expressions live in `registry.CRON_SCHEDULES` |
| `worker.py` | `run_job` (the one SAQ function), `build_workers`, `serve`; `python -m app.modules.jobs.worker` |
| `router.py`, `schemas.py` | `POST /api/v1/jobs`, `GET /api/v1/jobs/{id}` |
| `mail.py` | Outbound e-mail's queue (T-0058): `install` sets core mail's post-commit enqueuer at start-up (API lifespan, worker), `MailDispatcher` stores the payload in Redis (`kerp:mail:<send id>`, TTL) and enqueues `kerp_send_mail` on the interactive queue with ids only; `send_mail_job` runs `app.core.mail.service.deliver` (SAQ retries with back-off). See `app/core/mail/README.md` |

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

## Retry later (T-0218)

A kind registered with `max_attempts` > 1 (and `retry_delay_s`, `retry_max_delay_s`) may raise
`RetryLaterError("safe message")` for a passing condition (e.g. the malware scanner is down).
`run_job` then puts the row back to `queued` with the message and re-raises, so SAQ runs the
job again after `retry_delay_s`, doubling per attempt up to `retry_max_delay_s` (SAQ adds
jitter). On the last attempt the row fails with the message. Any other exception still fails
the job at once. `start_job` counts every attempt in `attempts`.

A **cancelled** run (SAQ's job timeout, or the worker stopping for a deploy) is treated the
same way (T-0346): while SAQ will run it again (`ctx["job"].retryable`) the row goes back to
`queued` (`Job timed out or was cancelled; it is retried`), so that run can start it; on the
last attempt, or without an SAQ job, it fails with `Job timed out or was cancelled`. Before,
the row failed and SAQ's retry found a `failed` row it may not start (an upload scan cut by a
deploy was then rejected as `verify_failed`). A kind with `max_attempts` 1 still fails on a
cancel; SAQ re-runs such a job after a shutdown, but the worker never starts a `failed` row.

## Queue monitor (T-0218, diff §4.7)

The System console's endpoints live in the admin module (`/api/v1/admin/system/queues...`,
`admin/system_router.py`, `system_admin` only) and call only this service. SAQ's own web UI is
never mounted (it has no KERP auth).

* **Counts** (`queue_statuses`): per queue SAQ's `queued`, `active` and `scheduled` (Redis,
  deployment-wide: e-mail and cron jobs included) and the age of the oldest queued job; plus the
  `failed` job rows per queue (`failed_counts`, under the caller's RLS).
* **Cron jobs** (`cron_statuses`): the batch worker's crons (`registry.CRON_SCHEDULES`; there is
  no cron queue). `scheduler.tracked` records each run's start, end and outcome in Redis
  (`<prefix>:cron:<name>`); the next run is the time SAQ has the `cron:<name>` job scheduled.
* **Failed jobs** (`list_failed_jobs`): kind, queue, project code (projects service), attempts,
  error class (read from the sanitised error: `Internal error (X)` -> `X`, the worker's own
  texts -> `Cancelled` / `Aborted`, any other `JobError` text -> `JobError`) and the message
  (<= 500 characters). Never the payload, the result or SAQ kwargs; outbound e-mail payloads are
  not job rows at all.
* **Retry** (`retry_failed_job`, `failed` only) and **abort** (`abort_job`, `queued` or
  `running`): the row must be visible to the caller's tenant-level request session under normal
  RLS (else 404). The change is written in a unit of work bound to the job's own scope (what the
  worker binds; no bypass role) and audited in the request session in the tenant-level chain
  `admin-system:<tenant>` (`event` `job_retried` / `job_aborted`, job id, kind, queue, project
  id; actor = the caller). A retry is enqueued after its commit with the kind's retry settings;
  an abort fails the row with `Aborted by an administrator`, then the router aborts the SAQ job
  (best effort: the worker never starts a `failed` row, and `finish_job(only_running=True)`
  never overwrites an aborted one).
* Visibility: under ADR 0016 (T-0274) a `system_admin` session reads (and the console retries
  and aborts) the job rows of every project of its tenant without a membership, under normal
  RLS; never another tenant's (TD-451).

## Single-run tenant-level jobs (T-0219)

`create_single_run_job(session, kind, payload)` queues a tenant-level job (no project) unless
one of the same kind is `queued` or `running` for the session's tenant, and then returns that one
(`created` false). Concurrent callers are serialised by a transaction-scoped lock (PostgreSQL
advisory lock per tenant and kind; SQLite: the write lock). `pending_job` and
`last_succeeded_job` read only tenant-level rows of the session's own tenant, filtered
explicitly (a `system_admin` session sees every project's rows too, ADR 0016). The System
console's object-store totals use them (`admin.object_store_totals`, its `result` is the cache).
The guard assumes READ COMMITTED (PostgreSQL's default and the request sessions'): the pending
check runs after the lock in a fresh statement snapshot, so it sees a run the previous holder
committed; a REPEATABLE READ transaction could miss it.

## Worker (rule 1)

`run_job` gets `job_id`, `tenant_id`, `project_id`, `request_id` as SAQ kwargs and opens a unit
of work bound to that tenant/project **before** reading the row, so `oe_jobs_job` stays under
RLS and the worker role needs no `BYPASSRLS`. Each queue has its own SAQ worker and
concurrency (`KERP_JOBS_INTERACTIVE_CONCURRENCY`, default 4; `KERP_JOBS_BATCH_CONCURRENCY`,
default 1), so batch work never delays an interactive job. The compose service `worker`
runs it (`make worker`); liveness probe and re-queue on shutdown: TD-157. Before it connects
to Redis, `serve` runs the API's database role check (`check_database_role`,
`app/core/startup_safety.py`, T-0342): in production a role that skips RLS or may edit the
audit ledger refuses the start.

## Sweep

`sweep(db, broker, scopes)` re-enqueues rows still `queued` after `older_than` that SAQ does
not know, one bound unit of work per scope. `scheduler.py` (T-0214) runs it every five
minutes as an SAQ cron job on the batch worker (`CronJob(unique=True)`: one run per tick across
replicas). Its scopes (`deployment_scopes`) are the `KERP_DEFAULT_TENANT_ID` tenant-level scope
plus one per live project, listed by `projects.live_project_ids` in a tenant-level unit of work
bound with `is_system_admin=True` (it reads only the project rows; since ADR 0016 that flag
would also show every project's records of the tenant, but the read touches no other table; no
BYPASSRLS). Broker enqueue retry/backoff and a heal on `GET /jobs/{id}`: TD-304.

The sweep also recovers **lost `running` rows** (T-0346): a row still `running` longer than its
kind's `timeout_s` plus `RUNNING_MARGIN` (5 min) since `started_at`, whose key SAQ no longer
has (the worker was killed: OOM, a hard crash), is re-checked under a row lock (still
`running`, same attempt) and then requeued and enqueued while the kind allows more attempts
(`attempts < max_attempts`), else failed with `Job timed out or was cancelled`. A row SAQ still
knows is left to SAQ. So a single-run job killed mid-run no longer blocks
`create_single_run_job` for good. SAQ keeps a finished job's record for its TTL, so a row whose
SAQ job SAQ itself failed is recovered only once that record is gone.

## Tests

`tests/test_jobs_retry.py` (retry later), `tests/test_queue_monitor.py` (the console API with a
fake monitor; SQLite and PostgreSQL), `tests/test_queue_monitor_saq.py` (counts, abort, cron
records and a retried job on real SAQ; needs `KERP_REDIS_URL`),
`tests/test_jobs.py` (SQLite and PostgreSQL, no Redis: a recording broker) and
`tests/test_jobs_saq.py` (real SAQ on Redis; needs `KERP_REDIS_URL`, set in CI's PostgreSQL step).
`tests/test_rls_bypass.py` runs jobs through the real SAQ workers under PostgreSQL RLS and
proves the handler is bound to the job's own scope and cannot read or write another's.
