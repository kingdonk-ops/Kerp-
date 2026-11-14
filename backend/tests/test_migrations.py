"""Alembic is configured and in sync with the metadata (T-0005).

``alembic check`` fails when autogenerate would emit any operation, so a model change
without its migration breaks CI.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.util.exc import CommandError
from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect

from app.core.config import get_settings
from app.core.db import include_object
from tests.db_support import database_url

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _config(url: str | None = None) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    if url is not None:
        cfg.attributes["url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture
def url(tmp_path: Path) -> str:
    return database_url(tmp_path)


def test_upgrade_head_then_check_is_clean(url: str) -> None:
    cfg = _config(url)
    command.upgrade(cfg, "head")
    try:
        command.check(cfg)  # raises if autogenerate would produce a migration
        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                assert MigrationContext.configure(conn).get_current_revision() == "0013"
        finally:
            engine.dispose()
    finally:
        command.downgrade(cfg, "base")


def test_autogenerate_detects_drift(url: str) -> None:
    # A database at head compared with a model it lacks must produce a diff.
    cfg = _config(url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    try:
        md = MetaData()
        Table("drifted", md, Column("id", String(36), primary_key=True))
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"include_object": include_object})
            diff = compare_metadata(ctx, md)
        assert any(op[0] == "add_table" and op[1].name == "drifted" for op in diff)
    finally:
        engine.dispose()
        command.downgrade(cfg, "base")


def test_env_falls_back_to_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = "sqlite:///" + (tmp_path / "from_settings.db").as_posix()
    monkeypatch.setenv("KERP_MIGRATION_DATABASE_URL", target)
    get_settings.cache_clear()
    command.upgrade(_config(), "head")
    engine = create_engine(target)
    try:
        assert "alembic_version" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_env_requires_a_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KERP_MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.delenv("KERP_DATABASE_URL", raising=False)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="KERP_MIGRATION_DATABASE_URL"):
        command.upgrade(_config(), "head")


def test_offline_sql_generation(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_config(url), "head", sql=True)
    assert "alembic_version" in capsys.readouterr().out


def test_unknown_revision_is_an_error(url: str) -> None:
    with pytest.raises(CommandError):
        command.upgrade(_config(url), "does-not-exist")
