"""The queue monitor and job retries on real SAQ over Redis (T-0218).

Needs ``KERP_REDIS_URL`` (CI's PostgreSQL step and ``make up`` provide Redis 7). Every test
uses its own queue prefix, so runs never see each other's jobs. All data is synthetic.
"""

import asyncio
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from saq import Job as SaqJob

from app.core.config import Settings
from app.core.db import Database, utcnow
from app.modules.jobs import service
from app.modules.jobs.broker import CronRun, JobRef, LoopDispatcher, SaqBroker
from app.modules.jobs.registry import JobContext, JobKind, JobRegistry
from app.modules.jobs.worker import build_workers
from tests.audit_support import migrated_database
from tests.tenancy_support import CTX_A

REDIS_URL = os.environ.get("KERP_REDIS_URL", "")
SECRET = "synthetic-secret-5be1"

pytestmark = pytest.mark.skipif(
    not REDIS_URL, reason="needs Redis (KERP_REDIS_URL); runs in CI's PostgreSQL step (TD-052)"
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def _broker() -> SaqBroker:
    return SaqBroker(REDIS_URL, prefix=f"kerp-test-{uuid.uuid4().hex[:8]}")


def _ref(queue: str = "interactive") -> JobRef:
    return JobRef(uuid.uuid4(), queue, CTX_A.tenant_id, None, None, 30)  # type: ignore[arg-type]


def test_counts_oldest_age_abort_and_kwargs_stay_inside() -> None:
    async def scenario() -> None:
        broker = _broker()
        try:
            empty = await broker.counts("interactive")
            assert (empty.queued, empty.active, empty.scheduled) == (0, 0, 0)
            assert empty.oldest_queued_at is None
            first, second = _ref(), _ref()
            await broker.enqueue(first)
            await broker.enqueue(second)
            later = SaqJob(
                function="kerp_run_job",
                key="later",
                kwargs={"token": SECRET},
                scheduled=int((utcnow() + timedelta(hours=1)).timestamp()),
            )
            await broker.queues["interactive"].enqueue(later)
            counts = await broker.counts("interactive")
            assert (counts.queued, counts.active, counts.scheduled) == (2, 0, 1)
            assert counts.oldest_queued_at is not None
            assert abs((utcnow() - counts.oldest_queued_at).total_seconds()) < 60
            assert SECRET not in repr(counts)
            assert await broker.abort("interactive", first.key, service.ABORTED) is True
            assert await broker.abort("interactive", "no-such-job", service.ABORTED) is False
            after = await broker.counts("interactive")
            assert after.queued == 1
            assert (await broker.counts("batch")).queued == 0
        finally:
            await broker.close()

    asyncio.run(scenario())


def test_cron_runs_are_recorded_and_the_next_run_is_read_from_saq() -> None:
    async def scenario() -> None:
        broker = _broker()
        try:
            assert await broker.cron_runs([]) == {}
            started = datetime(2026, 11, 21, 3, 17, tzinfo=UTC)
            run = CronRun(started, started + timedelta(seconds=3), "succeeded")
            await broker.record_cron_run("sweep_cron", run)
            await broker.queues["batch"].redis.set(f"{broker.prefix}:cron:broken", "{not json")
            runs = await broker.cron_runs(["sweep_cron", "hourly_project_jobs", "broken"])
            assert runs == {"sweep_cron": run}
            assert await broker.next_cron_runs(["sweep_cron"]) == {}
            at = int((utcnow() + timedelta(minutes=4)).timestamp())
            cron = SaqJob(function="sweep_cron", key="cron:sweep_cron", scheduled=at)
            await broker.queues["batch"].enqueue(cron)
            upcoming = await broker.next_cron_runs(["sweep_cron", "hourly_project_jobs"])
            assert upcoming == {"sweep_cron": datetime.fromtimestamp(at, UTC)}
        finally:
            await broker.close()

    asyncio.run(scenario())


def test_a_retry_later_job_is_run_again_by_saq_and_then_succeeds(db: Database) -> None:
    calls: list[int] = []

    async def flaky(job: JobContext) -> dict[str, Any]:
        calls.append(1)
        if len(calls) < 3:
            raise service.RetryLaterError("synthetic scanner unavailable")
        return {"ok": True}

    registry = JobRegistry()
    kind = registry.add(
        JobKind("demo.flaky", "interactive", flaky, max_attempts=5, retry_delay_s=0.05)
    )
    settings = Settings(environment="test")

    async def scenario() -> uuid.UUID:
        broker = _broker()
        dispatcher = LoopDispatcher(broker, asyncio.get_running_loop())
        service.set_dispatcher(dispatcher)
        workers = build_workers(settings, db=db, registry=registry, broker=broker)
        tasks = [asyncio.create_task(w.start()) for w in workers]
        try:
            with db.unit_of_work(CTX_A) as s:
                job_id = service.create_job(s, kind, {}).id
            await dispatcher.drain()

            async def done() -> None:
                while True:
                    with db.unit_of_work(CTX_A) as s:
                        if service.get_job(s, job_id).status == "succeeded":
                            return
                    await asyncio.sleep(0.05)

            await asyncio.wait_for(done(), 20)
            return job_id
        finally:
            service.set_dispatcher(None)
            for w in workers:
                await w.stop()
            for t in tasks:
                t.cancel()
            await broker.close()

    job_id = asyncio.run(scenario())
    assert len(calls) == 3
    with db.unit_of_work(CTX_A) as s:
        assert service.get_job(s, job_id).attempts == 3
