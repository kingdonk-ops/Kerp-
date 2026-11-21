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
from app.core.mail import service as mail_service
from app.core.mail.models import MailSend
from app.core.mail.service import send_mail
from app.core.tenancy.context import TenancyContext
from app.modules.jobs import mail as jobs_mail
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
    crons = {
        # Each cron function is wrapped by ``scheduler.tracked`` (T-0218) under its own name.
        w.queue.name: [(c.function.__wrapped__, c.cron, c.timeout) for c in w.cron_jobs]  # type: ignore[attr-defined]
        for w in workers
    }
    assert crons == {
        "t:interactive": [],
        "t:batch": [
            (scheduler.sweep_cron, scheduler.SWEEP_CRON, scheduler.CRON_TIMEOUT_S),
            (scheduler.hourly_project_jobs, scheduler.HOURLY_CRON, scheduler.CRON_TIMEOUT_S),
            (scheduler.expire_mail_cron, scheduler.MAIL_EXPIRY_CRON, scheduler.CRON_TIMEOUT_S),
        ],
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


def test_the_jobs_module_declares_its_dependency_on_projects() -> None:
    from app.core.module_loader.loader import discover_modules

    assert "projects" in discover_modules().get("jobs").depends_on


def test_the_hourly_cron_queues_every_scheduled_kind_once_per_live_project(
    db: Database,
) -> None:
    registry = JobRegistry()
    hourly = registry.add(JobKind("demo.hourly", "batch", _noop))
    registry.add_hourly_project_job(hourly.kind)
    registry.add(JobKind("demo.unscheduled", "batch", _noop))
    a, b = _project(db, TENANT, "HR1"), _project(db, TENANT, "HR2")
    _delete(db, TENANT, _project(db, TENANT, "HR3"))
    dispatched: list[JobRef] = []
    service.set_dispatcher(dispatched.extend)
    try:
        ctx: Any = {
            "db": db,
            "registry": registry,
            "settings": Settings(environment="test", default_tenant_id=TENANT),
        }
        asyncio.run(scheduler.hourly_project_jobs(ctx))
    finally:
        service.set_dispatcher(None)
    assert sorted((r.project_id, r.queue) for r in dispatched) == sorted(
        [(a, "batch"), (b, "batch")]
    )
    for project in (a, b):
        with db.unit_of_work(TenancyContext(tenant_id=TENANT, project_id=project)) as s:
            rows = s.query(Job).filter(Job.project_id == project).all()
            assert [(r.kind, r.source) for r in rows] == [("demo.hourly", "system")]


def _hourly_ctx(db: Database, registry: JobRegistry, tenant: uuid.UUID | None = TENANT) -> Any:
    return {
        "db": db,
        "registry": registry,
        "settings": Settings(environment="test", default_tenant_id=tenant),
    }


def test_one_failing_project_does_not_skip_the_others(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = JobRegistry()
    registry.add_hourly_project_job(registry.add(JobKind("demo.hourly", "batch", _noop)).kind)
    a, b = _project(db, TENANT, "HF1"), _project(db, TENANT, "HF2")
    real = scheduler._queue_hourly
    queued: list[uuid.UUID | None] = []

    def flaky(db_: Database, scope: TenancyContext, *args: Any) -> int:
        if scope.project_id == min(a, b):
            raise RuntimeError("synthetic failure in one project")
        queued.append(scope.project_id)
        return real(db_, scope, *args)

    monkeypatch.setattr(scheduler, "_queue_hourly", flaky)
    asyncio.run(scheduler.hourly_project_jobs(_hourly_ctx(db, registry)))
    assert queued == [max(a, b)]


def test_an_unregistered_hourly_kind_is_skipped_not_fatal(db: Database) -> None:
    registry = JobRegistry()
    registry.add_hourly_project_job("demo.missing")  # scheduled, never registered
    registry.add_hourly_project_job(registry.add(JobKind("demo.hourly", "batch", _noop)).kind)
    project = _project(db, TENANT, "HU1")
    dispatched: list[JobRef] = []
    service.set_dispatcher(dispatched.extend)
    try:
        asyncio.run(scheduler.hourly_project_jobs(_hourly_ctx(db, registry)))
    finally:
        service.set_dispatcher(None)
    assert [(r.project_id, r.queue) for r in dispatched] == [(project, "batch")]


@pytest.mark.parametrize("case", ["no deployment tenant", "nothing scheduled"])
def test_the_hourly_cron_does_nothing_without_a_tenant_or_kinds(db: Database, case: str) -> None:
    registry = JobRegistry()
    if case == "no deployment tenant":
        registry.add_hourly_project_job(registry.add(JobKind("demo.hourly", "batch", _noop)).kind)
    _project(db, TENANT, "HN1")
    dispatched: list[JobRef] = []
    service.set_dispatcher(dispatched.extend)
    try:
        tenant = None if case == "no deployment tenant" else TENANT
        asyncio.run(scheduler.hourly_project_jobs(_hourly_ctx(db, registry, tenant)))
    finally:
        service.set_dispatcher(None)
    assert dispatched == []


def test_job_status_is_none_outside_the_sessions_scope(db: Database) -> None:
    here, there = _project(db, TENANT, "JS1"), _project(db, TENANT, "JS2")
    job_id = _stuck_job(db, TenancyContext(tenant_id=TENANT, project_id=here))
    with db.unit_of_work(TenancyContext(tenant_id=TENANT, project_id=here)) as s:
        assert service.job_status(s, job_id) == "queued"
    with db.unit_of_work(TenancyContext(tenant_id=TENANT, project_id=there)) as s:
        assert service.job_status(s, job_id) is None
    with db.unit_of_work(TenancyContext(tenant_id=TENANT, project_id=here)) as s:
        assert service.job_status(s, uuid.uuid4()) is None


# --- mail stuck queued past its payload lifetime (T-0346, TD-336) -----------------------------

_MAGIC_LINK = {"token": "tok-SYNTHETIC-expiry-0123456789", "expires_minutes": 15}


def _mail(db: Database, scope: TenancyContext, age: timedelta, status: str = "queued") -> uuid.UUID:
    """A send-log row whose enqueue was lost (no mail queue installed), ``age`` old."""
    with db.unit_of_work(scope) as s:
        send_id = send_mail(s, "auth.magic_link", "someone@example.test", _MAGIC_LINK)
    with db.unit_of_work(scope) as s:
        s.execute(
            update(MailSend)
            .where(MailSend.id == send_id, MailSend.tenant_id == scope.tenant_id)
            .values(created_at=utcnow() - age, status=status)
        )
    return send_id


def _mail_status(db: Database, scope: TenancyContext, send_id: uuid.UUID) -> tuple[str, str | None]:
    with db.unit_of_work(scope) as s:
        row = s.get(MailSend, send_id)
        assert row is not None
        return row.status, row.error_code


def test_the_mail_expiry_cron_fails_sends_stuck_queued_past_the_payload_lifetime(
    db: Database,
) -> None:
    tenant = TenancyContext(tenant_id=TENANT)
    project = TenancyContext(tenant_id=TENANT, project_id=_project(db, TENANT, "MLX"))
    past = scheduler.MAIL_EXPIRE_AFTER + timedelta(minutes=5)
    lost_tenant = _mail(db, tenant, past)
    lost_project = _mail(db, project, past)
    young = _mail(db, tenant, timedelta(hours=2))  # its payload may still be in Redis
    sent = _mail(db, tenant, past, status="sent")
    ctx: Any = {"db": db, "settings": Settings(environment="test", default_tenant_id=TENANT)}
    asyncio.run(scheduler.expire_mail_cron(ctx))
    assert _mail_status(db, tenant, lost_tenant) == ("failed", "payload_expired")
    assert _mail_status(db, project, lost_project) == ("failed", "payload_expired")
    assert _mail_status(db, tenant, young) == ("queued", None)
    assert _mail_status(db, tenant, sent) == ("sent", None)
    assert timedelta(seconds=jobs_mail.PAYLOAD_TTL_S) < scheduler.MAIL_EXPIRE_AFTER


def test_the_mail_expiry_cron_does_nothing_without_a_deployment_tenant(db: Database) -> None:
    tenant = TenancyContext(tenant_id=TENANT)
    lost = _mail(db, tenant, scheduler.MAIL_EXPIRE_AFTER + timedelta(minutes=5))
    ctx: Any = {"db": db, "settings": Settings(environment="test", default_tenant_id=None)}
    asyncio.run(scheduler.expire_mail_cron(ctx))
    assert _mail_status(db, tenant, lost) == ("queued", None)


def test_one_scope_failing_does_not_stop_the_mail_expiry_of_the_others(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = TenancyContext(tenant_id=TENANT)
    project = TenancyContext(tenant_id=TENANT, project_id=_project(db, TENANT, "MLY"))
    lost = _mail(db, project, scheduler.MAIL_EXPIRE_AFTER + timedelta(minutes=5))
    real = mail_service.expire_queued

    def flaky(db: Database, scope: TenancyContext, *args: Any) -> int:
        if scope.project_id is None:
            raise RuntimeError("database went away")
        return real(db, scope, *args)

    monkeypatch.setattr(mail_service, "expire_queued", flaky)
    cutoff = utcnow() - scheduler.MAIL_EXPIRE_AFTER
    assert scheduler._expire_mail(db, [tenant, project], cutoff) == 1
    assert _mail_status(db, project, lost) == ("failed", "payload_expired")
