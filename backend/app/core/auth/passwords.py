"""Password hashing with stdlib scrypt.

Adapted from Inspection-ERP ``core/auth/security.py`` (ADR 0001; scrypt per Inspection-ERP
ADR 0001). Stored format: ``scrypt$<n>$<r>$<p>$<salt hex>$<digest hex>``. The parameters
travel with the hash, so they can be raised later without invalidating stored passwords.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from functools import cache
from typing import Final

SCHEME: Final = "scrypt"
SCRYPT_N: Final = 2**14
SCRYPT_R: Final = 8
SCRYPT_P: Final = 1
_DKLEN: Final = 32
_SALT_BYTES: Final = 16
# Refuse absurd work factors from a tampered hash (memory = 128 * n * r bytes).
_MAX_N: Final = 2**17
_MAX_R: Final = 16
_MAX_P: Final = 16


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=r, p=p, dklen=_DKLEN, maxmem=256 * n * r
    )


def hash_password(password: str) -> str:
    """Hash ``password`` with a fresh random salt."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"{SCHEME}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of ``password`` against a stored hash. Any malformed hash is False.

    ``stored=None`` (a user without a local password, e.g. SSO only) still runs one scrypt
    round, so the response time does not reveal whether the account has a password.
    """
    if stored is None:
        _scrypt(password, _dummy_salt(), SCRYPT_N, SCRYPT_R, SCRYPT_P)
        return False
    try:
        scheme, n_text, r_text, p_text, salt_hex, digest_hex = stored.split("$")
        n, r, p = int(n_text), int(r_text), int(p_text)
        if scheme != SCHEME or not (1 < n <= _MAX_N and 0 < r <= _MAX_R and 0 < p <= _MAX_P):
            return False
        digest = _scrypt(password, bytes.fromhex(salt_hex), n, r, p)
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


def burn_password_check(password: str) -> None:
    """Spend the same time as a real check (unknown user), so timing does not leak existence."""
    verify_password(password, None)


@cache
def _dummy_salt() -> bytes:
    return secrets.token_bytes(_SALT_BYTES)
