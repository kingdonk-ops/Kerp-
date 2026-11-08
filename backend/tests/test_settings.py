"""Typed settings loaded from the environment (T-0001)."""

from importlib.metadata import version as package_version

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings

_KERP_VARS = (
    "KERP_APP_NAME",
    "KERP_ENVIRONMENT",
    "KERP_DEBUG",
    "KERP_VERSION",
    "KERP_DATABASE_URL",
    "KERP_REDIS_URL",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in _KERP_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_defaults_without_env(clean_env: pytest.MonkeyPatch) -> None:
    s = Settings()
    assert s.app_name == "KERP"
    assert s.environment == "development"
    assert s.debug is False
    assert s.database_url is None
    assert s.redis_url is None


def test_version_defaults_to_installed_package_version(clean_env: pytest.MonkeyPatch) -> None:
    assert Settings().version == package_version("kerp-backend")


def test_values_are_read_from_kerp_prefixed_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("KERP_APP_NAME", "KERP Synthetic")
    clean_env.setenv("KERP_ENVIRONMENT", "production")
    clean_env.setenv("KERP_DEBUG", "true")
    clean_env.setenv("KERP_VERSION", "1.2.3")
    clean_env.setenv("KERP_DATABASE_URL", "postgresql+psycopg://u:p@db.invalid:5432/kerp")
    clean_env.setenv("KERP_REDIS_URL", "redis://cache.invalid:6379/0")

    s = Settings()

    assert s.app_name == "KERP Synthetic"
    assert s.environment == "production"
    assert s.debug is True
    assert s.version == "1.2.3"
    assert s.database_url == "postgresql+psycopg://u:p@db.invalid:5432/kerp"
    assert s.redis_url == "redis://cache.invalid:6379/0"


def test_unprefixed_env_is_ignored(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ENVIRONMENT", "production")
    clean_env.setenv("APP_NAME", "not-kerp")
    s = Settings()
    assert s.environment == "development"
    assert s.app_name == "KERP"


def test_invalid_environment_is_rejected(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("KERP_ENVIRONMENT", "staging-ish")
    with pytest.raises(ValidationError):
        Settings()


def test_invalid_bool_is_rejected(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("KERP_DEBUG", "perhaps")
    with pytest.raises(ValidationError):
        Settings()


def test_connection_urls_are_not_in_repr(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("KERP_DATABASE_URL", "postgresql+psycopg://u:synthetic-pw@db.invalid/kerp")
    clean_env.setenv("KERP_REDIS_URL", "redis://:synthetic-pw@cache.invalid:6379/0")
    assert "synthetic-pw" not in repr(Settings())


def test_get_settings_is_cached(clean_env: pytest.MonkeyPatch) -> None:
    assert get_settings() is get_settings()


def test_get_settings_reads_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("KERP_VERSION", "4.5.6")
    assert get_settings().version == "4.5.6"
