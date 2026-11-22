"""The per-app auth runtime: keys, tokens, revocation store, magic-link delivery.

Built from the app's settings and cached on ``app.state.auth``. Outside production it is
built lazily on first use, so an app that never authenticates (``/health``) needs no key or
Redis. In production the API ``lifespan`` builds it at startup, so a missing or invalid
signing key refuses the start instead of failing the first sign-in (T-0342, TD-124). Tests
install their own runtime with ``install_auth_runtime`` (e.g. one backed by ``fakeredis``).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from fastapi import FastAPI, Request

from app.core.auth.field import MagicLinkSender, sender_from_settings
from app.core.auth.keys import AuthConfigError, KeySet, keyset_from_settings
from app.core.auth.revocation import (
    InMemoryRevocationStore,
    RedisRevocationStore,
    RevocationStore,
)
from app.core.auth.tokens import TokenService
from app.core.config import Settings

if TYPE_CHECKING:
    # Imported where used: ``entra`` needs the ORM models, which import the DB layer, which
    # imports this module (bearer → runtime).
    from app.core.auth.entra import EntraOidcProvider, EntraTransport

STATE_ATTR: Final = "auth"
_logger = logging.getLogger("kerp.auth")
_build_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class EntraSso:
    """The SSO provider and the transport it talks to Microsoft through (a fake in tests)."""

    provider: EntraOidcProvider
    transport: EntraTransport


@dataclass(frozen=True, slots=True)
class AuthRuntime:
    settings: Settings
    keys: KeySet
    tokens: TokenService
    store: RevocationStore
    magic_links: MagicLinkSender
    # Entra ID SSO (T-0054): ``None`` when the deployment does not offer it.
    entra: EntraSso | None = None

    def close(self) -> None:
        self.store.close()


def build_auth_runtime(
    settings: Settings,
    *,
    keys: KeySet | None = None,
    store: RevocationStore | None = None,
    magic_links: MagicLinkSender | None = None,
    entra_transport: EntraTransport | None = None,
) -> AuthRuntime:
    """Assemble the runtime. Raises ``AuthConfigError`` on unusable production config."""
    if keys is None:
        keys, ephemeral = keyset_from_settings(settings)
        if ephemeral:
            _logger.warning(
                "no JWT signing key configured: using an ephemeral key "
                "(tokens stop verifying when the process restarts)"
            )
    if store is None:
        store = _store_from_settings(settings)
    tokens = TokenService(
        keys,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        access_ttl=timedelta(minutes=settings.access_token_minutes),
        refresh_ttl=timedelta(hours=settings.refresh_token_hours),
    )
    return AuthRuntime(
        settings=settings,
        keys=keys,
        tokens=tokens,
        store=store,
        magic_links=magic_links or sender_from_settings(settings),
        entra=_entra_from_settings(settings, store, entra_transport),
    )


def _entra_from_settings(
    settings: Settings, store: RevocationStore, transport: EntraTransport | None
) -> EntraSso | None:
    from app.core.auth.entra import EntraJwks, EntraOidcProvider, HttpEntraTransport, entra_config

    config = entra_config(settings)
    if config is None:
        return None
    transport = transport or HttpEntraTransport()
    bound = transport
    jwks = EntraJwks(lambda: bound.fetch_jwks(config), store)
    provider = EntraOidcProvider(config, jwks, internal_domains=settings.internal_email_domains)
    return EntraSso(provider=provider, transport=transport)


def check_startup_config(settings: Settings) -> None:
    """Fail fast at startup on auth configuration production cannot run without."""
    if settings.environment == "production" and settings.default_tenant_id is None:
        raise AuthConfigError("KERP_DEFAULT_TENANT_ID must be set in production (diff §4.1)")
    if settings.entra_enabled and settings.environment == "production":
        # T-0054: the secret comes from the secret store / environment, never the repo.
        if settings.entra_client_secret is None:
            raise AuthConfigError("Entra ID SSO needs KERP_ENTRA_CLIENT_SECRET in production")
        if settings.entra_redirect_uri is None:
            raise AuthConfigError("Entra ID SSO needs KERP_ENTRA_REDIRECT_URI in production")
    from app.core.auth.entra import startup_warning

    startup_warning(settings)


def revocation_store_from_settings(settings: Settings) -> RevocationStore:
    """A revocation store of its own for a process without an auth runtime (the worker's
    impersonation sweep, T-0277). The caller closes it."""
    return _store_from_settings(settings)


def _store_from_settings(settings: Settings) -> RevocationStore:
    if settings.redis_url:
        return RedisRevocationStore.from_url(settings.redis_url)
    if settings.environment == "production":
        raise AuthConfigError("KERP_REDIS_URL must be set in production (token revocation)")
    _logger.warning("KERP_REDIS_URL not set: token revocation is process-local (in memory)")
    return InMemoryRevocationStore()


def install_auth_runtime(app: FastAPI, runtime: AuthRuntime) -> None:
    setattr(app.state, STATE_ATTR, runtime)


def app_auth_runtime(app: FastAPI) -> AuthRuntime:
    """The app's runtime, built from ``app.state.settings`` on first use."""
    runtime: AuthRuntime | None = getattr(app.state, STATE_ATTR, None)
    if runtime is None:
        with _build_lock:
            runtime = getattr(app.state, STATE_ATTR, None)
            if runtime is None:
                runtime = build_auth_runtime(app.state.settings)
                install_auth_runtime(app, runtime)
    return runtime


def get_auth_runtime(request: Request) -> AuthRuntime:
    """FastAPI dependency: the running app's auth runtime."""
    return app_auth_runtime(request.app)


def close_auth_runtime(app: FastAPI) -> None:
    """Release the store's connections at shutdown (no-op if auth was never used)."""
    runtime: AuthRuntime | None = getattr(app.state, STATE_ATTR, None)
    if runtime is not None:
        runtime.close()
