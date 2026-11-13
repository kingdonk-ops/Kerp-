"""Typed application settings, loaded from ``KERP_``-prefixed environment variables."""

import uuid
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_NAME = "kerp-backend"

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _installed_version() -> str:
    """Version of the installed backend distribution (single source: pyproject.toml)."""
    try:
        return package_version(PACKAGE_NAME)
    except PackageNotFoundError:  # pragma: no cover - only when run from an uninstalled tree
        return "0.0.0+unknown"


class Settings(BaseSettings):
    """Runtime configuration. Every field can be overridden by ``KERP_<FIELD>``."""

    model_config = SettingsConfigDict(
        env_prefix="KERP_",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    app_name: str = "KERP"
    environment: Environment = "development"
    debug: bool = False
    version: str = Field(default_factory=_installed_version)

    # Observability (T-0021).
    log_level: LogLevel = "INFO"

    # Security headers (T-0021). The API never serves HTML (the SPA is hosted separately),
    # so the default CSP denies everything. ``hsts_enabled=None`` means "only in production".
    content_security_policy: str = "default-src 'none'; frame-ancestors 'none'"
    hsts_enabled: bool | None = None

    # Database (T-0005). Kept out of repr so credentials embedded in URLs are never logged.
    # ``database_url`` is the runtime role (NOSUPERUSER NOBYPASSRLS on PostgreSQL);
    # ``migration_database_url`` is the schema owner Alembic runs as (falls back to the former).
    database_url: str | None = Field(default=None, repr=False)
    migration_database_url: str | None = Field(default=None, repr=False)
    # Redis (job queue, auth token revocation).
    redis_url: str | None = Field(default=None, repr=False)
    # SAQ worker (T-0015): concurrent jobs per queue in one worker process.
    jobs_interactive_concurrency: int = Field(default=4, ge=1, le=64)
    jobs_batch_concurrency: int = Field(default=1, ge=1, le=16)

    # Auth tokens (T-0008, diff §4). Asymmetric JWTs, public keys served as JWKS.
    # ``jwt_private_key`` is a PEM (RSA for RS256, Ed25519 for EdDSA); ``jwt_private_key_file``
    # points at one instead. Unset outside production: an ephemeral key is generated per
    # process (tokens die with it). Production refuses to issue tokens without a key.
    jwt_algorithm: Literal["EdDSA", "RS256"] = "EdDSA"
    jwt_private_key: SecretStr | None = Field(default=None, repr=False)
    jwt_private_key_file: Path | None = None
    # Public PEMs of retired signing keys, still accepted and published during rotation
    # (JSON list in the environment).
    jwt_retired_public_keys: tuple[str, ...] = ()
    jwt_issuer: str = "kerp"
    jwt_audience: str = "kerp-api"
    access_token_minutes: int = Field(default=15, ge=1, le=15)
    refresh_token_days: int = Field(default=14, ge=1, le=90)
    # Tenant a login without an explicit ``tenant_id`` signs in to (single-company deployment).
    default_tenant_id: uuid.UUID | None = None

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @property
    def hsts_active(self) -> bool:
        """Whether ``Strict-Transport-Security`` is sent (explicit setting, else production)."""
        if self.hsts_enabled is None:
            return self.environment == "production"
        return self.hsts_enabled


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings instance (call ``get_settings.cache_clear()`` in tests)."""
    return Settings()
