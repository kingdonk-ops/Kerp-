"""The four RLS-bypass contract tests (diff §4): SAQ worker, cross-module service call,
raw-SQL aggregation and DB migration (T-0007; the worker runs through real SAQ since
T-0060).

Since ADR 0016 (T-0274) each path also asserts the system_admin rule exactly: a session bound
with ``app.is_system_admin`` reads and writes the project rows of every project of its own
tenant without a membership, never a tenant-level row from a project-bound session and never
another tenant's row; a non-admin stays project-exact, an unbound session sees nothing, and a
worker job never inherits the flag of the session that enqueued it.

Each path skips the ORM scope in some way, so only PostgreSQL RLS stands between it and
another project's rows. These tests need real PostgreSQL and a role that is neither
SUPERUSER nor BYPASSRLS (CI's ``kerp_app``). RLS does not exist on SQLite. The SAQ worker
tests also need Redis (``KERP_REDIS_URL``), which CI's PostgreSQL step provides.
"""

import asyncio
import dataclasses
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
from app.core.tenancy.context import TenancyContext
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
from tests import pg_databases
from tests.audit_support import migrated_database
from tests.db_support import ON_POSTGRES, Gadget, Widget, postgres_url, seed_tenants
from tests.tenancy_support import (
    ADMIN_A,
    ADMIN_T1,
    ADMIN_T2,
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
    VISIBLE_TO_T1_ADMIN,
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
# task and the ``run_job`` wrapper, on one asyncio loop. The handler's scope comes only from
# the job row: its ``JobContext.tenancy`` and the PostgreSQL settings of its session are
# checked. (The tenant/project context variables an ambient scope could leak through were
# dead code and are gone, T-0349. The thread-based test this replaces proved nothing.)


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


@pytest.mark.skipif(not REDIS_URL, reason="needs Redis (KERP_REDIS_URL); CI's PG step (TD-052)")
def test_a_job_enqueued_by_a_system_admin_stays_project_exact(
    jobs_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The worker binds the job's own tenant/project only: a job enqueued from an admin session
    # does not inherit app.is_system_admin (ADR 0016: a job binds it only explicitly, after
    # re-checking the requester's grant at job start).
    seen: dict[str, object] = {}
    params = {"pb": PB}

    async def probe(job: JobContext) -> None:
        seen["tenancy"] = job.tenancy
        with job.db.unit_of_work(job.tenancy) as s:
            seen["flag"] = s.execute(
                text("SELECT NULLIF(current_setting('app.is_system_admin', true), '')")
            ).scalar_one()
            seen["names"] = set(s.scalars(select(Widget.name)))
            seen["b"] = s.execute(
                text("SELECT count(*) FROM test_widget WHERE project_id = :pb"), params
            ).scalar_one()
            seen["renamed"] = s.execute(
                text("UPDATE test_widget SET name = 'x' WHERE project_id = :pb"), params
            ).rowcount
        try:
            with job.db.unit_of_work(job.tenancy) as s:
                s.execute(Widget.__table__.insert().values(**_raw_row(PB), name="smuggled"))
        except DBAPIError as exc:
            seen["insert_refused"] = RLS_ERROR in str(exc)

    job = _run_through_saq(jobs_db, monkeypatch, JobKind("rls.admin", "batch", probe), ADMIN_A)

    assert (job.status, job.error) == ("succeeded", None)
    assert seen == {
        "tenancy": CTX_A,
        "flag": None,
        "names": VISIBLE_TO_A,
        "b": 0,
        "renamed": 0,
        "insert_refused": True,
    }


@pytest.mark.skipif(not REDIS_URL, reason="needs Redis (KERP_REDIS_URL); CI's PG step (TD-052)")
def test_a_job_that_binds_a_system_admin_spans_its_tenant_only(
    jobs_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A job that binds the flag itself (after re-checking the requester's grant, ADR 0016)
    # reads and writes every project of its tenant, and still nothing of tenant T2.
    seen: dict[str, object] = {}
    others = {"t2": T2}

    async def admin_job(job: JobContext) -> None:
        elevated = dataclasses.replace(job.tenancy, is_system_admin=True)
        with job.db.unit_of_work(elevated) as s:
            seen["names"] = set(s.scalars(select(Widget.name)))
            seen["t2"] = s.execute(
                text("SELECT count(*) FROM test_widget WHERE tenant_id = :t2"), others
            ).scalar_one()
            seen["t2_renamed"] = s.execute(
                text("UPDATE test_widget SET name = 'x' WHERE tenant_id = :t2"), others
            ).rowcount
            s.execute(Widget.__table__.insert().values(**_raw_row(PB), name="b-by-admin-job"))
        try:
            with job.db.unit_of_work(elevated) as s:
                row = {**_raw_row(PC), "tenant_id": T2}
                s.execute(Widget.__table__.insert().values(**row, name="smuggled"))
        except DBAPIError as exc:
            seen["t2_insert_refused"] = RLS_ERROR in str(exc)

    kind = JobKind("rls.elevated", "batch", admin_job)
    job = _run_through_saq(jobs_db, monkeypatch, kind, CTX_A)

    assert (job.status, job.error) == ("succeeded", None)
    assert seen == {
        "names": VISIBLE_TO_T1_ADMIN,
        "t2": 0,
        "t2_renamed": 0,
        "t2_insert_refused": True,
    }
    with jobs_db.unit_of_work(CTX_B) as s:
        assert "b-by-admin-job" in set(s.execute(text("SELECT name FROM test_widget")).scalars())
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
    engine = create_engine(postgres_url(), poolclass=QueuePool, pool_size=1, max_overflow=0)
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


def test_cross_module_call_of_a_system_admin_spans_its_tenants_projects(db: Database) -> None:
    with db.unit_of_work(ADMIN_A) as s:
        assert _beta_count_gadgets(s) == 3  # ga, gb1, gb2: never T2's gc
        assert sorted(s.scalars(select(Gadget.label))) == ["ga", "gb1", "gb2"]
        assert _beta_count_gadgets_own_session(db) == 0  # the side session is still unbound
        s.execute(Gadget.__table__.insert().values(**_raw_row(PB), label="gb-by-admin"))
    with db.unit_of_work(CTX_B) as s:
        assert sorted(s.scalars(select(Gadget.label))) == ["gb-by-admin", "gb1", "gb2"]
    # A non-admin member of A still gets project A only.
    with db.unit_of_work(CTX_A) as s:
        assert _beta_count_gadgets(s) == 1


def test_cross_module_write_of_a_system_admin_into_another_tenant_is_rejected(
    db: Database,
) -> None:
    row = {**_raw_row(PC), "tenant_id": T2}
    with pytest.raises(DBAPIError, match=RLS_ERROR), db.unit_of_work(ADMIN_A) as s:
        s.execute(Gadget.__table__.insert().values(**row, label="smuggled"))
    with db.unit_of_work(CTX_C) as s:
        assert s.scalars(select(Gadget.label)).all() == ["gc"]


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


def test_raw_sql_aggregation_of_a_system_admin_spans_its_tenant_only(db: Database) -> None:
    group = "SELECT project_id, count(*) FROM test_widget GROUP BY project_id"
    union = "SELECT tenant_id FROM test_widget UNION SELECT tenant_id FROM test_gadget"
    for ctx in (ADMIN_A, ADMIN_T1):
        with db.unit_of_work(ctx) as s:
            # Raw SQL has no soft-delete filter: a1, a2, a-deleted; b1-b3; the library row.
            assert dict(s.execute(text(group)).all()) == {PA: 3, PB: 3, None: 1}
            assert set(s.execute(text(union)).scalars()) == {T1}
    # The admin of T2 sees T2 only: the flag never crosses tenants.
    with db.unit_of_work(ADMIN_T2) as s:
        assert dict(s.execute(text(group)).all()) == {PC: 1}
    # The flag alone (no tenant bound) sees nothing; a non-admin still sees its project only.
    with db.engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.is_system_admin', 'true', true)"))
        assert conn.execute(text("SELECT count(*) FROM test_widget")).scalar_one() == 0
    with db.unit_of_work(CTX_A) as s:
        assert dict(s.execute(text(group)).all()) == {PA: 3, None: 1}


def test_raw_sql_of_a_system_admin_writes_project_rows_of_its_tenant_only(db: Database) -> None:
    params = {"pb": PB, "t2": T2}
    with db.engine.begin() as conn:
        bind_connection(conn, ADMIN_A)
        mark_b = text("UPDATE test_widget SET name = concat(name, '!') WHERE project_id = :pb")
        assert conn.execute(mark_b, params).rowcount == 3
        # Tenant-level rows: read-only to a project-bound session, admin or not.
        library = text("UPDATE test_widget SET name = 'x' WHERE project_id IS NULL")
        assert conn.execute(library).rowcount == 0
        assert conn.execute(text("DELETE FROM test_widget WHERE project_id IS NULL")).rowcount == 0
        # Another tenant: invisible, untouchable.
        other = text("UPDATE test_widget SET name = 'x' WHERE tenant_id = :t2")
        assert conn.execute(other, params).rowcount == 0
        drop_t2 = text("DELETE FROM test_widget WHERE tenant_id = :t2")
        assert conn.execute(drop_t2, params).rowcount == 0
        assert conn.execute(text("DELETE FROM test_widget WHERE name = 'b3!'")).rowcount == 1
    t2_row = {**_raw_row(PC), "tenant_id": T2}
    refused: list[tuple[Any, dict[str, object] | None]] = [
        (Widget.__table__.insert().values(**_raw_row(None), name="t1-smuggled"), None),
        (text("UPDATE test_widget SET project_id = NULL WHERE name = 'a1'"), None),
        (Widget.__table__.insert().values(**t2_row, name="smuggled"), None),
        (text("UPDATE test_widget SET tenant_id = :t2 WHERE name = 'a1'"), params),
    ]
    for statement, values in refused:
        with pytest.raises(DBAPIError, match=RLS_ERROR), db.unit_of_work(ADMIN_A) as s:
            s.execute(statement, values)
    with db.unit_of_work(CTX_B) as s:
        names = set(s.execute(text("SELECT name FROM test_widget")).scalars())
    assert names == {"b1!", "b2!", "t1-library"}
    with db.unit_of_work(CTX_C) as s:
        assert set(s.execute(text("SELECT name FROM test_widget")).scalars()) == {"c1"}
    # A tenant-level admin session keeps the tenant-level write rule (it may write them), and
    # moves a project row between projects of its tenant.
    move = text("UPDATE test_widget SET name = 'a1-moved', project_id = :pb WHERE name = 'a1'")
    with db.unit_of_work(ADMIN_T1) as s:
        s.execute(Widget.__table__.insert().values(**_raw_row(None), name="t1-shared"))
        s.execute(move, params)
    with db.unit_of_work(CTX_B) as s:
        names = set(s.execute(text("SELECT name FROM test_widget")).scalars())
    assert names == {"a1-moved", "b1!", "b2!", "t1-library", "t1-shared"}


# --- 4. DB migration ------------------------------------------------------------------------------


def test_runtime_role_cannot_bypass_rls(db: Database) -> None:
    assert rls_enforcement_problems(db.engine) == []


@pytest.mark.skipif(not OWNER_URL, reason="needs KERP_TEST_OWNER_URL (set in CI) (TD-052)")
def test_superuser_role_is_reported() -> None:
    # Role attributes are cluster-wide; with per-test databases use the maintenance database,
    # never the template source another worker may be copying (T-0083, PR #141 review).
    url = pg_databases.owner_url("postgres") if pg_databases.PER_TEST else OWNER_URL
    engine = create_engine(url)
    try:
        assert any("SUPERUSER" in p for p in rls_enforcement_problems(engine))
    finally:
        engine.dispose()


def test_migrated_schema_has_rls_on_every_model_table() -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=postgres_url(), configure_logger=False)
    command.upgrade(cfg, "head")
    engine = create_engine(postgres_url())
    try:
        import_all_models()
        with engine.connect() as conn:
            assert rls_coverage_problems(conn, Base.metadata) == []
    finally:
        engine.dispose()
        command.downgrade(cfg, "base")


_POLICIES = (
    "SELECT c.relname, p.polname, pg_get_expr(p.polqual, p.polrelid),"
    " pg_get_expr(p.polwithcheck, p.polrelid) FROM pg_policy p"
    " JOIN pg_class c ON c.oid = p.polrelid ORDER BY 1, 2"
)
_ADMIN_RECORDS = "SELECT count(*) FROM audit_ledger WHERE project_id = :pb"


def _policy_snapshot(url: str) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(text(_POLICIES))}
    finally:
        engine.dispose()


def _admin_reads_of_b(url: str) -> int:
    """How many of project B's audit entries a T1 system_admin session (no membership) reads."""
    database = Database(url)
    try:
        with database.unit_of_work(ADMIN_T1) as s:
            return int(s.execute(text(_ADMIN_RECORDS), {"pb": PB}).scalar_one())
    finally:
        database.dispose()


def test_migration_0035_widens_only_project_isolation_policy_and_downgrades() -> None:
    """ADR 0016 (T-0274): ``0035`` replaces ``project_isolation_policy`` on every table that has
    it, changes no other policy, and its downgrade restores the exact rule."""
    from app.core.audit import service as audit

    url = postgres_url()
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=url, configure_logger=False)
    # At 0035 itself, not head: later revisions create tables (0036, T-0101) that a downgrade
    # to 0034 would drop, and this test is about what 0035 alone changes.
    command.upgrade(cfg, "0035")
    writer = Database(url)
    try:
        seed_tenants(writer, T1)
        with writer.unit_of_work(CTX_B) as s:
            audit.append(s, f"widget:{uuid.uuid4()}", {"note": "synthetic"})
    finally:
        writer.dispose()
    head = _policy_snapshot(url)
    isolation = {t: e for (t, name), e in head.items() if name == "project_isolation_policy"}
    assert len(isolation) >= 18
    # One rule on every table, the same in USING and WITH CHECK, with the admin disjunct.
    assert len(set(isolation.values())) == 1
    (using, check), *_ = isolation.values()
    assert using == check
    assert using is not None
    assert "app.is_system_admin" in using
    assert "project_id IS NOT NULL" in using
    assert _admin_reads_of_b(url) == 1
    try:
        command.downgrade(cfg, "0034")
        before = _policy_snapshot(url)
        assert before.keys() == head.keys()
        changed = {key for key in head if head[key] != before[key]}
        assert changed == {(t, "project_isolation_policy") for t in isolation}
        assert all("is_system_admin" not in (before[key][0] or "") for key in changed)
        assert all(before[key][0] == before[key][1] for key in changed)
        assert _admin_reads_of_b(url) == 0  # the exact rule again: no membership, no rows
    finally:
        command.upgrade(cfg, "0035")
    assert _policy_snapshot(url) == head
    assert _admin_reads_of_b(url) == 1


def test_a_migration_that_forgets_rls_is_caught() -> None:
    md = MetaData()
    Table(
        "test_unprotected",
        md,
        Column("id", GUID(), primary_key=True),
        Column("tenant_id", GUID(), nullable=False),
        Column("project_id", GUID()),
    )
    engine = create_engine(postgres_url())
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
