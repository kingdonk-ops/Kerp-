"""System console: queue monitor, mounted with the admin API at ``/api/v1/admin/system``
(T-0218, absorbed T-0220; diff §4.7).

Every endpoint is ``system_admin`` only: any other caller, a PER-09 of every project included,
gets 403 (an admin surface, not a project record). KERP's own endpoints, not SAQ's web UI
(it has no KERP auth and is never mounted).

* ``GET /system/queues``: per queue (``interactive``, ``batch``) SAQ's queued, active and
  scheduled counts, the oldest queued job's age and the failed job rows; the batch worker's
  cron jobs with last run, outcome and next run.
* ``GET /system/queues/failed``: failed job rows (kind, queue, project code, attempts, error
  class, message <= 500 characters), never a payload, result or SAQ kwargs.
* ``POST /system/queues/failed/{job_id}/retry``: queue a failed job again (else 409).
* ``POST /system/queues/{job_id}/abort``: fail a queued or running job and abort it in SAQ
  (else 409).

Retry and abort are written to the tenant-level console chain ``admin-system:<tenant>`` with
the actor and the job kind. Rows are read under the caller's normal RLS (an unknown or
invisible id is 404); the jobs module does all of it through its ``service.py``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Final, Literal

from anyio import from_thread
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.auth.access import CallerIsSystemAdmin
from app.core.auth.deps import CurrentPrincipal
from app.core.db import Database, DbSession
from app.modules.admin.schemas import (
    CronJobRead,
    FailedJobRead,
    JobActionRead,
    QueueOverviewRead,
    QueueRead,
)
from app.modules.jobs import service as jobs
from app.modules.jobs.service import MAX_FAILED_LIST

logger = logging.getLogger("kerp.admin")

QUEUES: Final = "/system/queues"

router = APIRouter(tags=["admin"])


def _require_system_admin(is_admin: CallerIsSystemAdmin) -> None:
    if not is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


SystemAdminOnly = Depends(_require_system_admin)


def _unavailable(what: str) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"{what} is not configured")


def _database(request: Request) -> Database:
    db: Database | None = getattr(request.app.state, "db", None)
    if db is None:
        raise _unavailable("the database")
    return db


def _conflict(exc: jobs.JobStateError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, {"code": "job_state", "message": str(exc)})


def _outcome(value: str | None) -> Literal["succeeded", "failed"] | None:
    if value == "succeeded":
        return "succeeded"
    return "failed" if value == "failed" else None


@router.get(QUEUES, response_model=QueueOverviewRead, dependencies=[SystemAdminOnly])
def queue_overview(session: DbSession) -> QueueOverviewRead:
    monitor = jobs.get_queue_monitor()
    if monitor is None:
        raise _unavailable("the job queue")
    failed = jobs.failed_counts(session)
    queues = from_thread.run(jobs.queue_statuses, monitor, failed)
    crons = from_thread.run(jobs.cron_statuses, monitor)
    return QueueOverviewRead(
        queues=[
            QueueRead(
                name=q.name,
                queued=q.queued,
                active=q.active,
                scheduled=q.scheduled,
                failed=q.failed,
                oldest_queued_age_s=q.oldest_queued_age_s,
            )
            for q in queues
        ],
        cron_jobs=[
            CronJobRead(
                name=c.name,
                schedule=c.schedule,
                last_run_at=c.last_run_at,
                last_finished_at=c.last_finished_at,
                last_outcome=_outcome(c.last_outcome),
                next_run_at=c.next_run_at,
            )
            for c in crons
        ],
    )


@router.get(f"{QUEUES}/failed", response_model=list[FailedJobRead], dependencies=[SystemAdminOnly])
def failed_jobs(
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_FAILED_LIST)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[FailedJobRead]:
    return [
        FailedJobRead(
            id=j.id,
            kind=j.kind,
            queue="interactive" if j.queue == "interactive" else "batch",
            project_id=j.project_id,
            project_code=j.project_code,
            attempts=j.attempts,
            error_class=j.error_class,
            message=j.message,
            failed_at=j.failed_at,
        )
        for j in jobs.list_failed_jobs(session, limit=limit, offset=offset)
    ]


def _action(job_id: uuid.UUID, kind: str, state: str) -> JobActionRead:
    return JobActionRead.model_validate({"id": job_id, "kind": kind, "status": state})


@router.post(
    f"{QUEUES}/failed/{{job_id}}/retry",
    response_model=JobActionRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[SystemAdminOnly],
)
def retry_job(
    job_id: uuid.UUID, principal: CurrentPrincipal, request: Request, session: DbSession
) -> JobActionRead:
    try:
        job = jobs.retry_failed_job(
            session, _database(request), job_id, actor_id=principal.user_id
        )
    except jobs.JobStateError as exc:
        raise _conflict(exc) from None
    return _action(job.id, job.kind, "queued")


@router.post(
    f"{QUEUES}/{{job_id}}/abort", response_model=JobActionRead, dependencies=[SystemAdminOnly]
)
def abort_job(
    job_id: uuid.UUID, principal: CurrentPrincipal, request: Request, session: DbSession
) -> JobActionRead:
    try:
        job = jobs.abort_job(session, _database(request), job_id, actor_id=principal.user_id)
    except jobs.JobStateError as exc:
        raise _conflict(exc) from None
    monitor = jobs.get_queue_monitor()
    if monitor is not None:
        queue: jobs.QueueName = "interactive" if job.queue == "interactive" else "batch"
        try:
            from_thread.run(monitor.abort, queue, str(job.id), jobs.ABORTED)
        except Exception:  # the row is failed already; the worker never runs or finishes it
            logger.exception("aborting the SAQ job failed", extra={"job_id": str(job.id)})
    return _action(job.id, job.kind, "failed")
