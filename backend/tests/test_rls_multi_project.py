"""Explicit multi-project read scope under PostgreSQL RLS (T-0010, TD-053, edge spec §6).

Raw SQL skips the ORM scope, so only the RLS policies decide here. Needs real PostgreSQL
and the ``NOSUPERUSER NOBYPASSRLS`` role ``kerp_app`` (CI's PostgreSQL step).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import QueuePool

from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import ALLOWED_SETTING, bind_connection, install_pool_reset
from tests.db_support import ON_POSTGRES, POSTGRES_URL, Widget
from tests.tenancy_support import PA, PB, PC, T1, seeded_database
from tests.test_rls_bypass import RLS_ERROR, _raw_row

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)"
)

# A portfolio persona (e.g. PER-01) with memberships in A and B, writing project A.
PORTFOLIO_A = TenancyContext(tenant_id=T1, project_id=PA, allowed_project_ids=frozenset({PA, PB}))
ALL_OF_A_AND_B = {"a1", "a2", "a-deleted", "b1", "b2", "b3", "t1-library"}


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from seeded_database(tmp_path)


def _raw_names(db: Database, ctx: TenancyContext | None) -> set[str]:
    with db.engine.begin() as conn:
        bind_connection(conn, ctx)
        return set(conn.execute(text("SELECT name FROM test_widget")).scalars())


def test_portfolio_session_reads_every_allowed_project(db: Database) -> None:
    assert _raw_names(db, PORTFOLIO_A) == ALL_OF_A_AND_B
    # Without a write project (tenant-level session) the allowed list still reads A and B.
    reader = TenancyContext(tenant_id=T1, allowed_project_ids=frozenset({PA, PB}))
    assert _raw_names(db, reader) == ALL_OF_A_AND_B


def test_write_to_an_allowed_project_is_refused(db: Database) -> None:
    with pytest.raises(DBAPIError, match=RLS_ERROR), db.unit_of_work(PORTFOLIO_A) as s:
        s.execute(Widget.__table__.insert().values(**_raw_row(PB), name="smuggled"))
    with db.engine.begin() as conn:
        bind_connection(conn, PORTFOLIO_A)
        renamed = conn.execute(
            text("UPDATE test_widget SET name = 'x' WHERE project_id = :pb"), {"pb": PB}
        )
        assert renamed.rowcount == 0
        deleted = conn.execute(text("DELETE FROM test_widget WHERE project_id = :pb"), {"pb": PB})
        assert deleted.rowcount == 0
        moved = conn.execute(text("UPDATE test_widget SET name = 'a1-renamed' WHERE name = 'a1'"))
        assert moved.rowcount == 1  # the bound project stays writable
    with pytest.raises(DBAPIError, match=RLS_ERROR), db.unit_of_work(PORTFOLIO_A) as s:
        s.execute(text("UPDATE test_widget SET project_id = :pb WHERE name = 'a2'"), {"pb": PB})
    assert _raw_names(db, PORTFOLIO_A) == (ALL_OF_A_AND_B - {"a1"}) | {"a1-renamed"}


def test_allowed_list_of_a_cannot_read_b(db: Database) -> None:
    only_a = TenancyContext(tenant_id=T1, allowed_project_ids=frozenset({PA}))
    assert _raw_names(db, only_a) == {"a1", "a2", "a-deleted", "t1-library"}
    # Another tenant's project in the list matches nothing: the tenant still has to match.
    foreign = TenancyContext(tenant_id=T1, allowed_project_ids=frozenset({PC}))
    assert _raw_names(db, foreign) == {"t1-library"}


def test_unbound_session_still_sees_nothing(db: Database) -> None:
    assert _raw_names(db, None) == set()


def test_pool_reset_clears_the_allowed_list(db: Database) -> None:
    engine = create_engine(POSTGRES_URL, poolclass=QueuePool, pool_size=1, max_overflow=0)
    install_pool_reset(engine)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config(:a, :ids, false)"), {"a": ALLOWED_SETTING, "ids": str(PB)}
            )
            conn.commit()
        with engine.connect() as conn:
            leftover = conn.execute(
                text(f"SELECT NULLIF(current_setting('{ALLOWED_SETTING}', true), '')")
            ).scalar_one()
        assert leftover is None
    finally:
        engine.dispose()
