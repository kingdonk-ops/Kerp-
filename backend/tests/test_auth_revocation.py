"""Revocation store: jti / session blacklist and one-time refresh tokens (T-0008).

Both stores run the same contract. The Redis store runs on ``fakeredis`` (no live Redis).
"""

import uuid
from collections.abc import Callable
from datetime import timedelta

import fakeredis
import pytest
import redis

from app.core.auth.revocation import (
    KEY_PREFIX,
    InMemoryRevocationStore,
    RedisRevocationStore,
    RevocationStore,
    RevocationUnavailableError,
)

TTL = timedelta(minutes=15)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(params=["redis", "memory"])
def store(request: pytest.FixtureRequest) -> RevocationStore:
    if request.param == "redis":
        return RedisRevocationStore(fakeredis.FakeRedis())
    return InMemoryRevocationStore()


def test_fresh_token_is_not_revoked(store: RevocationStore) -> None:
    assert not store.is_revoked("jti-1", uuid.uuid4())


def test_revoked_jti_is_revoked_and_others_are_not(store: RevocationStore) -> None:
    session = uuid.uuid4()
    store.revoke_token("jti-1", TTL)
    assert store.is_revoked("jti-1", session)
    assert not store.is_revoked("jti-2", session)


def test_revoked_session_revokes_every_token_of_it(store: RevocationStore) -> None:
    session, other = uuid.uuid4(), uuid.uuid4()
    store.revoke_session(session, TTL)
    assert store.is_revoked("any", session)
    assert store.is_revoked("other", session)
    assert not store.is_revoked("any", other)


def test_refresh_token_can_be_consumed_exactly_once(store: RevocationStore) -> None:
    store.register_refresh("r-1", uuid.uuid4(), TTL)
    assert store.consume_refresh("r-1") is True
    assert store.consume_refresh("r-1") is False
    assert store.consume_refresh("never-registered") is False


def test_consuming_with_a_moment_remembers_when_the_token_was_spent(
    store: RevocationStore,
) -> None:
    """T-0345: the consumption moment is what tells a client's retry from a replay."""
    store.register_refresh("r-1", uuid.uuid4(), TTL)
    assert store.refresh_spent_at("r-1") is None  # live, not spent
    assert store.consume_refresh("r-1", spent_at=1_763_715_600.25, remember=TTL) is True
    assert store.refresh_spent_at("r-1") == 1_763_715_600.25
    # A second consumption fails and does not move the moment.
    assert store.consume_refresh("r-1", spent_at=1_763_715_700.0, remember=TTL) is False
    assert store.refresh_spent_at("r-1") == 1_763_715_600.25
    # A token that was never registered is never marked as spent.
    assert store.consume_refresh("never", spent_at=1.0, remember=TTL) is False
    assert store.refresh_spent_at("never") is None


def test_the_spent_moment_expires_with_its_ttl() -> None:
    clock = FakeClock()
    store = InMemoryRevocationStore(clock=clock)
    store.register_refresh("r", uuid.uuid4(), TTL)
    store.consume_refresh("r", spent_at=5.0, remember=timedelta(seconds=60))
    clock.now += 59
    assert store.refresh_spent_at("r") == 5.0
    clock.now += 2
    assert store.refresh_spent_at("r") is None
    client = fakeredis.FakeRedis()
    redis_store = RedisRevocationStore(client)
    redis_store.register_refresh("r", uuid.uuid4(), TTL)
    redis_store.consume_refresh("r", spent_at=5.0, remember=timedelta(seconds=60))
    assert 0 < client.ttl(f"{KEY_PREFIX}refresh-spent:r") <= 60
    assert not client.exists(f"{KEY_PREFIX}refresh:r")


def test_failure_count_reads_without_counting(store: RevocationStore) -> None:
    window = timedelta(hours=1)
    assert store.failure_count("k") == 0
    for _ in range(3):
        store.begin_attempt("k", window, lambda _n: timedelta(0))
    assert store.failure_count("k") == 3
    assert store.failure_count("k") == 3  # reading counts nothing
    store.clear_failures("k")
    assert store.failure_count("k") == 0


def test_close(store: RevocationStore) -> None:
    store.close()


# --- expiry ---------------------------------------------------------------------------------


def test_redis_entries_carry_a_ttl() -> None:
    client = fakeredis.FakeRedis()
    store = RedisRevocationStore(client)
    session = uuid.uuid4()
    store.revoke_token("j", timedelta(seconds=90))
    store.revoke_session(session, timedelta(days=14))
    store.register_refresh("r", session, timedelta(seconds=0.2))
    assert 0 < client.ttl(f"{KEY_PREFIX}revoked:jti:j") <= 90
    assert client.ttl(f"{KEY_PREFIX}revoked:sid:{session}") > 13 * 86400
    assert client.ttl(f"{KEY_PREFIX}refresh:r") == 1  # rounded up, never 0 (= no expiry)
    assert client.get(f"{KEY_PREFIX}refresh:r") == str(session).encode()


def test_memory_entries_expire() -> None:
    clock = FakeClock()
    store = InMemoryRevocationStore(clock=clock)
    session = uuid.uuid4()
    store.revoke_token("j", timedelta(seconds=10))
    store.revoke_session(session, timedelta(seconds=20))
    store.register_refresh("r", session, timedelta(seconds=5))
    clock.now += 11
    assert store.is_revoked("x", session)  # session still revoked
    assert not store.is_revoked("j", uuid.uuid4())  # jti entry gone
    assert store.consume_refresh("r") is False  # expired refresh cannot be used
    clock.now += 10
    assert not store.is_revoked("x", session)


# --- failures fail closed ---------------------------------------------------------------


class _BrokenPipeline:
    """Queues commands like a pipeline; sending them fails like an unreachable server."""

    def __getattr__(self, name: str) -> Callable[..., object]:
        return lambda *args, **kwargs: self

    def execute(self) -> list[object]:
        raise redis.ConnectionError("down")

    def watch(self, *names: object) -> None:  # WATCH is sent at once, not queued
        raise redis.ConnectionError("down")


class BrokenRedis(fakeredis.FakeRedis):
    def execute_command(self, *args: object, **options: object) -> object:
        raise redis.ConnectionError("down")

    def pipeline(self, transaction: bool = True, shard_hint: object = None) -> object:  # type: ignore[override]
        return _BrokenPipeline()


@pytest.mark.parametrize(
    "operation",
    [
        lambda s: s.revoke_token("j", TTL),
        lambda s: s.revoke_session(uuid.uuid4(), TTL),
        lambda s: s.is_revoked("j", uuid.uuid4()),
        lambda s: s.register_refresh("r", uuid.uuid4(), TTL),
        lambda s: s.consume_refresh("r"),
        lambda s: s.consume_refresh("r", spent_at=1.0, remember=TTL),
        lambda s: s.refresh_spent_at("r"),
        lambda s: s.failure_count("k"),
    ],
)
def test_redis_errors_raise_unavailable(operation: Callable[[RevocationStore], object]) -> None:
    with pytest.raises(RevocationUnavailableError):
        operation(RedisRevocationStore(BrokenRedis()))


def test_from_url_builds_a_client_without_connecting() -> None:
    store = RedisRevocationStore.from_url("redis://cache.invalid:6379/0")
    store.close()
