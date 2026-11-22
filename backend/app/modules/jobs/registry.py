"""Job kinds: each declares its queue and an ``async`` handler (ADR 0007 rules 3 and 4).

A module registers its handlers in ``app/modules/<name>/jobs.py`` with ``register_job``; the
module loader imports that file for every loaded module (``import_job_handlers``). Heavy
libraries are imported inside the handler, never at module level (T-0022 lint).
"""

from __future__ import annotations

import inspect
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from app.core.tenancy.context import TenancyContext

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.core.db import Database

QueueName = Literal["interactive", "batch"]
KIND_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{1,39}\.[a-z][a-z0-9_.]{0,79}$")
DEFAULT_TIMEOUT_S: Final = 600
# The SAQ cron jobs of the batch worker (``scheduler.py``; there is no cron queue): function
# name -> cron expression. Listed here, free of imports, for the queue monitor (T-0218).
CRON_SCHEDULES: Final[dict[str, str]] = {
    "sweep_cron": "*/5 * * * *",  # stuck-job sweep (T-0214)
    "hourly_project_jobs": "17 * * * *",  # hourly per-project jobs, off the full hour (T-0215)
    "expire_mail_cron": "41 * * * *",  # fail mail stuck queued past its payload (T-0346)
    # Ends expired or revoked System admin impersonations nobody used again (T-0277).
    "impersonation_sweep": "*/5 * * * *",
}


class JobUpdate(Protocol):
    """Progress callback a handler awaits: ``await job.update(40, "Page 2 of 5")``."""

    async def __call__(self, progress: int, message: str | None = None) -> None: ...


@dataclass(frozen=True, slots=True)
class JobContext:
    """What a handler receives. ``tenancy`` is bound from the SAQ job kwargs.

    ``db`` is the worker's database: a handler opens its own short units of work with
    ``job.db.unit_of_work(job.tenancy)`` and keeps slow I/O outside them.
    """

    job_id: uuid.UUID
    kind: str
    tenancy: TenancyContext
    payload: dict[str, Any]
    request_id: str | None
    update: JobUpdate
    db: Database


Handler = Callable[[JobContext], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True, slots=True)
class JobKind:
    """A registered kind. ``api``: clients may enqueue it through ``POST /jobs``.

    ``max_attempts`` > 1 lets a handler raise ``service.RetryLaterError`` (T-0218): SAQ runs it
    again after ``retry_delay_s``, doubling per attempt up to ``retry_max_delay_s`` (with
    jitter); any other error still fails the job at once.
    """

    kind: str
    queue: QueueName
    handler: Handler
    api: bool = False
    timeout_s: int = DEFAULT_TIMEOUT_S
    max_attempts: int = 1
    retry_delay_s: float = 0.0
    retry_max_delay_s: float | None = None


# A module's start-up precondition for the worker, e.g. "production needs a real scanner".
StartupCheck = Callable[["Settings"], None]


class UnknownJobKindError(LookupError):
    """No handler is registered for the kind."""


class JobRegistry:
    """Kind name -> ``JobKind``. One process-wide instance: ``JOB_REGISTRY``."""

    def __init__(self) -> None:
        self._kinds: dict[str, JobKind] = {}
        self._hourly_project_kinds: list[str] = []
        self._startup_checks: list[StartupCheck] = []

    def add(self, job_kind: JobKind) -> JobKind:
        if not KIND_PATTERN.match(job_kind.kind):
            raise ValueError(f"job kind {job_kind.kind!r} must look like '<module>.<name>'")
        if job_kind.queue not in ("interactive", "batch"):
            raise ValueError(f"job kind {job_kind.kind!r}: unknown queue {job_kind.queue!r}")
        if not inspect.iscoroutinefunction(job_kind.handler):
            raise TypeError(f"job kind {job_kind.kind!r}: the handler must be 'async def'")
        if job_kind.max_attempts < 1 or job_kind.retry_delay_s < 0:
            raise ValueError(f"job kind {job_kind.kind!r}: invalid retry settings")
        if job_kind.kind in self._kinds:
            raise ValueError(f"job kind {job_kind.kind!r} is already registered")
        self._kinds[job_kind.kind] = job_kind
        return job_kind

    def get(self, kind: str) -> JobKind:
        try:
            return self._kinds[kind]
        except KeyError:
            raise UnknownJobKindError(kind) from None

    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._kinds))

    def add_hourly_project_job(self, kind: str) -> str:
        """Queue ``kind`` (payload ``{}``) once an hour in every live project (T-0215)."""
        if kind not in self._hourly_project_kinds:
            self._hourly_project_kinds.append(kind)
        return kind

    def hourly_project_kinds(self) -> tuple[str, ...]:
        return tuple(self._hourly_project_kinds)

    def add_startup_check(self, check: StartupCheck) -> StartupCheck:
        """A module's check the worker runs before it starts (T-0213); it raises to refuse."""
        if check not in self._startup_checks:
            self._startup_checks.append(check)
        return check

    def startup_checks(self) -> tuple[StartupCheck, ...]:
        return tuple(self._startup_checks)

    def run_startup_checks(self, settings: Settings) -> None:
        """Run every registered check in order; the first one that raises stops the worker."""
        for check in self._startup_checks:
            check(settings)


JOB_REGISTRY = JobRegistry()


def register_job(
    kind: str,
    *,
    queue: QueueName,
    api: bool = False,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    max_attempts: int = 1,
    retry_delay_s: float = 0.0,
    retry_max_delay_s: float | None = None,
) -> Callable[[Handler], Handler]:
    """Register the decorated ``async`` handler as ``kind`` on ``queue``."""

    def decorate(handler: Handler) -> Handler:
        JOB_REGISTRY.add(
            JobKind(
                kind,
                queue,
                handler,
                api=api,
                timeout_s=timeout_s,
                max_attempts=max_attempts,
                retry_delay_s=retry_delay_s,
                retry_max_delay_s=retry_max_delay_s,
            )
        )
        return handler

    return decorate


def schedule_hourly_per_project(kind: str) -> str:
    """Have the worker's hourly cron queue ``kind`` in every live project (T-0215)."""
    return JOB_REGISTRY.add_hourly_project_job(kind)


def register_startup_check(check: StartupCheck) -> StartupCheck:
    """Register ``check`` (decorator) to run when the worker starts, before it connects."""
    return JOB_REGISTRY.add_startup_check(check)
