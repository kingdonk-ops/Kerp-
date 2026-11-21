"""system_admin global project visibility on the real tables under PostgreSQL RLS (ADR 0016,
T-0274; diff §4.1, edge spec §6).

Per table: every table under the standard project policy carries, at ``alembic upgrade head``,
exactly the ``project_isolation_policy`` (and unchanged read policies) the live helper emits,
the rule ``test_rls_bypass.py`` exercises on the scratch tables. Then real records (audit
entries, jobs) written in projects A and B and in tenant T2: a system_admin without any
membership reads and writes A and B, a non-admin member of A does not see B, and T2 stays
invisible. Raw SQL skips the ORM scope, so only RLS decides. Needs real PostgreSQL and the
``NOSUPERUSER NOBYPASSRLS`` role ``kerp_app`` (CI's PostgreSQL step). All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.audit import service as audit
from app.core.db import Base, Database, import_all_models
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import (
    ALLOWED_READ_POLICY_NAME,
    POLICY_NAME,
    READ_POLICY_NAME,
    bind_connection,
    rls_coverage_problems,
    rls_enforcement_problems,
    rls_tables,
    table_policy,
)
from app.modules.jobs import service as jobs
from app.modules.jobs.registry import JobContext, JobKind
from tests.audit_support import migrated_database
from tests.db_support import ON_POSTGRES
from tests.tenancy_support import (
    ADMIN_A,
    ADMIN_T1,
    ADMIN_T2,
    CTX_A,
    CTX_B,
    CTX_C,
    PA,
    PB,
    PC,
    T1,
    T2,
    seeded_database,
)
from tests.test_rls_bypass import RLS_ERROR

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)"
)

import_all_models()
STANDARD_TABLES = [
    name for name in rls_tables(Base.metadata) if table_policy(Base.metadata.tables[name]) is None
]
SCRATCH = "test_widget"  # created by the live helper (tests/tenancy_support.py)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    """The migrated schema at head plus the scratch tables under the live helper's policies."""
    migrated = migrated_database(tmp_path)
    database = next(migrated)
    try:
        assert rls_enforcement_problems(database.engine) == []  # really kerp_app
        yield from seeded_database(tmp_path)
    finally:
        next(migrated, None)


def _policy(db: Database, table: str, name: str) -> tuple[str | None, str | None, str]:
    with db.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT pg_get_expr(p.polqual, p.polrelid), pg_get_expr(p.polwithcheck,"
                " p.polrelid), p.polcmd::text FROM pg_policy p JOIN pg_class c"
                " ON c.oid = p.polrelid WHERE c.relname = :t AND p.polname = :n"
            ),
            {"t": table, "n": name},
        ).one()
    return row[0], row[1], row[2]


def test_the_standard_tables_are_the_ones_migration_0035_rewrote() -> None:
    assert len(STANDARD_TABLES) >= 18
    assert {"audit_ledger", "oe_jobs_job", "oe_uploads_file", "tenants"} <= set(STANDARD_TABLES)


@pytest.mark.parametrize("table", STANDARD_TABLES)
def test_every_project_table_has_exactly_the_system_admin_rule(db: Database, table: str) -> None:
    isolation = _policy(db, table, POLICY_NAME)
    assert isolation == _policy(db, SCRATCH, POLICY_NAME)
    using, check, command = isolation
    assert command == "*"  # FOR ALL
    assert using == check
    assert using is not None
    assert "app.is_system_admin" in using
    # The read-only policies are untouched: no admin disjunct there.
    for name in (READ_POLICY_NAME, ALLOWED_READ_POLICY_NAME):
        read = _policy(db, table, name)
        assert read == _policy(db, SCRATCH, name)
        assert "is_system_admin" not in (read[0] or "")


def test_the_migrated_schema_still_has_rls_on_every_model_table(db: Database) -> None:
    with db.engine.connect() as conn:
        assert rls_coverage_problems(conn, Base.metadata) == []
    assert rls_enforcement_problems(db.engine) == []


# --- real records -----------------------------------------------------------------------------


def _rows(db: Database, ctx: TenancyContext | None, sql: str, **params: Any) -> list[Any]:
    with db.engine.begin() as conn:
        bind_connection(conn, ctx)
        return list(conn.execute(text(sql), params).all())


async def _noop(job: JobContext) -> dict[str, Any]:
    return {}


KIND = JobKind("rls.system_admin_probe", "interactive", _noop)


def _seed_records(db: Database) -> dict[uuid.UUID | None, uuid.UUID]:
    """One audit entry and one job per scope (A, B, tenant-level T1, C of T2); job ids back."""
    job_ids: dict[uuid.UUID | None, uuid.UUID] = {}
    for ctx in (CTX_A, CTX_B, TenancyContext(tenant_id=T1), CTX_C):
        with db.unit_of_work(ctx) as s:
            audit.append(s, f"widget:{uuid.uuid4()}", {"scope": str(ctx.project_id)})
            job_ids[ctx.project_id] = jobs.create_job(s, KIND, {}).id
    return job_ids


_PER_PROJECT = {
    table: f"SELECT project_id, count(*) FROM {table} GROUP BY project_id"
    for table in ("audit_ledger", "oe_jobs_job")
}


@pytest.mark.parametrize("table", sorted(_PER_PROJECT))
def test_a_system_admin_without_membership_reads_every_project_of_its_tenant(
    db: Database, table: str
) -> None:
    _seed_records(db)
    query = _PER_PROJECT[table]
    for admin in (ADMIN_A, ADMIN_T1):
        assert dict(_rows(db, admin, query)) == {PA: 1, PB: 1, None: 1}
    # A non-admin member of A: A and the tenant level only; B and T2 do not exist for it.
    assert dict(_rows(db, CTX_A, query)) == {PA: 1, None: 1}
    # The admin of T2 reads T2 only; T1's admin nothing of T2; unbound nothing at all.
    assert dict(_rows(db, ADMIN_T2, query)) == {PC: 1}
    t2 = f"SELECT count(*) FROM {table} WHERE tenant_id = :t2"
    assert _rows(db, ADMIN_T1, t2, t2=T2) == [(0,)]
    assert _rows(db, None, f"SELECT count(*) FROM {table}") == [(0,)]


def test_a_system_admin_without_membership_writes_another_projects_records(
    db: Database,
) -> None:
    job_ids = _seed_records(db)
    # The ORM path (service + get_or_404) and raw SQL alike, from a session bound to A.
    with db.unit_of_work(ADMIN_A) as s:
        jobs.get_job(s, job_ids[PB]).status = "failed"
    in_b = "SELECT status FROM oe_jobs_job WHERE project_id = :pb"
    assert _rows(db, CTX_B, in_b, pb=PB) == [("failed",)]
    rename = "UPDATE oe_jobs_job SET kind = 'rls.renamed' WHERE project_id = :p"
    with db.engine.begin() as conn:
        bind_connection(conn, ADMIN_A)
        assert conn.execute(text(rename), {"p": PB}).rowcount == 1
        # Tenant-level rows stay read-only to a project-bound admin session; T2 is invisible.
        assert conn.execute(text(rename.replace("= :p", "IS NULL"))).rowcount == 0
        assert conn.execute(text(rename), {"p": PC}).rowcount == 0
    # A non-admin member of A writes nothing of B.
    with db.engine.begin() as conn:
        bind_connection(conn, CTX_A)
        assert conn.execute(text(rename), {"p": PB}).rowcount == 0
    # An admin appends to B's audit chain from a B-bound session (chains stay per scope).
    admin_b = TenancyContext(tenant_id=T1, project_id=PB, is_system_admin=True)
    with db.unit_of_work(admin_b) as s:
        audit.append(s, "admin:probe", {"note": "synthetic"})
    b_entries = "SELECT count(*) FROM audit_ledger WHERE project_id = :pb"
    assert _rows(db, CTX_B, b_entries, pb=PB) == [(2,)]
    # A T1 admin session cannot create a T2 record.
    move = text("UPDATE oe_jobs_job SET tenant_id = :t2 WHERE project_id = :pa")
    with pytest.raises(DBAPIError, match=RLS_ERROR), db.unit_of_work(ADMIN_T1) as s:
        s.execute(move, {"t2": T2, "pa": PA})
    assert dict(_rows(db, ADMIN_T2, _PER_PROJECT["oe_jobs_job"])) == {PC: 1}
