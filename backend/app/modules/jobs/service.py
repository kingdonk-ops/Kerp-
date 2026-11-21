"""Job record lifecycle: create (enqueue after commit), poll, start, progress, finish, sweep.

Other modules call ``create_job`` inside their own unit of work. The row is written in that
transaction and the SAQ job is enqueued only after it commits (``after_commit``), so a
rolled-back request never leaves a queued job (ADR 0007 rule 2). If the enqueue never
happens (process died, Redis down), ``sweep`` re-enqueues rows still ``queued``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session, SessionTransaction

from app.core.audit import service as audit
from app.core.db import Database, utcnow
from app.core.request_context import get_request_id
from app.core.stats.registry import register_stats_provider
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import OutOfScopeError, get_or_404, is_visible
from app.modules.jobs.broker import QUEUE_NAMES, JobBroker, JobRef, QueueMonitor
from app.modules.jobs.models import MAX_TEXT, STATUSES, TABLE, Job
from app.modules.jobs.registry import (
    CRON_SCHEDULES,
    DEFAULT_TIMEOUT_S,
    JOB_REGISTRY,
    JobKind,
    JobRegistry,
    QueueName,
    UnknownJobKindError,
    register_job,
)
from app.modules.projects import service as projects

__all__ = [
    "JOB_REGISTRY",
    "CronStatus",
    "FailedJob",
    "JobError",
    "JobRegistry",
    "JobStateError",
    "QueueName",
    "QueueStatus",
    "RetryLaterError",
    "UnknownJobKindError",
    "abort_job",
    "create_job",
    "create_single_run_job",
    "cron_statuses",
    "deployment_stats",
    "failed_counts",
    "finish_job",
    "get_job",
    "get_queue_monitor",
    "job_status",
    "last_succeeded_job",
    "list_failed_jobs",
    "pending_job",
    "queue_statuses",
    "register_job",
    "report_progress",
    "requeue_for_retry",
    "retry_failed_job",
    "sanitise_error",
    "set_dispatcher",
    "set_queue_monitor",
    "start_job",
    "sweep",
]

PENDING_KEY: Final = "kerp_jobs_pending"
DEFAULT_SWEEP_AGE: Final = timedelta(minutes=2)
# A ``running`` row this long past its kind's timeout, unknown to SAQ, lost its worker
# (T-0346). SAQ cancels a job at its timeout; the margin covers the cancel and the write.
RUNNING_MARGIN: Final = timedelta(minutes=5)
# What the worker writes on a cancel or timeout (``worker.CANCELLED``); the sweep writes it
# for a lost ``running`` row on its last attempt.
TIMED_OUT: Final = "Job timed out or was cancelled"
LOST_RETRY: Final = "The worker stopped while running the job; it is retried"

Dispatcher = Callable[[list[JobRef]], None]

logger = logging.getLogger("kerp.jobs")
_dispatcher: Dispatcher | None = None
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class JobError(Exception):
    """Raise from a handler with a message that is safe to store and show to the user."""


class RetryLaterError(JobError):
    """Raise from a handler to run it again later (T-0218), e.g. a scanner that is down.

    The kind's ``max_attempts`` and back-off (``register_job``) decide when; meanwhile the row
    is ``queued`` again with the message. On the last attempt the job fails with it.
    """


def set_dispatcher(dispatcher: Dispatcher | None) -> None:
    """Install the process's post-commit dispatcher (API lifespan, worker start-up)."""
    global _dispatcher
    _dispatcher = dispatcher


def _bounded(text: str) -> str:
    text = _CONTROL.sub(" ", text).strip()
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def sanitise_error(exc: BaseException) -> str:
    """The error stored on the row: no traceback, no internals, at most ``MAX_TEXT`` long.

    A ``JobError`` carries a user-safe message; anything else is stored as its class name
    only (its text may hold SQL, paths or payload values). The full traceback is logged.
    """
    if isinstance(exc, JobError):
        lines = str(exc).strip().splitlines()
        return _bounded(lines[0] if lines else "Job failed")
    return _bounded(f"Internal error ({type(exc).__name__})")


def create_job(
    session: Session,
    job_kind: JobKind,
    payload: dict[str, Any] | None = None,
    *,
    actor_id: uuid.UUID | None = None,
    source: str = "api",
) -> Job:
    """Add a ``queued`` job in the session's scope; it is enqueued after the commit."""
    context = session_tenancy(session)
    if context is None:
        raise RuntimeError("create_job needs a session bound to a tenant (and project)")
    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        tenant_id=context.tenant_id,
        project_id=context.project_id,
        kind=job_kind.kind,
        queue=job_kind.queue,
        status="queued",
        progress=0,
        payload=payload or {},
        saq_job_key=str(job_id),
        request_id=get_request_id(),
        created_by=actor_id,
        updated_by=actor_id,
        source=source,
    )
    session.add(job)
    session.flush()
    _stage(session, _ref(job, job_kind))
    return job


def _stage(session: Session, ref: JobRef) -> None:
    """Enqueue ``ref`` once the session commits (dropped if its (sub)transaction rolls back)."""
    # Staged with the innermost transaction, so rolling back a SAVEPOINT drops only its jobs.
    transaction = session.get_nested_transaction() or session.get_transaction()
    pending: list[tuple[SessionTransaction | None, JobRef]] = session.info.setdefault(
        PENDING_KEY, []
    )
    pending.append((transaction, ref))


def _ref(job: Job, job_kind: JobKind | None) -> JobRef:
    queue: QueueName = "interactive" if job.queue == "interactive" else "batch"
    if job_kind is None:  # a kind no longer registered: defaults, the worker fails it
        return JobRef(
            job.id, queue, job.tenant_id, job.project_id, job.request_id, DEFAULT_TIMEOUT_S
        )
    return JobRef(
        job.id,
        queue,
        job.tenant_id,
        job.project_id,
        job.request_id,
        job_kind.timeout_s,
        max_attempts=job_kind.max_attempts,
        retry_delay_s=job_kind.retry_delay_s,
        retry_max_delay_s=job_kind.retry_max_delay_s,
    )


@event.listens_for(Session, "after_commit")
def _dispatch_after_commit(session: Session) -> None:
    if session.in_nested_transaction():  # a SAVEPOINT was released; the outer may still roll back
        return
    pending: list[tuple[SessionTransaction | None, JobRef]] = session.info.pop(PENDING_KEY, [])
    refs = [ref for _, ref in pending]
    if not refs:
        return
    if _dispatcher is None:
        logger.warning("no job dispatcher installed; %d job(s) wait for the sweep", len(refs))
        return
    try:
        _dispatcher(refs)
    except Exception:  # the commit stands; the rows stay queued for the sweep
        logger.exception("job dispatch failed; %d job(s) wait for the sweep", len(refs))


def _within(transaction: SessionTransaction | None, ancestor: SessionTransaction) -> bool:
    while transaction is not None:
        if transaction is ancestor:
            return True
        transaction = transaction.parent
    return False


@event.listens_for(Session, "after_soft_rollback")
def _forget_after_rollback(session: Session, previous_transaction: SessionTransaction) -> None:
    """Drop the jobs staged in the rolled-back (sub)transaction and its children only."""
    pending: list[tuple[SessionTransaction | None, JobRef]] | None = session.info.get(
        PENDING_KEY
    )
    if pending:
        pending[:] = [(t, r) for t, r in pending if not _within(t, previous_transaction)]


def job_status(session: Session, job_id: uuid.UUID) -> str | None:
    """The job's status if it is in the session's scope, else ``None`` (other modules, T-0215)."""
    job = session.get(Job, job_id)
    return job.status if job is not None and is_visible(session, job) else None


def get_job(session: Session, job_id: uuid.UUID) -> Job:
    """The job if it is in the session's scope, else ``OutOfScopeError`` (404)."""
    return get_or_404(session, Job, job_id)


# --- worker side (called by ``worker.run_job`` in a unit of work bound from the kwargs) ----


def start_job(session: Session, job_id: uuid.UUID) -> Job | None:
    """Mark the job ``running`` (one more attempt). ``None`` if not visible or already done."""
    job = session.get(Job, job_id)
    if job is None or not is_visible(session, job) or job.status not in ("queued", "running"):
        return None
    job.status = "running"
    job.attempts += 1
    job.started_at = utcnow()
    job.error = None
    return job


def requeue_for_retry(session: Session, job_id: uuid.UUID, message: str) -> None:
    """The handler asked to run again later (``RetryLaterError``): ``queued`` with ``message``."""
    job = get_or_404(session, Job, job_id)
    if job.status != "running":
        return
    job.status, job.message = "queued", _bounded(message)


def report_progress(
    session: Session, job_id: uuid.UUID, progress: int, message: str | None
) -> None:
    job = get_or_404(session, Job, job_id)
    job.progress = max(0, min(100, int(progress)))
    job.message = _bounded(message) if message is not None else None


def finish_job(
    session: Session,
    job_id: uuid.UUID,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    only_running: bool = False,
) -> None:
    """Store the outcome: ``succeeded`` with ``result``, or ``failed`` with ``error``.

    ``result`` must be a JSON object or ``None`` (``JobRead.result``); anything else raises
    ``TypeError`` before the row changes, and the worker fails the job instead (T-0060).
    ``only_running`` (the worker): leave a row that is no longer ``running`` as it is, e.g.
    one a System admin aborted meanwhile (T-0218).
    """
    if result is not None and not isinstance(result, dict):
        raise TypeError(f"a job result must be a JSON object or None, not {type(result).__name__}")
    job = get_or_404(session, Job, job_id)
    if only_running and job.status != "running":
        return
    job.finished_at = utcnow()
    if error is None:
        job.status, job.result, job.progress = "succeeded", result, 100
    else:
        job.status, job.error = "failed", _bounded(error)


# --- sweep ----------------------------------------------------------------------------------


def _stuck(db: Database, scope: TenancyContext, cutoff: datetime) -> list[Job]:
    with db.unit_of_work(scope) as session:
        query = select(Job).where(
            Job.tenant_id == scope.tenant_id,
            Job.project_id.is_(None)
            if scope.project_id is None
            else Job.project_id == scope.project_id,
            Job.status == "queued",
            Job.created_at < cutoff,
        )
        return list(session.scalars(query))


def _running_since(db: Database, scope: TenancyContext, cutoff: datetime) -> list[Job]:
    with db.unit_of_work(scope) as session:
        query = select(Job).where(
            Job.tenant_id == scope.tenant_id,
            Job.project_id.is_(None)
            if scope.project_id is None
            else Job.project_id == scope.project_id,
            Job.status == "running",
            Job.started_at < cutoff,
        )
        return list(session.scalars(query))


def _aware(moment: datetime) -> datetime:
    """SQLite hands back naive datetimes; every stored timestamp is UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _recover(
    db: Database, scope: TenancyContext, job_id: uuid.UUID, attempts: int, max_attempts: int
) -> str | None:
    """Requeue (attempts left) or fail a ``running`` row its worker lost; its new status.

    Re-checked under a row lock: a row that finished or started another attempt since the
    sweep read it is left alone (``None``)."""
    with db.unit_of_work(scope) as session:
        row = get_or_404(session, Job, job_id)
        session.refresh(row, with_for_update=True)
        if row.status != "running" or row.attempts != attempts:
            return None
        if row.attempts < max_attempts:
            row.status, row.message = "queued", _bounded(LOST_RETRY)
        else:
            row.status, row.error, row.finished_at = "failed", TIMED_OUT, utcnow()
        return row.status


async def _recover_running(
    db: Database,
    broker: JobBroker,
    scope: TenancyContext,
    registry: JobRegistry,
    moment: datetime,
    margin: timedelta,
) -> list[uuid.UUID]:
    """Recover the scope's ``running`` rows past their timeout plus ``margin`` that SAQ no
    longer knows (the worker was killed: OOM, a hard crash). Returns the re-enqueued ids."""
    requeued: list[uuid.UUID] = []
    for job in await asyncio.to_thread(_running_since, db, scope, moment - margin):
        job_kind = _kind(registry, job.kind)
        ref = _ref(job, job_kind)
        started = _aware(job.started_at) if job.started_at is not None else moment
        if started + timedelta(seconds=ref.timeout_s) + margin > moment:
            continue  # may still be running within its timeout
        if await broker.has_job(ref.queue, ref.key):
            continue  # SAQ still has it (running, or about to retry): SAQ decides
        status = await asyncio.to_thread(
            _recover, db, scope, job.id, job.attempts, ref.max_attempts
        )
        if status == "queued":
            await broker.enqueue(ref)
            requeued.append(job.id)
            logger.warning("re-enqueued a lost running job", extra={"job_id": ref.key})
        elif status == "failed":
            logger.error("failed a lost running job", extra={"job_id": ref.key})
    return requeued


async def sweep(
    db: Database,
    broker: JobBroker,
    scopes: Iterable[TenancyContext],
    *,
    registry: JobRegistry | None = None,
    older_than: timedelta = DEFAULT_SWEEP_AGE,
    running_margin: timedelta = RUNNING_MARGIN,
) -> list[uuid.UUID]:
    """Re-enqueue rows ``queued`` for longer than ``older_than`` that SAQ does not know, and
    recover rows still ``running`` past their kind's timeout plus ``running_margin`` that SAQ
    does not know either (T-0346): requeued while the kind allows more attempts, else
    ``failed``. So a single-run job killed mid-run never blocks the next one.

    Runs per scope, each in a bound unit of work: the table stays under RLS and the sweep
    needs no bypass. Returns the ids it re-enqueued.
    """
    registry = registry or JOB_REGISTRY
    moment = utcnow()
    cutoff = moment - older_than
    requeued: list[uuid.UUID] = []
    for scope in scopes:
        for job in await asyncio.to_thread(_stuck, db, scope, cutoff):
            ref = _ref(job, _kind(registry, job.kind))
            if await broker.has_job(ref.queue, ref.key):
                continue
            await broker.enqueue(ref)
            requeued.append(job.id)
            logger.warning("re-enqueued a stuck job", extra={"job_id": ref.key})
        requeued += await _recover_running(db, broker, scope, registry, moment, running_margin)
    return requeued


def _kind(registry: JobRegistry, kind: str) -> JobKind | None:
    try:
        return registry.get(kind)
    except UnknownJobKindError:
        return None


# --- queue monitor (T-0218, diff §4.7): System console, numbers and safe text only ----------

# The tenant-level audit chain of the System console (same naming as T-0219, T-0223).
CONSOLE_CHAIN: Final = "admin-system:{tenant_id}"
ABORTED: Final = "Aborted by an administrator"
MAX_FAILED_LIST: Final = 200
_INTERNAL = re.compile(r"^Internal error \(([A-Za-z_][A-Za-z0-9_.]*)\)$")
# Error texts the worker writes itself, by class (``worker.CANCELLED``, ``ABORTED``).
_KNOWN_ERRORS: Final = {
    TIMED_OUT: "Cancelled",
    ABORTED: "Aborted",
}

_monitor: QueueMonitor | None = None


def set_queue_monitor(monitor: QueueMonitor | None) -> None:
    """Install the process's queue monitor (API lifespan: the SAQ broker; tests: a fake)."""
    global _monitor
    _monitor = monitor


def get_queue_monitor() -> QueueMonitor | None:
    return _monitor


class JobStateError(RuntimeError):
    """The job is not in a state that allows the action (the API answers 409)."""


@dataclass(frozen=True, slots=True)
class QueueStatus:
    name: QueueName
    queued: int
    active: int
    scheduled: int
    failed: int
    oldest_queued_age_s: float | None


@dataclass(frozen=True, slots=True)
class CronStatus:
    name: str
    schedule: str
    last_run_at: datetime | None
    last_finished_at: datetime | None
    last_outcome: str | None
    next_run_at: datetime | None


@dataclass(frozen=True, slots=True)
class FailedJob:
    """A failed job row as the console shows it: never its payload, result or SAQ kwargs."""

    id: uuid.UUID
    kind: str
    queue: str
    project_id: uuid.UUID | None
    project_code: str | None
    attempts: int
    error_class: str
    message: str
    failed_at: datetime | None


def error_class(error: str | None) -> str:
    """The class of a stored (already sanitised) error: ``Internal error (X)`` -> ``X``."""
    if not error:
        return "Unknown"
    if (match := _INTERNAL.match(error)) is not None:
        return match.group(1)
    return _KNOWN_ERRORS.get(error, "JobError")


def failed_counts(session: Session) -> dict[str, int]:
    """Failed job rows per queue, under the caller's RLS."""
    rows = session.execute(
        select(Job.queue, func.count()).where(Job.status == "failed").group_by(Job.queue)
    ).all()
    return {str(queue): int(count) for queue, count in rows}


async def queue_statuses(monitor: QueueMonitor, failed: dict[str, int]) -> list[QueueStatus]:
    """Per queue: SAQ's queued, active and scheduled counts (deployment-wide, e-mail and cron
    jobs included), the age of the oldest queued job, and the failed rows of ``failed``."""
    moment = utcnow()
    statuses: list[QueueStatus] = []
    for name in QUEUE_NAMES:
        counts = await monitor.counts(name)
        oldest = counts.oldest_queued_at
        statuses.append(
            QueueStatus(
                name=name,
                queued=counts.queued,
                active=counts.active,
                scheduled=counts.scheduled,
                failed=failed.get(name, 0),
                oldest_queued_age_s=(
                    max(0.0, (moment - oldest).total_seconds()) if oldest is not None else None
                ),
            )
        )
    return statuses


async def cron_statuses(monitor: QueueMonitor) -> list[CronStatus]:
    """The batch worker's cron jobs with their last run (recorded by the worker) and next run."""
    names = list(CRON_SCHEDULES)
    last = await monitor.cron_runs(names)
    upcoming = await monitor.next_cron_runs(names)
    statuses: list[CronStatus] = []
    for name in names:
        run = last.get(name)
        statuses.append(
            CronStatus(
                name=name,
                schedule=CRON_SCHEDULES[name],
                last_run_at=run.started_at if run is not None else None,
                last_finished_at=run.finished_at if run is not None else None,
                last_outcome=run.outcome if run is not None else None,
                next_run_at=upcoming.get(name),
            )
        )
    return statuses


def list_failed_jobs(session: Session, *, limit: int = 50, offset: int = 0) -> list[FailedJob]:
    """Failed job rows the caller's RLS shows, newest first, with the project code.

    Under ADR 0016 (T-0274) a ``system_admin`` session reads the rows of every project of its
    tenant without a membership (normal RLS, never a bypass role; TD-451).
    """
    limit = max(1, min(limit, MAX_FAILED_LIST))
    jobs = list(
        session.scalars(
            select(Job)
            .where(Job.status == "failed")
            .order_by(Job.finished_at.desc(), Job.id)
            .limit(limit)
            .offset(max(0, offset))
        )
    )
    project_ids = {j.project_id for j in jobs if j.project_id is not None}
    codes = {p.id: p.code for p in projects.list_projects(session, project_ids)}
    return [
        FailedJob(
            id=j.id,
            kind=j.kind,
            queue=j.queue,
            project_id=j.project_id,
            project_code=codes.get(j.project_id) if j.project_id is not None else None,
            attempts=j.attempts,
            error_class=error_class(j.error),
            message=_bounded(j.error or ""),
            failed_at=j.finished_at,
        )
        for j in jobs
    ]


def _console_tenant(session: Session) -> uuid.UUID:
    """The tenant of the caller's tenant-level request session (checked before any write)."""
    context = session_tenancy(session)
    if context is None or context.project_id is not None:
        raise RuntimeError("the queue monitor needs a tenant-level bound session")
    return context.tenant_id


def _visible_job(session: Session, job_id: uuid.UUID) -> Job:
    """The job if the caller's session shows it (normal RLS + ORM scope), else 404.

    This is the only gate before the write in the job's own scope: a job of a project outside
    the caller's read scope, or of another tenant, is ``OutOfScopeError`` (CLAUDE.md §6).
    """
    tenant_id = _console_tenant(session)
    job = get_job(session, job_id)
    if job.tenant_id != tenant_id:  # defence in depth: RLS and the ORM scope already hide it
        raise OutOfScopeError("Job not found")
    return job


def _console_audit(session: Session, event: str, job: Job, actor_id: uuid.UUID | None) -> None:
    # Runs after the job write has committed in its own unit of work: two transactions, so a
    # failed commit of the request session leaves the action unaudited (TD-455).
    audit.append(
        session,
        CONSOLE_CHAIN.format(tenant_id=_console_tenant(session)),
        {
            "event": event,
            "job_id": str(job.id),
            "kind": job.kind,
            "queue": job.queue,
            "project_id": str(job.project_id) if job.project_id else None,
        },
        actor_id=actor_id,
        source="api",
    )


def _own_scope(job: Job, actor_id: uuid.UUID | None) -> TenancyContext:
    """The job's own scope, for the write: what the worker binds; never a bypass role."""
    return TenancyContext(tenant_id=job.tenant_id, project_id=job.project_id, user_id=actor_id)


def retry_failed_job(
    session: Session,
    db: Database,
    job_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None,
    registry: JobRegistry | None = None,
) -> Job:
    """Queue a failed job again (audited); SAQ gets it once the write commits.

    ``session`` is the caller's tenant-level request session: the row must be visible to it
    under normal RLS (else ``OutOfScopeError``, 404). The row is then changed in a unit of
    work bound to the job's own scope, as the worker does. Not ``failed``: ``JobStateError``.
    """
    job = _visible_job(session, job_id)
    if job.status != "failed":
        raise JobStateError(f"the job is {job.status}")
    job_kind = _kind(registry or JOB_REGISTRY, job.kind)
    with db.unit_of_work(_own_scope(job, actor_id)) as scoped:
        row = get_or_404(scoped, Job, job_id)
        scoped.refresh(row, with_for_update=True)
        if row.status != "failed":
            raise JobStateError(f"the job is {row.status}")
        row.status, row.error, row.progress, row.message = "queued", None, 0, None
        row.started_at = row.finished_at = None
        row.updated_by = actor_id
        scoped.flush()
        _stage(scoped, _ref(row, job_kind))
    _console_audit(session, "job_retried", job, actor_id)
    return job


def abort_job(
    session: Session, db: Database, job_id: uuid.UUID, *, actor_id: uuid.UUID | None
) -> Job:
    """Fail a ``queued`` or ``running`` job with ``ABORTED`` (audited).

    Visibility and scope as ``retry_failed_job``. The caller then aborts the SAQ job
    (``QueueMonitor.abort``); even if that fails, the worker never starts a failed row and
    never overwrites it (``finish_job(only_running=True)``). Else ``JobStateError``.
    """
    job = _visible_job(session, job_id)
    if job.status not in ("queued", "running"):
        raise JobStateError(f"the job is {job.status}")
    with db.unit_of_work(_own_scope(job, actor_id)) as scoped:
        row = get_or_404(scoped, Job, job_id)
        scoped.refresh(row, with_for_update=True)
        if row.status not in ("queued", "running"):
            raise JobStateError(f"the job is {row.status}")
        row.status, row.error, row.finished_at = "failed", ABORTED, utcnow()
        row.updated_by = actor_id
    _console_audit(session, "job_aborted", job, actor_id)
    return job


# --- tenant-level single-run jobs and deployment stats (T-0219) --------------------------------

_SQLITE_WRITE_LOCK: Final = text(f"UPDATE {TABLE} SET status = status WHERE 0")


def _tenant_scope(session: Session) -> uuid.UUID:
    context = session_tenancy(session)
    if context is None or context.project_id is not None:
        raise RuntimeError("a tenant-level job needs a tenant-level bound session")
    return context.tenant_id


def _tenant_jobs(session: Session, kind: str, statuses: Iterable[str]) -> Job | None:
    """The newest tenant-level job of ``kind`` in ``statuses`` of the session's own tenant.

    Explicit filters (visibility is not scope): a ``system_admin`` session also sees every
    project's job rows (ADR 0016), and they are not this tenant-level job."""
    query = (
        select(Job)
        .where(
            Job.tenant_id == _tenant_scope(session),
            Job.project_id.is_(None),
            Job.kind == kind,
            Job.status.in_(list(statuses)),
        )
        .order_by(Job.created_at.desc(), Job.id)
        .limit(1)
    )
    return session.scalars(query).first()


def pending_job(session: Session, kind: str) -> Job | None:
    """The tenant-level job of ``kind`` that is ``queued`` or ``running``, if any."""
    return _tenant_jobs(session, kind, ("queued", "running"))


def last_succeeded_job(session: Session, kind: str) -> Job | None:
    """The newest ``succeeded`` tenant-level job of ``kind`` (its ``result`` is a cache)."""
    query = (
        select(Job)
        .where(
            Job.tenant_id == _tenant_scope(session),
            Job.project_id.is_(None),
            Job.kind == kind,
            Job.status == "succeeded",
        )
        .order_by(Job.finished_at.desc(), Job.id)
        .limit(1)
    )
    return session.scalars(query).first()


def _lock_kind(session: Session, tenant_id: uuid.UUID, kind: str) -> None:
    """Serialise ``create_single_run_job`` per tenant and kind until the transaction ends."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        digest = hashlib.sha256(f"oe_jobs_single_run|{tenant_id}|{kind}".encode()).digest()
        key = int.from_bytes(digest[:8], "big", signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    elif dialect == "sqlite":
        session.execute(_SQLITE_WRITE_LOCK)


def create_single_run_job(
    session: Session,
    job_kind: JobKind,
    payload: dict[str, Any] | None = None,
    *,
    actor_id: uuid.UUID | None = None,
) -> tuple[Job, bool]:
    """Queue a tenant-level job of ``job_kind`` unless one is already queued or running.

    Returns ``(job, created)``: the new job, or the pending one with ``created`` false. One
    run at a time per tenant and kind, also for concurrent callers (a transaction-scoped
    lock: PostgreSQL advisory lock, SQLite write lock)."""
    _lock_kind(session, _tenant_scope(session), job_kind.kind)
    pending = pending_job(session, job_kind.kind)
    if pending is not None:
        return pending, False
    return create_job(session, job_kind, payload, actor_id=actor_id), True


@register_stats_provider("jobs")
def deployment_stats(session: Session) -> dict[str, int]:
    """Job rows per status of the caller's tenant (every project's for a ``system_admin``,
    ADR 0016, plus the tenant-level ones); counts only, under the caller's RLS."""
    context = session_tenancy(session)
    if context is None:
        raise RuntimeError("the jobs stats need a session bound to a tenant")
    rows = session.execute(
        select(Job.status, func.count())
        .where(Job.tenant_id == context.tenant_id)
        .group_by(Job.status)
    ).all()
    by_status = {str(status): int(count) for status, count in rows}
    return {f"jobs_{status}": by_status.get(status, 0) for status in STATUSES}
