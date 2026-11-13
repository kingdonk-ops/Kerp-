"""Job record lifecycle: create (enqueue after commit), poll, start, progress, finish, sweep.

Other modules call ``create_job`` inside their own unit of work. The row is written in that
transaction and the SAQ job is enqueued only after it commits (``after_commit``), so a
rolled-back request never leaves a queued job (ADR 0007 rule 2). If the enqueue never
happens (process died, Redis down), ``sweep`` re-enqueues rows still ``queued``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import event, select
from sqlalchemy.orm import Session, SessionTransaction

from app.core.db import Database, utcnow
from app.core.request_context import get_request_id
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import get_or_404, is_visible
from app.modules.jobs.broker import JobBroker, JobRef
from app.modules.jobs.models import MAX_TEXT, Job
from app.modules.jobs.registry import (
    DEFAULT_TIMEOUT_S,
    JOB_REGISTRY,
    JobKind,
    JobRegistry,
    QueueName,
    UnknownJobKindError,
    register_job,
)

__all__ = [
    "JOB_REGISTRY",
    "JobError",
    "JobRegistry",
    "UnknownJobKindError",
    "create_job",
    "finish_job",
    "get_job",
    "register_job",
    "report_progress",
    "sanitise_error",
    "set_dispatcher",
    "start_job",
    "sweep",
]

PENDING_KEY: Final = "kerp_jobs_pending"
DEFAULT_SWEEP_AGE: Final = timedelta(minutes=2)

Dispatcher = Callable[[list[JobRef]], None]

logger = logging.getLogger("kerp.jobs")
_dispatcher: Dispatcher | None = None
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class JobError(Exception):
    """Raise from a handler with a message that is safe to store and show to the user."""


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
    # Staged with the innermost transaction, so rolling back a SAVEPOINT drops only its jobs.
    transaction = session.get_nested_transaction() or session.get_transaction()
    pending: list[tuple[SessionTransaction | None, JobRef]] = session.info.setdefault(
        PENDING_KEY, []
    )
    pending.append((transaction, _ref(job, job_kind.timeout_s)))
    return job


def _ref(job: Job, timeout_s: int) -> JobRef:
    queue: QueueName = "interactive" if job.queue == "interactive" else "batch"
    return JobRef(job.id, queue, job.tenant_id, job.project_id, job.request_id, timeout_s)


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
) -> None:
    """Store the outcome: ``succeeded`` with ``result``, or ``failed`` with ``error``.

    ``result`` must be a JSON object or ``None`` (``JobRead.result``); anything else raises
    ``TypeError`` before the row changes, and the worker fails the job instead (T-0060).
    """
    if result is not None and not isinstance(result, dict):
        raise TypeError(f"a job result must be a JSON object or None, not {type(result).__name__}")
    job = get_or_404(session, Job, job_id)
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


async def sweep(
    db: Database,
    broker: JobBroker,
    scopes: Iterable[TenancyContext],
    *,
    registry: JobRegistry | None = None,
    older_than: timedelta = DEFAULT_SWEEP_AGE,
) -> list[uuid.UUID]:
    """Re-enqueue rows ``queued`` for longer than ``older_than`` that SAQ does not know.

    Runs per scope, each in a bound unit of work: the table stays under RLS and the sweep
    needs no bypass. Returns the ids it re-enqueued.
    """
    registry = registry or JOB_REGISTRY
    cutoff = utcnow() - older_than
    requeued: list[uuid.UUID] = []
    for scope in scopes:
        for job in await asyncio.to_thread(_stuck, db, scope, cutoff):
            ref = _ref(job, _timeout(registry, job.kind))
            if await broker.has_job(ref.queue, ref.key):
                continue
            await broker.enqueue(ref)
            requeued.append(job.id)
            logger.warning("re-enqueued a stuck job", extra={"job_id": ref.key})
    return requeued


def _timeout(registry: JobRegistry, kind: str) -> int:
    try:
        return registry.get(kind).timeout_s
    except UnknownJobKindError:
        return DEFAULT_TIMEOUT_S
