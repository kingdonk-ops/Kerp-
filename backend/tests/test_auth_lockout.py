"""Progressive lockout and the field-worker auth state in the store (T-0009, diff §4).

The lockout and the magic-link / PIN entries live in the same store as the revocation list:
Redis in production (``fakeredis`` here), process memory in development. Both stores run
the same contract.
"""

import hashlib
import threading
import uuid
from collections.abc import Callable
from datetime import timedelta

import fakeredis
import pytest
import redis

from app.core.auth.field import PIN_TTL_MARGIN
from app.core.auth.lockout import (
    LOGIN_ACCOUNT_POLICY,
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
    # PIN: 4 guesses back to back, then 30 s doubling to 15 min; 10 in total per session.
    assert PIN_POLICY.free_attempts == 3
    assert PIN_POLICY.max_attempts == 10
    # The cap is per session: the default window covers the longest refresh lifetime (90 d).
    assert PIN_POLICY.window >= timedelta(days=90) + PIN_TTL_MARGIN
    assert PIN_POLICY.delay_after(4) == timedelta(seconds=30)
    assert PIN_POLICY.delay_after(9) == timedelta(minutes=15)
    # Password login: 5 free, then the same escalation, never permanent.
    assert LOGIN_POLICY.free_attempts == 5
    assert LOGIN_POLICY.max_attempts is None
    assert LOGIN_POLICY.delay_after(6) == timedelta(seconds=30)
    # The per-account backstop (T-0068): far looser, never final.
    assert LOGIN_ACCOUNT_POLICY.free_attempts == 50
    assert LOGIN_ACCOUNT_POLICY.max_attempts is None
    assert LOGIN_ACCOUNT_POLICY.delay_after(50) == timedelta(0)
    assert LOGIN_ACCOUNT_POLICY.delay_after(51) == timedelta(seconds=30)
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


def test_locked_attempts_are_not_counted() -> None:
    clock = FakeClock()
    lockout = ProgressiveLockout(InMemoryRevocationStore(clock=clock), POLICY, "pin")
    for _ in range(3):
        lockout.begin("s")
    for _ in range(5):
        with pytest.raises(LockedOutError) as locked:
            lockout.begin("s")
    clock.now += locked.value.retry_after
    assert lockout.begin("s") == 4  # the five refused attempts were not counted


def test_success_resets_the_counter_and_the_lock(store: RevocationStore) -> None:
    lockout = ProgressiveLockout(store, POLICY, "pin")
    for _ in range(3):
        lockout.begin("s")
    lockout.succeeded("s")
    assert lockout.begin("s") == 1  # the lock is lifted and the count starts again


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


def _no_delay(attempt: int) -> timedelta:
    return timedelta(0)


def test_redis_counter_window_starts_at_the_first_failure() -> None:
    client = fakeredis.FakeRedis()
    store = RedisRevocationStore(client)
    counter = f"{KEY_PREFIX}lockout:magic:k:n"
    for expected in (1, 2, 3):
        assert store.begin_attempt("magic:k", timedelta(hours=1), _no_delay) == (expected, 0)
        assert 0 < client.ttl(counter) <= 3600
    # Expired between two failures: a fresh window starts, again with a TTL.
    client.delete(counter)
    assert store.begin_attempt("magic:k", timedelta(hours=1), _no_delay) == (1, 0)
    assert 0 < client.ttl(counter) <= 3600


def test_redis_counter_keeps_its_window() -> None:
    client = fakeredis.FakeRedis()
    store = RedisRevocationStore(client)
    store.begin_attempt("k", timedelta(hours=1), _no_delay)
    client.expire(f"{KEY_PREFIX}lockout:k:n", 100)
    store.begin_attempt("k", timedelta(hours=1), _no_delay)  # the running window is kept
    assert 0 < client.ttl(f"{KEY_PREFIX}lockout:k:n") <= 100


def test_redis_lock_is_written_with_its_delay() -> None:
    client = fakeredis.FakeRedis()
    store = RedisRevocationStore(client)
    lockout = ProgressiveLockout(store, POLICY, "pin")
    for _ in range(3):  # the third failure arms a 30 s lock
        lockout.begin("s")
    lock = next(k for k in client.keys(f"{KEY_PREFIX}lockout:pin:*:lock"))
    assert 0 < client.ttl(lock) <= 30


class InterleavedRedis(fakeredis.FakeRedis):
    """Another worker's attempt lands between this one's WATCH and EXEC (once)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.interleave: Callable[[], object] | None = None

    def pipeline(self, transaction: bool = True, shard_hint: object = None):  # type: ignore[no-untyped-def]
        pipe = super().pipeline(transaction, shard_hint)  # type: ignore[arg-type]
        outer = self
        original_multi = pipe.multi

        def multi() -> None:
            if outer.interleave is not None:
                run, outer.interleave = outer.interleave, None
                run()
            original_multi()

        pipe.multi = multi
        return pipe


def test_redis_attempt_retries_when_another_attempt_interleaves() -> None:
    """TD-191: a concurrent attempt that arms the lock between our read and our write must
    not be overwritten or bypassed; the transaction retries and then sees the lock."""
    client = InterleavedRedis()
    store = RedisRevocationStore(client)
    lockout = ProgressiveLockout(store, POLICY, "pin")
    lockout.begin("s")
    lockout.begin("s")
    # While our 3rd attempt is between WATCH and MULTI, another worker takes attempt 3 and
    # arms the 30 s lock. Ours must then be refused, not counted as a second "3".
    rival = ProgressiveLockout(RedisRevocationStore(client), POLICY, "pin")
    client.interleave = lambda: rival.begin("s")
    with pytest.raises(LockedOutError):
        lockout.begin("s")
    counter = next(k for k in client.keys(f"{KEY_PREFIX}lockout:pin:*:n"))
    assert int(client.get(counter)) == 3


def test_redis_lock_with_under_half_a_second_left_still_refuses() -> None:
    client = fakeredis.FakeRedis()
    lockout = ProgressiveLockout(RedisRevocationStore(client), POLICY, "pin")
    for _ in range(3):  # the third failure arms the lock
        lockout.begin("s")
    lock = next(k for k in client.keys(f"{KEY_PREFIX}lockout:pin:*:lock"))
    client.pexpire(lock, 300)  # TTL would round this to 0 seconds
    with pytest.raises(LockedOutError) as locked:
        lockout.begin("s")
    assert locked.value.retry_after == 1


class AlwaysConflicting(fakeredis.FakeRedis):
    """Every transaction loses its WATCH (an endless stream of rival attempts)."""

    def pipeline(self, transaction: bool = True, shard_hint: object = None):  # type: ignore[no-untyped-def]
        pipe = super().pipeline(transaction, shard_hint)  # type: ignore[arg-type]

        def execute(raise_on_error: bool = True) -> list[object]:
            raise redis.WatchError("watched key changed")

        pipe.execute = execute
        return pipe


def test_redis_attempt_gives_up_after_bounded_retries_and_fails_closed() -> None:
    lockout = ProgressiveLockout(RedisRevocationStore(AlwaysConflicting()), POLICY, "pin")
    with pytest.raises(RevocationUnavailableError):
        lockout.begin("s")


def test_a_burst_cannot_pass_the_lock(store: RevocationStore) -> None:
    """TD-191: of many simultaneous attempts, exactly the free ones plus the one that arms
    the lock run; every other one is refused."""
    lockout = ProgressiveLockout(store, POLICY, "pin")
    barrier = threading.Barrier(16)
    outcomes: list[str] = []

    def attempt() -> None:
        barrier.wait()
        try:
            lockout.begin("s")
            outcomes.append("ran")
        except LockedOutError:
            outcomes.append("locked")

    threads = [threading.Thread(target=attempt) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("ran") == POLICY.free_attempts + 1
    assert outcomes.count("locked") == 16 - POLICY.free_attempts - 1


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
        lambda s: s.begin_attempt("k", TTL, _no_delay),
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
