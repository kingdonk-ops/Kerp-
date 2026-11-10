"""The per-app auth runtime: key set, token service and revocation store (T-0008).

Built lazily from the app's settings on first use and cached on ``app.state.auth``, so
an app that never authenticates (``/health``) needs no key or Redis. Tests install their
own runtime with ``install_auth_runtime`` (e.g. one backed by ``fakeredis``).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from fastapi import FastAPI, Request

from app.core.auth.keys import AuthConfigError, KeySet, keyset_from_settings
from app.core.auth.revocation import (
    InMemoryRevocationStore,
    RedisRevocationStore,
    RevocationStore,
)
from app.core.auth.tokens import TokenService
from app.core.config import Settings

STATE_ATTR: Final = "auth"
_logger = logging.getLogger("kerp.auth")
_build_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class AuthRuntime:
    settings: Settings
    keys: KeySet
    tokens: TokenService
    store: RevocationStore

    def close(self) -> None:
        self.store.close()


def build_auth_runtime(
    settings: Settings,
    *,
    keys: KeySet | None = None,
    store: RevocationStore | None = None,
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
        refresh_ttl=timedelta(days=settings.refresh_token_days),
    )
    return AuthRuntime(settings=settings, keys=keys, tokens=tokens, store=store)


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
