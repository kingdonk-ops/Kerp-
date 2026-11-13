"""Real SAQ over Redis: both queues process, priority is honoured, the worker serves (T-0015).

Needs ``KERP_REDIS_URL`` (CI's PostgreSQL step and ``make up`` provide Redis 7). Every test
uses its own queue prefix, so runs never see each other's jobs.
"""

import asyncio
import os
import signal
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.db import Database
from app.modules.jobs import service
from app.modules.jobs import worker as worker_module
from app.modules.jobs.broker import LoopDispatcher, SaqBroker
from app.modules.jobs.models import Job
from app.modules.jobs.registry import JobContext, JobKind, JobRegistry
from app.modules.jobs.worker import build_workers
from tests.audit_support import migrated_database
from tests.tenancy_support import CTX_A

REDIS_URL = os.environ.get("KERP_REDIS_URL", "")

pytestmark = pytest.mark.skipif(
    not REDIS_URL, reason="needs Redis (KERP_REDIS_URL); runs in CI's PostgreSQL step (TD-052)"
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


class Gate:
    """Batch handler that holds its worker slot until released."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.order: list[str] = []

    async def batch(self, job: JobContext) -> dict[str, Any]:
        self.order.append(f"batch-start:{job.payload['n']}")
        await self.release.wait()
        self.order.append(f"batch-done:{job.payload['n']}")
        return {"n": job.payload["n"]}

    async def interactive(self, job: JobContext) -> dict[str, Any]:
        await job.update(50, "scanning")
        self.order.append("interactive-done")
        return {"ok": True}


async def wait_for(
    db: Database, job_ids: list[uuid.UUID], status: str, timeout: float = 10
) -> uuid.UUID:
    """Wait until one of ``job_ids`` has ``status``; return it."""

    async def poll() -> uuid.UUID:
        while True:
            with db.unit_of_work(CTX_A) as s:
                for job_id in job_ids:
                    if service.get_job(s, job_id).status == status:
                        return job_id
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout)


def test_both_queues_process_and_a_busy_batch_queue_never_delays_interactive(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_module, "SHUTDOWN_GRACE_S", 0)
    gate = Gate()
    registry = JobRegistry()
    batch = registry.add(JobKind("demo.mdr", "batch", gate.batch))
    interactive = registry.add(JobKind("demo.ocr", "interactive", gate.interactive))
    settings = Settings(environment="test", jobs_batch_concurrency=1)

    async def scenario() -> None:
        broker = SaqBroker(REDIS_URL, prefix=f"kerp-test-{uuid.uuid4().hex[:8]}")
        dispatcher = LoopDispatcher(broker, asyncio.get_running_loop())
        service.set_dispatcher(dispatcher)
        workers = build_workers(settings, db=db, registry=registry, broker=broker)
        tasks = [asyncio.create_task(w.start()) for w in workers]
        try:
            with db.unit_of_work(CTX_A) as s:
                batch_ids = [service.create_job(s, batch, {"n": n}).id for n in (1, 2)]
            n = {job_id: i for i, job_id in enumerate(batch_ids, start=1)}
            await dispatcher.drain()
            first = await wait_for(db, batch_ids, "running")
            second = next(i for i in batch_ids if i != first)
            with db.unit_of_work(CTX_A) as s:
                ocr_id = service.create_job(s, interactive).id
            await dispatcher.drain()
            await wait_for(db, [ocr_id], "succeeded")
            # The single batch slot is still held and the other batch job waits behind it.
            assert gate.order == [f"batch-start:{n[first]}", "interactive-done"]
            with db.unit_of_work(CTX_A) as s:
                assert service.get_job(s, second).status == "queued"
            assert await broker.has_job("batch", str(second))
            gate.release.set()
            await wait_for(db, [second], "succeeded")
        finally:
            service.set_dispatcher(None)
            for w in workers:
                await w.stop()
            for t in tasks:
                t.cancel()
            await broker.close()

    asyncio.run(scenario())
    assert len(gate.order) == 5
    with db.unit_of_work(CTX_A) as s:
        jobs = list(s.scalars(select(Job)))
    assert {j.status for j in jobs} == {"succeeded"}


@pytest.mark.parametrize("stop", ["sigterm", "cancel"])
def test_serve_runs_both_workers_until_sigterm_or_cancellation(
    db: Database, monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    url = str(db.engine.url.render_as_string(hide_password=False))
    settings = Settings(environment="test", database_url=url, redis_url=REDIS_URL)
    monkeypatch.setattr(worker_module, "SHUTDOWN_GRACE_S", 0)
    built: list[Any] = []
    real_build = worker_module.build_workers

    def spy(*args: Any, **kwargs: Any) -> Any:
        built.extend(real_build(*args, **kwargs))
        return built

    monkeypatch.setattr(worker_module, "build_workers", spy)

    async def scenario() -> None:
        task = asyncio.create_task(worker_module.serve(settings))
        await asyncio.sleep(0.5)
        assert not task.done()
        if stop == "sigterm":
            signal.raise_signal(signal.SIGTERM)
            await asyncio.wait_for(task, 10)
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert [w.queue.name for w in built] == ["kerp:interactive", "kerp:batch"]
    assert all(w.event.is_set() for w in built)  # both stopped, not only the signalled one
