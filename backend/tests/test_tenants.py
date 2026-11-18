"""The tenants directory table and the ``tenant_id`` foreign keys (T-0079, ADR 0011).

Runs on SQLite and, in CI, on PostgreSQL 16 as the ``NOSUPERUSER NOBYPASSRLS`` app role,
against the real migrations (``0026`` + ``0027``). Synthetic tenant ids only.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import GUID, TENANTS_TABLE, Database
from app.core.module_loader.models import ProjectModuleSetting
from app.core.tenancy import provision
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.models import Tenant, tenant_fk_problems
from app.core.tenancy.provision import ensure_default_tenant, ensure_tenant
from app.main import create_app
from tests.audit_support import ALEMBIC_INI, migrated_database
from tests.db_support import ON_POSTGRES, database_url

T1 = uuid.UUID("00000000-0000-4000-8000-0000000079a1")
T2 = uuid.UUID("00000000-0000-4000-8000-0000000079a2")
UNKNOWN = uuid.UUID("00000000-0000-4000-8000-0000000079ff")
PROJECT = uuid.UUID("00000000-0000-4000-8000-0000000079b1")
ON_PG = pytest.mark.skipif(
    not ON_POSTGRES,
    reason="needs PostgreSQL RLS and enforced FKs; runs in CI's PostgreSQL step (TD-052)",
)


def _config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=url, configure_logger=False)
    return cfg


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@contextmanager
def _session(db: Database, ctx: TenancyContext | None) -> Iterator[Session]:
    """A unit of work that may also be unbound (``ctx=None``: RLS shows it nothing)."""
    yield from db.session(ctx)


def _tenant_ids(db: Database, ctx: TenancyContext | None) -> list[uuid.UUID]:
    with _session(db, ctx) as s:
        return list(s.scalars(select(Tenant.id).execution_options(include_deleted=True)))


# --- schema contract ---------------------------------------------------------------------


def test_every_tenant_id_references_the_directory(db: Database) -> None:
    with db.engine.connect() as conn:
        assert tenant_fk_problems(conn) == []
        tables = inspect(conn).get_table_names()
    assert TENANTS_TABLE in tables
    assert len(tables) > 10  # the check really looked at the schema


def test_a_table_without_the_tenant_fk_is_caught(db: Database) -> None:
    md = MetaData()
    rogue = Table(
        "test_rogue_tenant_ref",
        md,
        Column("id", GUID(), primary_key=True),
        Column("tenant_id", GUID(), nullable=False),
        Column("name", String(20)),
    )
    with db.engine.begin() as conn:
        md.create_all(conn)
        try:
            assert tenant_fk_problems(conn) == [
                "test_rogue_tenant_ref: tenant_id has no foreign key to tenants.id"
            ]
        finally:
            rogue.drop(conn)


def test_downgrade_drops_the_fks_and_the_table(tmp_path: Path) -> None:
    url = database_url(tmp_path)
    cfg = _config(url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    try:
        command.downgrade(cfg, "0025")
        with engine.connect() as conn:
            insp = inspect(conn)
            assert TENANTS_TABLE not in insp.get_table_names()
            assert not any(
                fk["referred_table"] == TENANTS_TABLE
                for table in insp.get_table_names()
                for fk in insp.get_foreign_keys(table)
            )
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert tenant_fk_problems(conn) == []
    finally:
        engine.dispose()
        command.downgrade(cfg, "base")


def test_upgrade_backfills_tenants_in_use_and_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = database_url(tmp_path)
    cfg = _config(url)
    command.upgrade(cfg, "0025")
    db = Database(url)
    try:
        for tenant in (T1, T1, T2):
            with db.unit_of_work(TenancyContext(tenant_id=tenant, project_id=PROJECT)) as s:
                s.add(
                    ProjectModuleSetting(
                        tenant_id=tenant,
                        project_id=PROJECT,
                        module_name=f"m{uuid.uuid4().hex[:8]}",
                        enabled=True,
                    )
                )
        default = uuid.UUID("00000000-0000-4000-8000-0000000079d1")
        monkeypatch.setenv("KERP_DEFAULT_TENANT_ID", str(default))
        get_settings.cache_clear()
        command.upgrade(cfg, "head")
        for tenant in (T1, T2, default):  # one row each, visible to its own tenant only
            assert _tenant_ids(db, TenancyContext(tenant_id=tenant)) == [tenant]
        with db.engine.connect() as conn:
            assert tenant_fk_problems(conn) == []
    finally:
        db.dispose()
        command.downgrade(cfg, "base")


# --- directory row: contract, RLS -------------------------------------------------------


def test_directory_row_is_its_own_tenant(db: Database) -> None:
    with pytest.raises(IntegrityError), db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        s.add(Tenant(id=T2, tenant_id=T1, display_name="mismatched"))


def test_a_session_sees_only_its_own_tenant_row(db: Database) -> None:
    ensure_tenant(db, T1)
    ensure_tenant(db, T2)
    assert _tenant_ids(db, TenancyContext(tenant_id=T1)) == [T1]
    assert _tenant_ids(db, TenancyContext(tenant_id=T2)) == [T2]
    assert _tenant_ids(db, TenancyContext(tenant_id=T1, project_id=PROJECT)) == [T1]


@ON_PG
def test_rls_hides_other_tenant_rows_from_raw_sql(db: Database) -> None:
    ensure_tenant(db, T1)
    ensure_tenant(db, T2)
    with _session(db, TenancyContext(tenant_id=T1)) as s:
        rows = s.execute(text(f"SELECT id FROM {TENANTS_TABLE}")).scalars().all()
    assert [uuid.UUID(str(r)) for r in rows] == [T1]
    with _session(db, None) as s:  # unbound: nothing
        assert s.execute(text(f"SELECT id FROM {TENANTS_TABLE}")).all() == []


@ON_PG
def test_a_row_of_an_unknown_tenant_is_refused(db: Database) -> None:
    ctx = TenancyContext(tenant_id=UNKNOWN, project_id=PROJECT)
    fk = "fk_project_module_setting_tenant_id_tenants"
    with pytest.raises(IntegrityError, match=fk), db.unit_of_work(ctx) as s:
        s.add(
            ProjectModuleSetting(
                tenant_id=UNKNOWN, project_id=PROJECT, module_name="x", enabled=True
            )
        )


# --- provisioning -----------------------------------------------------------------------


def test_ensure_tenant_is_idempotent(db: Database) -> None:
    assert ensure_tenant(db, T1, "Synthetic tenant") is True
    assert ensure_tenant(db, T1, "Synthetic tenant") is False
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        row = s.get(Tenant, T1)
        assert row is not None
        assert (row.tenant_id, row.project_id, row.source) == (T1, None, "system")
        row.soft_delete()
    assert ensure_tenant(db, T1) is False  # a soft-deleted row still counts
    assert _tenant_ids(db, TenancyContext(tenant_id=T1)) == [T1]


def test_ensure_default_tenant_needs_a_database_and_a_tenant(db: Database) -> None:
    assert ensure_default_tenant(None, Settings(default_tenant_id=T1)) is False
    assert ensure_default_tenant(db, Settings(default_tenant_id=None)) is False
    assert _tenant_ids(db, TenancyContext(tenant_id=T1)) == []


def test_app_startup_provisions_the_default_tenant(db: Database) -> None:
    settings = Settings(environment="test", database_url=db.url, default_tenant_id=T1)
    for _ in range(2):  # the second start creates no duplicate
        with TestClient(create_app(settings)):
            pass
    assert _tenant_ids(db, TenancyContext(tenant_id=T1)) == [T1]


def test_command_line_provisions_the_default_tenant(
    db: Database, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KERP_DATABASE_URL", db.url)
    monkeypatch.setenv("KERP_DEFAULT_TENANT_ID", str(T2))
    get_settings.cache_clear()
    assert provision.main([]) == 0
    assert provision.main([]) == 0
    assert capsys.readouterr().out == "default tenant created\ndefault tenant present\n"
    assert _tenant_ids(db, TenancyContext(tenant_id=T2)) == [T2]


def test_command_line_needs_the_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("KERP_DEFAULT_TENANT_ID", raising=False)
    get_settings.cache_clear()
    assert provision.main([]) == 2
    assert "KERP_DEFAULT_TENANT_ID" in capsys.readouterr().err


def test_startup_provisioning_waits_for_the_migration(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    unmigrated = Database(database_url(tmp_path))
    try:
        assert ensure_default_tenant(unmigrated, Settings(default_tenant_id=T1)) is False
    finally:
        unmigrated.dispose()
    assert "tenant provisioning skipped" in caplog.text
