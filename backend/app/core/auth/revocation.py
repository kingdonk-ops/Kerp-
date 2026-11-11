"""Server-side token revocation, refresh rotation and field-worker auth state (diff §4).

Three kinds of revocation entries (T-0008), each expiring on its own (Redis TTL) once it
can no longer matter:

* ``revoked:jti:<jti>``: one revoked token (an access token at logout). TTL = the token's
  remaining lifetime.
* ``revoked:sid:<sid>``: a revoked login session: every access and refresh token that
  carries this ``sid`` is dead. TTL = the refresh lifetime (no token of the session can
  outlive it).
* ``refresh:<jti>``: the one refresh token of a session that may still be used. A refresh
  consumes it atomically (``DEL`` returns 1 exactly once) and registers the successor.
  A refresh token presented a second time finds nothing: that is reuse, and the caller
  revokes the whole session (OAuth 2.0 Security BCP refresh-token rotation).

Field-worker auth (T-0009) keeps its short-lived state here too:

* ``magic:<sha256(token)>``: a pending magic link (JSON ``{uid, tid}``). Taken atomically
  (``GETDEL``), so a link signs in exactly once. TTL = the link lifetime.
* ``pin:<sid>``: the scrypt hash of the 6-digit PIN that gates a field-worker session. Its
  presence marks the session as PIN-gated (``/refresh`` refuses it; only the PIN unlock
  rotates it). TTL = the refresh lifetime plus a margin, re-armed on every unlock.
* ``lockout:<scope>:<subject digest>:n`` / ``:lock``: the progressive-lockout failure
  counter (TTL = the counting window, from the first failure) and the current lock
  (TTL = the delay). See ``lockout.py``.

Any store failure raises ``RevocationUnavailableError``; callers fail closed (503), they
never treat an unreachable store as "not revoked" or "not locked".
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

    def put_magic_link(self, digest: str, payload: bytes, ttl: timedelta) -> None: ...

    def take_magic_link(self, digest: str) -> bytes | None: ...

    def set_pin(self, session_id: uuid.UUID, pin_hash: str, ttl: timedelta) -> None: ...

    def get_pin(self, session_id: uuid.UUID) -> str | None: ...

    def lock_remaining(self, key: str) -> int: ...

    def count_failure(self, key: str, window: timedelta) -> int: ...

    def failure_count(self, key: str) -> int: ...

    def set_lock(self, key: str, ttl: timedelta) -> None: ...

    def clear_failures(self, key: str) -> None: ...

    def close(self) -> None: ...


def _seconds(ttl: timedelta) -> int:
    return max(1, math.ceil(ttl.total_seconds()))


def _jti_key(jti: str) -> str:
    return f"{KEY_PREFIX}revoked:jti:{jti}"


def _sid_key(session_id: uuid.UUID) -> str:
    return f"{KEY_PREFIX}revoked:sid:{session_id}"


def _refresh_key(jti: str) -> str:
    return f"{KEY_PREFIX}refresh:{jti}"


def _magic_key(digest: str) -> str:
    return f"{KEY_PREFIX}magic:{digest}"


def _pin_key(session_id: uuid.UUID) -> str:
    return f"{KEY_PREFIX}pin:{session_id}"


def _failures_key(key: str) -> str:
    return f"{KEY_PREFIX}lockout:{key}:n"


def _lock_key(key: str) -> str:
    return f"{KEY_PREFIX}lockout:{key}:lock"


def _as_bytes(value: object) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    return str(value).encode()


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

    def put_magic_link(self, digest: str, payload: bytes, ttl: timedelta) -> None:
        self._call(lambda: self._client.set(_magic_key(digest), payload, ex=_seconds(ttl)))

    def take_magic_link(self, digest: str) -> bytes | None:
        # GETDEL is atomic: two concurrent redemptions cannot both get the payload.
        return _as_bytes(self._call(lambda: self._client.getdel(_magic_key(digest))))

    def set_pin(self, session_id: uuid.UUID, pin_hash: str, ttl: timedelta) -> None:
        self._call(
            lambda: self._client.set(_pin_key(session_id), pin_hash.encode(), ex=_seconds(ttl))
        )

    def get_pin(self, session_id: uuid.UUID) -> str | None:
        value = _as_bytes(self._call(lambda: self._client.get(_pin_key(session_id))))
        return value.decode() if value is not None else None

    def lock_remaining(self, key: str) -> int:
        remaining = self._call(lambda: self._client.ttl(_lock_key(key)))
        # -2: no lock. Locks are always written with a TTL, so -1 does not occur.
        return max(int(remaining), 0)

    def count_failure(self, key: str, window: timedelta) -> int:
        counter = _failures_key(key)
        # The window starts at the first failure (NX keeps a running window). Both commands
        # run in one MULTI/EXEC transaction: the counter cannot expire between SET NX and
        # INCR (which would let INCR create a key without a TTL), and INCR keeps the TTL.
        seconds = _seconds(window)

        def transaction() -> list[object]:
            pipe = self._client.pipeline(transaction=True)
            pipe.set(counter, 0, nx=True, ex=seconds)
            pipe.incr(counter)
            return pipe.execute()

        return int(str(self._call(transaction)[-1]))

    def failure_count(self, key: str) -> int:
        value = _as_bytes(self._call(lambda: self._client.get(_failures_key(key))))
        return int(value) if value is not None else 0

    def set_lock(self, key: str, ttl: timedelta) -> None:
        self._call(lambda: self._client.set(_lock_key(key), b"1", ex=_seconds(ttl)))

    def clear_failures(self, key: str) -> None:
        self._call(lambda: self._client.delete(_failures_key(key), _lock_key(key)))

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

    With several workers each has its own store, so a revocation (or a lockout) is not seen
    by the others.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._entries: dict[str, tuple[float, bytes]] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def _set(self, key: str, ttl: timedelta, value: bytes = b"1") -> None:
        with self._lock:
            self._entries[key] = (self._clock() + _seconds(ttl), value)

    def _get(self, key: str) -> bytes | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry[0] <= self._clock():
            del self._entries[key]
            return None
        return entry[1]

    def _live(self, key: str) -> bool:
        return self._get(key) is not None

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

    def put_magic_link(self, digest: str, payload: bytes, ttl: timedelta) -> None:
        self._set(_magic_key(digest), ttl, payload)

    def take_magic_link(self, digest: str) -> bytes | None:
        with self._lock:
            value = self._get(_magic_key(digest))
            self._entries.pop(_magic_key(digest), None)
            return value

    def set_pin(self, session_id: uuid.UUID, pin_hash: str, ttl: timedelta) -> None:
        self._set(_pin_key(session_id), ttl, pin_hash.encode())

    def get_pin(self, session_id: uuid.UUID) -> str | None:
        with self._lock:
            value = self._get(_pin_key(session_id))
        return value.decode() if value is not None else None

    def lock_remaining(self, key: str) -> int:
        with self._lock:
            if self._get(_lock_key(key)) is None:
                return 0
            return max(1, math.ceil(self._entries[_lock_key(key)][0] - self._clock()))

    def count_failure(self, key: str, window: timedelta) -> int:
        counter = _failures_key(key)
        with self._lock:
            current = self._get(counter)
            if current is None:
                expires, count = self._clock() + _seconds(window), 1
            else:
                expires, count = self._entries[counter][0], int(current) + 1
            self._entries[counter] = (expires, str(count).encode())
            return count

    def failure_count(self, key: str) -> int:
        with self._lock:
            value = self._get(_failures_key(key))
        return int(value) if value is not None else 0

    def set_lock(self, key: str, ttl: timedelta) -> None:
        self._set(_lock_key(key), ttl)

    def clear_failures(self, key: str) -> None:
        with self._lock:
            self._entries.pop(_failures_key(key), None)
            self._entries.pop(_lock_key(key), None)

    def close(self) -> None:
        with self._lock:
            self._entries.clear()
