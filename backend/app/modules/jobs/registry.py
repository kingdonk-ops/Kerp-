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
from typing import Any, Final, Literal, Protocol

from app.core.tenancy.context import TenancyContext

QueueName = Literal["interactive", "batch"]
KIND_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{1,39}\.[a-z][a-z0-9_.]{0,79}$")
DEFAULT_TIMEOUT_S: Final = 600


class JobUpdate(Protocol):
    """Progress callback a handler awaits: ``await job.update(40, "Page 2 of 5")``."""

    async def __call__(self, progress: int, message: str | None = None) -> None: ...


@dataclass(frozen=True, slots=True)
class JobContext:
    """What a handler receives. ``tenancy`` is bound from the SAQ job kwargs."""

    job_id: uuid.UUID
    kind: str
    tenancy: TenancyContext
    payload: dict[str, Any]
    request_id: str | None
    update: JobUpdate


Handler = Callable[[JobContext], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True, slots=True)
class JobKind:
    """A registered kind. ``api``: clients may enqueue it through ``POST /jobs``."""

    kind: str
    queue: QueueName
    handler: Handler
    api: bool = False
    timeout_s: int = DEFAULT_TIMEOUT_S


class UnknownJobKindError(LookupError):
    """No handler is registered for the kind."""


class JobRegistry:
    """Kind name -> ``JobKind``. One process-wide instance: ``JOB_REGISTRY``."""

    def __init__(self) -> None:
        self._kinds: dict[str, JobKind] = {}

    def add(self, job_kind: JobKind) -> JobKind:
        if not KIND_PATTERN.match(job_kind.kind):
            raise ValueError(f"job kind {job_kind.kind!r} must look like '<module>.<name>'")
        if job_kind.queue not in ("interactive", "batch"):
            raise ValueError(f"job kind {job_kind.kind!r}: unknown queue {job_kind.queue!r}")
        if not inspect.iscoroutinefunction(job_kind.handler):
            raise TypeError(f"job kind {job_kind.kind!r}: the handler must be 'async def'")
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


JOB_REGISTRY = JobRegistry()


def register_job(
    kind: str,
    *,
    queue: QueueName,
    api: bool = False,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> Callable[[Handler], Handler]:
    """Register the decorated ``async`` handler as ``kind`` on ``queue``."""

    def decorate(handler: Handler) -> Handler:
        JOB_REGISTRY.add(JobKind(kind, queue, handler, api=api, timeout_s=timeout_s))
        return handler

    return decorate
