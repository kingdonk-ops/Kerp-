"""Scheduled work of the jobs module (T-0214, TD-155): the stuck-job sweep, from SAQ cron.

``service.sweep`` re-enqueues ``queued`` rows that never reached Redis (a crash or a Redis
outage between the request's commit and its enqueue). The rows stay under RLS, so the sweep
runs per scope; ``deployment_scopes`` lists them: the deployment tenant's tenant-level scope
plus one per live project. The project list is read in a tenant-level unit of work bound with
``is_system_admin=True`` (owner decision 2026-11-15): that flag reads every project row (and,
since ADR 0016, every project's records of the tenant; the read touches only the project rows),
so no BYPASSRLS is needed. The sweep's own sessions carry the bare scope only.

``hourly_project_jobs`` (T-0215) queues, once an hour, every kind a module registered with
``registry.schedule_hourly_per_project`` (e.g. the uploads orphan sweep) in every live project.

Only the worker imports this module; both cron jobs run on the batch worker (SAQ
``CronJob(unique=True)``: one run per tick across worker replicas).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Final

from app.core.db import utcnow
from app.core.tenancy.context import TenancyContext
from app.modules.jobs import service
from app.modules.jobs.broker import CronRun
from app.modules.jobs.registry import CRON_SCHEDULES, UnknownJobKindError
from app.modules.projects import service as projects

if TYPE_CHECKING:
    from app.core.db import Database
    from app.modules.jobs.registry import JobRegistry

SWEEP_CRON: Final = CRON_SCHEDULES["sweep_cron"]
# Explicit SAQ job timeout for the cron run (SAQ's default is 10 s): one DB round-trip per
# scope plus a Redis lookup per stale row. An interrupted run is resumed by the next tick.
CRON_TIMEOUT_S: Final = 120
# Hourly per-project jobs (T-0215), off the full hour so they do not pile up with other crons.
HOURLY_CRON: Final = CRON_SCHEDULES["hourly_project_jobs"]

logger = logging.getLogger("kerp.jobs")


def deployment_scopes(db: Database, tenant_id: uuid.UUID) -> list[TenancyContext]:
    """The tenant-level scope, then one scope per live project of ``tenant_id``."""
    reader = TenancyContext(tenant_id=tenant_id, is_system_admin=True)
    with db.unit_of_work(reader) as session:
        project_ids = projects.live_project_ids(session)
    return [
        TenancyContext(tenant_id=tenant_id),
        *(TenancyContext(tenant_id=tenant_id, project_id=p) for p in project_ids),
    ]


async def sweep_cron(ctx: Mapping[str, Any]) -> None:
    """SAQ cron: re-enqueue the stuck jobs of every scope of the deployment tenant."""
    settings = ctx["settings"]
    tenant_id = settings.default_tenant_id
    if tenant_id is None:
        logger.warning("jobs sweep skipped: KERP_DEFAULT_TENANT_ID is not set")
        return
    scopes = await asyncio.to_thread(deployment_scopes, ctx["db"], tenant_id)
    requeued = await service.sweep(ctx["db"], ctx["broker"], scopes, registry=ctx["registry"])
    if requeued:
        logger.warning("jobs sweep re-enqueued %d stuck job(s)", len(requeued))


def _queue_hourly(
    db: Database, scope: TenancyContext, registry: JobRegistry, kinds: tuple[str, ...]
) -> int:
    """Add one job row per hourly kind in ``scope``; each is enqueued after the commit."""
    with db.unit_of_work(scope) as session:
        for kind in kinds:
            service.create_job(session, registry.get(kind), {}, source="system")
    return len(kinds)


async def hourly_project_jobs(ctx: Mapping[str, Any]) -> None:
    """SAQ cron: queue every hourly per-project kind in every live project."""
    tenant_id = ctx["settings"].default_tenant_id
    if tenant_id is None:
        logger.warning("hourly project jobs skipped: KERP_DEFAULT_TENANT_ID is not set")
        return
    registry: JobRegistry = ctx["registry"]
    kinds = _known_kinds(registry)
    if not kinds:
        return
    scopes = await asyncio.to_thread(deployment_scopes, ctx["db"], tenant_id)
    for scope in scopes[1:]:  # the projects; the first scope is tenant-level
        try:
            await asyncio.to_thread(_queue_hourly, ctx["db"], scope, registry, kinds)
        except Exception:  # one project's failure must not skip every project after it
            logger.exception(
                "hourly project jobs failed for a project",
                extra={"project_id": str(scope.project_id)},
            )


def _known_kinds(registry: JobRegistry) -> tuple[str, ...]:
    """The scheduled kinds that have a handler; an unknown one is logged and skipped."""
    known: list[str] = []
    for kind in registry.hourly_project_kinds():
        try:
            registry.get(kind)
        except UnknownJobKindError:
            logger.error("hourly project job %s has no registered handler", kind)
            continue
        known.append(kind)
    return tuple(known)


CronFunction = Callable[[Mapping[str, Any]], Awaitable[None]]


def tracked(function: CronFunction) -> CronFunction:
    """Record each run's start, end and outcome for the queue monitor (T-0218).

    Keeps the function's name (SAQ keys the cron job by it). Recording is best effort: a
    Redis error is logged and never changes the run's outcome.
    """

    @functools.wraps(function)
    async def run(ctx: Mapping[str, Any]) -> None:
        started, outcome = utcnow(), "failed"
        try:
            await function(ctx)
            outcome = "succeeded"
        finally:
            monitor = ctx.get("broker")
            if monitor is not None:
                try:
                    await monitor.record_cron_run(
                        function.__name__, CronRun(started, utcnow(), outcome)
                    )
                except Exception:
                    logger.exception("recording the cron run failed")

    return run
