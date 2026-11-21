"""The per-test PostgreSQL database helper refuses a source that still holds a schema (T-0083,
PR #141 review): every "empty" copy would otherwise inherit it."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests import pg_databases

pytestmark = pytest.mark.skipif(
    not pg_databases.PER_TEST,
    reason="needs PostgreSQL + KERP_TEST_OWNER_URL; runs in CI's PostgreSQL step (TD-052)",
)

SCRATCH = f"kerp_t_{pg_databases.WORKER}_srccheck"
COPY = f"kerp_t_{pg_databases.WORKER}_srccheck_copy"


def _owner_exec(name: str, sql: str) -> None:
    engine = create_engine(pg_databases.owner_url(name), poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


@pytest.fixture
def dirty_source() -> Iterator[str]:
    pg_databases._create(SCRATCH, "template1")
    try:
        yield SCRATCH
    finally:
        pg_databases.drop(COPY)
        pg_databases.drop(SCRATCH)


def test_an_empty_source_passes() -> None:
    pg_databases._create(COPY, "template1")
    try:
        pg_databases._check_empty(COPY, "template1")
    finally:
        pg_databases.drop(COPY)


def test_a_leftover_table_is_refused(dirty_source: str) -> None:
    _owner_exec(dirty_source, "CREATE TABLE public.leftover (id int)")
    pg_databases._create(COPY, dirty_source)
    with pytest.raises(RuntimeError, match=r"not empty \(tables: \['leftover'\]"):
        pg_databases._check_empty(COPY, dirty_source)


def test_a_stamped_alembic_version_is_refused(dirty_source: str) -> None:
    _owner_exec(
        dirty_source,
        "CREATE TABLE public.alembic_version (version_num varchar(32) PRIMARY KEY);"
        "INSERT INTO public.alembic_version VALUES ('0030')",
    )
    pg_databases._create(COPY, dirty_source)
    with pytest.raises(RuntimeError, match="alembic_version rows: 1"):
        pg_databases._check_empty(COPY, dirty_source)
