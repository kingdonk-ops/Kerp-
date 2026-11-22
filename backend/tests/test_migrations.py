"""Alembic is configured and in sync with the metadata (T-0005).

``alembic check`` fails when autogenerate would emit any operation, so a model change
without its migration breaks CI.
"""

import io
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
                assert MigrationContext.configure(conn).get_current_revision() == "0041"
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


def test_offline_sql_commits_each_revision_apart() -> None:
    """``--sql`` output runs ``0027``'s VALIDATE in its own transaction (T-0079, ADR 0011).

    Offline generation never connects, so a PostgreSQL URL renders the transactional DDL.
    """
    cfg = _config("postgresql+psycopg://offline@localhost/offline")
    cfg.output_buffer = io.StringIO()
    command.upgrade(cfg, "0025:0027", sql=True)
    lines = [
        line
        for line in cfg.output_buffer.getvalue().splitlines()
        if line in ("BEGIN;", "COMMIT;") or line.startswith("-- Running upgrade")
    ]
    assert lines == [
        "BEGIN;",
        "-- Running upgrade 0025 -> 0026",
        "COMMIT;",
        "BEGIN;",
        "-- Running upgrade 0026 -> 0027",
        "COMMIT;",
    ]


def test_offline_runtime_role_grants_carry_the_role_and_the_ledger_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``0039`` (T-0343, ADR 0019): the role checks run when the script is applied; the
    runtime role never gets more than ``SELECT, INSERT`` on ``audit_ledger``."""
    monkeypatch.setenv("KERP_RUNTIME_DB_ROLE", "kerp_runtime_x")
    get_settings.cache_clear()
    cfg = _config("postgresql+psycopg://offline@localhost/offline")
    cfg.output_buffer = io.StringIO()
    command.upgrade(cfg, "0038:0039", sql=True)
    out = cfg.output_buffer.getvalue()
    assert "rolname = 'kerp_runtime_x'" in out
    assert "'kerp_runtime_x' = current_user" in out
    assert out.count("ALTER DEFAULT PRIVILEGES") == 3
    assert "WHEN r.relname = 'audit_ledger' THEN ARRAY['SELECT', 'INSERT']" in out
    assert "TRUNCATE" not in out
    cfg.output_buffer = io.StringIO()
    command.downgrade(cfg, "0039:0038", sql=True)
    down = cfg.output_buffer.getvalue()
    assert down.count("ALTER DEFAULT PRIVILEGES") == 3
    assert "REVOKE EXECUTE ON FUNCTIONS FROM" in down
    assert "GRANT " not in down


def test_offline_runtime_role_grants_skip_an_empty_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERP_RUNTIME_DB_ROLE", "")
    get_settings.cache_clear()
    cfg = _config("postgresql+psycopg://offline@localhost/offline")
    cfg.output_buffer = io.StringIO()
    command.upgrade(cfg, "0038:0039", sql=True)
    out = cfg.output_buffer.getvalue()
    assert "-- Running upgrade 0038 -> 0039" in out
    assert "GRANT" not in out


def test_unknown_revision_is_an_error(url: str) -> None:
    with pytest.raises(CommandError):
        command.upgrade(_config(url), "does-not-exist")
