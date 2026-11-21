"""Helpers for the auth tests: an in-process Redis store, synthetic users, an app factory.

Keys are generated at test time; nothing secret is stored in the repository.
"""

import uuid
from datetime import UTC, datetime
from functools import cache
from typing import Any

import fakeredis
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from saq.queue.redis import RedisQueue

from app.core.auth.field import MagicLink, MagicLinkSender
from app.core.auth.keys import KeySet, generate_signing_key, private_key_pem
from app.core.auth.models import ProjectMembership
from app.core.auth.passwords import hash_password
from app.core.auth.revocation import RedisRevocationStore
from app.core.auth.router import REFRESH_COOKIE
from app.core.auth.runtime import AuthRuntime, build_auth_runtime, install_auth_runtime
from app.core.auth.service import create_user
from app.core.config import Settings
from app.core.db import Database
from app.core.module_loader.loader import DEFAULT_PACKAGE
from app.core.tenancy.context import TenancyContext
from app.main import create_app
from app.modules.jobs.broker import QUEUE_NAMES, SaqBroker
from app.modules.settings.cache import RedisSettingsCache

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


# What production needs for outbound e-mail to pass the startup check (T-0058); synthetic.
PRODUCTION_MAIL: dict[str, Any] = {
    "mail_transport": "ses",
    "mail_from_address": "no-reply@example.test",
    "mail_sns_topic_arn": "arn:aws:sns:ap-southeast-2:000000000000:kerp-mail-test",
    "web_origin": "https://kerp.example.test",
}
# What production needs to pass the runtime startup checks (T-0342): a Redis URL and a
# signing key generated at import time. Synthetic; nothing secret is stored. Nothing listens
# at the URL: a test that starts a production app runs its broker and settings cache on
# fakeredis (``fake_production_redis``). No database either (also not CI's unmigrated
# PostgreSQL source database, which the role checks would refuse): those tests need none.
PRODUCTION_RUNTIME: dict[str, Any] = {
    "database_url": None,
    "redis_url": "redis://127.0.0.1:1/0",
    "jwt_private_key": private_key_pem(generate_signing_key("EdDSA")),
}


class FakeRedisBroker(SaqBroker):
    """``SaqBroker`` on an in-process fakeredis instead of the URL's server."""

    def __init__(self, _redis_url: str, *, prefix: str = "kerp") -> None:
        self.prefix = prefix
        server = fakeredis.FakeServer()
        self.queues = {
            name: RedisQueue(fakeredis.aioredis.FakeRedis(server=server), name=f"{prefix}:{name}")
            for name in QUEUE_NAMES
        }


def fake_production_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start production apps without a Redis server (the SQLite job has none): the API's job
    broker and settings cache run on fakeredis; the revocation store connects lazily."""
    monkeypatch.setattr("app.main.SaqBroker", FakeRedisBroker)
    monkeypatch.setattr(
        RedisSettingsCache,
        "from_url",
        classmethod(lambda cls, _url, **_kwargs: cls(fakeredis.FakeRedis())),
    )


def make_settings(**overrides: Any) -> Settings:
    """Test settings; the deployment's hidden tenant is ``T1`` unless overridden."""
    values: dict[str, Any] = {"environment": "test", "redis_url": None, "default_tenant_id": T1}
    values.update(overrides)
    return Settings(**values)


class CapturingSender:
    """Magic-link delivery for tests: keeps every link instead of e-mailing it."""

    def __init__(self) -> None:
        self.sent: list[MagicLink] = []

    def send(self, _session: Any, link: MagicLink) -> None:
        self.sent.append(link)


def auth_app(
    settings: Settings,
    *,
    store: Any = None,
    keys: KeySet | None = None,
    magic_links: MagicLinkSender | None = None,
    entra_transport: Any = None,
    modules_package: str = DEFAULT_PACKAGE,
) -> tuple[FastAPI, AuthRuntime]:
    """``create_app`` with a runtime on a fake Redis (or the given store); ``modules_package``
    swaps the domain modules (``tests.module_support``)."""
    app = create_app(settings, modules_package=modules_package)
    runtime = build_auth_runtime(
        settings,
        keys=keys or KeySet(generate_signing_key(settings.jwt_algorithm)),
        store=store if store is not None else fake_store(),
        magic_links=magic_links,
        entra_transport=entra_transport,
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


# The synthetic project a Field Worker grant names when a test needs one but no project row.
FIELD_PROJECT = uuid.UUID("00000000-0000-4000-8000-0000000000f1")


def grant_persona(
    db: Database,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    persona: str = "PER-14",
    project_id: uuid.UUID = FIELD_PROJECT,
) -> None:
    """Give ``user_id`` a direct ``persona`` membership (default PER-14 Field Worker, the one
    persona that may sign in by magic link, T-0057)."""
    with db.unit_of_work(TenancyContext(tenant_id=tenant_id, is_system_admin=True)) as session:
        session.add(
            ProjectMembership(
                tenant_id=tenant_id,
                user_id=user_id,
                member_project_id=project_id,
                persona=persona,
            )
        )


def seed_field_worker(db: Database, tenant_id: uuid.UUID, email: str, **fields: Any) -> uuid.UUID:
    """``seed_user`` plus a PER-14 grant: an account that may sign in by magic link."""
    user_id = seed_user(db, tenant_id, email, **fields)
    grant_persona(db, tenant_id, user_id)
    return user_id


def revoke_project_viewers(db: Database, tenant_id: uuid.UUID = T1) -> None:
    """Soft-delete the tenant's PER-16 memberships: the ``0012`` downgrade refuses while any
    is active, so tests that grant PER-16 call this before migrating down to base."""
    # A System admin session reads every membership (membership-based policy, T-0014).
    with db.unit_of_work(TenancyContext(tenant_id=tenant_id, is_system_admin=True)) as session:
        for row in session.query(ProjectMembership).filter_by(persona="PER-16"):
            row.deleted_at = datetime.now(UTC)


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
