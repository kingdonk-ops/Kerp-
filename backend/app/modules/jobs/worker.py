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

from saq import CronJob, Worker
from saq.types import Context

from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.logging import configure_logging
from app.core.mail.service import Delivery
from app.core.mail.transport import MailTransport, transport_from_settings
from app.core.module_loader.loader import (
    discover_modules,
    import_job_handlers,
    import_setting_defs,
)
from app.core.request_context import request_id_var
from app.core.startup_safety import check_database_role
from app.core.tenancy.context import TenancyContext, use_tenancy
from app.modules.jobs import mail, scheduler, service
from app.modules.jobs.broker import QUEUE_NAMES, RUN_JOB, LoopDispatcher, SaqBroker
from app.modules.jobs.registry import JOB_REGISTRY, JobContext, JobRegistry
from app.modules.settings import cache as settings_cache

logger = logging.getLogger("kerp.jobs")
STOP_SIGNALS: Final = (signal.SIGINT, signal.SIGTERM)
SHUTDOWN_GRACE_S: Final = 8  # running jobs may finish before cancellation (compose stops at 10s)
CANCELLED: Final = service.TIMED_OUT
CANCELLED_RETRY: Final = "Job timed out or was cancelled; it is retried"


class WorkerContext(Context, total=False):
    db: Database
    registry: JobRegistry
    broker: SaqBroker
    settings: Settings
    mail_queue: mail.MailQueue
    mail_delivery: Delivery


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
        # The start commit is shielded: a cancellation (SAQ's job timeout) that arrives while
        # it runs or returns waits for it, then fails the row it may have made ``running``.
        start = asyncio.ensure_future(asyncio.to_thread(_start, db, tenancy, row_id))
        try:
            started = await asyncio.shield(start)
        except asyncio.CancelledError:
            if (begun := await _settle(start)) is not None:
                logger.exception(
                    "job cancelled while starting",
                    extra={"job_id": job_id, "kind": begun[0], "request_id": request_id},
                )
                await _settle_cancelled(ctx, db, tenancy, row_id)
            raise
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
                    JobContext(row_id, kind, tenancy, payload, request_id, update, db)
                )
        except service.RetryLaterError as exc:
            # The handler asked to run again later (T-0218): SAQ retries with back-off while
            # the kind allows more attempts; the last one fails the row with the message.
            message = service.sanitise_error(exc)
            saq_job = ctx.get("job")
            if saq_job is not None and saq_job.retryable:
                logger.warning(
                    "job will be retried",
                    extra={"job_id": job_id, "kind": kind, "request_id": request_id},
                )
                await asyncio.to_thread(_requeue, db, tenancy, row_id, message)
                raise
            await asyncio.to_thread(_finish, db, tenancy, row_id, None, message)
            return
        except asyncio.CancelledError:
            logger.exception(
                "job cancelled", extra={"job_id": job_id, "kind": kind, "request_id": request_id}
            )
            await _settle_cancelled(ctx, db, tenancy, row_id)
            raise
        except Exception as exc:
            logger.exception(
                "job failed", extra={"job_id": job_id, "kind": kind, "request_id": request_id}
            )
            error = service.sanitise_error(exc)
            await asyncio.shield(asyncio.to_thread(_finish, db, tenancy, row_id, None, error))
            return
        try:
            await asyncio.to_thread(_finish, db, tenancy, row_id, result, None)
        except Exception as exc:  # e.g. a result that is not JSON, or a database error
            logger.exception(
                "storing the job result failed",
                extra={"job_id": job_id, "kind": kind, "request_id": request_id},
            )
            error = service.sanitise_error(exc)
            await asyncio.to_thread(_finish, db, tenancy, row_id, None, error)
    finally:
        request_id_var.reset(token)


async def _settle_cancelled(
    ctx: WorkerContext, db: Database, tenancy: TenancyContext, row_id: uuid.UUID
) -> None:
    """Settle a row whose run was cancelled: SAQ's job timeout or a worker shutdown (deploy).

    SAQ runs a retryable job again (a timeout while attempts are left; a shutdown always), so
    the row goes back to ``queued`` for that run, as for ``RetryLaterError`` (T-0346, audit
    item 8); a ``failed`` row would never start again. On the last attempt it fails. Shielded:
    the write lands even if the cancellation is repeated.
    """
    saq_job = ctx.get("job")
    if saq_job is not None and saq_job.retryable:
        write = asyncio.to_thread(_requeue, db, tenancy, row_id, CANCELLED_RETRY)
    else:
        write = asyncio.to_thread(_finish, db, tenancy, row_id, None, CANCELLED)
    await asyncio.shield(write)


async def _settle(
    start: asyncio.Future[tuple[str, dict[str, Any]] | None],
) -> tuple[str, dict[str, Any]] | None:
    """Wait for the start thread despite further cancellations; ``None`` unless it started."""
    while not start.done():
        try:
            await asyncio.wait([start])
        except asyncio.CancelledError:
            continue  # the caller re-raises the first cancellation once the start has landed
    if (exc := start.exception()) is not None:
        logger.error("starting the cancelled job failed", exc_info=exc)
        return None  # nothing was committed: the unit of work rolled back
    return start.result()


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


def _requeue(db: Database, tenancy: TenancyContext, job_id: uuid.UUID, message: str) -> None:
    with db.unit_of_work(tenancy) as session:
        service.requeue_for_retry(session, job_id, message)


def _finish(
    db: Database,
    tenancy: TenancyContext,
    job_id: uuid.UUID,
    result: dict[str, Any] | None,
    error: str | None,
) -> None:
    # A row aborted by a System admin meanwhile keeps its outcome (T-0218).
    with db.unit_of_work(tenancy) as session:
        service.finish_job(session, job_id, result=result, error=error, only_running=True)


def load_registry() -> JobRegistry:
    """Import every module's ``jobs.py`` so its ``register_job`` handlers are known, and its
    ``setting_defs.py`` so its runtime settings are declared (T-0230)."""
    modules = discover_modules()
    import_job_handlers(modules)
    import_setting_defs(modules)
    return JOB_REGISTRY


def build_workers(
    settings: Settings,
    *,
    db: Database,
    registry: JobRegistry,
    broker: SaqBroker,
    mail_transport: MailTransport | None = None,
) -> list[Worker[WorkerContext]]:
    """One SAQ worker per queue, sharing ``db`` and ``registry`` through the job context.

    The interactive worker also runs ``kerp_send_mail`` (T-0058) through ``mail_transport``
    (default: the one the settings name; production refuses the log transport)."""
    transport = mail_transport or transport_from_settings(settings)
    delivery = Delivery(db=db, transport=transport, settings=settings)
    concurrency = {
        "interactive": settings.jobs_interactive_concurrency,
        "batch": settings.jobs_batch_concurrency,
    }

    async def startup(ctx: WorkerContext) -> None:
        ctx["db"] = db
        ctx["registry"] = registry
        ctx["broker"] = broker
        ctx["settings"] = settings
        ctx["mail_queue"] = broker.queues["interactive"]
        ctx["mail_delivery"] = delivery

    # The stuck-job sweep (T-0214), the hourly per-project jobs (T-0215) and the expiry of
    # mail stuck queued (T-0346): batch worker only.
    cron_jobs: dict[str, list[CronJob[WorkerContext]]] = {
        "interactive": [],
        "batch": [
            # ``tracked``: each run's time and outcome for the queue monitor (T-0218).
            CronJob(
                scheduler.tracked(scheduler.sweep_cron),
                cron=scheduler.SWEEP_CRON,
                timeout=scheduler.CRON_TIMEOUT_S,
            ),
            CronJob(
                scheduler.tracked(scheduler.hourly_project_jobs),
                cron=scheduler.HOURLY_CRON,
                timeout=scheduler.CRON_TIMEOUT_S,
            ),
            # Mail stuck ``queued`` past its payload lifetime is failed (T-0346, TD-336).
            CronJob(
                scheduler.tracked(scheduler.expire_mail_cron),
                cron=scheduler.MAIL_EXPIRY_CRON,
                timeout=scheduler.CRON_TIMEOUT_S,
            ),
        ],
    }
    functions: dict[str, list[tuple[str, Any]]] = {
        "interactive": [(RUN_JOB, run_job), (mail.SEND_MAIL, mail.send_mail_job)],
        "batch": [(RUN_JOB, run_job)],
    }
    return [
        Worker[WorkerContext](
            broker.queues[name],
            functions=functions[name],
            concurrency=concurrency[name],
            cron_jobs=cron_jobs[name],
            startup=startup,
            id=f"kerp-{name}",
            shutdown_grace_period_s=SHUTDOWN_GRACE_S,
        )
        for name in QUEUE_NAMES
    ]


async def serve(settings: Settings) -> None:
    """Run both workers until SIGTERM/SIGINT (or cancellation), then stop both.

    Every module's startup check (``register_startup_check``) runs first, before any
    connection is made: a check that raises refuses to start the worker (T-0213). Then the
    database role is checked like the API's (``check_database_role``, T-0342), before Redis."""
    registry = load_registry()
    registry.run_startup_checks(settings)
    transport = transport_from_settings(settings)  # production refuses without SES (T-0058)
    if not settings.redis_url:
        raise RuntimeError("KERP_REDIS_URL is not set")
    db = Database.from_settings(settings)
    try:
        # T-0342: the same database role rule as the API (refused in production).
        check_database_role(db, settings)
    except BaseException:
        db.dispose()
        raise
    broker = SaqBroker(settings.redis_url)
    loop = asyncio.get_running_loop()
    service.set_dispatcher(LoopDispatcher(broker, loop))
    mail_dispatcher = mail.install(broker.queues["interactive"], loop)
    # Runtime settings (T-0230): this process drops its cached values when one changes.
    cached_settings = settings_cache.open_cache(settings.redis_url)
    settings_cache.install(cached_settings)
    workers = build_workers(
        settings, db=db, registry=registry, broker=broker, mail_transport=transport
    )
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
        mail.uninstall()
        await mail_dispatcher.drain()
        settings_cache.install(None)
        cached_settings.close()
        await broker.close()
        db.dispose()


def main() -> None:  # pragma: no cover - process entry point
    settings = get_settings()
    configure_logging(settings)
    asyncio.run(serve(settings))


if __name__ == "__main__":  # pragma: no cover
    main()
