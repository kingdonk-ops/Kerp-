"""The pool-checkin reset of the tenancy settings (T-0348, audit item 18).

A connection that goes back to the pool has every tenancy setting cleared with one
``set_config`` statement and one commit, so it carries no tenant, project or user into the
next checkout. The fake-driver test runs everywhere; the rest needs PostgreSQL (CI's
PostgreSQL step). All data is synthetic.
"""

import os
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool

from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import (
    POOL_RESET_SQL,
    SETTINGS,
    install_pool_reset,
    reset_connection_settings,
    setting_values,
)
from tests.db_support import ON_POSTGRES, postgres_url
from tests.tenancy_support import PA, PB, T1, seeded_database

on_postgres = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)"
)

# Every setting non-empty: a system admin of T1 bound to project A, a member of A and B.
EVERYTHING = TenancyContext(
    tenant_id=T1,
    project_id=PA,
    allowed_project_ids=frozenset({PA, PB}),
    member_project_ids=frozenset({PA, PB}),
    user_id=uuid.UUID("00000000-0000-4000-8000-00000000d001"),
    is_system_admin=True,
    admin_project_ids=frozenset({PA}),
)


class _FakeCursor:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def execute(self, statement: str, *_args: Any) -> None:
        self.log.append(statement)

    def close(self) -> None:
        self.log.append("<close>")


class _FakeConnection:
    """Records what the reset sends to the driver."""

    def __init__(self) -> None:
        self.log: list[str] = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.log)

    def commit(self) -> None:
        self.log.append("<commit>")


def test_the_reset_is_one_statement_and_one_commit() -> None:
    fake = _FakeConnection()
    reset_connection_settings(fake)  # type: ignore[arg-type]  # duck-typed driver connection
    assert fake.log == [POOL_RESET_SQL, "<close>", "<commit>"]


def test_the_reset_statement_clears_every_setting_at_session_level() -> None:
    assert POOL_RESET_SQL.startswith("SELECT ")
    assert "RESET" not in POOL_RESET_SQL
    for name in SETTINGS:
        assert f"set_config('{name}', '', false)" in POOL_RESET_SQL
    assert POOL_RESET_SQL.count("set_config(") == len(SETTINGS) == 7


# --- PostgreSQL -------------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    if not ON_POSTGRES:
        pytest.skip("needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)")
    yield from seeded_database(tmp_path)


@pytest.fixture
def engine() -> Iterator[Engine]:
    """One pooled connection with the reset installed, so the next checkout reuses it."""
    if not ON_POSTGRES:
        pytest.skip("needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)")
    engine = create_engine(postgres_url(), poolclass=QueuePool, pool_size=1, max_overflow=0)
    install_pool_reset(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _set_session_level(engine: Engine, context: TenancyContext) -> int:
    """Set every setting for the whole session (``is_local => false``); the backend pid."""
    values = setting_values(context)
    with engine.connect() as conn:
        for name, value in values.items():
            conn.execute(text("SELECT set_config(:n, :v, false)"), {"n": name, "v": value})
        conn.commit()
        pid: int = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
    return pid


@on_postgres
def test_a_returned_connection_carries_no_setting_into_the_next_checkout(engine: Engine) -> None:
    pid = _set_session_level(engine, EVERYTHING)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT pg_backend_pid()")).scalar_one() == pid  # reused
        for name in SETTINGS:
            leftover = conn.execute(
                text("SELECT NULLIF(current_setting(:n, true), '')"), {"n": name}
            ).scalar_one()
            assert leftover is None, name


@on_postgres
def test_a_reused_connection_reads_no_tenant_rows(db: Database, engine: Engine) -> None:
    """The policies read the empty value as unset: a reused connection sees nothing."""
    with engine.connect() as conn:  # the same scratch rows are visible while bound
        conn.execute(text("SELECT set_config(:n, :v, false)"), {"n": SETTINGS[0], "v": str(T1)})
        conn.execute(text("SELECT set_config(:n, :v, false)"), {"n": SETTINGS[1], "v": str(PA)})
        bound = set(conn.execute(text("SELECT name FROM test_widget")).scalars())
        conn.commit()
    assert {"a1", "a2", "t1-library"} <= bound
    with engine.connect() as conn:
        assert set(conn.execute(text("SELECT name FROM test_widget")).scalars()) == set()


@on_postgres
def test_the_reset_is_three_round_trips(engine: Engine) -> None:
    """libpq's protocol trace: the driver's ``BEGIN``, the one statement and the ``COMMIT``."""
    from psycopg import pq

    _set_session_level(engine, EVERYTHING)
    raw = engine.raw_connection()
    try:
        driver = raw.driver_connection
        assert driver is not None
        fd, path = tempfile.mkstemp()
        try:
            driver.pgconn.trace(fd)
            driver.pgconn.set_trace_flags(pq.Trace.SUPPRESS_TIMESTAMPS | pq.Trace.REGRESS_MODE)
            reset_connection_settings(raw.dbapi_connection)  # type: ignore[arg-type]
            driver.pgconn.untrace()
            sent = [
                line.split("\t", 3)
                for line in Path(path).read_text(encoding="utf-8").splitlines()
                if line.startswith("F\t")
            ]
        finally:
            os.close(fd)
            os.unlink(path)
    finally:
        raw.close()
    queries = [fields[3].strip().strip('"') for fields in sent if fields[2] == "Query"]
    assert queries == ["BEGIN", POOL_RESET_SQL, "COMMIT"]
    assert all(fields[2] == "Query" for fields in sent)  # nothing else went to the server
