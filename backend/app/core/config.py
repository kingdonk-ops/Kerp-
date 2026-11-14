"""Typed application settings, loaded from ``KERP_``-prefixed environment variables."""

import uuid
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    # Fail safe (T-0070): a deployment that forgets KERP_ENVIRONMENT gets production behaviour
    # (no ephemeral JWT key, no in-memory revocation, magic-link tokens never logged).
    # Development, tests and CI opt in to "development" / "test" explicitly.
    environment: Environment = "production"
    debug: bool = False
    version: str = Field(default_factory=_installed_version)

    # Observability (T-0021).
    log_level: LogLevel = "INFO"

    # Security headers (T-0021). The API never serves HTML (the SPA is hosted separately),
    # so the default CSP denies everything. ``hsts_enabled=None`` means "only in production".
    content_security_policy: str = "default-src 'none'; frame-ancestors 'none'"
    hsts_enabled: bool | None = None
    # API docs (T-0030, TD-022): /docs, /redoc, the OAuth2 redirect and /openapi.json are not
    # served in production. ``api_docs_enabled=None`` means "everywhere except production".
    api_docs_enabled: bool | None = None

    # Request bounds (T-0060): no endpoint accepts a larger body (413). File bytes go to object
    # storage by presigned URL, never through the API, so this does not cap uploads.
    max_body_bytes: int = Field(default=1024 * 1024, ge=1024, le=100 * 1024 * 1024)

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

    # Object storage (T-0016, TD-004): S3 in production, MinIO in dev/test. Without keys the
    # AWS default credential chain applies (IAM role). ``s3_public_endpoint_url`` is the
    # endpoint browsers reach with presigned URLs, if it differs from ``s3_endpoint_url``.
    storage_bucket: str = "kerp-vault"
    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None
    # Per deployment (TD-171 decision 13a, diff §4.4): ap-southeast-2 (Sydney) is the default
    # for Australian deployments; set KERP_S3_REGION elsewhere. MinIO ignores it.
    s3_region: str = "ap-southeast-2"
    s3_access_key_id: str | None = Field(default=None, repr=False)
    s3_secret_access_key: SecretStr | None = Field(default=None, repr=False)
    # Uploads: size limit (single-request copy caps it at 5 GiB) and presigned URL lifetimes.
    upload_max_bytes: int = Field(default=100 * 1024 * 1024, ge=1, le=5 * 1024**3)
    upload_url_seconds: int = Field(default=900, ge=60, le=3600)
    download_url_seconds: int = Field(default=300, ge=30, le=3600)

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
    # A login session ends this long after sign-in; rotation never extends it (diff §4.2).
    refresh_token_hours: int = Field(default=12, ge=1, le=24)
    # The deployment's one hidden tenant (diff §4.1, TD-120): every login signs in to it.
    # Required in production (checked at startup).
    default_tenant_id: uuid.UUID | None = None
    # Origin of the web SPA allowed to use the refresh cookie (CSRF Origin check on
    # ``/auth/refresh`` and ``/auth/pin/unlock``). Unset: the API's own origin (one URL).
    web_origin: str | None = None
    # E-mail domains whose accounts are internal staff (T-0049, diff §4.1 Internal staff):
    # comma-separated in the environment, lower-cased, exact match (no wildcards, a subdomain
    # is a different domain). Empty: no domain rule (only the Entra member link counts).
    internal_email_domains: Annotated[tuple[str, ...], NoDecode] = ()

    # Notification channels (T-0019, TD-168): the registered channel names every notification
    # goes to (JSON list in the environment). ``in_app`` is the inbox record and always on;
    # FCM push (T-0144) and e-mail (TD-084) are added here once they exist.
    notification_channels: tuple[str, ...] = ("in_app",)

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("notification_channels")
    @classmethod
    def _in_app_channel(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if "in_app" not in value:
            raise ValueError("notification_channels must include 'in_app'")
        return value

    @field_validator("internal_email_domains", mode="before")
    @classmethod
    def _parse_internal_email_domains(cls, value: object) -> object:
        """``"a.example, B.example"`` → ``("a.example", "b.example")``; refused at startup when an
        entry is not a bare domain (``@``, whitespace or ``*``)."""
        if isinstance(value, str):
            value = value.split(",")
        if not isinstance(value, list | tuple | set | frozenset):
            return value
        domains: set[str] = set()
        for raw in value:
            if not isinstance(raw, str):
                raise ValueError("internal_email_domains entries must be strings")
            entry = raw.strip().lower()
            if not entry:
                continue
            if "@" in entry or "*" in entry or any(ch.isspace() for ch in entry):
                raise ValueError(
                    f"internal_email_domains entry {entry!r} is not a bare domain "
                    "(no '@', spaces or wildcards; e.g. 'company.example')"
                )
            domains.add(entry)
        return tuple(sorted(domains))

    @model_validator(mode="after")
    def _no_debug_in_production(self) -> "Settings":
        # Debug mode shows tracebacks to callers; refuse to start with it in production (T-0030).
        if self.debug and self.environment == "production":
            raise ValueError("KERP_DEBUG=true is not allowed with KERP_ENVIRONMENT=production")
        return self

    @property
    def api_docs_active(self) -> bool:
        """Whether the API docs and OpenAPI schema are served (explicit setting, else non-prod)."""
        if self.api_docs_enabled is None:
            return self.environment != "production"
        return self.api_docs_enabled

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
