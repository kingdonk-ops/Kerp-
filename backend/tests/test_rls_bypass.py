"""The four RLS-bypass contract tests (diff §4): SAQ worker, cross-module service call,
raw-SQL aggregation and DB migration (T-0007; the worker runs through real SAQ since
T-0060).

Each path skips the ORM scope in some way, so only PostgreSQL RLS stands between it and
another project's rows. These tests need real PostgreSQL and a role that is neither
SUPERUSER nor BYPASSRLS (CI's ``kerp_app``). RLS does not exist on SQLite. The SAQ worker
tests also need Redis (``KERP_REDIS_URL``), which CI's PostgreSQL step provides.
"""

import asyncio
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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

from app.core.config import Settings
from app.core.db import GUID, Base, Database, import_all_models
from app.core.tenancy.context import (
    TenancyContext,
    current_project_id,
    current_tenant_id,
    use_tenancy,
)
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
from app.modules.jobs import service
from app.modules.jobs import worker as worker_module
from app.modules.jobs.broker import LoopDispatcher, SaqBroker
from app.modules.jobs.models import Job
from app.modules.jobs.registry import JobContext, JobKind, JobRegistry
from app.modules.jobs.worker import build_workers
from tests.audit_support import migrated_database
from tests.db_support import ON_POSTGRES, POSTGRES_URL, Gadget, Widget
from tests.tenancy_support import (
    CTX_A,
    CTX_B,
    CTX_C,
    CTX_T1,
    PA,
    PB,
    PC,
    T1,
    T2,
    VISIBLE_TO_A,
    seeded_database,
)

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)"
)

OWNER_URL = os.environ.get("KERP_TEST_OWNER_URL", "")
REDIS_URL = os.environ.get("KERP_REDIS_URL", "")
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


# The worker tests below run a job through the real SAQ path: ``build_workers`` (the same
# workers ``python -m app.modules.jobs.worker`` serves), a Redis queue, SAQ's job processing
# task and the ``run_job`` wrapper, on one asyncio loop. The workers are started inside an
# ambient ``use_tenancy(CTX_C)``: asyncio tasks copy the context variables of the task that
# creates them, so a wrapper that did not bind the job's own scope would hand the handler
# tenant T2's context. (The thread-based test this replaces proved nothing: a new thread
# starts with empty context variables whatever the code under test does.)


@pytest.fixture
def jobs_db(tmp_path: Path) -> Iterator[Database]:
    """The migrated schema (the job table, under RLS) plus the seeded scratch tables."""
    migrated = migrated_database(tmp_path)
    next(migrated)
    try:
        yield from seeded_database(tmp_path)
    finally:
        migrated.close()  # dispose and downgrade


def _run_through_saq(
    db: Database, monkeypatch: pytest.MonkeyPatch, kind: JobKind, enqueue_in: TenancyContext
) -> Job:
    """Enqueue one job in ``enqueue_in`` and let the real SAQ workers run it to the end."""
    monkeypatch.setattr(worker_module, "SHUTDOWN_GRACE_S", 0)
    registry = JobRegistry()
    registry.add(kind)

    async def scenario() -> uuid.UUID:
        broker = SaqBroker(REDIS_URL, prefix=f"kerp-rls-{uuid.uuid4().hex[:8]}")
        dispatcher = LoopDispatcher(broker, asyncio.get_running_loop())
        service.set_dispatcher(dispatcher)
        settings = Settings(environment="test")
        workers = build_workers(settings, db=db, registry=registry, broker=broker)
        with use_tenancy(CTX_C):  # an ambient context the worker tasks inherit
            tasks = [asyncio.create_task(w.start()) for w in workers]
        try:
            with db.unit_of_work(enqueue_in) as s:
                job_id = service.create_job(s, kind).id
            await dispatcher.drain()

            async def finished() -> None:
                while True:
                    with db.unit_of_work(enqueue_in) as s:
                        if service.get_job(s, job_id).status in ("succeeded", "failed"):
                            return
                    await asyncio.sleep(0.05)

            await asyncio.wait_for(finished(), 15)
            return job_id
        finally:
            service.set_dispatcher(None)
            for w in workers:
                await w.stop()
            for t in tasks:
                t.cancel()
            await broker.close()

    job_id = asyncio.run(scenario())
    with db.unit_of_work(enqueue_in) as s:
        return service.get_job(s, job_id)


@pytest.mark.skipif(not REDIS_URL, reason="needs Redis (KERP_REDIS_URL); CI's PG step (TD-052)")
def test_the_saq_worker_binds_the_jobs_own_scope_under_rls(
    jobs_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    async def probe(job: JobContext) -> dict[str, Any]:
        seen["context"] = (current_tenant_id.get(), current_project_id.get())
        seen["tenancy"] = job.tenancy
        with job.db.unit_of_work(job.tenancy) as s:
            seen["settings"] = _setting_values(s.connection())
            seen["names"] = set(s.scalars(select(Widget.name)))
            seen["raw"] = s.execute(text("SELECT count(*) FROM test_widget")).scalar_one()
            s.add(Widget(tenant_id=T1, project_id=PA, name="written-by-job"))
        with job.db.sessionmaker() as unbound:  # no inherited binding: fails closed
            seen["unbound"] = unbound.execute(text("SELECT count(*) FROM test_widget")).scalar_one()
        return {"ok": True}

    job = _run_through_saq(jobs_db, monkeypatch, JobKind("rls.probe", "interactive", probe), CTX_A)

    assert (job.status, job.error, job.result) == ("succeeded", None, {"ok": True})
    assert (job.tenant_id, job.project_id) == (T1, PA)
    assert seen["context"] == (T1, PA)  # not CTX_C, the context the worker tasks inherited
    assert seen["tenancy"] == CTX_A
    assert seen["settings"] == (str(T1), str(PA))  # the handler's session is bound in PostgreSQL
    assert seen["names"] == VISIBLE_TO_A
    assert seen["raw"] == 4  # a1, a2, a-deleted (raw SQL has no soft-delete filter), library
    assert seen["unbound"] == 0
    with jobs_db.unit_of_work(CTX_A) as s:
        assert "written-by-job" in set(s.scalars(select(Widget.name)))


@pytest.mark.skipif(not REDIS_URL, reason="needs Redis (KERP_REDIS_URL); CI's PG step (TD-052)")
def test_a_saq_job_cannot_read_or_write_another_tenants_or_projects_rows(
    jobs_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}
    others = {"pb": PB, "t2": T2}

    async def snoop(job: JobContext) -> None:
        with job.db.unit_of_work(job.tenancy) as s:
            seen["orm"] = set(s.scalars(select(Widget.name).where(Widget.project_id.in_([PB, PC]))))
            seen["raw"] = s.execute(
                text("SELECT count(*) FROM test_widget WHERE project_id = :pb OR tenant_id = :t2"),
                others,
            ).scalar_one()
            seen["gadgets"] = set(
                s.execute(text("SELECT label FROM test_gadget WHERE tenant_id = :t2"), others)
                .scalars()
                .all()
            )
            seen["renamed"] = s.execute(
                text("UPDATE test_widget SET name = 'x' WHERE project_id = :pb OR tenant_id = :t2"),
                others,
            ).rowcount
        with job.db.unit_of_work(job.tenancy) as s:  # a write into project B is refused
            s.execute(Widget.__table__.insert().values(**_raw_row(PB), name="smuggled"))

    job = _run_through_saq(jobs_db, monkeypatch, JobKind("rls.snoop", "batch", snoop), CTX_A)

    assert seen == {"orm": set(), "raw": 0, "gadgets": set(), "renamed": 0}
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.startswith("Internal error (")  # the RLS violation, sanitised
    with jobs_db.unit_of_work(CTX_B) as s:
        names = set(s.execute(text("SELECT name FROM test_widget")).scalars())
    assert names == {"b1", "b2", "b3", "t1-library"}
    with jobs_db.unit_of_work(CTX_C) as s:
        assert set(s.execute(text("SELECT name FROM test_widget")).scalars()) == {"c1"}


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
                "test_unprotected: policy allowed_projects_read_policy missing",
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
