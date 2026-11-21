"""Retry with back-off for ``RetryLaterError`` (T-0218): the row, the SAQ job, the kind.

No Redis: ``run_job`` is called directly with a stand-in SAQ job (``retryable``) and the broker
is checked on the SAQ ``Job`` it builds. ``test_queue_monitor_saq.py`` runs it on real SAQ.
"""

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.modules.jobs import service
from app.modules.jobs.broker import JobRef, SaqBroker
from app.modules.jobs.registry import JobContext, JobKind, JobRegistry
from app.modules.jobs.worker import run_job
from tests.audit_support import migrated_database
from tests.auth_support import T1

CTX = TenancyContext(tenant_id=T1)


async def later(job: JobContext) -> dict[str, Any]:
    raise service.RetryLaterError("synthetic service unavailable")


RETRYING = JobKind(
    "demo.retrying", "interactive", later, max_attempts=4, retry_delay_s=2, retry_max_delay_s=60
)


class SaqJobStandIn:
    def __init__(self, retryable: bool) -> None:
        self.retryable = retryable


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def dispatched() -> Iterator[list[JobRef]]:
    refs: list[JobRef] = []
    service.set_dispatcher(refs.extend)
    yield refs
    service.set_dispatcher(None)


def _queued(db: Database) -> tuple[uuid.UUID, dict[str, Any]]:
    with db.unit_of_work(CTX) as s:
        job = service.create_job(s, RETRYING, {})
    return job.id, {
        "job_id": str(job.id),
        "tenant_id": str(T1),
        "project_id": None,
        "request_id": None,
    }


def _ctx(db: Database, saq_job: object | None) -> Any:
    registry = JobRegistry()
    registry.add(RETRYING)
    ctx: dict[str, Any] = {"db": db, "registry": registry}
    if saq_job is not None:
        ctx["job"] = saq_job
    return ctx


def test_a_retryable_job_goes_back_to_queued_and_saq_retries_it(
    db: Database, dispatched: list[JobRef]
) -> None:
    job_id, kwargs = _queued(db)
    with pytest.raises(service.RetryLaterError):
        asyncio.run(run_job(_ctx(db, SaqJobStandIn(retryable=True)), **kwargs))
    with db.unit_of_work(CTX) as s:
        row = service.get_job(s, job_id)
        assert (row.status, row.attempts, row.error) == ("queued", 1, None)
        assert row.message == "synthetic service unavailable"


@pytest.mark.parametrize("saq_job", [SaqJobStandIn(retryable=False), None])
def test_the_last_attempt_fails_the_row_with_the_message(
    db: Database, dispatched: list[JobRef], saq_job: object | None
) -> None:
    job_id, kwargs = _queued(db)
    asyncio.run(run_job(_ctx(db, saq_job), **kwargs))  # no raise: SAQ completes it
    with db.unit_of_work(CTX) as s:
        row = service.get_job(s, job_id)
        assert (row.status, row.error) == ("failed", "synthetic service unavailable")


def test_the_ref_carries_the_kind_s_back_off(db: Database, dispatched: list[JobRef]) -> None:
    _queued(db)
    (ref,) = dispatched
    assert (ref.max_attempts, ref.retry_delay_s, ref.retry_max_delay_s) == (4, 2, 60)


def test_the_saq_job_gets_retries_and_capped_exponential_back_off() -> None:
    built: list[Any] = []

    class Queue:
        async def enqueue(self, job: Any) -> None:
            built.append(job)

    broker = SaqBroker("redis://localhost:1/0", prefix="t")
    broker.queues = {"interactive": Queue(), "batch": Queue()}  # type: ignore[dict-item]
    base = (uuid.uuid4(), "interactive", T1, None, None, 30)
    asyncio.run(broker.enqueue(JobRef(*base)))  # type: ignore[arg-type]
    asyncio.run(
        broker.enqueue(
            JobRef(*base, max_attempts=4, retry_delay_s=2, retry_max_delay_s=60)  # type: ignore[arg-type]
        )
    )
    asyncio.run(broker.enqueue(JobRef(*base, max_attempts=3, retry_delay_s=5)))  # type: ignore[arg-type]
    assert [(j.retries, j.retry_delay, j.retry_backoff) for j in built] == [
        (1, 0.0, False),
        (4, 2, 60),
        (3, 5, True),
    ]


def test_a_kind_refuses_invalid_retry_settings() -> None:
    with pytest.raises(ValueError, match="retry settings"):
        JobRegistry().add(JobKind("demo.bad", "batch", later, max_attempts=0))


def test_requeue_leaves_a_row_that_is_no_longer_running(
    db: Database, dispatched: list[JobRef]
) -> None:
    job_id, _ = _queued(db)
    with db.unit_of_work(CTX) as s:
        service.requeue_for_retry(s, job_id, "x")  # still queued, never started: unchanged
        assert service.get_job(s, job_id).message is None
