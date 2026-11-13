"""The SAQ worker process: ``python -m app.modules.jobs.worker`` (T-0015, ADR 0007).

One process runs two SAQ workers on one event loop, one per queue, each with its own
concurrency (``KERP_JOBS_INTERACTIVE_CONCURRENCY``, ``KERP_JOBS_BATCH_CONCURRENCY``).

Every job row runs through ``run_job``: it binds the tenant and project from the SAQ kwargs
*before* it reads the row (the table stays under RLS), calls the kind's ``async`` handler,
and stores the result or a sanitised error. Database calls run in a thread so a slow query
never stalls the other jobs on the loop.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from typing import Any, Final

from saq import Worker
from saq.types import Context

from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.logging import configure_logging
from app.core.module_loader.loader import discover_modules, import_job_handlers
from app.core.request_context import request_id_var
from app.core.tenancy.context import TenancyContext, use_tenancy
from app.modules.jobs import service
from app.modules.jobs.broker import QUEUE_NAMES, RUN_JOB, LoopDispatcher, SaqBroker
from app.modules.jobs.registry import JOB_REGISTRY, JobContext, JobRegistry

logger = logging.getLogger("kerp.jobs")
STOP_SIGNALS: Final = (signal.SIGINT, signal.SIGTERM)
SHUTDOWN_GRACE_S: Final = 8  # running jobs may finish before cancellation (compose stops at 10s)


class WorkerContext(Context, total=False):
    db: Database
    registry: JobRegistry


async def run_job(
    ctx: WorkerContext,
    *,
    job_id: str,
    tenant_id: str,
    project_id: str | None,
    request_id: str | None,
) -> None:
    """Run one job row in the scope named by the kwargs (never an inherited context)."""
    db, registry = ctx["db"], ctx["registry"]
    row_id = uuid.UUID(job_id)
    tenancy = TenancyContext(
        tenant_id=uuid.UUID(tenant_id), project_id=uuid.UUID(project_id) if project_id else None
    )
    token = request_id_var.set(request_id)
    try:
        started = await asyncio.to_thread(_start, db, tenancy, row_id)
        if started is None:
            logger.warning("job not in its scope or already finished", extra={"job_id": job_id})
            return
        kind, payload = started

        async def update(progress: int, message: str | None = None) -> None:
            await asyncio.to_thread(_progress, db, tenancy, row_id, progress, message)

        try:
            handler = registry.get(kind).handler
            with use_tenancy(tenancy):
                result = await handler(
                    JobContext(row_id, kind, tenancy, payload, request_id, update)
                )
        except (Exception, asyncio.CancelledError) as exc:
            logger.exception(
                "job failed", extra={"job_id": job_id, "kind": kind, "request_id": request_id}
            )
            error = (
                "Job timed out or was cancelled"
                if isinstance(exc, asyncio.CancelledError)
                else service.sanitise_error(exc)
            )
            await asyncio.shield(asyncio.to_thread(_finish, db, tenancy, row_id, None, error))
            if isinstance(exc, asyncio.CancelledError):
                raise
            return
        await asyncio.to_thread(_finish, db, tenancy, row_id, result, None)
    finally:
        request_id_var.reset(token)


def _start(
    db: Database, tenancy: TenancyContext, job_id: uuid.UUID
) -> tuple[str, dict[str, Any]] | None:
    with db.unit_of_work(tenancy) as session:
        job = service.start_job(session, job_id)
        return (job.kind, dict(job.payload)) if job is not None else None


def _progress(
    db: Database, tenancy: TenancyContext, job_id: uuid.UUID, progress: int, message: str | None
) -> None:
    with db.unit_of_work(tenancy) as session:
        service.report_progress(session, job_id, progress, message)


def _finish(
    db: Database,
    tenancy: TenancyContext,
    job_id: uuid.UUID,
    result: dict[str, Any] | None,
    error: str | None,
) -> None:
    with db.unit_of_work(tenancy) as session:
        service.finish_job(session, job_id, result=result, error=error)


def load_registry() -> JobRegistry:
    """Import every module's ``jobs.py`` so its ``register_job`` handlers are known."""
    import_job_handlers(discover_modules())
    return JOB_REGISTRY


def build_workers(
    settings: Settings,
    *,
    db: Database,
    registry: JobRegistry,
    broker: SaqBroker,
) -> list[Worker[WorkerContext]]:
    """One SAQ worker per queue, sharing ``db`` and ``registry`` through the job context."""
    concurrency = {
        "interactive": settings.jobs_interactive_concurrency,
        "batch": settings.jobs_batch_concurrency,
    }

    async def startup(ctx: WorkerContext) -> None:
        ctx["db"] = db
        ctx["registry"] = registry

    return [
        Worker[WorkerContext](
            broker.queues[name],
            functions=[(RUN_JOB, run_job)],
            concurrency=concurrency[name],
            startup=startup,
            id=f"kerp-{name}",
            shutdown_grace_period_s=SHUTDOWN_GRACE_S,
        )
        for name in QUEUE_NAMES
    ]


async def serve(settings: Settings) -> None:
    """Run both workers until SIGTERM/SIGINT (or cancellation), then stop both."""
    if not settings.redis_url:
        raise RuntimeError("KERP_REDIS_URL is not set")
    db = Database.from_settings(settings)
    broker = SaqBroker(settings.redis_url)
    loop = asyncio.get_running_loop()
    service.set_dispatcher(LoopDispatcher(broker, loop))
    workers = build_workers(settings, db=db, registry=load_registry(), broker=broker)
    stopping = asyncio.Event()
    # Each SAQ worker would install its own SIGTERM/SIGINT handler (the last one wins and only
    # that worker would stop), so the signals are handled here and stop both at once.
    for worker in workers:
        worker.SIGNALS = []
    for signum in STOP_SIGNALS:
        loop.add_signal_handler(signum, stopping.set)
    tasks = [asyncio.create_task(worker.start()) for worker in workers]
    signalled = asyncio.create_task(stopping.wait())
    try:
        await asyncio.wait([*tasks, signalled], return_when=asyncio.FIRST_COMPLETED)
    finally:
        signalled.cancel()
        for signum in STOP_SIGNALS:
            loop.remove_signal_handler(signum)
        await asyncio.gather(*(worker.stop() for worker in workers))
        await asyncio.gather(*tasks, return_exceptions=True)
        service.set_dispatcher(None)
        await broker.close()
        db.dispose()


def main() -> None:  # pragma: no cover - process entry point
    settings = get_settings()
    configure_logging(settings)
    asyncio.run(serve(settings))


if __name__ == "__main__":  # pragma: no cover
    main()
