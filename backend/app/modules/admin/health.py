"""System console health probes (T-0219, diff §4.7): PostgreSQL, Redis, object store, scanner.

Each probe answers ``True`` (healthy), ``False`` (failed) or ``None`` (nothing to probe: the
stub scanner, a storage without a probe) and runs in a small thread pool with a deadline of
``PROBE_TIMEOUT_S`` (planner default 2 s). The status is

* ``ok``: answered healthy within ``DEGRADED_AFTER_S``;
* ``degraded``: answered healthy but slower than that, or there is nothing real to probe
  (the always-clean stub scanner: uploads would be accepted unscanned);
* ``down``: failed, raised, timed out or not configured.

The answer is the status and the latency only. A probe's exception text is never returned or
logged with its message (it can name a host, a DSN or a bucket); only its class is logged.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Final, Literal

import redis
from sqlalchemy import text

from app.core.config import Settings
from app.core.db import Database
from app.modules.uploads import service as uploads

logger = logging.getLogger("kerp.admin")

ProbeName = Literal["postgresql", "redis", "object_store", "malware_scanner"]
ProbeStatus = Literal["ok", "degraded", "down"]
Probe = Callable[[], bool | None]

PROBE_TIMEOUT_S: Final = 2.0
DEGRADED_AFTER_S: Final = 1.0
PROBE_ORDER: Final[tuple[ProbeName, ...]] = (
    "postgresql",
    "redis",
    "object_store",
    "malware_scanner",
)
_SEVERITY: Final[dict[ProbeStatus, int]] = {"ok": 0, "degraded": 1, "down": 2}
# Bounded: a hung dependency can hold a worker until its own timeout, never pile up threads.
_POOL: Final = ThreadPoolExecutor(max_workers=8, thread_name_prefix="kerp-health")


@dataclass(frozen=True, slots=True)
class ProbeResult:
    name: ProbeName
    status: ProbeStatus
    latency_ms: float | None


def ping_postgresql(db: Database | None) -> bool:
    if db is None:
        return False
    with db.engine.connect() as connection:
        return connection.execute(text("SELECT 1")).scalar() == 1


def ping_redis(redis_url: str | None, timeout: float = PROBE_TIMEOUT_S) -> bool:
    if not redis_url:
        return False
    client = redis.Redis.from_url(redis_url, socket_timeout=timeout, socket_connect_timeout=timeout)
    try:
        return bool(client.ping())
    finally:
        client.close()


def ping_object_store() -> bool | None:
    return uploads.ping_storage()


def ping_malware_scanner(settings: Settings) -> bool | None:
    return uploads.ping_scanner(settings)


def default_probes(db: Database | None, settings: Settings) -> dict[ProbeName, Probe]:
    """The four probes of a running API (``app.state``)."""
    return {
        "postgresql": lambda: ping_postgresql(db),
        "redis": lambda: ping_redis(settings.redis_url),
        "object_store": ping_object_store,
        "malware_scanner": lambda: ping_malware_scanner(settings),
    }


def _timed(probe: Probe) -> tuple[bool | None, float]:
    started = time.perf_counter()
    try:
        answer = probe()
    except Exception as exc:  # down; never the message (it can name the host)
        logger.warning("health probe failed", extra={"error_class": type(exc).__name__})
        answer = False
    return answer, time.perf_counter() - started


def status_of(answer: bool | None, latency_s: float) -> ProbeStatus:
    if answer is None:
        return "degraded"
    if not answer:
        return "down"
    return "degraded" if latency_s > DEGRADED_AFTER_S else "ok"


def run_probes(
    probes: Mapping[ProbeName, Probe], *, timeout_s: float | None = None
) -> list[ProbeResult]:
    """Run every probe concurrently; one not done after ``timeout_s`` (default
    ``PROBE_TIMEOUT_S``) is ``down``."""
    deadline = PROBE_TIMEOUT_S if timeout_s is None else timeout_s
    started = time.perf_counter()
    futures: dict[ProbeName, Future[tuple[bool | None, float]]] = {
        name: _POOL.submit(_timed, probe) for name, probe in probes.items()
    }
    wait(futures.values(), timeout=deadline)
    waited_ms = round((time.perf_counter() - started) * 1000, 1)
    results: list[ProbeResult] = []
    for name in PROBE_ORDER:
        future = futures.get(name)
        if future is None:
            continue
        if not future.done():
            future.cancel()  # not started yet: never runs; running: its own timeout ends it
            results.append(ProbeResult(name, "down", waited_ms))
            continue
        answer, latency_s = future.result()
        latency_ms = round(latency_s * 1000, 1) if answer is not None else None
        results.append(ProbeResult(name, status_of(answer, latency_s), latency_ms))
    return results


def overall(results: list[ProbeResult]) -> ProbeStatus:
    worst: ProbeStatus = "ok"
    for result in results:
        if _SEVERITY[result.status] > _SEVERITY[worst]:
            worst = result.status
    return worst
