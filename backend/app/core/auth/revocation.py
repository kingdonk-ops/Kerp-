"""Server-side token revocation and refresh-token rotation state (T-0008, diff §4).

Three kinds of entries, each expiring on its own (Redis TTL) once it can no longer matter:

* ``revoked:jti:<jti>``: one revoked token (an access token at logout). TTL = the token's
  remaining lifetime.
* ``revoked:sid:<sid>``: a revoked login session: every access and refresh token that
  carries this ``sid`` is dead. TTL = the refresh lifetime (no token of the session can
  outlive it).
* ``refresh:<jti>``: the one refresh token of a session that may still be used. A refresh
  consumes it atomically (``DEL`` returns 1 exactly once) and registers the successor.
  A refresh token presented a second time finds nothing: that is reuse, and the caller
  revokes the whole session (OAuth 2.0 Security BCP refresh-token rotation).

Any store failure raises ``RevocationUnavailableError``; callers fail closed (503), they
never treat an unreachable store as "not revoked".
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Final, Protocol

import redis

KEY_PREFIX: Final = "kerp:auth:"


class RevocationUnavailableError(RuntimeError):
    """The revocation store cannot be reached. Authentication must fail closed."""


class RevocationStore(Protocol):
    def revoke_token(self, jti: str, ttl: timedelta) -> None: ...

    def revoke_session(self, session_id: uuid.UUID, ttl: timedelta) -> None: ...

    def is_revoked(self, jti: str, session_id: uuid.UUID) -> bool: ...

    def register_refresh(self, jti: str, session_id: uuid.UUID, ttl: timedelta) -> None: ...

    def consume_refresh(self, jti: str) -> bool: ...

    def close(self) -> None: ...


def _seconds(ttl: timedelta) -> int:
    return max(1, math.ceil(ttl.total_seconds()))


def _jti_key(jti: str) -> str:
    return f"{KEY_PREFIX}revoked:jti:{jti}"


def _sid_key(session_id: uuid.UUID) -> str:
    return f"{KEY_PREFIX}revoked:sid:{session_id}"


def _refresh_key(jti: str) -> str:
    return f"{KEY_PREFIX}refresh:{jti}"


class RedisRevocationStore:
    """The production store. ``client`` is a ``redis.Redis`` (``fakeredis`` in tests)."""

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str) -> RedisRevocationStore:
        return cls(redis.Redis.from_url(url, socket_timeout=2.0, socket_connect_timeout=2.0))

    def revoke_token(self, jti: str, ttl: timedelta) -> None:
        self._call(lambda: self._client.set(_jti_key(jti), b"1", ex=_seconds(ttl)))

    def revoke_session(self, session_id: uuid.UUID, ttl: timedelta) -> None:
        self._call(lambda: self._client.set(_sid_key(session_id), b"1", ex=_seconds(ttl)))

    def is_revoked(self, jti: str, session_id: uuid.UUID) -> bool:
        count = self._call(lambda: self._client.exists(_jti_key(jti), _sid_key(session_id)))
        return bool(count)

    def register_refresh(self, jti: str, session_id: uuid.UUID, ttl: timedelta) -> None:
        self._call(
            lambda: self._client.set(_refresh_key(jti), str(session_id).encode(), ex=_seconds(ttl))
        )

    def consume_refresh(self, jti: str) -> bool:
        return bool(self._call(lambda: self._client.delete(_refresh_key(jti))))

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _call[T](operation: Callable[[], T]) -> T:
        try:
            return operation()
        except redis.RedisError as exc:
            raise RevocationUnavailableError("token revocation store unavailable") from exc


class InMemoryRevocationStore:
    """Process-local store for development and tests without Redis. Never used in production.

    With several workers each has its own store, so a revocation is not seen by the others.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._entries: dict[str, float] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def _set(self, key: str, ttl: timedelta) -> None:
        with self._lock:
            self._entries[key] = self._clock() + _seconds(ttl)

    def _live(self, key: str) -> bool:
        expires = self._entries.get(key)
        if expires is None:
            return False
        if expires <= self._clock():
            del self._entries[key]
            return False
        return True

    def revoke_token(self, jti: str, ttl: timedelta) -> None:
        self._set(_jti_key(jti), ttl)

    def revoke_session(self, session_id: uuid.UUID, ttl: timedelta) -> None:
        self._set(_sid_key(session_id), ttl)

    def is_revoked(self, jti: str, session_id: uuid.UUID) -> bool:
        with self._lock:
            return self._live(_jti_key(jti)) or self._live(_sid_key(session_id))

    def register_refresh(self, jti: str, session_id: uuid.UUID, ttl: timedelta) -> None:
        self._set(_refresh_key(jti), ttl)

    def consume_refresh(self, jti: str) -> bool:
        with self._lock:
            live = self._live(_refresh_key(jti))
            self._entries.pop(_refresh_key(jti), None)
            return live

    def close(self) -> None:
        with self._lock:
            self._entries.clear()
