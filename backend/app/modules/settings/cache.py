"""Cache of a tenant's stored setting values: Redis, with pub/sub invalidation (T-0230, §4.7).

``load(tenant, loader)`` answers, in order: the process's own copy, the copy in Redis, the
database (``loader``), filling the two caches on the way. ``invalidate(tenant)`` runs after a
change has committed: it starts a new **generation** for the tenant in Redis and publishes the
tenant on ``CHANNEL``; every API and worker process listens on it (``start``, one daemon
thread) and drops its own copy at once.

* A reader that read the database before the change stores its copy under the generation it
  saw, which nobody reads any more, so it cannot bring the old values back; it does not keep
  its own copy either if the invalidation reached its process meanwhile.
* The process's own copy also expires after ``local_ttl`` (30 s) and the Redis copy after
  ``redis_ttl`` (60 s): the bound on staleness if a pub/sub message or the invalidation
  itself is lost (Redis unreachable at that moment).
* Redis down is never an error here: the database is read instead (and nothing is cached).

Only values are cached, never anything secret (the registry refuses secrets). Without Redis
(development, tests) ``NullCache`` reads the database every time.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, Final, Protocol, cast

import redis

KEY_PREFIX: Final = "kerp:settings:"
CHANNEL: Final = KEY_PREFIX + "invalidate"
LOCAL_TTL_S: Final = 30.0
REDIS_TTL_S: Final = 60
_RETRY_S: Final = 1.0

logger = logging.getLogger("kerp.settings")
Values = dict[str, Any]
Loader = Callable[[], Values]


class SettingsCache(Protocol):
    def load(self, tenant_id: uuid.UUID, loader: Loader) -> Values: ...

    def invalidate(self, tenant_id: uuid.UUID) -> None: ...

    def close(self) -> None: ...


class NullCache:
    """No cache: every read is the database's."""

    def load(self, tenant_id: uuid.UUID, loader: Loader) -> Values:
        return loader()

    def invalidate(self, tenant_id: uuid.UUID) -> None:
        return None

    def close(self) -> None:
        return None


class RedisSettingsCache:
    def __init__(
        self,
        client: redis.Redis,
        *,
        local_ttl: float = LOCAL_TTL_S,
        redis_ttl: int = REDIS_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client, self._local_ttl, self._redis_ttl, self._clock = (
            client,
            local_ttl,
            redis_ttl,
            clock,
        )
        self._local: dict[uuid.UUID, tuple[float, Values]] = {}
        self._dropped: dict[uuid.UUID, int] = {}  # invalidations seen, per tenant
        self._lock = threading.Lock()
        self._pubsub: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._subscribed = threading.Event()

    @classmethod
    def from_url(cls, url: str) -> RedisSettingsCache:
        return cls(redis.Redis.from_url(url))

    # --- reads and writes ---------------------------------------------------------------

    def cached(self, tenant_id: uuid.UUID) -> Values | None:
        """This process's live copy for the tenant, if it has one."""
        with self._lock:
            entry = self._local.get(tenant_id)
            if entry is None or entry[0] <= self._clock():
                self._local.pop(tenant_id, None)
                return None
            return dict(entry[1])

    def load(self, tenant_id: uuid.UUID, loader: Loader) -> Values:
        if (values := self.cached(tenant_id)) is not None:
            return values
        with self._lock:
            seen = self._dropped.get(tenant_id, 0)
        try:
            stored = cast("bytes | None", self._client.get(self._generation_key(tenant_id)))
            key = f"{KEY_PREFIX}values:{tenant_id}:{int(stored or 0)}"
            raw = cast("bytes | None", self._client.get(key))
            if raw is None:
                values = loader()
                self._client.set(key, json.dumps(values), ex=self._redis_ttl)
            else:
                values = json.loads(raw)
        except redis.RedisError:
            logger.warning("settings cache unavailable; reading the database")
            return loader()
        with self._lock:
            # Not kept if an invalidation arrived while this read was under way.
            if self._dropped.get(tenant_id, 0) == seen:
                self._local[tenant_id] = (self._clock() + self._local_ttl, values)
        return dict(values)

    def _drop(self, tenant_id: uuid.UUID) -> None:
        with self._lock:
            self._local.pop(tenant_id, None)
            self._dropped[tenant_id] = self._dropped.get(tenant_id, 0) + 1

    def invalidate(self, tenant_id: uuid.UUID) -> None:
        self._drop(tenant_id)
        try:
            self._client.incr(self._generation_key(tenant_id))
            self._client.publish(CHANNEL, str(tenant_id))
        except redis.RedisError:
            logger.exception("settings cache invalidation failed; copies expire on their own")

    @staticmethod
    def _generation_key(tenant_id: uuid.UUID) -> str:
        return f"{KEY_PREFIX}generation:{tenant_id}"

    # --- pub/sub ------------------------------------------------------------------------

    def subscribe(self) -> None:
        """Listen on ``CHANNEL``. Messages sent before this are lost, so the copies go."""
        client: Any = self._client  # redis-py leaves ``pubsub`` untyped
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(CHANNEL)
        pubsub.get_message(timeout=_RETRY_S)  # the subscribe confirmation (ignored)
        with self._lock:
            self._local.clear()
        self._pubsub = pubsub
        self._subscribed.set()

    def pump(self, timeout: float = 0.0) -> int:
        """Handle the invalidations received so far (waiting up to ``timeout`` s for the
        first); the number handled."""
        handled = 0
        while (message := self._pubsub.get_message(timeout=timeout)) is not None:
            timeout = 0.0
            try:
                tenant_id = uuid.UUID(message["data"].decode())
            except (ValueError, AttributeError):
                continue
            self._drop(tenant_id)
            handled += 1
        return handled

    def start(self) -> None:
        """Listen in a daemon thread until ``close`` (reconnecting when Redis comes back)."""
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._listen, name="kerp-settings-cache", daemon=True
            )
            self._thread.start()

    def wait_subscribed(self, timeout: float) -> bool:
        return self._subscribed.wait(timeout)

    def _listen(self) -> None:
        while not self._stop.is_set():
            try:
                if self._pubsub is None:
                    self.subscribe()
                self.pump(timeout=0.2)
            except redis.RedisError:
                logger.warning("settings cache listener lost Redis; retrying")
                self._drop_subscription()
                self._stop.wait(_RETRY_S)

    def _drop_subscription(self) -> None:
        pubsub, self._pubsub = self._pubsub, None
        self._subscribed.clear()
        with self._lock:
            self._local.clear()
        if pubsub is not None:
            with contextlib.suppress(redis.RedisError):
                pubsub.close()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._drop_subscription()
        with contextlib.suppress(redis.RedisError):
            self._client.close()


_NULL: Final = NullCache()
_current: SettingsCache = _NULL


def current() -> SettingsCache:
    return _current


def install(cache: SettingsCache | None) -> None:
    """Make ``cache`` the process's settings cache (``None``: no cache)."""
    global _current
    _current = cache if cache is not None else _NULL


def open_cache(redis_url: str | None) -> SettingsCache:
    """The cache of an API or worker process: on Redis and listening, or none without Redis."""
    if not redis_url:
        return _NULL
    cache = RedisSettingsCache.from_url(redis_url)
    cache.start()
    return cache
