"""The queue seam: ``JobBroker`` (SAQ over Redis in production) and the post-commit dispatcher.

Two SAQ queues, ``<prefix>:interactive`` and ``<prefix>:batch`` (TD-027, ADR 0007 rule 3).
Each is drained by its own worker with its own concurrency, so a full batch queue (MDR, P6)
never delays an interactive job (OCR, barcode). The SAQ job key is the row id, so enqueueing
the same row twice is a no-op.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol, cast

from saq import Job as SaqJob
from saq import Status
from saq.queue.redis import RedisQueue

from app.modules.jobs.registry import QueueName

RUN_JOB: Final = "kerp_run_job"  # the SAQ function every job row runs through
QUEUE_NAMES: Final[tuple[QueueName, ...]] = ("interactive", "batch")

logger = logging.getLogger("kerp.jobs")


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


@dataclass(frozen=True, slots=True)
class JobRef:
    """What the worker needs to bind the scope and find the row (the SAQ job kwargs)."""

    job_id: uuid.UUID
    queue: QueueName
    tenant_id: uuid.UUID
    project_id: uuid.UUID | None
    request_id: str | None
    timeout_s: int
    # Retries with back-off for ``RetryLaterError`` (T-0218); 1 = no retry.
    max_attempts: int = 1
    retry_delay_s: float = 0.0
    retry_max_delay_s: float | None = None

    @property
    def key(self) -> str:
        return str(self.job_id)

    def kwargs(self) -> dict[str, str | None]:
        return {
            "job_id": str(self.job_id),
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id) if self.project_id else None,
            "request_id": self.request_id,
        }


class JobBroker(Protocol):
    async def enqueue(self, ref: JobRef) -> None: ...

    async def has_job(self, queue: QueueName, key: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class QueueCounts:
    """One queue's numbers (queue monitor, T-0218): never a job's kwargs or payload."""

    queued: int
    active: int
    scheduled: int
    oldest_queued_at: datetime | None


@dataclass(frozen=True, slots=True)
class CronRun:
    """The last run of a cron job, as the batch worker recorded it."""

    started_at: datetime
    finished_at: datetime | None
    outcome: str  # ``succeeded`` | ``failed``


class QueueMonitor(Protocol):
    """What the System console reads from and does to the queues (T-0218)."""

    async def counts(self, queue: QueueName) -> QueueCounts: ...

    async def abort(self, queue: QueueName, key: str, reason: str) -> bool: ...

    async def record_cron_run(self, name: str, run: CronRun) -> None: ...

    async def cron_runs(self, names: Iterable[str]) -> dict[str, CronRun]: ...

    async def next_cron_runs(self, names: Iterable[str]) -> dict[str, datetime]: ...


class SaqBroker:
    """``JobBroker`` on SAQ ``RedisQueue``s. Connects lazily on first use."""

    def __init__(self, redis_url: str, *, prefix: str = "kerp") -> None:
        self.prefix = prefix
        self.queues: dict[QueueName, RedisQueue] = {
            name: RedisQueue.from_url(redis_url, name=f"{prefix}:{name}") for name in QUEUE_NAMES
        }

    async def enqueue(self, ref: JobRef) -> None:
        job = SaqJob(
            function=RUN_JOB,
            key=ref.key,
            kwargs=ref.kwargs(),
            timeout=ref.timeout_s,
            retries=ref.max_attempts,
            retry_delay=ref.retry_delay_s,
            retry_backoff=(
                False
                if ref.max_attempts <= 1 or ref.retry_delay_s <= 0
                else (ref.retry_max_delay_s or True)
            ),
        )
        await self.queues[ref.queue].enqueue(job)

    async def has_job(self, queue: QueueName, key: str) -> bool:
        return await self.queues[queue].job(key) is not None

    # --- queue monitor (T-0218): counts and timestamps only, never kwargs ------------------

    async def counts(self, queue: QueueName) -> QueueCounts:
        saq_queue = self.queues[queue]
        queued = await saq_queue.count("queued")
        active = await saq_queue.count("active")
        incomplete = await saq_queue.count("incomplete")
        oldest: datetime | None = None
        # The worker dequeues from the left (``BLMOVE ... LEFT``): index 0 waited longest.
        pending = saq_queue.redis.lindex(saq_queue.namespace("queued"), 0)
        head = await cast("Awaitable[bytes | str | None]", pending)
        if head is not None:
            job = await saq_queue.job(saq_queue.job_key_from_id(_text(head)))
            if job is not None and job.queued:
                oldest = datetime.fromtimestamp(job.queued / 1000, UTC)
        return QueueCounts(
            queued=queued,
            active=active,
            scheduled=max(0, incomplete - queued - active),
            oldest_queued_at=oldest,
        )

    async def abort(self, queue: QueueName, key: str, reason: str) -> bool:
        """Abort the SAQ job ``key`` (queued, scheduled or running); ``False`` if unknown."""
        saq_queue = self.queues[queue]
        job = await saq_queue.job(key)
        if job is None:
            return False
        await saq_queue.abort(job, reason)
        return True

    def _cron_key(self, name: str) -> str:
        return f"{self.prefix}:cron:{name}"

    async def record_cron_run(self, name: str, run: CronRun) -> None:
        value = {
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "outcome": run.outcome,
        }
        await self.queues["batch"].redis.set(self._cron_key(name), json.dumps(value))

    async def next_cron_runs(self, names: Iterable[str]) -> dict[str, datetime]:
        """When the batch worker has scheduled each cron job next (SAQ key ``cron:<name>``)."""
        upcoming: dict[str, datetime] = {}
        for name in names:
            job = await self.queues["batch"].job(f"cron:{name}")
            if job is not None and job.status == Status.QUEUED and job.scheduled:
                upcoming[name] = datetime.fromtimestamp(job.scheduled, UTC)
        return upcoming

    async def cron_runs(self, names: Iterable[str]) -> dict[str, CronRun]:
        names = list(names)
        if not names:
            return {}
        values = await self.queues["batch"].redis.mget([self._cron_key(n) for n in names])
        runs: dict[str, CronRun] = {}
        for name, raw in zip(names, values, strict=True):
            if raw is None:
                continue
            try:
                data = json.loads(raw)
                runs[name] = CronRun(
                    started_at=datetime.fromisoformat(data["started_at"]),
                    finished_at=(
                        datetime.fromisoformat(data["finished_at"])
                        if data.get("finished_at")
                        else None
                    ),
                    outcome=str(data["outcome"]),
                )
            except (ValueError, KeyError, TypeError):
                logger.warning("unreadable cron run record", extra={"cron": name})
        return runs

    async def close(self) -> None:
        for queue in self.queues.values():
            await queue.disconnect()


class LoopDispatcher:
    """Hands committed job refs to the broker on ``loop``, from any thread.

    Called from SQLAlchemy's ``after_commit`` hook, which runs in FastAPI's worker threads
    or in the event loop's own thread. Fire and forget: a failed enqueue is logged and the
    row stays ``queued`` for ``service.sweep`` to pick up.
    """

    def __init__(self, broker: JobBroker, loop: asyncio.AbstractEventLoop) -> None:
        self.broker = broker
        self.loop = loop
        self._tasks: set[asyncio.Task[None]] = set()  # touched on ``loop`` only

    def __call__(self, refs: Iterable[JobRef]) -> None:
        refs = list(refs)
        try:
            running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self._start(refs)
        else:
            self.loop.call_soon_threadsafe(self._start, refs)

    def _start(self, refs: list[JobRef]) -> None:
        for ref in refs:
            task = self.loop.create_task(self._enqueue(ref))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _enqueue(self, ref: JobRef) -> None:
        try:
            await self.broker.enqueue(ref)
        except Exception:
            logger.exception(
                "enqueue failed; the sweep will retry",
                extra={"job_id": ref.key, "request_id": ref.request_id},
            )

    async def drain(self) -> None:
        """Wait for every scheduled enqueue (shutdown, tests). Call on ``loop``."""
        await asyncio.sleep(0)  # let refs handed over from other threads start
        while self._tasks:
            await asyncio.gather(*self._tasks)
