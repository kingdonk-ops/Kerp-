"""The four RLS-bypass contract tests (diff §4): SAQ worker, cross-module service call,
raw-SQL aggregation and DB migration (T-0007).

Each path skips the ORM scope in some way, so only PostgreSQL RLS stands between it and
another project's rows. These tests need real PostgreSQL and a role that is neither
SUPERUSER nor BYPASSRLS (CI's ``kerp_app``). RLS does not exist on SQLite.
"""

import os
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Column, MetaData, Table, create_engine, func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from app.core.db import GUID, Base, Database, import_all_models
from app.core.tenancy.context import TenancyContext, current_tenant_id, use_tenancy
from app.core.tenancy.rls import (
    PROJECT_SETTING,
    TENANT_SETTING,
    bind_connection,
    bind_tenancy,
    install_pool_reset,
    rls_coverage_problems,
    rls_enforcement_problems,
    rls_policy_sql,
)
from tests.db_support import ON_POSTGRES, POSTGRES_URL, Gadget, Widget
from tests.tenancy_support import (
    CTX_A,
    CTX_B,
    CTX_C,
    CTX_T1,
    PA,
    PB,
    T1,
    VISIBLE_TO_A,
    seeded_database,
)

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)"
)

OWNER_URL = os.environ.get("KERP_TEST_OWNER_URL", "")
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
RLS_ERROR = "row-level security"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from seeded_database(tmp_path)


def _setting_values(conn: Connection) -> tuple[str | None, str | None]:
    row = conn.execute(
        text(
            f"SELECT NULLIF(current_setting('{TENANT_SETTING}', true), ''),"
            f" NULLIF(current_setting('{PROJECT_SETTING}', true), '')"
        )
    ).one()
    return row[0], row[1]


# --- 1. SAQ worker -------------------------------------------------------------------------


def test_worker_without_context_reads_and_writes_nothing(db: Database) -> None:
    with db.sessionmaker() as s:
        assert s.scalar(select(func.count()).select_from(Widget)) == 0
        assert s.execute(text("SELECT count(*) FROM test_widget")).scalar_one() == 0
    with pytest.raises(DBAPIError, match=RLS_ERROR), db.sessionmaker.begin() as s:
        s.add(Widget(tenant_id=T1, project_id=PA, name="orphan-job"))


def test_worker_binds_the_job_context_not_the_enqueuers(db: Database) -> None:
    seen: dict[str, object] = {}

    def job() -> None:
        seen["inherited_tenant"] = current_tenant_id.get()
        with db.unit_of_work(CTX_A) as s:
            seen["names"] = set(s.scalars(select(Widget.name)))
            seen["raw"] = s.execute(text("SELECT count(*) FROM test_widget")).scalar_one()

    with use_tenancy(CTX_C):  # the enqueuing request's context
        worker = threading.Thread(target=job)
        worker.start()
        worker.join()
    assert seen["inherited_tenant"] is None
    assert seen["names"] == VISIBLE_TO_A
    assert seen["raw"] == 4  # a1, a2, a-deleted (raw SQL has no soft-delete filter), library


def test_job_context_survives_commits(db: Database) -> None:
    with db.sessionmaker() as s:
        bind_tenancy(s, CTX_A)
        s.add(Widget(tenant_id=T1, project_id=PA, name="step-1"))
        s.commit()
        assert set(s.scalars(select(Widget.name))) == {*VISIBLE_TO_A, "step-1"}
        s.commit()
        assert s.execute(text("SELECT count(*) FROM test_widget")).scalar_one() == 5


@pytest.mark.parametrize("reset", [True, False], ids=["with-reset", "without-reset"])
def test_pooled_connection_never_carries_a_context(db: Database, reset: bool) -> None:
    engine = create_engine(POSTGRES_URL, poolclass=QueuePool, pool_size=1, max_overflow=0)
    if reset:
        install_pool_reset(engine)
    try:
        with engine.connect() as conn:  # a job that wrongly sets a session-level context
            conn.execute(
                text("SELECT set_config(:t, :tid, false), set_config(:p, :pid, false)"),
                {"t": TENANT_SETTING, "tid": str(T1), "p": PROJECT_SETTING, "pid": str(PA)},
            )
            conn.commit()
        with engine.connect() as conn:  # the same pooled connection, next user
            leaked = _setting_values(conn) != (None, None)
            rows = conn.execute(text("SELECT count(*) FROM test_widget")).scalar_one()
        # Without the checkin reset the context leaks; that proves the test can fail.
        assert leaked is not reset
        assert (rows == 0) is reset
        with Session(engine) as s:  # a bound session on that connection, then unbound
            bind_tenancy(s, CTX_A)
            assert s.execute(text("SELECT count(*) FROM test_widget")).scalar_one() == 4
        if reset:
            with engine.connect() as conn:
                assert _setting_values(conn) == (None, None)
    finally:
        engine.dispose()


# --- 2. cross-module service call -------------------------------------------------------------


def _beta_count_gadgets(session: Session) -> int:
    """Module "beta" service: runs on the caller's session (the sanctioned pattern)."""
    return int(session.execute(select(func.count()).select_from(Gadget.__table__)).scalar_one())


def _beta_count_gadgets_own_session(db: Database) -> int:
    """A service that wrongly opens its own session instead of using the caller's."""
    with db.sessionmaker() as s:
        return int(s.execute(text("SELECT count(*) FROM test_gadget")).scalar_one())


def test_cross_module_call_stays_in_the_callers_project(db: Database) -> None:
    with db.unit_of_work(CTX_A) as s:
        # Core table select: the ORM loader criteria do not apply, RLS still does.
        assert _beta_count_gadgets(s) == 1
        assert s.scalars(select(Gadget.label)).all() == ["ga"]
        # A context-less side session fails closed, it does not see every project.
        assert _beta_count_gadgets_own_session(db) == 0


def test_cross_module_write_into_another_project_is_rejected(db: Database) -> None:
    with pytest.raises(DBAPIError, match=RLS_ERROR), db.unit_of_work(CTX_A) as s:
        s.execute(Gadget.__table__.insert().values(**_raw_row(PB), label="smuggled"))


# --- 3. raw-SQL aggregation ---------------------------------------------------------------------


def _raw_row(project: uuid.UUID | None) -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "tenant_id": T1,
        "project_id": project,
        "custom_fields": {},
        "source": "api",
        "revision": 1,
        "created_at": func.now(),
        "updated_at": func.now(),
    }


def test_raw_sql_aggregation_sees_only_the_bound_project(db: Database) -> None:
    with db.unit_of_work(CTX_A) as s:
        per_project = dict(
            s.execute(
                text("SELECT project_id, count(*) FROM test_widget GROUP BY project_id")
            ).all()
        )
        assert per_project == {PA: 3, None: 1}
        tenants = s.execute(
            text("SELECT tenant_id FROM test_widget UNION SELECT tenant_id FROM test_gadget")
        ).scalars()
        assert set(tenants) == {T1}
        joined = s.execute(
            text(
                "SELECT count(*) FROM test_widget w JOIN test_gadget g ON g.tenant_id = w.tenant_id"
            )
        ).scalar_one()
        assert joined == 4  # 4 visible widgets x 1 visible gadget
        driver = s.connection().exec_driver_sql("SELECT count(*) FROM test_gadget")
        assert driver.scalar_one() == 1


def test_raw_sql_cannot_touch_another_project(db: Database) -> None:
    params = {"pb": PB}
    with db.engine.begin() as conn:
        bind_connection(conn, CTX_A)
        renamed = conn.execute(
            text("UPDATE test_widget SET name = 'x' WHERE project_id = :pb"), params
        )
        assert renamed.rowcount == 0
        hidden = conn.execute(
            text("SELECT count(*) FROM test_widget WHERE project_id = :pb"), params
        )
        assert hidden.scalar_one() == 0
        deleted = conn.execute(text("DELETE FROM test_widget WHERE project_id = :pb"), params)
        assert deleted.rowcount == 0
    with pytest.raises(DBAPIError, match=RLS_ERROR), db.unit_of_work(CTX_A) as s:
        s.execute(text("UPDATE test_widget SET project_id = :pb"), params)
    with db.unit_of_work(TenancyContext(tenant_id=T1, project_id=PB)) as s:
        names = set(s.execute(text("SELECT name FROM test_widget")).scalars())
    assert names == {"b1", "b2", "b3", "t1-library"}


def test_project_session_cannot_write_tenant_level_rows_in_raw_sql(db: Database) -> None:
    # Tenant-level rows are read-only to a project session (tenant_level_read_policy is
    # FOR SELECT); creating, demoting to, changing or deleting one leaks across projects.
    with db.engine.begin() as conn:
        bind_connection(conn, CTX_A)
        library = {"name": "t1-library"}
        changed = conn.execute(
            text("UPDATE test_widget SET name = 'x' WHERE name = :name"), library
        )
        assert changed.rowcount == 0
        deleted = conn.execute(text("DELETE FROM test_widget WHERE name = :name"), library)
        assert deleted.rowcount == 0
    with pytest.raises(DBAPIError, match=RLS_ERROR), db.unit_of_work(CTX_A) as s:
        s.execute(text("UPDATE test_widget SET project_id = NULL WHERE name = 'a1'"))
    with pytest.raises(DBAPIError, match=RLS_ERROR), db.unit_of_work(CTX_A) as s:
        s.execute(Widget.__table__.insert().values(**_raw_row(None), name="t1-smuggled"))
    with db.unit_of_work(CTX_B) as s:
        names = set(s.execute(text("SELECT name FROM test_widget")).scalars())
    assert names == {"b1", "b2", "b3", "t1-library"}
    # The tenant-level session is the one that may write tenant-level rows.
    with db.unit_of_work(CTX_T1) as s:
        s.execute(Widget.__table__.insert().values(**_raw_row(None), name="t1-shared"))
        s.execute(text("DELETE FROM test_widget WHERE name = 't1-library'"))
    with db.unit_of_work(CTX_A) as s:
        names = set(s.execute(text("SELECT name FROM test_widget")).scalars())
    assert names == {"a1", "a2", "a-deleted", "t1-shared"}


# --- 4. DB migration ------------------------------------------------------------------------------


def test_runtime_role_cannot_bypass_rls(db: Database) -> None:
    assert rls_enforcement_problems(db.engine) == []


@pytest.mark.skipif(not OWNER_URL, reason="needs KERP_TEST_OWNER_URL (set in CI) (TD-052)")
def test_superuser_role_is_reported() -> None:
    engine = create_engine(OWNER_URL)
    try:
        assert any("SUPERUSER" in p for p in rls_enforcement_problems(engine))
    finally:
        engine.dispose()


def test_migrated_schema_has_rls_on_every_model_table() -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=POSTGRES_URL, configure_logger=False)
    command.upgrade(cfg, "head")
    engine = create_engine(POSTGRES_URL)
    try:
        import_all_models()
        with engine.connect() as conn:
            assert rls_coverage_problems(conn, Base.metadata) == []
    finally:
        engine.dispose()
        command.downgrade(cfg, "base")


def test_a_migration_that_forgets_rls_is_caught() -> None:
    md = MetaData()
    Table(
        "test_unprotected",
        md,
        Column("id", GUID(), primary_key=True),
        Column("tenant_id", GUID(), nullable=False),
        Column("project_id", GUID()),
    )
    engine = create_engine(POSTGRES_URL)
    try:
        with engine.begin() as conn:
            md.create_all(conn)
            assert rls_coverage_problems(conn, md) == [
                "test_unprotected: row level security not enabled",
                "test_unprotected: row level security not forced",
                "test_unprotected: policy project_isolation_policy missing",
                "test_unprotected: policy tenant_level_read_policy missing",
            ]
            op = Operations(MigrationContext.configure(conn))
            for statement in rls_policy_sql("test_unprotected"):
                op.execute(statement)
            assert rls_coverage_problems(conn, md) == []
            md.drop_all(conn)
            assert rls_coverage_problems(conn, md) == ["test_unprotected: table not found"]
    finally:
        engine.dispose()


def test_table_owner_running_a_migration_is_still_subject_to_rls(db: Database) -> None:
    # The role that created the tables owns them; FORCE RLS keeps it under the policy, so
    # an unbound data migration touches nothing (TD-055) rather than every project.
    with db.engine.begin() as conn:
        is_owner = conn.execute(
            text("SELECT tableowner = current_user FROM pg_tables WHERE tablename = 'test_widget'")
        ).scalar_one()
        assert is_owner is True
        assert conn.execute(text("UPDATE test_widget SET name = name")).rowcount == 0
        assert conn.execute(text("SELECT count(*) FROM test_widget")).scalar_one() == 0
