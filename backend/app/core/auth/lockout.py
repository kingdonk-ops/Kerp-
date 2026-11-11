"""Progressive lockout throttling, shared by all workers through the store (T-0009, diff §4).

Rewrite of Inspection-ERP ``core/auth/ratelimit.py`` (ADR 0001), whose in-memory throttle
was per worker. Here the counter and the lock live in Redis, so every worker sees them.

How an attempt runs (``ProgressiveLockout.begin``):

1. If the subject is locked, refuse at once with ``LockedOutError(retry_after)``. The
   secret is not even checked, and the refused attempt is not counted.
2. Otherwise count the attempt as a failure *before* checking the secret and, if the count
   is past the free attempts, arm the lock for the next one. Counting first means parallel
   requests cannot all slip through before a lock is written: each one takes its own
   attempt number, and attempts past ``max_attempts`` are refused by the caller outright.
3. The caller checks the secret. On success it calls ``succeeded``, which clears the
   counter and the lock (so a right answer leaves no trace).

The delay after failure ``n`` is ``0`` for the first ``free_attempts`` and then doubles from
``base_delay`` up to ``max_delay``. The counter forgets after ``window`` (from the first
failure). Subjects are hashed into the key, so no e-mail address ends up in Redis.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from app.core.auth.revocation import RevocationStore


class LockedOutError(Exception):
    """Too many failed attempts: try again in ``retry_after`` seconds (HTTP 429)."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"locked for {retry_after}s")
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class LockoutPolicy:
    free_attempts: int
    base_delay: timedelta
    max_delay: timedelta
    window: timedelta
    # Hard cap on attempts per window; ``None`` = never final (password login: a permanent
    # lock would let anyone lock a known user out).
    max_attempts: int | None = None

    def __post_init__(self) -> None:
        if self.free_attempts < 0:
            raise ValueError("free_attempts must be >= 0")
        if self.base_delay <= timedelta(0) or self.max_delay < self.base_delay:
            raise ValueError("need 0 < base_delay <= max_delay")
        if self.window <= timedelta(0):
            raise ValueError("window must be positive")
        if self.max_attempts is not None and self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")

    def delay_after(self, failures: int) -> timedelta:
        """How long the subject stays locked after its ``failures``-th failure."""
        over = failures - self.free_attempts
        if over <= 0:
            return timedelta(0)
        # Doubling passes any sane cap quickly; stop shifting before the number gets silly.
        factor = 1 << min(over - 1, 32)
        return min(self.base_delay * factor, self.max_delay)

    def exhausted(self, attempt: int) -> bool:
        """Attempt number ``attempt`` is past the hard cap and must not be checked."""
        return self.max_attempts is not None and attempt > self.max_attempts


# PIN unlock of a field-worker session: 3 free guesses, then 30 s doubling to 15 min, and
# 10 guesses per session in total (then the session is revoked: a new magic link is needed).
# 10 guesses out of 10**6 PINs: the chance of a lucky guess stays at 1e-5 per stolen device.
PIN_POLICY: Final = LockoutPolicy(
    free_attempts=3,
    base_delay=timedelta(seconds=30),
    max_delay=timedelta(minutes=15),
    window=timedelta(hours=24),
    max_attempts=10,
)

# Password login per tenant + e-mail (TD-123): 5 free, then the same escalation.
LOGIN_POLICY: Final = LockoutPolicy(
    free_attempts=5,
    base_delay=timedelta(seconds=30),
    max_delay=timedelta(minutes=15),
    window=timedelta(hours=24),
)

# Magic-link requests per tenant + e-mail: every request counts (there is nothing to get
# right), so this caps how many e-mails one address can be sent.
MAGIC_LINK_REQUEST_POLICY: Final = LockoutPolicy(
    free_attempts=3,
    base_delay=timedelta(minutes=1),
    max_delay=timedelta(minutes=15),
    window=timedelta(hours=1),
)


class ProgressiveLockout:
    """One policy applied to the subjects of one ``scope`` (``pin``, ``login``, ...)."""

    def __init__(self, store: RevocationStore, policy: LockoutPolicy, scope: str) -> None:
        self.store = store
        self.policy = policy
        self.scope = scope

    def _key(self, subject: str) -> str:
        return f"{self.scope}:{hashlib.sha256(subject.encode()).hexdigest()}"

    def begin(self, subject: str) -> int:
        """Start an attempt; returns its number (1-based). Raises ``LockedOutError``."""
        key = self._key(subject)
        remaining = self.store.lock_remaining(key)
        if remaining > 0:
            raise LockedOutError(remaining)
        attempt = self.store.count_failure(key, self.policy.window)
        delay = self.policy.delay_after(attempt)
        if delay > timedelta(0):
            self.store.set_lock(key, delay)
        return attempt

    def succeeded(self, subject: str) -> None:
        """The secret was right: forget the failures and lift the lock."""
        self.store.clear_failures(self._key(subject))

    def failures(self, subject: str) -> int:
        return self.store.failure_count(self._key(subject))
