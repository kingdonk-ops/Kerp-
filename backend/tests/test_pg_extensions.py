"""ltree and PostGIS are installed in the test database and usable by ``kerp_app`` (T-0003).

The extensions come from ``infra/postgres/initdb/20-kerp-app.sql``, which CI's backend job
and the docker-compose PostgreSQL both run. PostgreSQL only.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from tests.db_support import ON_POSTGRES, POSTGRES_URL

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL; runs in CI's PostgreSQL step (TD-052)"
)


@pytest.fixture
def conn() -> Iterator[Connection]:
    engine = create_engine(POSTGRES_URL)
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def test_ltree_and_postgis_are_installed(conn: Connection) -> None:
    names = set(conn.scalars(text("SELECT extname FROM pg_extension")))
    assert {"ltree", "postgis"} <= names


def test_ltree_works_for_the_runtime_role(conn: Connection) -> None:
    assert conn.scalar(text("SELECT 'site.area.asset'::ltree <@ 'site.area'::ltree")) is True


def test_postgis_works_for_the_runtime_role(conn: Connection) -> None:
    point = conn.scalar(text("SELECT ST_AsText(ST_SetSRID(ST_MakePoint(1, 2), 4326))"))
    assert point == "POINT(1 2)"


def test_runtime_role_is_not_superuser_and_cannot_bypass_rls(conn: Connection) -> None:
    row = conn.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).one()
    assert (row.rolsuper, row.rolbypassrls) == (False, False)
