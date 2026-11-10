"""Fixtures for the audit ledger tests: a migrated database and a tamper helper.

The ledger tests run against the real Alembic migration (``0002``), so on PostgreSQL the
REVOKE, the append-only triggers and the RLS policy are the ones production gets.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.db import Database
from tests.db_support import ON_POSTGRES, database_url

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
OWNER_URL = os.environ.get("KERP_TEST_OWNER_URL", "")


def migrated_database(tmp_path: Path) -> Iterator[Database]:
    """Upgrade to head, hand out a ``Database``, downgrade to base afterwards."""
    url = database_url(tmp_path)
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=url, configure_logger=False)
    command.upgrade(cfg, "head")
    db = Database(url)
    try:
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
    engine = create_engine(OWNER_URL)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            return conn.execute(text(sql), params or {}).rowcount
    finally:
        engine.dispose()
