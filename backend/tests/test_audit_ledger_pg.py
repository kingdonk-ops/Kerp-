"""Database-level append-only guard, RLS and concurrent appends for the audit ledger (T-0006).

PostgreSQL only: SQLite has no roles, no REVOKE and no RLS. CI's PostgreSQL step runs these
as ``kerp_app`` (NOSUPERUSER NOBYPASSRLS), which also *owns* the tables because it runs the
migrations (TD-056). An owner can revoke its own privileges, but can also grant them back,
so the migration adds BEFORE UPDATE/DELETE/TRUNCATE triggers as a second layer. The tests
prove each layer on its own.
"""

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.audit.guard import ledger_guard_problems
from app.core.audit.hashing import GENESIS_HASH
from app.core.audit.service import append, verify_chain
from app.core.db import Database
from tests.audit_support import migrated_database
from tests.db_support import ON_POSTGRES
from tests.tenancy_support import CTX_A, CTX_B, PA, PB, T1

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES,
    reason="needs PostgreSQL roles/REVOKE/RLS; runs in CI's PostgreSQL step (TD-052)",
)

KEY = "inspection:00000000-0000-4000-8000-000000000123"
DENIED = "permission denied"
APPEND_ONLY = "append-only"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def chain(db: Database) -> Database:
    with db.unit_of_work(CTX_A) as s:
        for n in range(3):
            append(s, KEY, {"step": n})
    return db


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_ledger SET payload = '{}'::jsonb",
        "UPDATE audit_ledger SET deleted_at = now()",
        "DELETE FROM audit_ledger",
        "TRUNCATE audit_ledger",
    ],
    ids=["update", "soft-delete", "delete", "truncate"],
)
def test_app_role_cannot_change_or_remove_entries(chain: Database, statement: str) -> None:
    with pytest.raises(DBAPIError, match=DENIED), chain.unit_of_work(CTX_A) as s:
        s.execute(text(statement))
    with chain.unit_of_work(CTX_A) as s:
        report = verify_chain(s, KEY)
    assert (report.ok, report.length) == (True, 3)


def test_app_role_keeps_insert_and_select(chain: Database) -> None:
    with chain.engine.connect() as conn:
        granted = conn.execute(
            text(
                "SELECT has_table_privilege('audit_ledger', 'INSERT'),"
                " has_table_privilege('audit_ledger', 'SELECT'),"
                " has_table_privilege('audit_ledger', 'UPDATE'),"
                " has_table_privilege('audit_ledger', 'DELETE'),"
                " has_table_privilege('audit_ledger', 'TRUNCATE')"
            )
        ).one()
    assert tuple(granted) == (True, True, False, False, False)
    with chain.engine.connect() as conn:
        assert ledger_guard_problems(conn) == []


def test_triggers_still_block_an_owner_that_grants_itself_update(chain: Database) -> None:
    # The CI role owns the table (TD-056) and may re-grant what the migration revoked.
    with chain.engine.begin() as conn:
        assert conn.execute(
            text("SELECT tableowner = current_user FROM pg_tables WHERE tablename = 'audit_ledger'")
        ).scalar_one()
        conn.execute(text("GRANT UPDATE, DELETE, TRUNCATE ON audit_ledger TO CURRENT_USER"))
    with chain.engine.connect() as conn:
        assert ledger_guard_problems(conn) == [
            "audit_ledger: current role has UPDATE",
            "audit_ledger: current role has DELETE",
            "audit_ledger: current role has TRUNCATE",
        ]
    for statement in (
        "UPDATE audit_ledger SET payload = '{}'::jsonb",
        "DELETE FROM audit_ledger",
        "TRUNCATE audit_ledger",
    ):
        with pytest.raises(DBAPIError, match=APPEND_ONLY), chain.unit_of_work(CTX_A) as s:
            s.execute(text(statement))
    with chain.unit_of_work(CTX_A) as s:
        assert verify_chain(s, KEY).length == 3


def test_guard_problems_report_missing_triggers(chain: Database) -> None:
    with chain.engine.begin() as conn:
        conn.execute(text("ALTER TABLE audit_ledger DISABLE TRIGGER audit_ledger_append_only"))
        assert ledger_guard_problems(conn) == [
            "audit_ledger: trigger audit_ledger_append_only missing or disabled"
        ]
        conn.execute(text("ALTER TABLE audit_ledger ENABLE TRIGGER audit_ledger_append_only"))
        assert ledger_guard_problems(conn) == []


def test_ledger_is_project_scoped_by_rls(chain: Database) -> None:
    with chain.unit_of_work(CTX_B) as s:
        assert s.execute(text("SELECT count(*) FROM audit_ledger")).scalar_one() == 0
        assert verify_chain(s, KEY).length == 0
    with chain.sessionmaker() as s:  # unbound: fails closed
        assert s.execute(text("SELECT count(*) FROM audit_ledger")).scalar_one() == 0
    # Project B appending to the same key starts B's own chain: it neither fails (no
    # existence oracle) nor blocks or touches A's chain.
    with chain.unit_of_work(CTX_B) as s:
        b_entry = append(s, KEY, {"project": "b"})
        b_report = verify_chain(s, KEY)
        b_rows = s.execute(text("SELECT count(*) FROM audit_ledger")).scalar_one()
    assert (b_entry.seq, b_entry.prev_hash, b_entry.project_id) == (1, GENESIS_HASH, PB)
    assert (b_report.ok, b_report.length, b_rows) == (True, 1, 1)
    with chain.unit_of_work(CTX_A) as s:
        a_next = append(s, KEY, {"project": "a"})
        a_report = verify_chain(s, KEY)
        a_rows = s.execute(text("SELECT count(*) FROM audit_ledger")).scalar_one()
    assert a_next.seq == 4
    assert (a_report.ok, a_report.length, a_rows) == (True, 4, 4)


def test_concurrent_appends_to_one_chain_do_not_fork(db: Database) -> None:
    workers, per_worker = 6, 5
    start = threading.Barrier(workers)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            start.wait()
            for i in range(per_worker):
                with db.unit_of_work(CTX_A) as s:
                    append(s, KEY, {"worker": n, "i": i})
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with db.unit_of_work(CTX_A) as s:
        report = verify_chain(s, KEY)
        rows = s.execute(
            text(
                "SELECT tenant_id, project_id, count(DISTINCT seq) FROM audit_ledger"
                " GROUP BY tenant_id, project_id"
            )
        ).one()
    assert (report.ok, report.length, report.problems) == (True, workers * per_worker, [])
    assert tuple(rows) == (T1, PA, workers * per_worker)
