"""The password policy (T-0077, diff §4.2 *Local passwords*): length only, NFKC, blocklist and
context words, the optional breach lookup, and the one set-password API. Synthetic values only.
"""

import io
import logging
import unicodedata
import urllib.error
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.auth import password_policy
from app.core.auth.password_policy import (
    MAX_LENGTH,
    PASSWORD_TOO_COMMON,
    PASSWORD_TOO_LONG,
    PASSWORD_TOO_SHORT,
    PasswordPolicy,
    PasswordPolicyError,
    breached_by_range_lookup,
    check_password,
    common_passwords,
    context_words,
    is_common,
    normalize_password,
    policy_from_settings,
)
from app.core.auth.passwords import verify_password
from app.core.auth.service import create_user, get_user, set_password, set_user_password
from app.core.config import Settings
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.scope import OutOfScopeError
from app.main import create_app
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, T1, T2, make_settings, seed_user

GOOD = "plum-Radio 41 kettle"  # synthetic
SETTINGS = make_settings()


def _fullwidth(text: str) -> str:
    """ASCII letters and digits as their full-width forms (NFKC folds them back)."""
    return "".join(chr(ord(ch) + 0xFEE0) for ch in text)


def _code(password: str, **kwargs: Any) -> str:
    with pytest.raises(PasswordPolicyError) as refused:
        check_password(password, settings=kwargs.pop("settings", SETTINGS), **kwargs)
    assert password not in str(refused.value)
    return refused.value.code


# --- length ---------------------------------------------------------------------------------


def test_seven_characters_are_refused_and_eight_accepted_at_the_default() -> None:
    assert SETTINGS.password_min_length == 8
    assert _code("xk7#Qp2") == PASSWORD_TOO_SHORT
    assert check_password("xk7#Qp2m", settings=SETTINGS) == "xk7#Qp2m"


def test_a_raised_minimum_is_honoured() -> None:
    settings = make_settings(password_min_length=12)
    assert _code("xk7#Qp2m-Lw", settings=settings) == PASSWORD_TOO_SHORT  # 11
    assert check_password("xk7#Qp2m-Lw9", settings=settings) == "xk7#Qp2m-Lw9"
    with pytest.raises(PasswordPolicyError) as refused:
        check_password("short", settings=settings)
    assert refused.value.min_length == 12
    assert "12" in str(refused.value)


@pytest.mark.parametrize("value", [7, 0, -1])
def test_a_minimum_below_eight_is_refused_by_the_settings(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", password_min_length=value)
    with pytest.raises(ValueError, match="never below 8"):
        PasswordPolicy(min_length=value)


def test_start_up_refuses_a_minimum_below_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("KERP_ENVIRONMENT", "test")
    monkeypatch.setenv("KERP_PASSWORD_MIN_LENGTH", "7")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="password_min_length"):
            create_app()
    finally:
        get_settings.cache_clear()


def test_up_to_256_characters_are_accepted_and_more_refused() -> None:
    long = "k7 Q" * 63 + "zzzy"  # 256, not a repetition
    assert len(long) == MAX_LENGTH
    assert check_password(long, settings=SETTINGS) == long
    assert _code(long + "!") == PASSWORD_TOO_LONG
    with pytest.raises(ValueError, match="max_length"):
        PasswordPolicy(min_length=10, max_length=9)


def test_no_composition_rules_spaces_and_unicode_are_fine() -> None:
    spaced = "        x       "
    for password in ("all lower case words", "ünïcödé pässphrase", "密码短语很安全的", spaced):
        assert check_password(password, settings=SETTINGS) == normalize_password(password)


# --- NFKC -----------------------------------------------------------------------------------


def test_the_password_is_nfkc_normalised_before_checks_and_hashing() -> None:
    decomposed = unicodedata.normalize("NFD", "café-Zürich 9")
    fullwidth = _fullwidth("caf") + "é-Zürich 9"
    assert decomposed != "café-Zürich 9"
    assert check_password(decomposed, settings=SETTINGS) == "café-Zürich 9"
    assert check_password(fullwidth, settings=SETTINGS) == "café-Zürich 9"
    # Length counts normalised characters: 7 composed characters typed as 9 code points.
    assert _code(unicodedata.normalize("NFD", "éàüö#Q2")) == PASSWORD_TOO_SHORT
    # A full-width spelling of a listed password is that listed password.
    assert _code(_fullwidth("password123")) == PASSWORD_TOO_COMMON


# --- blocklist and context words --------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [
        "password",
        "PASSWORD123",
        "Password1!",  # a listed word + digits / symbols
        "qwertyuiop",
        "12345678",
        "87654321",
        "iloveyou",
        "Summer2026!",
        "aaaaaaaa",
        "abcabcabc",
        "asdfghjkl",
        "lkjhgfdsa",
        "abcdefghij",
        "1q2w3e4r5t",
    ],
)
def test_common_passwords_and_patterns_are_refused(password: str) -> None:
    assert _code(password) == PASSWORD_TOO_COMMON


def test_the_bundled_blocklist_is_loaded_folded_and_without_comments() -> None:
    listed = common_passwords()
    assert len(listed) > 500
    assert "password" in listed
    assert "qwerty123" in listed
    assert not any(entry.startswith("#") or entry != entry.strip() for entry in listed)
    assert all(entry == entry.casefold() for entry in listed)


@pytest.mark.parametrize(
    "password",
    [
        ALICE,  # the e-mail itself
        ALICE.upper(),
        f"{ALICE}1",
        "kerpkerp",
        "KERP2026",
        "kerp-kerp!",
        "Kerp#1234",
        "k.e.r.p-2026",
    ],
)
def test_context_words_are_refused(password: str) -> None:
    assert _code(password, email=ALICE) == PASSWORD_TOO_COMMON


def test_the_local_part_and_the_deployment_name_are_context_words() -> None:
    email = "jordan.example-qa@example.test"
    assert context_words(email, ("Site Ops",)) >= {
        "kerp",
        "site ops",
        email,
        "jordan.example-qa",
        "jordanexampleqa",
    }
    for password in ("jordan.example-qa", "JordanExampleQA1", "jordan.example-qa99"):
        assert _code(password, email=email) == PASSWORD_TOO_COMMON
    named = make_settings(app_name="Northwind Field")
    assert _code("northwind field", settings=named) == PASSWORD_TOO_COMMON
    assert _code("Northwind Field 1", settings=named) == PASSWORD_TOO_COMMON
    # Another account's address is not this account's context.
    assert check_password("jordan.example-qa99", email=ALICE, settings=SETTINGS)


def test_a_listed_word_inside_a_longer_passphrase_is_fine() -> None:
    assert not is_common("password protects my summer dragon")
    assert not is_common("dragon-9173648-zebra")
    assert check_password(GOOD, email=ALICE, settings=SETTINGS) == GOOD


# --- the optional breach lookup ---------------------------------------------------------------


def test_the_breach_lookup_is_off_by_default_and_never_called() -> None:
    def boom(_password: str) -> bool:
        raise AssertionError("looked up")

    assert SETTINGS.password_breach_check is False
    assert policy_from_settings(SETTINGS, breach_check=boom).breach_check is None
    assert check_password(GOOD, settings=SETTINGS, breach_check=boom) == GOOD


def test_a_breached_password_is_refused_when_the_lookup_is_on() -> None:
    settings = make_settings(password_breach_check=True)
    seen: list[str] = []

    def lookup(password: str) -> bool:
        seen.append(password)
        return password == GOOD

    assert _code(GOOD, settings=settings, breach_check=lookup) == PASSWORD_TOO_COMMON
    assert check_password("other plum 77 radio", settings=settings, breach_check=lookup)
    assert seen == [GOOD, "other plum 77 radio"]
    assert policy_from_settings(settings).breach_check is breached_by_range_lookup


def test_a_failing_lookup_never_blocks(caplog: pytest.LogCaptureFixture) -> None:
    settings = make_settings(password_breach_check=True)

    def broken(_password: str) -> bool:
        raise RuntimeError("synthetic outage")

    with caplog.at_level(logging.WARNING, logger="kerp.auth.password_policy"):
        assert check_password(GOOD, settings=settings, breach_check=broken) == GOOD
    assert "not blocking" in caplog.text
    assert GOOD not in caplog.text


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def test_the_range_lookup_sends_only_a_five_character_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SHA-1("plum-Radio 41 kettle") is computed here, never hard-coded from a real leak.
    import hashlib

    digest = hashlib.sha1(GOOD.encode(), usedforsecurity=False).hexdigest().upper()
    requested: list[Any] = []

    def fake_urlopen(request: Any, timeout: float) -> _Response:
        requested.append((request.full_url, dict(request.header_items()), timeout))
        body = f"0000000000000000000000000000000000A:3\r\n{digest[5:]}:0\r\n"
        if len(requested) > 1:
            body += f"{digest[5:].lower()}:12\r\n"
        return _Response(body.encode())

    monkeypatch.setattr(password_policy.urllib.request, "urlopen", fake_urlopen)
    assert breached_by_range_lookup(GOOD) is False  # a padding entry (count 0) is no hit
    assert breached_by_range_lookup(GOOD) is False  # the first matching line decides
    url, headers, timeout = requested[0]
    assert url == f"https://api.pwnedpasswords.com/range/{digest[:5]}"
    assert digest[5:] not in url
    assert GOOD not in url
    assert headers["Add-padding"] == "true"
    assert timeout == password_policy.BREACH_TIMEOUT_SECONDS

    def listed(_request: Any, timeout: float) -> _Response:
        return _Response(f"{digest[5:]}:42\n".encode())

    monkeypatch.setattr(password_policy.urllib.request, "urlopen", listed)
    assert breached_by_range_lookup(GOOD) is True


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("synthetic: unreachable"),
        TimeoutError("synthetic: timeout"),
        b"\xff\xfe not ascii",
        b"x" * (300 * 1024),
        b"ABCDEF:not-a-number\n",
    ],
)
def test_the_range_lookup_fails_open(
    failure: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import hashlib

    suffix = hashlib.sha1(GOOD.encode(), usedforsecurity=False).hexdigest().upper()[5:]

    def fake_urlopen(_request: Any, timeout: float) -> _Response:
        if isinstance(failure, Exception):
            raise failure
        return _Response(failure.replace(b"ABCDEF", suffix.encode()))

    monkeypatch.setattr(password_policy.urllib.request, "urlopen", fake_urlopen)
    with caplog.at_level(logging.WARNING, logger="kerp.auth.password_policy"):
        assert breached_by_range_lookup(GOOD) is False
    assert "not blocking" in caplog.text
    assert GOOD not in caplog.text
    assert suffix not in caplog.text


# --- the one way to set a password ------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def test_set_user_password_enforces_the_policy_and_stores_the_nfkc_hash(db: Database) -> None:
    user_id = seed_user(db, T1, ALICE, password=None)
    scope = TenancyContext(tenant_id=T1)
    with db.unit_of_work(scope) as session:
        for bad, code in (("short", PASSWORD_TOO_SHORT), (ALICE, PASSWORD_TOO_COMMON)):
            with pytest.raises(PasswordPolicyError) as refused:
                set_user_password(session, user_id, bad, settings=SETTINGS)
            assert refused.value.code == code
        user = set_user_password(
            session, user_id, unicodedata.normalize("NFD", "café-Zürich 9"), settings=SETTINGS
        )
        assert user.password_changed_at is not None
        assert verify_password("café-Zürich 9", user.password_hash)
    with db.unit_of_work(scope) as session, pytest.raises(OutOfScopeError):
        set_user_password(session, uuid.UUID(int=1), GOOD, settings=SETTINGS)
    # Another tenant's session cannot reach the account.
    with db.unit_of_work(TenancyContext(tenant_id=T2)) as session, pytest.raises(OutOfScopeError):
        set_user_password(session, user_id, GOOD, settings=SETTINGS)


def test_a_refused_password_changes_nothing(db: Database) -> None:
    user_id = seed_user(db, T1, ALICE)
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as session:
        account = get_user(session, user_id)
        before = (account.password_hash, account.password_changed_at)
        with pytest.raises(PasswordPolicyError):
            set_password(account, "password123", settings=SETTINGS)
        assert (account.password_hash, account.password_changed_at) == before


def test_create_user_applies_the_policy_to_an_initial_password(db: Database) -> None:
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as session:
        new = "new@example.test"
        with pytest.raises(PasswordPolicyError) as refused:
            create_user(session, tenant_id=T1, email=new, password=new, settings=SETTINGS)
        assert refused.value.code == PASSWORD_TOO_COMMON
        typed = _fullwidth("full") + " width 77"
        user = create_user(session, tenant_id=T1, email=new, password=typed, settings=SETTINGS)
        assert verify_password("full width 77", user.password_hash)
        without = create_user(
            session, tenant_id=T1, email="none@example.test", password=None, settings=SETTINGS
        )
        assert without.password_hash is None
