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
* ``refresh-spent:<jti>``: when a refresh token was consumed (epoch seconds of the
  consuming service's clock), written in the same transaction as the consumption
  (T-0345). It tells a client's retry of a just-consumed token (a lost or failed response)
  from a replay: within the retry grace the retry is refused without revoking the session.
  TTL = a little longer than that grace.

Field-worker auth (T-0009) keeps its short-lived state here too:

* ``magic:<sha256(token)>``: a pending magic link (JSON ``{uid, tid, iat}``; ``iat`` lets a
  later password change invalidate it, T-0077). Taken atomically (``GETDEL``), so a link
  signs in exactly once. TTL = the link lifetime.
* ``lockout:<scope>:<subject digest>:n`` / ``:lock``: the progressive-lockout failure
  counter (TTL = the counting window, from the first failure) and the current lock
  (TTL = the delay). See ``lockout.py``. The field PIN's counter is per device
  (``pin-device``, T-0038); the PIN hash itself is on the device row, not here.

Device binding (T-0043, diff §4.3; the device registry itself is ``oe_identity_device``):

* ``device:sid:<sid>``: the enrolled device (its client ``device_id``) a login session is
  bound to. TTL = the refresh lifetime.
* ``device:sids:<device_id>``: the set of sessions bound to the device, so a revoke can kill
  every one of them at once. Its TTL is pushed out to the refresh lifetime on every bind.
* ``revoked:device-sid:<sid>``: the session was revoked *because its device was revoked*;
  the API then answers ``401`` with ``code: DEVICE_REVOKED`` (the client wipes itself)
  instead of a plain ``401``. TTL = the refresh lifetime.

Entra ID SSO (T-0054, diff §4.5) keeps short-lived values under ``sso:`` (``put_value``,
``take_value`` atomic ``GETDEL``, ``peek_value``, ``claim`` = ``SET NX``): the sign-in
``state`` entries (10 min, single use), native exchange codes (60 s, single use), the
deployment-wide JWKS refetch limit (1 min) and the ``acct_claim_missing`` health marker (24 h).

Self-service password reset (T-0077, diff §4.2; ``password_reset.py``) uses the same
short-lived values: ``pwreset:token:<sha256(token)>`` (JSON ``{uid, tid}``, taken atomically:
single use) and ``pwreset:user:<tid>:<uid>`` (the digest of the account's one live token),
both with the link lifetime as TTL.

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
from typing import Final, Protocol, cast

import redis

KEY_PREFIX: Final = "kerp:auth:"
# Optimistic-transaction retries in ``begin_attempt`` before giving up (fail closed). Each
# retry means another attempt committed in between, and once a lock is armed every attempt is
# read-only, so a real burst needs about ``free_attempts + 1``; this is defence in depth.
MAX_ATTEMPT_RETRIES: Final = 32


class RevocationUnavailableError(RuntimeError):
    """The revocation store cannot be reached. Authentication must fail closed."""


class RevocationStore(Protocol):
    def revoke_token(self, jti: str, ttl: timedelta) -> None: ...

    def revoke_session(self, session_id: uuid.UUID, ttl: timedelta) -> None: ...

    def is_revoked(self, jti: str, session_id: uuid.UUID) -> bool: ...

    def register_refresh(self, jti: str, session_id: uuid.UUID, ttl: timedelta) -> None: ...

    def consume_refresh(
        self, jti: str, *, spent_at: float | None = None, remember: timedelta | None = None
    ) -> bool: ...

    def refresh_spent_at(self, jti: str) -> float | None: ...

    def put_magic_link(self, digest: str, payload: bytes, ttl: timedelta) -> None: ...

    def take_magic_link(self, digest: str) -> bytes | None: ...

    def peek_magic_link(self, digest: str) -> bytes | None: ...

    def begin_attempt(
        self,
        key: str,
        window: timedelta,
        delay_after: Callable[[int], timedelta],
        extra: int = 0,
    ) -> tuple[int, int]: ...

    def clear_failures(self, key: str) -> None: ...

    def failure_count(self, key: str) -> int: ...

    def bind_device(self, session_id: uuid.UUID, device_id: uuid.UUID, ttl: timedelta) -> None: ...

    def session_device(self, session_id: uuid.UUID) -> uuid.UUID | None: ...

    def device_session_count(self, device_id: uuid.UUID) -> int: ...

    def revoke_device(self, device_id: uuid.UUID, ttl: timedelta) -> int: ...

    def revoke_device_session(self, session_id: uuid.UUID, ttl: timedelta) -> None: ...

    def revoked_by_device(self, session_id: uuid.UUID) -> bool: ...

    def put_value(self, key: str, value: bytes, ttl: timedelta) -> None: ...

    def peek_value(self, key: str) -> bytes | None: ...

    def take_value(self, key: str) -> bytes | None: ...

    def claim(self, key: str, ttl: timedelta) -> bool: ...

    def close(self) -> None: ...


def _seconds(ttl: timedelta) -> int:
    return max(1, math.ceil(ttl.total_seconds()))


def _jti_key(jti: str) -> str:
    return f"{KEY_PREFIX}revoked:jti:{jti}"


def _sid_key(session_id: uuid.UUID) -> str:
    return f"{KEY_PREFIX}revoked:sid:{session_id}"


def _refresh_key(jti: str) -> str:
    return f"{KEY_PREFIX}refresh:{jti}"


def _spent_key(jti: str) -> str:
    return f"{KEY_PREFIX}refresh-spent:{jti}"


def _spent_value(value: bytes | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.decode())
    except ValueError:
        return None


def _magic_key(digest: str) -> str:
    return f"{KEY_PREFIX}magic:{digest}"


def _device_of_key(session_id: uuid.UUID) -> str:
    return f"{KEY_PREFIX}device:sid:{session_id}"


def _device_sessions_key(device_id: uuid.UUID) -> str:
    return f"{KEY_PREFIX}device:sids:{device_id}"


def _device_revoked_key(session_id: uuid.UUID) -> str:
    return f"{KEY_PREFIX}revoked:device-sid:{session_id}"


def _uuid_or_none(value: bytes | None) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(value.decode())
    except ValueError:
        return None


def _value_key(key: str) -> str:
    return f"{KEY_PREFIX}{key}"


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

    def consume_refresh(
        self, jti: str, *, spent_at: float | None = None, remember: timedelta | None = None
    ) -> bool:
        """Spend the refresh token: ``True`` exactly once. With ``spent_at``, the moment is
        kept under ``refresh-spent:<jti>`` for ``remember``, written in the same transaction
        as the delete (WATCH/MULTI), so no retry can see the token gone but not yet marked."""
        key = _refresh_key(jti)
        if spent_at is None:
            return bool(self._call(lambda: self._client.delete(key)))
        marker, ttl = _spent_key(jti), _seconds(remember or timedelta(minutes=1))

        def transaction() -> bool:
            pipe = self._client.pipeline(transaction=True)
            try:
                for _ in range(MAX_ATTEMPT_RETRIES):
                    try:
                        pipe.watch(key)  # type: ignore[no-untyped-call]  # stub gap
                        if not pipe.exists(key):
                            return False
                        pipe.multi()
                        pipe.delete(key)
                        pipe.set(marker, repr(spent_at).encode(), ex=ttl)
                        pipe.execute()
                        return True
                    except redis.WatchError:
                        continue  # consumed (or re-registered) in between: look again
                raise redis.RedisError("refresh consumption kept conflicting")  # fail closed
            finally:
                pipe.reset()

        return self._call(transaction)

    def refresh_spent_at(self, jti: str) -> float | None:
        """When the refresh token was consumed, while that is remembered; else ``None``."""
        return _spent_value(_as_bytes(self._call(lambda: self._client.get(_spent_key(jti)))))

    def put_magic_link(self, digest: str, payload: bytes, ttl: timedelta) -> None:
        self._call(lambda: self._client.set(_magic_key(digest), payload, ex=_seconds(ttl)))

    def take_magic_link(self, digest: str) -> bytes | None:
        # GETDEL is atomic: two concurrent redemptions cannot both get the payload.
        return _as_bytes(self._call(lambda: self._client.getdel(_magic_key(digest))))

    def peek_magic_link(self, digest: str) -> bytes | None:
        # Validates a link without spending it (T-0080): ``take_magic_link`` spends it.
        return _as_bytes(self._call(lambda: self._client.get(_magic_key(digest))))

    def begin_attempt(
        self,
        key: str,
        window: timedelta,
        delay_after: Callable[[int], timedelta],
        extra: int = 0,
    ) -> tuple[int, int]:
        """Atomically: refuse while locked, else count the attempt and arm its lock.

        Returns ``(0, seconds_left)`` while locked, else ``(attempt, 0)``. ``extra`` failures
        made elsewhere (a field device's offline PIN failures, T-0038) are added in the same
        step, before this attempt, and only when it runs (never while locked). Check, count and
        lock run as one optimistic transaction (WATCH on the counter and the lock, MULTI/EXEC,
        retried on conflict), so a burst cannot slip past a lock another request is arming
        (TD-191). The counter's window starts at the first failure and is never extended;
        every write carries an explicit TTL, so neither key can outlive its window.
        """
        counter, lock = _failures_key(key), _lock_key(key)

        def transaction() -> tuple[int, int]:
            pipe = self._client.pipeline(transaction=True)
            try:
                for _ in range(MAX_ATTEMPT_RETRIES):
                    try:
                        pipe.watch(counter, lock)  # type: ignore[no-untyped-call]  # stub gap
                        # -2: no lock. Locks are always written with a TTL (-1 cannot occur).
                        # PTTL rounded up, so a lock with < 0.5 s left still refuses.
                        left_lock_ms = int(str(pipe.pttl(lock)))
                        if left_lock_ms > 0:
                            return 0, math.ceil(left_lock_ms / 1000)
                        current = _as_bytes(pipe.get(counter))
                        left_ms = int(str(pipe.pttl(counter))) if current is not None else -2
                        known = int(current) if current is not None and left_ms > 0 else 0
                        attempt = known + extra + 1
                        delay = delay_after(attempt)
                        pipe.multi()
                        if known == 0:
                            pipe.set(counter, attempt, ex=_seconds(window))
                        else:
                            pipe.set(counter, attempt, px=left_ms)
                        if delay > timedelta(0):
                            pipe.set(lock, b"1", ex=_seconds(delay))
                        pipe.execute()
                        return attempt, 0
                    except redis.WatchError:
                        continue  # another attempt landed in between: look again
                raise redis.RedisError("lockout transaction kept conflicting")  # fail closed
            finally:
                pipe.reset()  # also UNWATCHes

        return self._call(transaction)

    def clear_failures(self, key: str) -> None:
        self._call(lambda: self._client.delete(_failures_key(key), _lock_key(key)))

    def failure_count(self, key: str) -> int:
        """The live failure count of ``key`` (read-only: counts nothing, arms nothing)."""
        current = _as_bytes(self._call(lambda: self._client.get(_failures_key(key))))
        return int(current) if current is not None else 0

    def bind_device(self, session_id: uuid.UUID, device_id: uuid.UUID, ttl: timedelta) -> None:
        seconds = _seconds(ttl)
        members = _device_sessions_key(device_id)

        def write() -> None:
            pipe = self._client.pipeline(transaction=True)
            pipe.set(_device_of_key(session_id), str(device_id).encode(), ex=seconds)
            pipe.sadd(members, str(session_id))
            # Every bind uses the same lifetime, so the latest bind always ends last.
            pipe.expire(members, seconds)
            pipe.execute()

        self._call(write)

    def session_device(self, session_id: uuid.UUID) -> uuid.UUID | None:
        return _uuid_or_none(
            _as_bytes(self._call(lambda: self._client.get(_device_of_key(session_id))))
        )

    def device_session_count(self, device_id: uuid.UUID) -> int:
        """How many sessions are bound to the device now (read-only)."""
        return int(str(self._call(lambda: self._client.scard(_device_sessions_key(device_id)))))

    def revoke_device(self, device_id: uuid.UUID, ttl: timedelta) -> int:
        """Kill every session bound to the device; returns how many were bound."""
        members = _device_sessions_key(device_id)

        def revoke() -> int:
            raw = cast("set[bytes | str]", self._client.smembers(members))  # sync client
            sessions = [uuid.UUID(m.decode() if isinstance(m, bytes) else str(m)) for m in raw]
            pipe = self._client.pipeline(transaction=True)
            for session_id in sessions:
                pipe.set(_sid_key(session_id), b"1", ex=_seconds(ttl))
                pipe.set(_device_revoked_key(session_id), b"1", ex=_seconds(ttl))
            pipe.delete(members)
            pipe.execute()
            return len(sessions)

        return self._call(revoke)

    def revoke_device_session(self, session_id: uuid.UUID, ttl: timedelta) -> None:
        def revoke() -> None:
            pipe = self._client.pipeline(transaction=True)
            pipe.set(_sid_key(session_id), b"1", ex=_seconds(ttl))
            pipe.set(_device_revoked_key(session_id), b"1", ex=_seconds(ttl))
            pipe.execute()

        self._call(revoke)

    def revoked_by_device(self, session_id: uuid.UUID) -> bool:
        return bool(self._call(lambda: self._client.exists(_device_revoked_key(session_id))))

    def put_value(self, key: str, value: bytes, ttl: timedelta) -> None:
        self._call(lambda: self._client.set(_value_key(key), value, ex=_seconds(ttl)))

    def peek_value(self, key: str) -> bytes | None:
        return _as_bytes(self._call(lambda: self._client.get(_value_key(key))))

    def take_value(self, key: str) -> bytes | None:
        # GETDEL is atomic: a single-use value is handed out exactly once.
        return _as_bytes(self._call(lambda: self._client.getdel(_value_key(key))))

    def claim(self, key: str, ttl: timedelta) -> bool:
        """Set ``key`` unless it exists (``SET NX``): ``True`` for the one caller that set it."""
        return bool(
            self._call(lambda: self._client.set(_value_key(key), b"1", ex=_seconds(ttl), nx=True))
        )

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

    def consume_refresh(
        self, jti: str, *, spent_at: float | None = None, remember: timedelta | None = None
    ) -> bool:
        with self._lock:
            live = self._live(_refresh_key(jti))
            self._entries.pop(_refresh_key(jti), None)
            if live and spent_at is not None:
                expires = self._clock() + _seconds(remember or timedelta(minutes=1))
                self._entries[_spent_key(jti)] = (expires, repr(spent_at).encode())
            return live

    def refresh_spent_at(self, jti: str) -> float | None:
        with self._lock:
            return _spent_value(self._get(_spent_key(jti)))

    def put_magic_link(self, digest: str, payload: bytes, ttl: timedelta) -> None:
        self._set(_magic_key(digest), ttl, payload)

    def take_magic_link(self, digest: str) -> bytes | None:
        with self._lock:
            value = self._get(_magic_key(digest))
            self._entries.pop(_magic_key(digest), None)
            return value

    def peek_magic_link(self, digest: str) -> bytes | None:
        with self._lock:
            return self._get(_magic_key(digest))

    def begin_attempt(
        self,
        key: str,
        window: timedelta,
        delay_after: Callable[[int], timedelta],
        extra: int = 0,
    ) -> tuple[int, int]:
        counter, lock = _failures_key(key), _lock_key(key)
        with self._lock:
            if self._get(lock) is not None:
                return 0, max(1, math.ceil(self._entries[lock][0] - self._clock()))
            current = self._get(counter)
            if current is None:
                expires, attempt = self._clock() + _seconds(window), extra + 1
            else:
                expires, attempt = self._entries[counter][0], int(current) + extra + 1
            self._entries[counter] = (expires, str(attempt).encode())
            delay = delay_after(attempt)
            if delay > timedelta(0):
                self._entries[lock] = (self._clock() + _seconds(delay), b"1")
            return attempt, 0

    def clear_failures(self, key: str) -> None:
        with self._lock:
            self._entries.pop(_failures_key(key), None)
            self._entries.pop(_lock_key(key), None)

    def failure_count(self, key: str) -> int:
        with self._lock:
            current = self._get(_failures_key(key))
        return int(current) if current is not None else 0

    def bind_device(self, session_id: uuid.UUID, device_id: uuid.UUID, ttl: timedelta) -> None:
        with self._lock:
            expires = self._clock() + _seconds(ttl)
            self._entries[_device_of_key(session_id)] = (expires, str(device_id).encode())
            members = _device_sessions_key(device_id)
            current = self._get(members)
            bound = set(current.decode().split(",")) if current else set()
            bound.add(str(session_id))
            self._entries[members] = (expires, ",".join(sorted(bound)).encode())

    def session_device(self, session_id: uuid.UUID) -> uuid.UUID | None:
        with self._lock:
            return _uuid_or_none(self._get(_device_of_key(session_id)))

    def device_session_count(self, device_id: uuid.UUID) -> int:
        with self._lock:
            current = self._get(_device_sessions_key(device_id))
        return len(current.decode().split(",")) if current else 0

    def revoke_device(self, device_id: uuid.UUID, ttl: timedelta) -> int:
        with self._lock:
            current = self._get(_device_sessions_key(device_id))
            self._entries.pop(_device_sessions_key(device_id), None)
        sessions = [uuid.UUID(s) for s in current.decode().split(",")] if current else []
        for session_id in sessions:
            self.revoke_device_session(session_id, ttl)
        return len(sessions)

    def revoke_device_session(self, session_id: uuid.UUID, ttl: timedelta) -> None:
        self._set(_sid_key(session_id), ttl)
        self._set(_device_revoked_key(session_id), ttl)

    def revoked_by_device(self, session_id: uuid.UUID) -> bool:
        with self._lock:
            return self._live(_device_revoked_key(session_id))

    def put_value(self, key: str, value: bytes, ttl: timedelta) -> None:
        self._set(_value_key(key), ttl, value)

    def peek_value(self, key: str) -> bytes | None:
        with self._lock:
            return self._get(_value_key(key))

    def take_value(self, key: str) -> bytes | None:
        with self._lock:
            value = self._get(_value_key(key))
            self._entries.pop(_value_key(key), None)
            return value

    def claim(self, key: str, ttl: timedelta) -> bool:
        with self._lock:
            if self._get(_value_key(key)) is not None:
                return False
            self._entries[_value_key(key)] = (self._clock() + _seconds(ttl), b"1")
            return True

    def close(self) -> None:
        with self._lock:
            self._entries.clear()
