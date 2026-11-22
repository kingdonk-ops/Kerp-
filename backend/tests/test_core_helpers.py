"""The shared helpers that replaced copied code (T-0349): one SHA-256 hex digest and one
naive-to-aware timestamp helper. Every former copy is the shared function itself."""

import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.auth import devices, field, password_reset, sso
from app.core.digest import sha256_hex
from app.core.timestamps import aware_utc
from app.modules.admin import invites


def test_sha256_hex_is_the_hex_sha256_of_the_utf8_value() -> None:
    assert sha256_hex("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert sha256_hex("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert sha256_hex("é") == hashlib.sha256("é".encode()).hexdigest()


@pytest.mark.parametrize(
    "digest",
    [
        field.magic_link_digest,
        password_reset.reset_digest,
        sso.digest,
        devices.wipe_secret_digest,
        invites.token_digest,
    ],
)
def test_every_token_digest_is_the_one_helper(digest: object) -> None:
    assert digest is sha256_hex


def test_aware_utc_attaches_utc_to_a_naive_value_only() -> None:
    naive = datetime(2026, 1, 2, 3, 4, 5)
    assert aware_utc(naive) == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    aware = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=2)))
    assert aware_utc(aware) is aware  # never converted, only completed
