"""System console: deployment stats and health probes at ``/api/v1/admin/system`` (T-0219,
diff §4.7).

Every endpoint is ``system_admin`` only: any other caller, a PER-09 of every project included,
gets 403. Every call is audited in the tenant-level chain ``admin-system:<tenant>``.

* ``GET /system/stats``: projects (active, soft-deleted), users (active, deactivated),
  enrolled devices (active, revoked), every registered module stats provider's counts and the
  cached object-store totals per storage class with their "as of" time. Numbers only.
* ``POST /system/stats/object-store/refresh``: queue the ``batch`` job that lists the object
  store (202); one run at a time (409 with the run in progress).
* ``GET /system/health``: PostgreSQL, Redis, object store (head bucket), malware scanner
  (clamd ``PING``), each with a 2 s deadline: status ``ok | degraded | down`` and latency;
  never a host name, connection string or credential.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.core.auth.access import CallerIsSystemAdmin
from app.core.auth.deps import CurrentPrincipal
from app.core.config import Settings, get_settings
from app.core.db import Database, DbSession, utcnow
from app.core.module_loader.gate import registry_of
from app.modules.admin import health, system_stats
from app.modules.admin.jobs import OBJECT_STORE_KIND
from app.modules.admin.schemas import (
    HealthProbeRead,
    ObjectStoreRefreshRead,
    SystemHealthRead,
    SystemStatsRead,
)
from app.modules.jobs import service as jobs

STATS = "/system/stats"
HEALTH = "/system/health"

router = APIRouter(tags=["admin"])


def _require_system_admin(is_admin: CallerIsSystemAdmin) -> None:
    if not is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


SystemAdminOnly = Depends(_require_system_admin)


@router.get(STATS, response_model=SystemStatsRead, dependencies=[SystemAdminOnly])
def deployment_stats(
    request: Request, principal: CurrentPrincipal, session: DbSession
) -> SystemStatsRead:
    stats = system_stats.deployment_stats(session, modules=registry_of(request.app).names())
    system_stats.audit_call(session, "stats_viewed", principal.user_id)
    return stats


@router.post(
    f"{STATS}/object-store/refresh",
    response_model=ObjectStoreRefreshRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[SystemAdminOnly],
    responses={409: {"model": ObjectStoreRefreshRead, "description": "a run is in progress"}},
)
def refresh_object_store(
    principal: CurrentPrincipal, session: DbSession
) -> ObjectStoreRefreshRead | JSONResponse:
    job, created = jobs.create_single_run_job(
        session, jobs.JOB_REGISTRY.get(OBJECT_STORE_KIND), {}, actor_id=principal.user_id
    )
    event = "object_store_refresh_queued" if created else "object_store_refresh_running"
    system_stats.audit_call(session, event, principal.user_id, {"job_id": str(job.id)})
    body = ObjectStoreRefreshRead.model_validate(
        {"job_id": job.id, "status": "running" if job.status == "running" else "queued"}
    )
    if created:
        return body
    # Returned, not raised: the session commits, so the refused call is audited too.
    return JSONResponse(body.model_dump(mode="json"), status_code=status.HTTP_409_CONFLICT)


def _settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()


@router.get(HEALTH, response_model=SystemHealthRead, dependencies=[SystemAdminOnly])
def system_health(
    request: Request, principal: CurrentPrincipal, session: DbSession
) -> SystemHealthRead:
    db: Database | None = getattr(request.app.state, "db", None)
    results = health.run_probes(health.default_probes(db, _settings(request)))
    system_stats.audit_call(
        session,
        "health_checked",
        principal.user_id,
        {"statuses": {r.name: r.status for r in results}},
    )
    return SystemHealthRead(
        status=health.overall(results),
        checked_at=utcnow(),
        probes=[
            HealthProbeRead(name=r.name, status=r.status, latency_ms=r.latency_ms) for r in results
        ],
    )
