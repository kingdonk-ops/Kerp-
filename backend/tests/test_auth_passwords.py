"""scrypt password hashing (T-0008, ported from Inspection-ERP per ADR 0001)."""

import pytest

from app.core.auth.passwords import (
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    burn_password_check,
    hash_password,
    verify_password,
)


def test_hash_has_self_describing_format_and_random_salt() -> None:
    first, second = hash_password("s3cret"), hash_password("s3cret")
    assert first != second
    scheme, n, r, p, salt, digest = first.split("$")
    assert (scheme, int(n), int(r), int(p)) == ("scrypt", SCRYPT_N, SCRYPT_R, SCRYPT_P)
    assert len(bytes.fromhex(salt)) == 16
    assert len(bytes.fromhex(digest)) == 32


def test_verify_accepts_the_right_password_only() -> None:
    stored = hash_password("s3cret")
    assert verify_password("s3cret", stored)
    assert not verify_password("S3cret", stored)
    assert not verify_password("", stored)


def test_verify_handles_unicode() -> None:
    stored = hash_password("pässwörd-✓")
    assert verify_password("pässwörd-✓", stored)


def test_verify_uses_parameters_stored_with_the_hash() -> None:
    # A hash made with other (smaller) parameters still verifies: work factors can change.
    import hashlib

    salt = bytes(16)
    digest = hashlib.scrypt(b"pw", salt=salt, n=2**10, r=8, p=1, dklen=32)
    assert verify_password("pw", f"scrypt${2**10}$8$1${salt.hex()}${digest.hex()}")


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "",
        "not-a-hash",
        "bcrypt$16384$8$1$00$00",
        "scrypt$x$8$1$00$00",
        "scrypt$16384$8$1$zz$00",
        "scrypt$16384$8$1$00",
        f"scrypt${2**30}$8$1$00$00",  # absurd work factor from a tampered row
        "scrypt$1$8$1$00$00",
        "scrypt$16384$0$1$00$00",
        "scrypt$16384$8$0$00$00",
    ],
)
def test_verify_rejects_malformed_or_missing_hashes(stored: str | None) -> None:
    assert verify_password("pw", stored) is False


def test_burn_password_check_runs_without_error() -> None:
    burn_password_check("whatever")
