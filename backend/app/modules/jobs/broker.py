"""The queue seam: ``JobBroker`` (SAQ over Redis in production) and the post-commit dispatcher.

Two SAQ queues, ``<prefix>:interactive`` and ``<prefix>:batch`` (TD-027, ADR 0007 rule 3).
Each is drained by its own worker with its own concurrency, so a full batch queue (MDR, P6)
never delays an interactive job (OCR, barcode). The SAQ job key is the row id, so enqueueing
the same row twice is a no-op.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Protocol

from saq import Job as SaqJob
from saq.queue.redis import RedisQueue

from app.modules.jobs.registry import QueueName

RUN_JOB: Final = "kerp_run_job"  # the SAQ function every job row runs through
QUEUE_NAMES: Final[tuple[QueueName, ...]] = ("interactive", "batch")

logger = logging.getLogger("kerp.jobs")


@dataclass(frozen=True, slots=True)
class JobRef:
    """What the worker needs to bind the scope and find the row (the SAQ job kwargs)."""

    job_id: uuid.UUID
    queue: QueueName
    tenant_id: uuid.UUID
    project_id: uuid.UUID | None
    request_id: str | None
    timeout_s: int

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


class SaqBroker:
    """``JobBroker`` on SAQ ``RedisQueue``s. Connects lazily on first use."""

    def __init__(self, redis_url: str, *, prefix: str = "kerp") -> None:
        self.queues: dict[QueueName, RedisQueue] = {
            name: RedisQueue.from_url(redis_url, name=f"{prefix}:{name}") for name in QUEUE_NAMES
        }

    async def enqueue(self, ref: JobRef) -> None:
        job = SaqJob(
            function=RUN_JOB, key=ref.key, kwargs=ref.kwargs(), timeout=ref.timeout_s, retries=1
        )
        await self.queues[ref.queue].enqueue(job)

    async def has_job(self, queue: QueueName, key: str) -> bool:
        return await self.queues[queue].job(key) is not None

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
