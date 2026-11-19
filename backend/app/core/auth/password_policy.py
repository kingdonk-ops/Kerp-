"""The one password policy of a deployment (T-0077, diff §4.2 *Local passwords*).

NIST SP 800-63B rev. 4 style, product-owner decision 2026-11-14:

* **Length only.** At least ``KERP_PASSWORD_MIN_LENGTH`` characters (default 8, never below
  8: the settings refuse a smaller value at start-up), at most ``MAX_LENGTH`` (256). Every
  printable character, spaces and Unicode are allowed. No composition rules, no expiry, no
  hints.
* **NFKC before anything else.** ``normalize_password`` is applied before the checks and
  before hashing, so the same password typed on another keyboard or platform (composed or
  decomposed accents, full-width forms) is the same password. Sign-in normalises the same way
  (``providers.LocalPasswordProvider``).
* **Blocklist.** A bundled list of common and compromised passwords
  (``data/common_passwords.txt``), obvious patterns (one repeated character or short unit,
  keyboard and counting runs) and **context words**: the account's e-mail and its local
  part, ``kerp`` and the deployment's name (``KERP_APP_NAME``). A password that is such a
  word with up to ``MAX_AFFIX`` leading / trailing digits or symbols (``Password1!``,
  ``kerp2026``) is refused as well.
* **Optional breach lookup** (``KERP_PASSWORD_BREACH_CHECK``, off by default): the k-anonymity
  range API, which sees the first 5 hex characters of the SHA-1 only. A lookup that fails
  (network, timeout, a malformed answer) never blocks a password.

``check_password`` is the public entry point; it returns the normalised password to hash or
raises ``PasswordPolicyError`` with a machine-readable ``code`` (``PASSWORD_TOO_SHORT``,
``PASSWORD_TOO_LONG``, ``PASSWORD_TOO_COMMON``) the client translates. Callers that store a
password go through ``app.core.auth.service.set_user_password`` (or ``create_user``), which
call it: no module hashes a password or writes the users table itself (CLAUDE.md §6).

Nothing here logs or keeps a password.
"""

from __future__ import annotations

import hashlib
import logging
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Final, Literal

from app.core.config import Settings, get_settings

PASSWORD_TOO_SHORT: Final = "PASSWORD_TOO_SHORT"
PASSWORD_TOO_LONG: Final = "PASSWORD_TOO_LONG"
PASSWORD_TOO_COMMON: Final = "PASSWORD_TOO_COMMON"
PasswordCode = Literal["PASSWORD_TOO_SHORT", "PASSWORD_TOO_LONG", "PASSWORD_TOO_COMMON"]

# Accepted up to this many characters (diff §4.2: "maximum at least 64, accepted up to 256").
MAX_LENGTH: Final = 256
# The floor no deployment may go below (product owner, 2026-11-14).
MIN_LENGTH_FLOOR: Final = 8
# A listed word with at most this many leading / trailing digits or symbols is still listed.
MAX_AFFIX: Final = 6
# A listed word or context word shorter than this is only matched exactly, never as a core.
_MIN_CORE: Final = 3
# A password that is one unit of at most this many characters repeated is a pattern.
_MAX_REPEAT_UNIT: Final = 4
# Always a context word, whatever the deployment calls itself.
PRODUCT_WORD: Final = "kerp"

# Counting and keyboard runs: a password that is a slice of one (either direction) is refused.
_RUNS: Final = (
    "01234567890123456789012345678901234567890",
    "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz",
    "qwertyuiopasdfghjklzxcvbnm",
    "qwertzuiopasdfghjklyxcvbnm",
    "azertyuiopqsdfghjklmwxcvbn",
    "1qaz2wsx3edc4rfv5tgb6yhn7ujm8ik9ol0p",
    "1q2w3e4r5t6y7u8i9o0p",
)

BREACH_RANGE_URL: Final = "https://api.pwnedpasswords.com/range/"
BREACH_TIMEOUT_SECONDS: Final = 3.0
_BREACH_MAX_BYTES: Final = 256 * 1024

_logger = logging.getLogger("kerp.auth.password_policy")

# Answers whether the (normalised) password is known to be breached. Must not raise.
BreachCheck = Callable[[str], bool]


class PasswordPolicyError(ValueError):
    """The password is refused. ``code`` says why; the message never contains the password."""

    def __init__(
        self, code: PasswordCode, *, min_length: int, max_length: int = MAX_LENGTH
    ) -> None:
        super().__init__(_DETAILS[code].format(min_length=min_length, max_length=max_length))
        self.code: PasswordCode = code
        self.min_length = min_length
        self.max_length = max_length


_DETAILS: Final[dict[str, str]] = {
    PASSWORD_TOO_SHORT: "Password is too short: use at least {min_length} characters",
    PASSWORD_TOO_LONG: "Password is too long: use at most {max_length} characters",
    PASSWORD_TOO_COMMON: (
        "Password is too common or too easy to guess: choose a different one"
    ),
}


def normalize_password(password: str) -> str:
    """The form that is checked, hashed and verified: Unicode NFKC, nothing trimmed."""
    return unicodedata.normalize("NFKC", password)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


@cache
def common_passwords() -> frozenset[str]:
    """The bundled blocklist, folded like the passwords it is compared with."""
    resource = files(__package__ or "app.core.auth") / "data" / "common_passwords.txt"
    entries = (line.strip() for line in resource.read_text(encoding="utf-8").splitlines())
    return frozenset(_fold(entry) for entry in entries if entry and not entry.startswith("#"))


def context_words(email: str | None, names: Iterable[str] = ()) -> frozenset[str]:
    """Words an attacker tries first for this account: the e-mail, its local part (also
    without separators), the product name and the deployment's names."""
    words = {PRODUCT_WORD}
    words.update(_fold(name).strip() for name in names)
    if email:
        address = _fold(email).strip()
        local = address.partition("@")[0]
        words.update((address, local, "".join(ch for ch in local if ch.isalnum())))
    return frozenset(word for word in words if word)


def _core(folded: str) -> str:
    """``folded`` without leading / trailing characters that are not letters."""
    start, end = 0, len(folded)
    while start < end and not folded[start].isalpha():
        start += 1
    while end > start and not folded[end - 1].isalpha():
        end -= 1
    return folded[start:end]


def _is_repetition(folded: str) -> bool:
    """One unit of 1 to ``_MAX_REPEAT_UNIT`` characters, repeated (``aaaaaaaa``, ``abcabcabc``)."""
    size = len(folded)
    for unit in range(1, _MAX_REPEAT_UNIT + 1):
        if size > unit and size % unit == 0 and folded == folded[:unit] * (size // unit):
            return True
    return False


def _is_run(folded: str) -> bool:
    return any(folded in run or folded in run[::-1] for run in _RUNS)


def _repeats_a_word(folded: str, words: frozenset[str]) -> bool:
    """``kerpkerp``: nothing but one context word, two or more times."""
    for word in words:
        if len(word) < _MIN_CORE:
            continue
        count, rest = divmod(len(folded), len(word))
        if rest == 0 and count > 1 and folded == word * count:
            return True
    return False


def is_common(password: str, words: frozenset[str] = frozenset()) -> bool:
    """Whether the (normalised) password is on the blocklist, a pattern or a context word."""
    folded = _fold(password)
    listed = common_passwords()
    if folded in listed or folded in words:
        return True
    if _is_repetition(folded) or _is_run(folded) or _repeats_a_word(folded, words):
        return True
    core = _core(folded)
    affixed = len(core) >= _MIN_CORE and len(folded) - len(core) <= MAX_AFFIX
    if affixed and (core in listed or core in words or _repeats_a_word(core, words)):
        return True
    # Context words joined by separators only (``kerp-kerp!``, ``a.l.i.c.e``).
    squeezed = "".join(ch for ch in folded if ch.isalpha())
    return len(squeezed) >= _MIN_CORE and (
        squeezed in words or _repeats_a_word(squeezed, words)
    )


def breached_by_range_lookup(password: str) -> bool:
    """The k-anonymity lookup: ``True`` only when the range API lists the password's SHA-1.

    Only the first 5 hex characters of the digest are sent. Any failure is ``False`` (the
    lookup never blocks a password, diff §4.2) and is logged without the password or digest.
    """
    digest = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    request = urllib.request.Request(
        f"{BREACH_RANGE_URL}{prefix}",
        headers={"Add-Padding": "true", "User-Agent": "kerp-password-policy"},
    )
    try:
        with urllib.request.urlopen(request, timeout=BREACH_TIMEOUT_SECONDS) as response:
            body = response.read(_BREACH_MAX_BYTES + 1)
        if len(body) > _BREACH_MAX_BYTES:
            raise ValueError("response too large")
        return _suffix_listed(body.decode("ascii"), suffix)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        _logger.warning("breached-password lookup failed (%s): not blocking", type(exc).__name__)
        return False


def _suffix_listed(body: str, suffix: str) -> bool:
    for line in body.splitlines():
        candidate, _, count = line.strip().partition(":")
        if candidate.upper() == suffix:
            return int(count) > 0  # padding entries carry a count of 0
    return False


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    """A deployment's policy. ``names`` are the deployment's context words (its name)."""

    min_length: int = MIN_LENGTH_FLOOR
    max_length: int = MAX_LENGTH
    names: tuple[str, ...] = ()
    breach_check: BreachCheck | None = None

    def __post_init__(self) -> None:
        if self.min_length < MIN_LENGTH_FLOOR:
            raise ValueError(f"the minimum password length is never below {MIN_LENGTH_FLOOR}")
        if self.max_length < self.min_length:
            raise ValueError("max_length must be at least min_length")

    def check(self, password: str, *, email: str | None = None) -> str:
        """The normalised password, or ``PasswordPolicyError``."""
        normalised = normalize_password(password)
        if len(normalised) < self.min_length:
            raise self._refuse(PASSWORD_TOO_SHORT)
        if len(normalised) > self.max_length:
            raise self._refuse(PASSWORD_TOO_LONG)
        if is_common(normalised, context_words(email, self.names)):
            raise self._refuse(PASSWORD_TOO_COMMON)
        if self.breach_check is not None and self._breached(normalised):
            raise self._refuse(PASSWORD_TOO_COMMON)
        return normalised

    def _breached(self, normalised: str) -> bool:
        check = self.breach_check
        if check is None:  # pragma: no cover - guarded by the caller
            return False
        try:
            return bool(check(normalised))
        except Exception as exc:  # a broken lookup never blocks a password
            _logger.warning(
                "breached-password lookup failed (%s): not blocking", type(exc).__name__
            )
            return False

    def _refuse(self, code: PasswordCode) -> PasswordPolicyError:
        return PasswordPolicyError(code, min_length=self.min_length, max_length=self.max_length)


def policy_from_settings(
    settings: Settings | None = None, *, breach_check: BreachCheck | None = None
) -> PasswordPolicy:
    """The deployment's policy. ``breach_check`` replaces the range lookup (tests); it is
    only ever used when ``KERP_PASSWORD_BREACH_CHECK`` is on."""
    settings = settings or get_settings()
    lookup: BreachCheck | None = None
    if settings.password_breach_check:
        lookup = breach_check or breached_by_range_lookup
    return PasswordPolicy(
        min_length=settings.password_min_length,
        names=(settings.app_name,),
        breach_check=lookup,
    )


def check_password(
    password: str,
    *,
    email: str | None = None,
    settings: Settings | None = None,
    breach_check: BreachCheck | None = None,
) -> str:
    """Check ``password`` for the account ``email`` under the deployment's policy and return
    the normalised password to hash. Raises ``PasswordPolicyError``."""
    return policy_from_settings(settings, breach_check=breach_check).check(password, email=email)
