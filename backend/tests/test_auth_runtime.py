"""Auth runtime assembly from settings and its place in the app (T-0008)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.auth.keys import AuthConfigError, generate_signing_key, private_key_pem
from app.core.auth.revocation import InMemoryRevocationStore, RedisRevocationStore
from app.core.auth.runtime import (
    STATE_ATTR,
    app_auth_runtime,
    build_auth_runtime,
    close_auth_runtime,
)
from app.core.db import Base
from app.main import create_app
from tests.auth_support import auth_app, make_settings


def test_defaults_follow_the_spec() -> None:
    settings = make_settings()
    assert settings.access_token_minutes == 15
    assert settings.jwt_algorithm in {"EdDSA", "RS256"}
    runtime = build_auth_runtime(settings)
    assert runtime.tokens.ttl["access"].total_seconds() == 15 * 60


def test_access_tokens_cannot_be_configured_longer_than_15_minutes() -> None:
    with pytest.raises(ValueError, match="15"):
        make_settings(access_token_minutes=60)


def test_development_without_redis_uses_the_in_memory_store() -> None:
    runtime = build_auth_runtime(make_settings())
    assert isinstance(runtime.store, InMemoryRevocationStore)


def test_redis_url_selects_the_redis_store() -> None:
    runtime = build_auth_runtime(make_settings(redis_url="redis://cache.invalid:6379/0"))
    assert isinstance(runtime.store, RedisRevocationStore)
    runtime.close()


def test_production_requires_redis() -> None:
    key = private_key_pem(generate_signing_key("EdDSA"))
    with pytest.raises(AuthConfigError, match="KERP_REDIS_URL"):
        build_auth_runtime(make_settings(environment="production", jwt_private_key=key))


def test_production_requires_a_signing_key() -> None:
    with pytest.raises(AuthConfigError, match="production"):
        build_auth_runtime(
            make_settings(environment="production", redis_url="redis://cache.invalid:6379/0")
        )


def test_production_with_key_and_redis_builds() -> None:
    key = private_key_pem(generate_signing_key("EdDSA"))
    runtime = build_auth_runtime(
        make_settings(
            environment="production", jwt_private_key=key, redis_url="redis://cache.invalid/0"
        )
    )
    assert isinstance(runtime.store, RedisRevocationStore)
    runtime.close()


def test_runtime_is_built_lazily_once_per_app() -> None:
    app = create_app(make_settings())
    assert getattr(app.state, STATE_ATTR, None) is None  # /health needs no key or Redis
    first = app_auth_runtime(app)
    assert app_auth_runtime(app) is first
    assert create_app(make_settings()) is not app
    other = app_auth_runtime(create_app(make_settings()))
    assert other.keys.signing.kid != first.keys.signing.kid


def test_anonymous_requests_do_not_build_the_runtime() -> None:
    app = create_app(make_settings())
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 401
    assert getattr(app.state, STATE_ATTR, None) is None


def test_jwks_works_with_the_default_runtime() -> None:
    app = create_app(make_settings())
    with TestClient(app) as client:
        keys = client.get("/.well-known/jwks.json").json()["keys"]
    assert [k["kid"] for k in keys] == [app_auth_runtime(app).keys.signing.kid]


def test_shutdown_closes_the_store() -> None:
    class Recording(InMemoryRevocationStore):
        closed = False

        def close(self) -> None:
            Recording.closed = True

    app, _ = auth_app(make_settings(), store=Recording())
    with TestClient(app):
        pass
    assert Recording.closed


def test_close_without_runtime_is_a_no_op() -> None:
    close_auth_runtime(create_app(make_settings()))


def test_auth_user_table_is_on_the_shared_metadata() -> None:
    assert "auth_user" in Base.metadata.tables


def test_private_key_file_setting(tmp_path: Path) -> None:
    key = generate_signing_key("EdDSA")
    path = tmp_path / "k.pem"
    path.write_text(private_key_pem(key), encoding="utf-8")
    runtime = build_auth_runtime(make_settings(jwt_private_key_file=path))
    assert runtime.keys.signing.kid == key.kid
