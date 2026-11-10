"""Helpers for the auth tests: an in-process Redis store, synthetic users, an app factory.

Keys are generated at test time; nothing secret is stored in the repository.
"""

import uuid
from functools import cache
from typing import Any

import fakeredis
from fastapi import FastAPI

from app.core.auth.keys import KeySet, generate_signing_key
from app.core.auth.passwords import hash_password
from app.core.auth.revocation import RedisRevocationStore
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


@cache
def _hashed(password: str) -> str:
    return hash_password(password)


def fake_store() -> RedisRevocationStore:
    return RedisRevocationStore(fakeredis.FakeRedis())


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"environment": "test", "redis_url": None}
    values.update(overrides)
    return Settings(**values)


def auth_app(
    settings: Settings, *, store: Any = None, keys: KeySet | None = None
) -> tuple[FastAPI, AuthRuntime]:
    """``create_app`` with a runtime on a fake Redis (or the given store)."""
    app = create_app(settings)
    runtime = build_auth_runtime(
        settings,
        keys=keys or KeySet(generate_signing_key(settings.jwt_algorithm)),
        store=store if store is not None else fake_store(),
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
