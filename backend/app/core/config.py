"""Typed application settings, loaded from ``KERP_``-prefixed environment variables."""

from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Literal

from pydantic import Field, field_validator
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
    # Redis (job queue / revocation, later tasks).
    redis_url: str | None = Field(default=None, repr=False)

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
