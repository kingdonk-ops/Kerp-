"""Helpers for the auth tests: an in-process Redis store, synthetic users, an app factory.

Keys are generated at test time; nothing secret is stored in the repository.
"""

import uuid
from functools import cache
from typing import Any

import fakeredis
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.field import MagicLink, MagicLinkSender
from app.core.auth.keys import KeySet, generate_signing_key
from app.core.auth.passwords import hash_password
from app.core.auth.revocation import RedisRevocationStore
from app.core.auth.router import REFRESH_COOKIE
from app.core.auth.runtime import AuthRuntime, build_auth_runtime, install_auth_runtime
from app.core.auth.service import create_user
from app.core.config import Settings
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.main import create_app

T1 = uuid.UUID("00000000-0000-4000-8000-0000000000e1")
T2 = uuid.UUID("00000000-0000-4000-8000-0000000000e2")
PASSWORD = "correct horse battery staple"  # synthetic
ALICE = "alice@example.test"
BOB = "bob@example.test"
# TestClient's own origin: the Origin a same-origin browser request carries.
ORIGIN = "http://testserver"


@cache
def _hashed(password: str) -> str:
    return hash_password(password)


def fake_store() -> RedisRevocationStore:
    return RedisRevocationStore(fakeredis.FakeRedis())


def make_settings(**overrides: Any) -> Settings:
    """Test settings; the deployment's hidden tenant is ``T1`` unless overridden."""
    values: dict[str, Any] = {"environment": "test", "redis_url": None, "default_tenant_id": T1}
    values.update(overrides)
    return Settings(**values)


class CapturingSender:
    """Magic-link delivery for tests: keeps every link instead of e-mailing it."""

    def __init__(self) -> None:
        self.sent: list[MagicLink] = []

    def send(self, link: MagicLink) -> None:
        self.sent.append(link)


def auth_app(
    settings: Settings,
    *,
    store: Any = None,
    keys: KeySet | None = None,
    magic_links: MagicLinkSender | None = None,
) -> tuple[FastAPI, AuthRuntime]:
    """``create_app`` with a runtime on a fake Redis (or the given store)."""
    app = create_app(settings)
    runtime = build_auth_runtime(
        settings,
        keys=keys or KeySet(generate_signing_key(settings.jwt_algorithm)),
        store=store if store is not None else fake_store(),
        magic_links=magic_links,
    )
    install_auth_runtime(app, runtime)
    return app, runtime


def seed_user(
    db: Database,
    tenant_id: uuid.UUID,
    email: str,
    password: str | None = PASSWORD,
    **fields: Any,
) -> uuid.UUID:
    with db.unit_of_work(TenancyContext(tenant_id=tenant_id)) as session:
        user = create_user(session, tenant_id=tenant_id, email=email, password=None)
        if password is not None:
            # scrypt is slow on purpose; hash each synthetic password once per run.
            user.password_hash = _hashed(password)
        for name, value in fields.items():
            setattr(user, name, value)
        return user.id


def cookie_headers(refresh_token: str, origin: str | None = ORIGIN) -> dict[str, str]:
    """What the SPA's browser sends to the cookie endpoints: the refresh cookie + Origin.

    Explicit, because the client's cookie jar never sends a ``Secure`` cookie over the
    plain-HTTP test transport.
    """
    headers = {"Cookie": f"{REFRESH_COOKIE}={refresh_token}"}
    if origin is not None:
        headers["Origin"] = origin
    return headers


def token_body(response: httpx.Response) -> dict[str, str]:
    """A token response's body plus its refresh token, taken from the ``Set-Cookie``."""
    assert response.status_code == 200, response.text
    body: dict[str, str] = response.json()
    assert "refresh_token" not in body  # cookie only (diff §4.2)
    return {**body, "refresh_token": response.cookies[REFRESH_COOKIE]}


def refresh(client: TestClient, refresh_token: str, origin: str | None = ORIGIN) -> httpx.Response:
    return client.post("/api/v1/auth/refresh", headers=cookie_headers(refresh_token, origin))
