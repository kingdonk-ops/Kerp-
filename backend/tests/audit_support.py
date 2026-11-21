"""Fixtures for the audit ledger tests: a migrated database and a tamper helper.

The ledger tests run against the real Alembic migration (``0002``), so on PostgreSQL the
REVOKE, the append-only triggers and the RLS policy are the ones production gets.
"""

import atexit
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.db import Database
from tests import pg_databases
from tests.db_support import FIXTURE_TENANTS, ON_POSTGRES, database_url, seed_tenants

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
OWNER_URL = os.environ.get("KERP_TEST_OWNER_URL", "")


_sqlite_templates: dict[bool, Path] = {}


def _sqlite_template_file(*, seeded: bool) -> Path:
    """One migrated SQLite file per test process and variant (each xdist worker builds its
    own); ``seeded`` adds the fixture tenants' directory rows.

    Migrating per test cost ~1.3 s (all revisions up, then down again); a byte copy of this
    file gives each test the identical schema and rows in milliseconds (T-0081).
    """
    if seeded not in _sqlite_templates:
        folder = tempfile.mkdtemp(prefix="kerp-sqlite-template-")
        atexit.register(shutil.rmtree, folder, ignore_errors=True)
        path = Path(folder) / "template.db"
        url = "sqlite:///" + path.as_posix()
        cfg = Config(str(ALEMBIC_INI))
        cfg.attributes.update(url=url, configure_logger=False)
        command.upgrade(cfg, "head")
        if seeded:
            db = Database(url)
            try:
                seed_tenants(db, *FIXTURE_TENANTS)
            finally:
                db.dispose()
        _sqlite_templates[seeded] = path
    return _sqlite_templates[seeded]


def migrated_sqlite_file(tmp_path: Path, *, seeded: bool = True) -> str:
    """Copy a migrated template to ``tmp_path`` and return its URL (SQLite only)."""
    assert not ON_POSTGRES, "migrated_sqlite_file is the SQLite path of migrated_database"
    url = database_url(tmp_path)
    shutil.copyfile(_sqlite_template_file(seeded=seeded), tmp_path / "kerp.db")
    return url


def _migration_config(url: str) -> Config:
    """Alembic for the database of ``url``, as the schema owner when the suite has one
    (``KERP_TEST_MIGRATION_URL``, T-0343), else as ``url``'s role."""
    cfg = Config(str(ALEMBIC_INI))
    target = pg_databases.migration_url(url) if ON_POSTGRES else url
    cfg.attributes.update(url=target, configure_logger=False)
    return cfg


def migrate_and_seed(url: str) -> None:
    """Upgrade the database at ``url`` to head and seed the fixture tenants' directory rows
    (as ``url``'s role), leaving no connection open (PostgreSQL copies it as a template
    afterwards)."""
    cfg = _migration_config(url)
    command.upgrade(cfg, "head")
    db = Database(url)
    try:
        seed_tenants(db, *FIXTURE_TENANTS)
    finally:
        db.dispose()


def migrated_database(tmp_path: Path) -> Iterator[Database]:
    """Hand out a ``Database`` at head with the fixture tenants' directory rows seeded.

    SQLite: a copy of the per-process migrated template; the file goes away with
    ``tmp_path``. PostgreSQL with an owner URL: the test's own copy of the worker's migrated
    template database, dropped after the test (T-0082). Neither downgrades
    (``test_migrations.py`` covers the downgrade path on both dialects). PostgreSQL without an
    owner URL: the shared database, upgraded to head, seeded, and downgraded to base after.
    """
    if not ON_POSTGRES:
        db = Database(migrated_sqlite_file(tmp_path))
        try:
            yield db
        finally:
            db.dispose()
        return
    if pg_databases.PER_TEST:
        url, ready = pg_databases.migrated_test_database_url(migrate_and_seed)
        if not ready:  # the test made its (empty) database first; migrate that one
            migrate_and_seed(url)
        db = Database(url)
        try:
            yield db
        finally:
            db.dispose()  # pg_databases.end_test drops the database
        return
    url = database_url(tmp_path)
    cfg = _migration_config(url)
    command.upgrade(cfg, "head")
    db = Database(url)
    try:
        seed_tenants(db, *FIXTURE_TENANTS)
        yield db
    finally:
        db.dispose()
        command.downgrade(cfg, "base")


def tamper(db: Database, sql: str, params: dict[str, Any] | None = None) -> int:
    """Change ledger rows behind the application's back, the way a rogue DBA would.

    On SQLite there is no guard, so the statement runs directly. On PostgreSQL the runtime
    role cannot (REVOKE + trigger), so it runs as the superuser with triggers disabled
    (``session_replication_role = replica``), which also bypasses RLS.
    """
    if not ON_POSTGRES:
        with db.engine.begin() as conn:
            return conn.execute(text(sql), params or {}).rowcount
    owner = OWNER_URL
    if pg_databases.PER_TEST:  # the owner role on the test's own database
        name = make_url(str(db.engine.url)).database
        assert name, db.engine.url
        owner = pg_databases.owner_url(name)
    engine = create_engine(owner)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            return conn.execute(text(sql), params or {}).rowcount
    finally:
        engine.dispose()
