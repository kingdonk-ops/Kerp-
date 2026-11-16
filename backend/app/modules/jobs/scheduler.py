"""Scheduled work of the jobs module (T-0214, TD-155): the stuck-job sweep, from SAQ cron.

``service.sweep`` re-enqueues ``queued`` rows that never reached Redis (a crash or a Redis
outage between the request's commit and its enqueue). The rows stay under RLS, so the sweep
runs per scope; ``deployment_scopes`` lists them: the deployment tenant's tenant-level scope
plus one per live project. The project list is read in a tenant-level unit of work bound with
``is_system_admin=True`` (owner decision 2026-11-15): that flag reads every project row and
never a project-scoped record, so no BYPASSRLS is needed. The sweep's own sessions carry the
bare scope only.

Only the worker imports this module; the cron job runs on the batch worker every five
minutes (SAQ ``CronJob(unique=True)``: one run per tick across worker replicas).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from app.core.tenancy.context import TenancyContext
from app.modules.jobs import service
from app.modules.projects import service as projects

if TYPE_CHECKING:
    from app.core.db import Database

SWEEP_CRON: Final = "*/5 * * * *"
# Explicit SAQ job timeout for the cron run (SAQ's default is 10 s): one DB round-trip per
# scope plus a Redis lookup per stale row. An interrupted run is resumed by the next tick.
CRON_TIMEOUT_S: Final = 120

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
