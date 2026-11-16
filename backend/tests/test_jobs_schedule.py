"""The scheduled jobs sweep (T-0214, TD-155): the deployment's scopes are listed under RLS and
``service.sweep`` runs over them from an SAQ cron job on the batch worker.

Runs on the real migration (SQLite here, PostgreSQL under RLS in CI). All data synthetic.
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update

from app.core.config import Settings
from app.core.db import Database, utcnow
from app.core.tenancy.context import TenancyContext
from app.modules.jobs import scheduler, service
from app.modules.jobs.broker import JobRef, SaqBroker
from app.modules.jobs.models import Job
from app.modules.jobs.registry import JobKind, JobRegistry
from app.modules.jobs.worker import build_workers
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database

TENANT = uuid.UUID("00000000-0000-4000-8000-0000000051e1")
OTHER_TENANT = uuid.UUID("00000000-0000-4000-8000-0000000051e2")


async def _noop(_job: Any) -> None:
    return None


REGISTRY = JobRegistry()
KIND = REGISTRY.add(JobKind("demo.swept", "batch", _noop))


class RecordingBroker:
    """A ``JobBroker`` that keeps what it was given (stands in for SAQ)."""

    def __init__(self) -> None:
        self.enqueued: list[JobRef] = []
        self.known: set[str] = set()

    async def enqueue(self, ref: JobRef) -> None:
        self.enqueued.append(ref)
        self.known.add(ref.key)

    async def has_job(self, queue: str, key: str) -> bool:
        return key in self.known


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def _project(db: Database, tenant: uuid.UUID, code: str) -> uuid.UUID:
    with db.unit_of_work(TenancyContext(tenant_id=tenant, is_system_admin=True)) as s:
        return projects.create_project(s, ProjectCreate(code=code, name=f"Synthetic {code}")).id


def _delete(db: Database, tenant: uuid.UUID, project: uuid.UUID) -> None:
    with db.unit_of_work(TenancyContext(tenant_id=tenant, is_system_admin=True)) as s:
        projects.delete_project(s, project)


def _stuck_job(db: Database, scope: TenancyContext) -> uuid.UUID:
    """A ``queued`` row whose enqueue never happened, old enough for the sweep."""
    service.set_dispatcher(None)
    with db.unit_of_work(scope) as s:
        job_id = service.create_job(s, KIND).id
    with db.unit_of_work(scope) as s:
        s.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.tenant_id == scope.tenant_id,
                Job.project_id.is_(None)
                if scope.project_id is None
                else Job.project_id == scope.project_id,
            )
            .values(created_at=utcnow() - timedelta(minutes=10))
        )
    return job_id


def test_live_project_ids_lists_the_tenants_live_projects_only(db: Database) -> None:
    live = _project(db, TENANT, "LIVE")
    gone = _project(db, TENANT, "GONE")
    _delete(db, TENANT, gone)
    _project(db, OTHER_TENANT, "ELSEWHERE")
    with db.unit_of_work(TenancyContext(tenant_id=TENANT, is_system_admin=True)) as s:
        assert projects.live_project_ids(s) == [live]


def test_deployment_scopes_are_the_tenant_level_scope_and_every_live_project(
    db: Database,
) -> None:
    a, b = _project(db, TENANT, "AAA"), _project(db, TENANT, "BBB")
    _delete(db, TENANT, _project(db, TENANT, "DEL"))
    scopes = scheduler.deployment_scopes(db, TENANT)
    assert scopes[0] == TenancyContext(tenant_id=TENANT)
    assert set(scopes[1:]) == {
        TenancyContext(tenant_id=TENANT, project_id=a),
        TenancyContext(tenant_id=TENANT, project_id=b),
    }
    # Only the scope itself: no admin flag, user or read list leaks into the sweep's sessions.
    assert all(not s.is_system_admin and s.user_id is None for s in scopes)


def test_the_sweep_cron_re_enqueues_stuck_rows_of_every_scope_once(db: Database) -> None:
    project = _project(db, TENANT, "SWP")
    in_project = _stuck_job(db, TenancyContext(tenant_id=TENANT, project_id=project))
    tenant_level = _stuck_job(db, TenancyContext(tenant_id=TENANT))
    broker = RecordingBroker()
    ctx: Any = {
        "db": db,
        "registry": REGISTRY,
        "broker": broker,
        "settings": Settings(environment="test", default_tenant_id=TENANT),
    }
    asyncio.run(scheduler.sweep_cron(ctx))
    assert {r.job_id for r in broker.enqueued} == {in_project, tenant_level}
    asyncio.run(scheduler.sweep_cron(ctx))  # SAQ knows them now: nothing new
    assert len(broker.enqueued) == 2


def test_the_sweep_cron_does_nothing_without_a_deployment_tenant(db: Database) -> None:
    broker = RecordingBroker()
    ctx: Any = {
        "db": db,
        "registry": REGISTRY,
        "broker": broker,
        "settings": Settings(environment="test", default_tenant_id=None),
    }
    asyncio.run(scheduler.sweep_cron(ctx))
    assert broker.enqueued == []


def test_the_batch_worker_runs_the_sweep_every_five_minutes(db: Database) -> None:
    settings = Settings(environment="test")
    broker = SaqBroker("redis://localhost:6379/0", prefix="t")
    workers = build_workers(settings, db=db, registry=REGISTRY, broker=broker)
    crons = {w.queue.name: [(c.function, c.cron) for c in w.cron_jobs] for w in workers}
    assert crons == {
        "t:interactive": [],
        "t:batch": [(scheduler.sweep_cron, scheduler.SWEEP_CRON)],
    }
    assert scheduler.SWEEP_CRON == "*/5 * * * *"
    ctx: Any = {}
    assert workers[1].startup is not None
    asyncio.run(workers[1].startup[0](ctx))
    assert (ctx["db"], ctx["registry"], ctx["broker"], ctx["settings"]) == (
        db,
        REGISTRY,
        broker,
        settings,
    )
