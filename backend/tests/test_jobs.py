"""Jobs module: record, enqueue after commit, poll API, runner, sweep (T-0015, ADR 0007).

No Redis here: the SAQ broker is replaced by ``RecordingBroker``. Runs on SQLite and, in CI,
on PostgreSQL under project RLS. Real SAQ on Redis is ``tests/test_jobs_saq.py``.
"""

import asyncio
import logging
import subprocess
import sys
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.config import Settings
from app.core.db import Base, Database, contract_violations, utcnow
from app.core.module_loader.loader import discover_modules, import_job_handlers
from app.core.request_context import request_id_var
from app.core.tenancy.context import TenancyContext, current_tenant_id
from app.core.tenancy.rls import RLS_EXEMPT, rls_tables
from app.main import create_app
from app.modules.jobs import registry as registry_module
from app.modules.jobs import service
from app.modules.jobs.broker import JobRef, LoopDispatcher, SaqBroker
from app.modules.jobs.models import MAX_TEXT, TABLE, Job
from app.modules.jobs.registry import JobContext, JobKind, JobRegistry
from app.modules.jobs.worker import WorkerContext, build_workers, load_registry, run_job, serve
from tests.audit_support import migrated_database
from tests.module_support import FakeModule, ModulePackages
from tests.tenancy_support import CTX_A, CTX_B, CTX_C, CTX_T1
from tests.test_module_toggle import Caller

BACKEND = Path(__file__).resolve().parents[1]
JOBS = "/api/v1/jobs"


class RecordingBroker:
    """A ``JobBroker`` that keeps what it was given (stands in for SAQ)."""

    def __init__(self) -> None:
        self.enqueued: list[JobRef] = []
        self.known: set[str] = set()

    async def enqueue(self, ref: JobRef) -> None:
        self.enqueued.append(ref)
        self.known.add(ref.key)

    async def has_job(self, queue: str, key: str) -> bool:
        return key in self.known


async def echo(job: JobContext) -> dict[str, Any]:
    await job.update(50, "half way")
    return {"echo": job.payload, "tenant": str(current_tenant_id.get())}


async def boom(job: JobContext) -> None:
    raise ValueError("password=hunter2 in /srv/secret.txt\nTraceback (most recent call last)")


async def refuse(job: JobContext) -> None:
    raise service.JobError("Bad file: " + "x" * 2000)


async def not_json(job: JobContext) -> dict[str, Any]:
    return {"id": uuid.uuid4()}  # a UUID is not JSON: storing it fails at flush


async def slow(job: JobContext) -> None:
    await asyncio.sleep(60)


def make_registry() -> JobRegistry:
    registry = JobRegistry()
    registry.add(JobKind("demo.ocr", "interactive", echo, api=True))
    registry.add(JobKind("demo.mdr", "batch", echo, api=True))
    registry.add(JobKind("demo.internal", "batch", echo))
    registry.add(JobKind("demo.boom", "batch", boom))
    registry.add(JobKind("demo.refuse", "interactive", refuse))
    registry.add(JobKind("demo.slow", "batch", slow))
    registry.add(JobKind("demo.not_json", "batch", not_json))
    return registry


REGISTRY = make_registry()


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def dispatched() -> Iterator[list[JobRef]]:
    refs: list[JobRef] = []
    service.set_dispatcher(refs.extend)
    yield refs
    service.set_dispatcher(None)


def create(db: Database, ctx: TenancyContext, kind: str = "demo.ocr", **payload: Any) -> uuid.UUID:
    with db.unit_of_work(ctx) as s:
        return service.create_job(s, REGISTRY.get(kind), payload).id


def load(db: Database, ctx: TenancyContext, job_id: uuid.UUID) -> Job:
    with db.unit_of_work(ctx) as s:
        return service.get_job(s, job_id)


def run(db: Database, ref: JobRef, ctx: TenancyContext | None = None) -> None:
    worker_ctx: Any = {"db": db, "registry": REGISTRY}
    kwargs = ref.kwargs()
    if ctx is not None:  # kwargs naming another scope than the row's
        kwargs.update(tenant_id=str(ctx.tenant_id), project_id=str(ctx.project_id))
    asyncio.run(run_job(worker_ctx, **kwargs))  # type: ignore[arg-type]


# --- record contract (rule 1) ----------------------------------------------------------------


def test_job_table_meets_the_record_contract_and_is_under_rls_without_exemption() -> None:
    assert TABLE in Base.metadata.tables
    assert [p for p in contract_violations(Base.metadata) if p.startswith(TABLE)] == []
    assert TABLE in rls_tables(Base.metadata)
    assert TABLE not in RLS_EXEMPT


# --- registry (rules 3, 4) -------------------------------------------------------------------


def test_registry_rejects_sync_handlers_bad_names_queues_and_duplicates() -> None:
    registry = JobRegistry()

    def sync(job: JobContext) -> None: ...

    with pytest.raises(TypeError, match="async def"):
        registry.add(JobKind("demo.sync", "batch", sync))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"<module>\.<name>"):
        registry.add(JobKind("NoModule", "batch", echo))
    with pytest.raises(ValueError, match="unknown queue"):
        registry.add(JobKind("demo.x", "urgent", echo))  # type: ignore[arg-type]
    registry.add(JobKind("demo.x", "batch", echo))
    with pytest.raises(ValueError, match="already registered"):
        registry.add(JobKind("demo.x", "interactive", echo))
    with pytest.raises(service.UnknownJobKindError):
        registry.get("demo.y")
    assert registry.kinds() == ("demo.x",)


def test_loader_picks_up_each_modules_jobs_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fresh = JobRegistry()
    monkeypatch.setattr(registry_module, "JOB_REGISTRY", fresh)
    handlers = (
        "from app.modules.jobs.registry import register_job\n\n"
        "@register_job('scanner.read', queue='interactive')\n"
        "async def read(job):\n"
        "    import pytesseract  # heavy import inside the handler (T-0022)\n"
    )
    package = ModulePackages(tmp_path, monkeypatch).write(
        {"scanner": FakeModule(extra={"jobs.py": handlers}), "other": FakeModule()}
    )
    imported = import_job_handlers(discover_modules(package))
    assert imported == [f"{package}.scanner.jobs"]
    assert fresh.get("scanner.read").queue == "interactive"
    assert "pytesseract" not in sys.modules


def test_shipped_modules_register_their_handlers_in_the_worker_registry() -> None:
    assert load_registry() is registry_module.JOB_REGISTRY


# --- enqueue after commit (rule 2), queue routing (rule 3) -----------------------------------


def test_job_is_enqueued_on_its_kinds_queue_only_after_commit(
    db: Database, dispatched: list[JobRef]
) -> None:
    with db.unit_of_work(CTX_A) as s:
        ocr = service.create_job(s, REGISTRY.get("demo.ocr"), {"page": 1})
        mdr = service.create_job(s, REGISTRY.get("demo.mdr"))
        assert dispatched == []  # flushed, not committed
    assert [(r.job_id, r.queue) for r in dispatched] == [
        (ocr.id, "interactive"),
        (mdr.id, "batch"),
    ]
    ref = dispatched[0]
    assert ref.kwargs() == {
        "job_id": str(ocr.id),
        "tenant_id": str(CTX_A.tenant_id),
        "project_id": str(CTX_A.project_id),
        "request_id": None,
    }
    row = load(db, CTX_A, ocr.id)
    assert (row.status, row.queue, row.saq_job_key, row.progress) == (
        "queued",
        "interactive",
        str(ocr.id),
        0,
    )


def test_a_rolled_back_transaction_leaves_no_job_and_enqueues_nothing(
    db: Database, dispatched: list[JobRef]
) -> None:
    created: list[uuid.UUID] = []

    def failing_request() -> None:
        with db.unit_of_work(CTX_A) as s:
            created.append(service.create_job(s, REGISTRY.get("demo.ocr")).id)
            raise RuntimeError("request failed")

    with pytest.raises(RuntimeError, match="request failed"):
        failing_request()
    with db.unit_of_work(CTX_A) as s:
        assert s.scalars(select(Job)).all() == []
    assert len(created) == 1
    assert dispatched == []
    later = create(db, CTX_A)  # a later commit does not resurrect the rolled-back job
    assert [r.job_id for r in dispatched] == [later]


def test_rollback_then_commit_in_the_same_session_dispatches_nothing(
    db: Database, dispatched: list[JobRef]
) -> None:
    with db.unit_of_work(CTX_A) as s:
        service.create_job(s, REGISTRY.get("demo.ocr"))
        s.rollback()
        s.commit()
        kept = service.create_job(s, REGISTRY.get("demo.mdr")).id  # after the rollback
    assert [r.job_id for r in dispatched] == [kept]


def test_a_rolled_back_savepoint_drops_only_its_own_jobs(
    db: Database, dispatched: list[JobRef]
) -> None:
    with db.unit_of_work(CTX_A) as s:
        outer = service.create_job(s, REGISTRY.get("demo.ocr")).id
        savepoint = s.begin_nested()
        service.create_job(s, REGISTRY.get("demo.mdr"))
        savepoint.rollback()
    assert [r.job_id for r in dispatched] == [outer]
    with db.unit_of_work(CTX_A) as s:
        assert [j.id for j in s.scalars(select(Job))] == [outer]


def test_jobs_of_a_released_savepoint_follow_the_outer_transaction(
    db: Database, dispatched: list[JobRef]
) -> None:
    with db.unit_of_work(CTX_A) as s:
        released = s.begin_nested()
        kept = service.create_job(s, REGISTRY.get("demo.ocr")).id
        released.commit()
        s.rollback()  # the outer rollback discards the released savepoint's job too
        s.commit()
        released = s.begin_nested()
        kept = service.create_job(s, REGISTRY.get("demo.ocr")).id
        released.commit()
    assert [r.job_id for r in dispatched] == [kept]


def test_a_failing_dispatcher_does_not_fail_the_commit(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(refs: list[JobRef]) -> None:
        raise RuntimeError("loop closed")

    service.set_dispatcher(broken)
    try:
        with caplog.at_level(logging.ERROR, "kerp.jobs"):
            job_id = create(db, CTX_A)
    finally:
        service.set_dispatcher(None)
    assert load(db, CTX_A, job_id).status == "queued"
    assert "job dispatch failed" in caplog.text


def test_create_job_needs_a_bound_session(db: Database) -> None:
    with pytest.raises(RuntimeError, match="bound"), contextmanager(db.session)() as s:
        service.create_job(s, REGISTRY.get("demo.ocr"))


def test_without_a_dispatcher_rows_wait_for_the_sweep(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    service.set_dispatcher(None)
    with caplog.at_level(logging.WARNING, "kerp.jobs"):
        create(db, CTX_A)
    assert "wait for the sweep" in caplog.text


def test_loop_dispatcher_enqueues_from_worker_threads_and_survives_broker_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Flaky(RecordingBroker):
        async def enqueue(self, ref: JobRef) -> None:
            if ref.request_id == "fail":
                raise ConnectionError("redis down")
            await super().enqueue(ref)

    def ref(request_id: str) -> JobRef:
        return JobRef(uuid.uuid4(), "batch", CTX_A.tenant_id, None, request_id, 60)

    async def scenario() -> list[str | None]:
        broker = Flaky()
        dispatcher = LoopDispatcher(broker, asyncio.get_running_loop())
        thread = threading.Thread(target=dispatcher, args=([ref("thread"), ref("fail")],))
        thread.start()
        thread.join()
        dispatcher([ref("loop")])
        await dispatcher.drain()
        return [r.request_id for r in broker.enqueued]

    with caplog.at_level(logging.ERROR, "kerp.jobs"):
        assert sorted(asyncio.run(scenario()), key=str) == ["loop", "thread"]
    assert "the sweep will retry" in caplog.text


# --- worker: scope from the kwargs (rule 1), update(), sanitised error (rule 5) --------------


def test_worker_runs_the_handler_in_the_scope_from_the_kwargs(
    db: Database, dispatched: list[JobRef]
) -> None:
    job_id = create(db, CTX_A, page=3)
    run(db, dispatched[0])
    job = load(db, CTX_A, job_id)
    assert (job.status, job.progress, job.message, job.attempts) == (
        "succeeded",
        100,
        "half way",
        1,
    )
    assert job.result == {"echo": {"page": 3}, "tenant": str(CTX_A.tenant_id)}
    assert job.started_at is not None
    assert job.finished_at is not None
    run(db, dispatched[0])  # a duplicate delivery of a finished job is a no-op
    assert load(db, CTX_A, job_id).attempts == 1


@pytest.mark.parametrize("other", [CTX_B, CTX_C], ids=["other-project", "other-tenant"])
def test_worker_never_reads_a_row_outside_the_kwargs_scope(
    db: Database, dispatched: list[JobRef], other: TenancyContext
) -> None:
    job_id = create(db, CTX_A)
    run(db, dispatched[0], ctx=other)
    assert load(db, CTX_A, job_id).status == "queued"


def test_failing_handler_stores_a_sanitised_error_and_logs_the_traceback(
    db: Database, dispatched: list[JobRef], caplog: pytest.LogCaptureFixture
) -> None:
    token = request_id_var.set("req-123")  # the enqueuing request
    try:
        boom_id = create(db, CTX_A, "demo.boom")
    finally:
        request_id_var.reset(token)
    refuse_id = create(db, CTX_A, "demo.refuse")
    with caplog.at_level(logging.ERROR, "kerp.jobs"):
        for ref in dispatched:
            run(db, ref)
    failed = load(db, CTX_A, boom_id)
    assert (failed.status, failed.error) == ("failed", "Internal error (ValueError)")
    refused = load(db, CTX_A, refuse_id)
    assert refused.error is not None
    assert refused.error.startswith("Bad file: xxx")
    assert len(refused.error) == MAX_TEXT
    record = next(r for r in caplog.records if r.__dict__.get("job_id") == str(boom_id))
    assert record.__dict__["request_id"] == "req-123"
    assert record.exc_info is not None
    assert "hunter2" in str(record.exc_info[1])  # full detail in the log only


def test_a_cancelled_or_timed_out_job_is_marked_failed(
    db: Database, dispatched: list[JobRef]
) -> None:
    job_id = create(db, CTX_A, "demo.slow")

    async def scenario() -> None:
        ctx: Any = {"db": db, "registry": REGISTRY}
        task = asyncio.create_task(run_job(ctx, **dispatched[0].kwargs()))  # type: ignore[arg-type]
        while load(db, CTX_A, job_id).status != "running":
            await asyncio.sleep(0.01)
        task.cancel()  # what SAQ does when the job's timeout expires
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    job = load(db, CTX_A, job_id)
    assert (job.status, job.error) == ("failed", "Job timed out or was cancelled")


def test_a_result_that_cannot_be_stored_fails_the_job_with_a_sanitised_error(
    db: Database, dispatched: list[JobRef], caplog: pytest.LogCaptureFixture
) -> None:
    job_id = create(db, CTX_A, "demo.not_json")
    with caplog.at_level(logging.ERROR, "kerp.jobs"):
        run(db, dispatched[0])
    job = load(db, CTX_A, job_id)
    assert (job.status, job.error, job.result) == (
        "failed",
        "Internal error (StatementError)",
        None,
    )
    record = next(r for r in caplog.records if r.__dict__.get("job_id") == str(job_id))
    assert record.exc_info is not None


def test_sanitise_error_bounds_and_strips_control_characters() -> None:
    assert service.sanitise_error(service.JobError("")) == "Job failed"
    assert service.sanitise_error(service.JobError("a\tb\nsecond line")) == "a b"
    assert service.sanitise_error(KeyError("secret")) == "Internal error (KeyError)"


def test_progress_is_clamped_and_the_message_bounded(db: Database) -> None:
    job_id = create(db, CTX_A)
    with db.unit_of_work(CTX_A) as s:
        service.report_progress(s, job_id, 250, "m" * 900)
    job = load(db, CTX_A, job_id)
    assert (job.progress, len(job.message or "")) == (100, MAX_TEXT)


# --- sweep (rule 2) --------------------------------------------------------------------------


def age(db: Database, ctx: TenancyContext, ids: list[uuid.UUID]) -> None:
    """Backdate rows (bulk UPDATE: explicit scope in the WHERE, see tenancy/scope.py)."""
    with db.unit_of_work(ctx) as s:
        s.execute(
            update(Job)
            .where(
                Job.id.in_(ids),
                Job.tenant_id == ctx.tenant_id,
                Job.project_id.is_(None)
                if ctx.project_id is None
                else Job.project_id == ctx.project_id,
            )
            .values(created_at=utcnow() - timedelta(minutes=10))
        )


def test_sweep_re_enqueues_only_stuck_rows_saq_does_not_know(db: Database) -> None:
    service.set_dispatcher(None)  # simulate a crash between commit and enqueue
    stuck = create(db, CTX_A, "demo.mdr")
    known = create(db, CTX_A)
    fresh = create(db, CTX_A)
    other = create(db, CTX_B)
    tenant_level = create(db, CTX_T1)
    with db.unit_of_work(CTX_A) as s:  # a kind no longer registered still gets swept
        retired = service.create_job(s, JobKind("demo.retired", "batch", echo)).id
    for ctx, ids in ((CTX_A, [stuck, known, retired]), (CTX_B, [other]), (CTX_T1, [tenant_level])):
        age(db, ctx, ids)
    broker = RecordingBroker()
    broker.known.add(str(known))
    requeued = asyncio.run(service.sweep(db, broker, [CTX_A, CTX_T1], registry=REGISTRY))
    assert set(requeued) == {stuck, tenant_level, retired}
    assert {(r.job_id, r.queue, r.timeout_s) for r in broker.enqueued} == {
        (stuck, "batch", 600),
        (tenant_level, "interactive", 600),
        (retired, "batch", 600),
    }
    assert fresh not in requeued
    assert other not in requeued


# --- HTTP: POST enqueue, GET poll ------------------------------------------------------------


@pytest.fixture
def http(db: Database, dispatched: list[JobRef]) -> Iterator[tuple[TestClient, Caller]]:
    url = str(db.engine.url.render_as_string(hide_password=False))
    app: FastAPI = create_app(Settings(environment="test", database_url=url, redis_url=None))
    app.state.job_registry = REGISTRY
    with TestClient(app) as client:
        yield client, Caller(app)


FIELDS = {
    "id",
    "kind",
    "queue",
    "status",
    "progress",
    "message",
    "result",
    "error",
    "attempts",
    "created_at",
    "started_at",
    "finished_at",
}


def test_post_enqueues_and_get_polls_the_job(
    http: tuple[TestClient, Caller], db: Database, dispatched: list[JobRef]
) -> None:
    client, caller = http
    caller.as_(CTX_A)
    response = client.post(JOBS, json={"kind": "demo.ocr", "payload": {"page": 2}})
    assert response.status_code == 202
    body = response.json()
    assert set(body) == FIELDS
    assert (body["kind"], body["queue"], body["status"], body["progress"]) == (
        "demo.ocr",
        "interactive",
        "queued",
        0,
    )
    assert [str(r.job_id) for r in dispatched] == [body["id"]]
    assert dispatched[0].request_id == response.headers["X-Request-ID"]
    run(db, dispatched[0])
    polled = client.get(f"{JOBS}/{body['id']}").json()
    assert (polled["status"], polled["progress"], polled["message"]) == (
        "succeeded",
        100,
        "half way",
    )
    assert polled["result"]["echo"] == {"page": 2}
    assert polled["error"] is None


@pytest.mark.parametrize("other", [CTX_B, CTX_C, CTX_T1], ids=["project", "tenant", "level"])
def test_polling_another_scopes_job_is_404(
    http: tuple[TestClient, Caller], other: TenancyContext
) -> None:
    client, caller = http
    caller.as_(CTX_A)
    job_id = client.post(JOBS, json={"kind": "demo.mdr"}).json()["id"]
    caller.as_(other)
    response = client.get(f"{JOBS}/{job_id}")
    assert (response.status_code, response.json()) == (404, {"detail": "Not Found"})
    assert client.get(f"{JOBS}/{uuid.uuid4()}").status_code == 404


@pytest.mark.parametrize("kind", ["demo.internal", "demo.nope"])
def test_post_refuses_unknown_and_internal_kinds(
    http: tuple[TestClient, Caller], dispatched: list[JobRef], kind: str
) -> None:
    client, caller = http
    caller.as_(CTX_A)
    response = client.post(JOBS, json={"kind": kind})
    assert response.status_code == 422
    assert dispatched == []


@pytest.mark.parametrize("field", ["tenant_id", "project_id"])
def test_post_rejects_a_scope_in_the_body(
    http: tuple[TestClient, Caller], dispatched: list[JobRef], field: str
) -> None:
    client, caller = http
    caller.as_(CTX_A)
    body = {"kind": "demo.ocr", field: str(CTX_C.tenant_id)}
    assert client.post(JOBS, json=body).status_code == 422  # scope comes from the JWT only
    assert dispatched == []


def test_jobs_api_needs_authentication(http: tuple[TestClient, Caller]) -> None:
    client, caller = http
    caller.as_(None)
    assert client.post(JOBS, json={"kind": "demo.ocr"}).status_code == 401
    assert client.get(f"{JOBS}/{uuid.uuid4()}").status_code == 401


# --- worker process --------------------------------------------------------------------------


def test_worker_builds_one_saq_worker_per_queue_with_its_concurrency(db: Database) -> None:
    settings = Settings(environment="test", jobs_interactive_concurrency=3)
    broker = SaqBroker("redis://localhost:6379/0", prefix="t")
    workers = build_workers(settings, db=db, registry=REGISTRY, broker=broker)
    assert [(w.queue.name, w.concurrency) for w in workers] == [
        ("t:interactive", 3),
        ("t:batch", 1),
    ]
    assert all(set(w.functions) == {"kerp_run_job"} for w in workers)
    ctx: WorkerContext = {"worker": workers[0]}
    assert workers[0].startup is not None
    asyncio.run(workers[0].startup[0](ctx))
    assert (ctx["db"], ctx["registry"]) == (db, REGISTRY)


def test_serve_needs_redis() -> None:
    with pytest.raises(RuntimeError, match="KERP_REDIS_URL"):
        asyncio.run(serve(Settings(environment="test", redis_url=None)))


def test_the_api_process_does_not_import_saq_workers_or_heavy_libs_eagerly() -> None:
    code = (
        "import sys, app.main;"
        " assert 'app.modules.jobs.worker' not in sys.modules, 'worker imported by the API'"
    )
    subprocess.run([sys.executable, "-c", code], cwd=BACKEND, check=True)
