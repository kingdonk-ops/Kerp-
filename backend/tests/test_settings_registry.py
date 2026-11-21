"""Runtime settings registry (T-0230, diff §4.7): code-declared entries, per-key storage,
bounds and deployment caps (422, never clamped), audit, cache with pub/sub invalidation, the
read-only deployment configuration. Runs on SQLite and, in CI, on PostgreSQL under RLS against
the migrated schema. All data is synthetic.
"""

import json
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fakeredis
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.audit.service import chain_entries
from app.core.auth.access import SYSTEM_ADMIN
from app.core.config import Settings
from app.core.db import GUID, Base, Database, JSONType, contract_violations
from app.core.module_loader.loader import discover_modules, import_setting_defs
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import RLS_EXEMPT, rls_tables, table_policy
from app.modules.admin import service as admin
from app.modules.settings import cache as settings_cache
from app.modules.settings import deployment_config, service
from app.modules.settings.models import TABLE as LEGACY_TABLE
from app.modules.settings.models import VALUE_TABLE, DeploymentSettings, SettingValue
from app.modules.settings.registry import (
    REGISTRY,
    DeploymentCap,
    InvalidSettingError,
    SettingDef,
    SettingRegistrationError,
    SettingsRegistry,
    UnknownSettingError,
)
from app.modules.settings.schemas import DeploymentSettingsUpdate
from app.modules.uploads import service as uploads
from tests.audit_support import ALEMBIC_INI, migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, T1, T2, auth_app, make_settings, seed_user
from tests.db_support import database_url, seed_tenants

URL = "/api/v1/settings/registry"
DEPLOYMENT_URL = "/api/v1/settings/deployment"
CONFIG_URL = "/api/v1/settings/deployment-config"
CTX_T1 = TenancyContext(tenant_id=T1, is_system_admin=True)
CTX_T2 = TenancyContext(tenant_id=T2, is_system_admin=True)
MIB = 1024 * 1024
CONFIG = make_settings()


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def _int(key: str = "sample_limit", **overrides: Any) -> SettingDef:
    fields: dict[str, Any] = {
        "key": key,
        "category": "samples",
        "type": "int",
        "default": 5,
        "minimum": 1,
        "maximum": 10,
    }
    return SettingDef(**{**fields, **overrides})


# --- declaring entries ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("definition", "good", "bad"),
    [
        (_int(), [1, 10], [0, 11, True, 5.0, "5", None, [5]]),
        (
            _int(type="float", default=0.5, minimum=0.0, maximum=1.0),
            [0, 0.25, 1],
            [-0.1, 1.5, True, "0.5", float("nan"), float("inf")],
        ),
        (
            SettingDef(key="sample_flag", category="samples", type="bool", default=False),
            [True, False],
            [0, 1, "true", None],
        ),
        (
            SettingDef(
                key="sample_text", category="samples", type="string", default="a", max_length=3
            ),
            ["", "abc"],
            ["abcd", 1, None],
        ),
        (
            SettingDef(
                key="sample_mode",
                category="samples",
                type="enum",
                default="A",
                choices=("A", "B"),
            ),
            ["A", "B"],
            ["C", "a", 1],
        ),
        (
            _int(type="int_list", default=[1, 2], max_length=3),
            [[], [10, 1, 5]],
            [[0], [11], [1, 2, 3, 4], [True], ["1"], 1, "1"],
        ),
    ],
)
def test_values_are_checked_against_the_type_and_bounds(
    definition: SettingDef, good: list[Any], bad: list[Any]
) -> None:
    SettingsRegistry().register(definition)
    for value in good:
        assert definition.validate(value) == value
    for value in bad:
        with pytest.raises(InvalidSettingError):
            definition.validate(value)


@pytest.mark.parametrize(
    "key",
    [
        "smtp_password",
        "entra_client_secret",
        "jwt_private_key_file",
        "s3_access_key_id",
        "scim_token",
        "signing_key",
        "field_pin",
        "storage_credentials",
        "webhook_signature",
    ],
)
def test_registration_refuses_secret_like_keys(key: str) -> None:
    with pytest.raises(SettingRegistrationError, match="secret"):
        SettingsRegistry().register(_int(key))


@pytest.mark.parametrize("kind", ["secret", "password", "token", "bytes", "json", "url"])
def test_registration_refuses_types_outside_the_six(kind: str) -> None:
    with pytest.raises(SettingRegistrationError, match="type"):
        SettingsRegistry().register(_int(type=kind))


def test_registration_refuses_secret_typed_defaults() -> None:
    for default in (SecretStr("synthetic"), b"synthetic"):
        definition = SettingDef(key="sample_text", category="samples", type="string", default="")
        object.__setattr__(definition, "default", default)
        with pytest.raises(SettingRegistrationError):
            SettingsRegistry().register(definition)


@pytest.mark.parametrize(
    "definition",
    [
        _int("Bad-Key"),
        _int(category="Bad Category"),
        _int(minimum=None),
        _int(maximum=None),
        _int(minimum=10, maximum=1),
        _int(default=11),
        SettingDef(key="sample_mode", category="samples", type="enum", default="A"),
        SettingDef(
            key="sample_mode", category="samples", type="enum", default="C", choices=("A", "B")
        ),
    ],
)
def test_registration_refuses_malformed_entries(definition: SettingDef) -> None:
    with pytest.raises(SettingRegistrationError):
        SettingsRegistry().register(definition)


def test_a_key_is_declared_once() -> None:
    registry = SettingsRegistry()
    definition = _int()
    registry.register(definition)
    registry.register(definition)  # the same declaration again (a re-import) is a no-op
    assert [d.key for d in registry] == ["sample_limit"]
    with pytest.raises(SettingRegistrationError, match="already"):
        registry.register(_int(default=6))
    with pytest.raises(UnknownSettingError):
        registry.get("unknown_key")


def test_the_entries_of_later_tasks_can_be_declared() -> None:
    """Diff §4.7 keys that only *mention* a token, key or PIN are not secrets (T-0162, T-0277,
    T-0289, T-0308 declare them)."""
    registry = SettingsRegistry()
    registry.register(
        _int("cosign_pin_token_ttl_minutes", default=5, minimum=1, maximum=15),
        _int("impersonation_max_minutes", default=30, minimum=5, maximum=60),
        _int("signing_reauth_ttl_minutes", default=5, minimum=1, maximum=15),
        _int("api_key_rate_per_minute", default=600, minimum=60, maximum=6000),
    )
    assert len(list(registry)) == 4


def test_the_deployment_cap_is_enforced_and_never_clamps() -> None:
    cap = DeploymentCap(env="KERP_UPLOAD_URL_SECONDS", limit=lambda s: s.upload_url_seconds // 60)
    definition = _int(maximum=60, cap=cap)
    low = make_settings(upload_url_seconds=180)
    assert definition.cap_value(low) == 3
    assert definition.validate(3, config=low) == 3
    with pytest.raises(InvalidSettingError, match="KERP_UPLOAD_URL_SECONDS"):
        definition.validate(4, config=low)
    assert definition.effective(5, config=low) == 3  # a cap lowered after the value was stored


# --- the declared entries -------------------------------------------------------------------

DECLARED = {
    "offline_window_days": ("offline", "int", 7, 1, 14),
    "offline_grace_hours": ("offline", "int", 24, 1, 336),
    "max_checkout_clock_skew_s": ("offline", "int", 5, 1, 300),
    "default_status": ("project_defaults", "enum", "ACTIVE", None, None),
    "default_site_timezone": ("project_defaults", "string", "Australia/Perth", None, None),
    "default_currency_code": ("project_defaults", "string", "AUD", None, None),
    "default_locale": ("project_defaults", "string", "en-AU", None, None),
    "max_upload_size_mb": ("uploads_media", "int", 100, 1, 5120),
    "presigned_url_ttl_minutes": ("uploads_media", "int", 15, 1, 60),
    "tablet_proxy_max_px": ("uploads_media", "int", 1280, 640, 2560),
    "tablet_proxy_quality": ("uploads_media", "int", 72, 40, 95),
    "report_image_max_px": ("uploads_media", "int", 1024, 480, 2048),
    "report_image_quality": ("uploads_media", "int", 68, 40, 95),
}


def test_the_declared_entries_are_the_spec_values() -> None:
    """Diff §4.4 and §4.7. Entries whose consumer does not exist yet are declared by it."""
    assert import_setting_defs(discover_modules()) == [
        "app.modules.settings.setting_defs",
        "app.modules.uploads.setting_defs",
    ]
    declared = {d.key: (d.category, d.type, d.default, d.minimum, d.maximum) for d in REGISTRY}
    assert declared == DECLARED
    assert REGISTRY.get("default_status").choices == ("ACTIVE", "ON_HOLD", "CLOSED")
    assert {d.key for d in REGISTRY if d.cap} == {"max_upload_size_mb", "presigned_url_ttl_minutes"}
    assert {d.key for d in REGISTRY if d.client_visible} == {
        "offline_window_days",
        "offline_grace_hours",
        "max_checkout_clock_skew_s",
        "max_upload_size_mb",
    }
    assert "settings" in discover_modules().get("uploads").depends_on


def test_the_value_table_meets_the_record_contract_and_is_under_the_standard_policy() -> None:
    assert [p for p in contract_violations(Base.metadata) if p.startswith(VALUE_TABLE)] == []
    assert VALUE_TABLE in rls_tables(Base.metadata)
    assert VALUE_TABLE not in RLS_EXEMPT
    assert table_policy(Base.metadata.tables[VALUE_TABLE]) is None


# --- service --------------------------------------------------------------------------------


def _rows(db: Database, ctx: TenancyContext = CTX_T1) -> dict[str, Any]:
    with db.unit_of_work(ctx) as s:
        return {row.key: row.value for row in s.scalars(sa.select(SettingValue))}


def test_no_row_means_the_default(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        values = service.get_values(s, config=CONFIG)
        assert values == {d.key: d.default for d in REGISTRY}
        assert service.get_value(s, "tablet_proxy_max_px") == 1280
        assert service.client_values(s, config=CONFIG) == {
            "offline_window_days": 7,
            "offline_grace_hours": 24,
            "max_checkout_clock_skew_s": 5,
            "max_upload_size_mb": 100,
        }
        with pytest.raises(UnknownSettingError):
            service.get_value(s, "unknown_key")
    assert _rows(db) == {}


def test_a_change_stores_one_live_row_per_key_and_is_audited_with_the_actor(db: Database) -> None:
    actor = seed_user(db, T1, ALICE)
    with db.unit_of_work(CTX_T1) as s:
        changes = service.set_values(
            s, {"tablet_proxy_quality": 80, "default_currency_code": "NZD"}, actor_id=actor
        )
        assert changes == {
            "default_currency_code": ["AUD", "NZD"],
            "tablet_proxy_quality": [72, 80],
        }
        assert service.get_value(s, "tablet_proxy_quality") == 80  # read your own write
    with db.unit_of_work(CTX_T1) as s:
        service.set_values(s, {"tablet_proxy_quality": 90}, actor_id=actor)
        service.set_values(s, {"tablet_proxy_quality": 90}, actor_id=actor)  # nothing changes
    assert _rows(db) == {"tablet_proxy_quality": 90, "default_currency_code": "NZD"}
    with db.unit_of_work(CTX_T1) as s:
        entries = chain_entries(s, f"{service.CHAIN_PREFIX}{T1}")
        assert [e.payload for e in entries] == [
            {
                "action": "update",
                "changes": {
                    "default_currency_code": ["AUD", "NZD"],
                    "tablet_proxy_quality": [72, 80],
                },
            },
            {"action": "update", "changes": {"tablet_proxy_quality": [80, 90]}},
        ]
        assert all(e.created_by == actor for e in entries)
        row = s.scalars(sa.select(SettingValue).where(SettingValue.key == "tablet_proxy_quality"))
        assert [(r.tenant_id, r.project_id, r.updated_by) for r in row] == [(T1, None, actor)]
    assert _rows(db, CTX_T2) == {}
    with db.unit_of_work(CTX_T2) as s:
        assert service.get_value(s, "tablet_proxy_quality") == 72


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("offline_window_days", 15),
        ("offline_window_days", 0),
        ("tablet_proxy_max_px", 639),
        ("report_image_quality", 96),
        ("default_status", "ARCHIVED"),
        ("default_site_timezone", "Mars/Olympus_Mons"),
        ("default_currency_code", "aud"),
        ("default_locale", "en_AU"),
        ("offline_grace_hours", 24 * 7 + 1),  # above 24 x the stored window (7)
        ("max_upload_size_mb", 101),  # above KERP_UPLOAD_MAX_BYTES (100 MiB)
        ("presigned_url_ttl_minutes", 16),  # above KERP_UPLOAD_URL_SECONDS (900 s)
    ],
)
def test_out_of_bounds_values_are_refused_and_nothing_is_stored(
    db: Database, key: str, value: Any
) -> None:
    with db.unit_of_work(CTX_T1) as s, pytest.raises(InvalidSettingError):
        service.set_values(s, {key: value}, actor_id=None, config=CONFIG)
    assert _rows(db) == {}


def test_a_stored_value_the_entry_no_longer_accepts_falls_back_to_the_default(
    db: Database,
) -> None:
    """Bounds tightened in code after a value was stored: the default applies, nothing fails."""
    with db.unit_of_work(CTX_T1) as s:
        s.add(SettingValue(tenant_id=T1, key="tablet_proxy_quality", value=5))
        s.add(SettingValue(tenant_id=T1, key="retired_entry", value="x"))  # no longer declared
    with db.unit_of_work(CTX_T1) as s:
        assert service.get_int(s, "tablet_proxy_quality") == 72
        assert "retired_entry" not in service.get_values(s)
        with pytest.raises(TypeError, match="default_status"):
            service.get_int(s, "default_status")


def test_only_a_number_can_have_a_deployment_cap() -> None:
    cap = DeploymentCap(env="KERP_UPLOAD_URL_SECONDS", limit=lambda s: s.upload_url_seconds)
    flag = SettingDef(key="sample_flag", category="samples", type="bool", default=False, cap=cap)
    with pytest.raises(SettingRegistrationError, match="cap"):
        SettingsRegistry().register(flag)


def test_a_rule_across_keys_is_checked_on_the_result(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        service.set_values(s, {"offline_grace_hours": 48}, actor_id=None)
    with db.unit_of_work(CTX_T1) as s, pytest.raises(InvalidSettingError, match="offline_grace"):
        service.set_values(s, {"offline_window_days": 1}, actor_id=None)
    with db.unit_of_work(CTX_T1) as s:
        service.set_values(s, {"offline_window_days": 1, "offline_grace_hours": 24}, actor_id=None)
        assert service.get_value(s, "offline_window_days") == 1


def test_a_raised_deployment_cap_lets_the_entry_go_higher(db: Database) -> None:
    roomy = make_settings(upload_max_bytes=512 * MIB, upload_url_seconds=3600)
    with db.unit_of_work(CTX_T1) as s:
        service.set_values(
            s,
            {"max_upload_size_mb": 512, "presigned_url_ttl_minutes": 60},
            actor_id=None,
            config=roomy,
        )
        assert uploads.upload_limits(s, roomy) == (512 * MIB, 3600)
        # The deployment lowered its caps afterwards: the entry never exceeds them.
        assert uploads.upload_limits(s, CONFIG) == (100 * MIB, 900)
        assert service.get_value(s, "max_upload_size_mb", config=CONFIG) == 100


def test_uploads_read_their_limits_from_the_registry(db: Database) -> None:
    tiny = make_settings(upload_max_bytes=4096)  # below 1 MiB: the byte cap still holds
    with db.unit_of_work(CTX_T1) as s:
        assert uploads.upload_limits(s, CONFIG) == (100 * MIB, 900)
        assert uploads.upload_limits(s, tiny) == (4096, 900)
        service.set_values(
            s, {"max_upload_size_mb": 10, "presigned_url_ttl_minutes": 5}, actor_id=None
        )
        assert uploads.upload_limits(s, CONFIG) == (10 * MIB, 300)


def test_the_legacy_table_is_no_longer_written(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        service.ensure_record(s, actor_id=None)
        update = DeploymentSettingsUpdate(offline_window_days=3, offline_grace_hours=12)
        service.update_settings(s, update, actor_id=None)
        assert list(s.scalars(sa.select(DeploymentSettings))) == []
        assert service.get_settings(s).offline_window_days == 3
    assert _rows(db)["offline_window_days"] == 3
    assert len(_rows(db)) == 7


# --- migration 0037 -------------------------------------------------------------------------

_LEGACY = sa.Table(
    LEGACY_TABLE,
    sa.MetaData(),
    sa.Column("id", GUID()),
    sa.Column("tenant_id", GUID()),
    sa.Column("offline_window_days", sa.Integer()),
    sa.Column("offline_grace_hours", sa.Integer()),
    sa.Column("max_checkout_clock_skew_s", sa.Integer()),
    sa.Column("default_status", sa.String(32)),
    sa.Column("default_site_timezone", sa.String(64)),
    sa.Column("default_currency_code", sa.String(3)),
    sa.Column("default_locale", sa.String(35)),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
    sa.Column("custom_fields", JSONType()),
    sa.Column("updated_by", GUID()),
    sa.Column("source", sa.String(16)),
    sa.Column("revision", sa.Integer()),
)
STORED = {
    "offline_window_days": 10,
    "offline_grace_hours": 36,
    "max_checkout_clock_skew_s": 30,
    "default_status": "ON_HOLD",
    "default_site_timezone": "Pacific/Auckland",
    "default_currency_code": "NZD",
    "default_locale": "en-NZ",
}


def test_the_migration_copies_the_stored_deployment_settings(tmp_path: Path) -> None:
    url = database_url(tmp_path)
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=url, configure_logger=False)
    command.upgrade(cfg, "0036")
    db = Database(url)
    try:
        seed_tenants(db, T1, T2)
        actor = seed_user(db, T1, ALICE)
        now = datetime.now(UTC)
        with db.unit_of_work(CTX_T1) as s:
            s.execute(
                sa.insert(_LEGACY).values(
                    id=uuid.uuid4(),
                    tenant_id=T1,
                    created_at=now,
                    updated_at=now,
                    custom_fields={},
                    updated_by=actor,
                    source="api",
                    revision=1,
                    **STORED,
                )
            )
        command.upgrade(cfg, "head")
        assert _rows(db) == STORED
        assert _rows(db, CTX_T2) == {}
        with db.unit_of_work(CTX_T1) as s:
            assert service.get_settings(s).as_dict() == STORED
            record = service.stored_record(s)
            assert record is not None
            assert (record.tenant_id, record.updated_by) == (T1, actor)
            assert len({row.id for row in s.scalars(sa.select(SettingValue))}) == 7
            legacy = s.execute(sa.select(_LEGACY.c.offline_window_days)).scalars().all()
            assert legacy == [10]  # kept, read-only
        with db.unit_of_work(CTX_T2) as s:
            assert service.get_settings(s).offline_window_days == 7
    finally:
        db.dispose()
        command.downgrade(cfg, "base")


# --- cache ----------------------------------------------------------------------------------


class Loader:
    """Stands in for the database read behind the cache."""

    def __init__(self, **values: Any) -> None:
        self.values, self.calls = dict(values), 0

    def __call__(self) -> dict[str, Any]:
        self.calls += 1
        return dict(self.values)


def _redis_cache(server: fakeredis.FakeServer, **kwargs: Any) -> settings_cache.RedisSettingsCache:
    cache = settings_cache.RedisSettingsCache(fakeredis.FakeRedis(server=server), **kwargs)
    cache.subscribe()
    return cache


def test_values_are_cached_in_redis_and_shared_between_processes() -> None:
    server = fakeredis.FakeServer()
    first, second = _redis_cache(server), _redis_cache(server)
    loader = Loader(offline_window_days=3)
    assert first.load(T1, loader) == {"offline_window_days": 3}
    assert first.load(T1, loader) == {"offline_window_days": 3}  # the process's own copy
    assert second.load(T1, loader) == {"offline_window_days": 3}  # from Redis, no database read
    assert loader.calls == 1
    assert second.load(T2, loader) == {"offline_window_days": 3}  # another tenant: its own entry
    assert loader.calls == 2
    keys = [key.decode() for key in fakeredis.FakeRedis(server=server).keys("*")]
    assert all(key.startswith(settings_cache.KEY_PREFIX) for key in keys)


def test_a_change_makes_every_subscriber_drop_its_cache_at_once() -> None:
    server = fakeredis.FakeServer()
    api, worker, writer = _redis_cache(server), _redis_cache(server), _redis_cache(server)
    loader = Loader(offline_window_days=3)
    for cache in (api, worker, writer):
        cache.load(T1, loader)
        cache.load(T2, loader)
    loader.values["offline_window_days"] = 9
    assert api.load(T1, loader) == worker.load(T1, loader) == {"offline_window_days": 3}

    writer.invalidate(T1)
    for cache in (api, worker):
        assert cache.pump() == 1
        assert cache.cached(T1) is None
        assert cache.cached(T2) == {"offline_window_days": 3}  # only the changed tenant
        assert cache.load(T1, loader) == {"offline_window_days": 9}
    assert writer.load(T1, loader) == {"offline_window_days": 9}


def test_the_listener_thread_drops_the_cache_without_being_asked() -> None:
    server = fakeredis.FakeServer()
    caches = [settings_cache.RedisSettingsCache(fakeredis.FakeRedis(server=server)) for _ in "ab"]
    writer = _redis_cache(server)
    try:
        for cache in caches:
            cache.start()
            assert cache.wait_subscribed(5.0)
            cache.load(T1, Loader(offline_window_days=3))
        writer.invalidate(T1)
        deadline = time.monotonic() + 5.0
        while any(c.cached(T1) is not None for c in caches) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert [c.cached(T1) for c in caches] == [None, None]
    finally:
        for cache in caches:
            cache.close()


def test_the_local_copy_expires_and_a_stale_reader_cannot_overwrite_a_change() -> None:
    server = fakeredis.FakeServer()
    now = [100.0]
    cache = _redis_cache(server, local_ttl=30.0, clock=lambda: now[0])
    loader = Loader(offline_window_days=3)
    cache.load(T1, loader)
    now[0] += 31.0
    assert cache.cached(T1) is None  # safety net for a lost pub/sub message

    # A reader that read the database before the change stores its copy under the old
    # generation: nobody reads it after the invalidation, and it does not keep it either.
    other = _redis_cache(server)

    def stale() -> dict[str, Any]:
        cache.invalidate(T2)  # the change commits while this read is under way
        other.pump()
        return {"offline_window_days": 3}

    assert other.load(T2, stale) == {"offline_window_days": 3}
    assert other.cached(T2) is None
    fresh = Loader(offline_window_days=9)
    assert cache.load(T2, fresh) == other.load(T2, fresh) == {"offline_window_days": 9}
    assert fresh.calls == 1


class BrokenRedis:
    """Every call fails, as when Redis is unreachable."""

    def __getattr__(self, name: str) -> Any:
        import redis

        def fail(*_args: Any, **_kwargs: Any) -> Any:
            raise redis.ConnectionError("synthetic outage")

        return fail


def test_without_redis_the_database_is_read_and_nothing_fails() -> None:
    cache = settings_cache.RedisSettingsCache(BrokenRedis())  # type: ignore[arg-type]
    loader = Loader(offline_window_days=3)
    assert cache.load(T1, loader) == cache.load(T1, loader) == {"offline_window_days": 3}
    assert loader.calls == 2  # never cached when the generation cannot be read
    cache.invalidate(T1)
    cache.start()  # the listener keeps retrying and never raises
    assert cache.wait_subscribed(0.05) is False
    cache.close()
    assert settings_cache.NullCache().load(T1, loader) == {"offline_window_days": 3}
    assert isinstance(settings_cache.open_cache(None), settings_cache.NullCache)


@pytest.fixture
def cached() -> Iterator[tuple[settings_cache.RedisSettingsCache, fakeredis.FakeRedis]]:
    client = fakeredis.FakeRedis()
    cache = settings_cache.RedisSettingsCache(client)
    cache.subscribe()
    settings_cache.install(cache)
    try:
        yield cache, client
    finally:
        settings_cache.install(None)
        cache.close()


def test_the_service_reads_through_the_cache_and_invalidates_after_the_commit(
    db: Database, cached: tuple[settings_cache.RedisSettingsCache, fakeredis.FakeRedis]
) -> None:
    cache, client = cached
    listener = client.pubsub(ignore_subscribe_messages=True)
    listener.subscribe(settings_cache.CHANNEL)
    listener.get_message(timeout=0.1)
    with db.unit_of_work(CTX_T1) as s:
        assert service.get_value(s, "offline_window_days") == 7
    assert cache.cached(T1) == {}
    with db.unit_of_work(CTX_T1) as s:
        service.set_values(s, {"offline_window_days": 3}, actor_id=None)
        assert service.get_value(s, "offline_window_days") == 3  # bypasses the cache
        assert cache.cached(T1) == {}  # not dropped before the commit
        assert listener.get_message(timeout=0.01) is None
    message = listener.get_message(timeout=1.0)
    assert message is not None
    assert message["data"].decode() == str(T1)
    assert cache.cached(T1) is None
    with db.unit_of_work(CTX_T1) as s:
        assert service.get_settings(s).offline_window_days == 3
    assert cache.cached(T1) == {"offline_window_days": 3}
    assert json.loads(json.dumps(cache.cached(T1))) == {"offline_window_days": 3}


def test_a_rolled_back_change_publishes_nothing(
    db: Database, cached: tuple[settings_cache.RedisSettingsCache, fakeredis.FakeRedis]
) -> None:
    cache, client = cached
    listener = client.pubsub(ignore_subscribe_messages=True)
    listener.subscribe(settings_cache.CHANNEL)
    listener.get_message(timeout=0.1)

    def change_then_fail() -> None:
        with db.unit_of_work(CTX_T1) as s:
            service.set_values(s, {"offline_window_days": 3}, actor_id=None)
            raise RuntimeError("synthetic failure")

    with pytest.raises(RuntimeError, match="synthetic"):
        change_then_fail()
    assert listener.get_message(timeout=0.05) is None
    with db.unit_of_work(CTX_T1) as s:
        assert service.get_value(s, "offline_window_days") == 7
    assert cache.cached(T1) == {}


# --- HTTP (real tokens) ---------------------------------------------------------------------


class Api:
    def __init__(self, client: TestClient, db: Database) -> None:
        self.client, self.db = client, db
        self.users = {email: seed_user(db, T1, email) for email in (ALICE, BOB)}
        with db.unit_of_work(CTX_T1) as s:  # ALICE runs the deployment
            admin.grant(s, self.users[ALICE], SYSTEM_ADMIN, actor_id=None)

    def headers(self, email: str) -> dict[str, str]:
        body = {"email": email, "password": PASSWORD}
        response = self.client.post("/api/v1/auth/login", json=body)
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def entries(self) -> dict[str, dict[str, Any]]:
        response = self.client.get(URL, headers=self.headers(ALICE))
        assert response.status_code == 200, response.text
        return {entry["key"]: entry for entry in response.json()}


# Synthetic stand-ins for every secret field of ``Settings``: none may ever be returned.
SENTINEL = "synthetic-sentinel-7f3a"
SECRET_CONFIG = {
    "s3_access_key_id": SENTINEL,
    "s3_secret_access_key": SENTINEL,
    "jwt_private_key": SENTINEL,
    "entra_client_secret": SENTINEL,
    "clamd_address": "tcp://synthetic-sentinel-7f3a:3310",
    "migration_database_url": "sqlite:///synthetic-sentinel-7f3a.db",
}


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    settings = make_settings(
        database_url=database_url(tmp_path),
        mail_from_address="no-reply@example.test",
        upload_scanner_allow_stub=True,
        **SECRET_CONFIG,
    )
    app, _runtime = auth_app(settings)
    with TestClient(app) as client:
        yield Api(client, db)


def test_the_registry_api_is_for_system_admins(api: Api) -> None:
    patch = {"value": 3}
    for url in (URL, CONFIG_URL):
        assert api.client.get(url).status_code == 401
        assert api.client.get(url, headers=api.headers(BOB)).status_code == 403
    one = f"{URL}/offline_window_days"
    assert api.client.patch(one, json=patch).status_code == 401
    assert api.client.patch(one, json=patch, headers=api.headers(BOB)).status_code == 403
    assert api.entries()["offline_window_days"]["value"] == 7


def test_the_registry_lists_every_entry_with_value_default_bounds_and_category(api: Api) -> None:
    entries = api.entries()
    assert set(entries) == set(DECLARED)
    assert [e["key"] for e in entries.values()] == sorted(
        entries, key=lambda key: (entries[key]["category"], key)
    )
    assert entries["offline_window_days"] == {
        "key": "offline_window_days",
        "category": "offline",
        "type": "int",
        "description": entries["offline_window_days"]["description"],
        "value": 7,
        "default": 7,
        "is_default": True,
        "minimum": 1,
        "maximum": 14,
        "choices": [],
        "max_length": None,
        "client_visible": True,
        "cap": None,
        "cap_source": None,
    }
    assert all(entry["description"] for entry in entries.values())
    assert entries["default_status"]["choices"] == ["ACTIVE", "ON_HOLD", "CLOSED"]
    size = entries["max_upload_size_mb"]
    assert (size["maximum"], size["cap"], size["cap_source"]) == (
        5120,
        100,
        "KERP_UPLOAD_MAX_BYTES",
    )
    ttl = entries["presigned_url_ttl_minutes"]
    assert (ttl["maximum"], ttl["cap"], ttl["cap_source"]) == (60, 15, "KERP_UPLOAD_URL_SECONDS")


def test_patch_changes_one_key_and_audits_it(api: Api) -> None:
    headers = api.headers(ALICE)
    response = api.client.patch(f"{URL}/offline_window_days", json={"value": 14}, headers=headers)
    assert response.status_code == 200, response.text
    assert (response.json()["value"], response.json()["is_default"]) == (14, False)
    assert api.entries()["offline_window_days"]["value"] == 14
    # The T-0044 endpoint reads and writes the same values.
    assert api.client.get(DEPLOYMENT_URL, headers=headers).json()["offline_window_days"] == 14
    legacy = api.client.patch(DEPLOYMENT_URL, json={"default_locale": "en-NZ"}, headers=headers)
    assert legacy.status_code == 200, legacy.text
    assert api.entries()["default_locale"]["value"] == "en-NZ"
    with api.db.unit_of_work(CTX_T1) as s:
        entries = chain_entries(s, f"{service.CHAIN_PREFIX}{T1}")
        assert [e.payload["changes"] for e in entries] == [
            {"offline_window_days": [7, 14]},
            {"default_locale": ["en-AU", "en-NZ"]},
        ]
        assert all(e.created_by == api.users[ALICE] for e in entries)


@pytest.mark.parametrize(
    ("key", "body"),
    [
        ("offline_window_days", {"value": 15}),  # never clamped to 14
        ("offline_window_days", {"value": 0}),
        ("offline_window_days", {"value": "7"}),
        ("offline_window_days", {"value": True}),
        ("offline_window_days", {"value": 7.5}),
        ("offline_window_days", {"value": None}),
        ("offline_window_days", {}),
        ("offline_window_days", {"value": 7, "tenant_id": str(T2)}),
        ("offline_grace_hours", {"value": 24 * 7 + 1}),
        ("default_status", {"value": "ARCHIVED"}),
        ("max_upload_size_mb", {"value": 101}),  # over the deployment cap
        ("presigned_url_ttl_minutes", {"value": 16}),
        ("tablet_proxy_max_px", {"value": 2561}),
    ],
)
def test_invalid_patches_are_422_and_change_nothing(
    api: Api, key: str, body: dict[str, Any]
) -> None:
    response = api.client.patch(f"{URL}/{key}", json=body, headers=api.headers(ALICE))
    assert response.status_code == 422, response.text
    entry = api.entries()[key]
    assert (entry["value"], entry["is_default"]) == (entry["default"], True)


def test_an_unknown_key_is_404(api: Api) -> None:
    response = api.client.patch(
        f"{URL}/jwt_private_key", json={"value": "x"}, headers=api.headers(ALICE)
    )
    assert response.status_code == 404


def test_the_deployment_configuration_is_read_only_and_has_no_secret(api: Api) -> None:
    headers = api.headers(ALICE)
    response = api.client.get(CONFIG_URL, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "refresh_token_hours": 12,
        "pin_idle_minutes": 30,
        "pin_lockout": {
            "free_attempts": 3,
            "base_delay_seconds": 30,
            "max_delay_seconds": 900,
            "max_attempts": 10,
        },
        "region": "ap-southeast-2",
        "storage_region": "ap-southeast-2",
        "mail_transport": "log",
        "mail_from_address": "no-reply@example.test",
        "upload_scanner": "stub",
        "upload_max_bytes": 100 * MIB,
        "upload_url_seconds": 900,
        "ai_provider": "none",
    }
    assert SENTINEL not in response.text
    for method in ("patch", "put", "post", "delete"):
        assert getattr(api.client, method)(CONFIG_URL, headers=headers).status_code == 405


def test_no_secret_field_of_settings_can_appear_in_the_deployment_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = deployment_config.secret_fields()
    # Every SecretStr and every field hidden from repr() is a secret, whatever its name.
    typed = {name for name, f in Settings.model_fields.items() if "SecretStr" in str(f.annotation)}
    hidden = {name for name, f in Settings.model_fields.items() if f.repr is False}
    assert typed
    assert hidden
    assert (typed | hidden) <= secrets
    assert SECRET_CONFIG.keys() <= secrets
    assert {"database_url", "redis_url", "jwt_private_key_file"} <= secrets
    assert not secrets & set(deployment_config.FIELDS)
    assert set(deployment_config.FIELDS) <= set(Settings.model_fields)
    shown = json.dumps(deployment_config.read(make_settings(**SECRET_CONFIG)).model_dump())
    assert SENTINEL not in shown
    for name in sorted(secrets):  # showing one is refused at run time, not only by this test
        monkeypatch.setattr(deployment_config, "FIELDS", (*deployment_config.FIELDS, name))
        with pytest.raises(RuntimeError, match="secret"):
            deployment_config.read(CONFIG)
