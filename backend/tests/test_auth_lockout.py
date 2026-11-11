"""Progressive lockout and the field-worker auth state in the store (T-0009, diff §4).

The lockout and the magic-link / PIN entries live in the same store as the revocation list:
Redis in production (``fakeredis`` here), process memory in development. Both stores run
the same contract.
"""

import hashlib
import uuid
from collections.abc import Callable
from datetime import timedelta

import fakeredis
import pytest

from app.core.auth.lockout import (
    LOGIN_POLICY,
    MAGIC_LINK_REQUEST_POLICY,
    PIN_POLICY,
    LockedOutError,
    LockoutPolicy,
    ProgressiveLockout,
)
from app.core.auth.revocation import (
    KEY_PREFIX,
    InMemoryRevocationStore,
    RedisRevocationStore,
    RevocationStore,
    RevocationUnavailableError,
)
from tests.test_auth_revocation import BrokenRedis, FakeClock

POLICY = LockoutPolicy(
    free_attempts=2,
    base_delay=timedelta(seconds=30),
    max_delay=timedelta(minutes=2),
    window=timedelta(hours=1),
    max_attempts=6,
)


@pytest.fixture(params=["redis", "memory"])
def store(request: pytest.FixtureRequest) -> RevocationStore:
    if request.param == "redis":
        return RedisRevocationStore(fakeredis.FakeRedis())
    return InMemoryRevocationStore()


# --- policy ---------------------------------------------------------------------------------


def test_delay_is_zero_for_the_free_attempts_then_doubles_up_to_the_cap() -> None:
    delays = [POLICY.delay_after(n).total_seconds() for n in range(1, 8)]
    assert delays == [0, 0, 30, 60, 120, 120, 120]


def test_delay_does_not_overflow_for_huge_failure_counts() -> None:
    assert POLICY.delay_after(10_000) == POLICY.max_delay


def test_exhausted_only_past_the_hard_cap() -> None:
    assert not POLICY.exhausted(6)
    assert POLICY.exhausted(7)
    assert not LOGIN_POLICY.exhausted(10_000)  # login never locks permanently


def test_shipped_policies() -> None:
    # PIN: 3 free guesses, then 30 s doubling to 15 min; 10 guesses in total per session.
    assert PIN_POLICY.free_attempts == 3
    assert PIN_POLICY.max_attempts == 10
    assert PIN_POLICY.delay_after(4) == timedelta(seconds=30)
    assert PIN_POLICY.delay_after(9) == timedelta(minutes=15)
    # Password login: 5 free, then the same escalation, never permanent.
    assert LOGIN_POLICY.free_attempts == 5
    assert LOGIN_POLICY.max_attempts is None
    assert LOGIN_POLICY.delay_after(6) == timedelta(seconds=30)
    assert MAGIC_LINK_REQUEST_POLICY.max_attempts is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"free_attempts": -1},
        {"base_delay": timedelta(0)},
        {"max_delay": timedelta(seconds=1)},
        {"window": timedelta(0)},
        {"max_attempts": 0},
    ],
)
def test_policy_rejects_nonsense(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "free_attempts": 2,
        "base_delay": timedelta(seconds=30),
        "max_delay": timedelta(minutes=2),
        "window": timedelta(hours=1),
        "max_attempts": 6,
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=r"must be|need"):
        LockoutPolicy(**values)  # type: ignore[arg-type]


# --- lockout over a store -------------------------------------------------------------------


def test_attempts_are_numbered_and_lock_after_the_free_ones(store: RevocationStore) -> None:
    lockout = ProgressiveLockout(store, POLICY, "pin")
    assert lockout.begin("s") == 1
    assert lockout.begin("s") == 2
    assert lockout.begin("s") == 3  # third attempt still runs, but arms a 30 s lock
    with pytest.raises(LockedOutError) as locked:
        lockout.begin("s")
    assert 0 < locked.value.retry_after <= 30


def test_locked_attempts_are_not_counted(store: RevocationStore) -> None:
    lockout = ProgressiveLockout(store, POLICY, "pin")
    for _ in range(3):
        lockout.begin("s")
    for _ in range(5):
        with pytest.raises(LockedOutError):
            lockout.begin("s")
    assert lockout.failures("s") == 3


def test_success_resets_the_counter_and_the_lock(store: RevocationStore) -> None:
    lockout = ProgressiveLockout(store, POLICY, "pin")
    for _ in range(3):
        lockout.begin("s")
    lockout.succeeded("s")
    assert lockout.failures("s") == 0
    assert lockout.begin("s") == 1


def test_subjects_and_scopes_are_independent(store: RevocationStore) -> None:
    pin = ProgressiveLockout(store, POLICY, "pin")
    login = ProgressiveLockout(store, POLICY, "login")
    for _ in range(3):
        pin.begin("a")
    assert pin.begin("b") == 1
    assert login.begin("a") == 1


def test_lock_escalates_and_expires() -> None:
    clock = FakeClock()
    lockout = ProgressiveLockout(InMemoryRevocationStore(clock=clock), POLICY, "pin")
    retry_afters = []
    for attempt in range(1, 7):
        while True:
            try:
                number = lockout.begin("s")
                break
            except LockedOutError as exc:
                retry_afters.append(exc.retry_after)
                clock.now += exc.retry_after
        assert number == attempt
    assert retry_afters == [30, 60, 120]


def test_counter_forgets_after_the_window() -> None:
    clock = FakeClock()
    lockout = ProgressiveLockout(InMemoryRevocationStore(clock=clock), POLICY, "pin")
    lockout.begin("s")
    lockout.begin("s")
    clock.now += timedelta(hours=1).total_seconds() + 1
    assert lockout.begin("s") == 1


def test_redis_keys_hash_the_subject_and_carry_ttls() -> None:
    client = fakeredis.FakeRedis()
    lockout = ProgressiveLockout(RedisRevocationStore(client), POLICY, "login")
    subject = f"{uuid.uuid4()}:alice@example.test"
    for _ in range(3):
        lockout.begin(subject)
    keys = sorted(key.decode() for key in client.keys("*"))
    assert all("alice" not in key for key in keys)  # no e-mail addresses in Redis keys
    digest = hashlib.sha256(subject.encode()).hexdigest()
    assert keys == [
        f"{KEY_PREFIX}lockout:login:{digest}:lock",
        f"{KEY_PREFIX}lockout:login:{digest}:n",
    ]
    assert 0 < client.ttl(f"{KEY_PREFIX}lockout:login:{digest}:n") <= 3600
    assert 0 < client.ttl(f"{KEY_PREFIX}lockout:login:{digest}:lock") <= 30


# --- magic links and PINs in the store ------------------------------------------------------


def test_magic_link_can_be_taken_exactly_once(store: RevocationStore) -> None:
    store.put_magic_link("d1", b"payload", TTL)
    assert store.take_magic_link("d1") == b"payload"
    assert store.take_magic_link("d1") is None
    assert store.take_magic_link("never") is None


def test_pin_hash_is_stored_per_session(store: RevocationStore) -> None:
    session, other = uuid.uuid4(), uuid.uuid4()
    assert store.get_pin(session) is None
    store.set_pin(session, "scrypt$hash", TTL)
    assert store.get_pin(session) == "scrypt$hash"
    assert store.get_pin(other) is None


def test_magic_link_and_pin_entries_expire() -> None:
    clock = FakeClock()
    store = InMemoryRevocationStore(clock=clock)
    session = uuid.uuid4()
    store.put_magic_link("d", b"p", timedelta(seconds=10))
    store.set_pin(session, "h", timedelta(seconds=20))
    clock.now += 11
    assert store.take_magic_link("d") is None
    assert store.get_pin(session) == "h"
    clock.now += 10
    assert store.get_pin(session) is None


def test_redis_magic_link_and_pin_entries_carry_a_ttl() -> None:
    client = fakeredis.FakeRedis()
    store = RedisRevocationStore(client)
    session = uuid.uuid4()
    store.put_magic_link("d", b"p", timedelta(minutes=15))
    store.set_pin(session, "h", timedelta(days=15))
    assert 0 < client.ttl(f"{KEY_PREFIX}magic:d") <= 900
    assert client.ttl(f"{KEY_PREFIX}pin:{session}") > 14 * 86400


TTL = timedelta(minutes=15)


@pytest.mark.parametrize(
    "operation",
    [
        lambda s: s.put_magic_link("d", b"p", TTL),
        lambda s: s.take_magic_link("d"),
        lambda s: s.set_pin(uuid.uuid4(), "h", TTL),
        lambda s: s.get_pin(uuid.uuid4()),
        lambda s: s.lock_remaining("k"),
        lambda s: s.count_failure("k", TTL),
        lambda s: s.failure_count("k"),
        lambda s: s.set_lock("k", TTL),
        lambda s: s.clear_failures("k"),
    ],
)
def test_redis_errors_raise_unavailable(operation: Callable[[RevocationStore], object]) -> None:
    with pytest.raises(RevocationUnavailableError):
        operation(RedisRevocationStore(BrokenRedis()))


def test_lockout_fails_closed_when_the_store_is_down() -> None:
    lockout = ProgressiveLockout(RedisRevocationStore(BrokenRedis()), POLICY, "pin")
    with pytest.raises(RevocationUnavailableError):
        lockout.begin("s")
