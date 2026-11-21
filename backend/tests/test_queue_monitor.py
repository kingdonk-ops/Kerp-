"""System console queue monitor (T-0218, absorbed T-0220; diff §4.7).

``/api/v1/admin/system/queues...``: ``system_admin`` only (403 for everyone else, a PER-09 of
every project included), never a job's payload, result or kwargs, audited retry and abort.
SAQ is a fake ``QueueMonitor`` here; ``test_queue_monitor_saq.py`` runs the real one on Redis.
Runs on SQLite and, in CI, on PostgreSQL under RLS. All data is synthetic.
"""

import asyncio
import uuid
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount

from app.core.audit.service import chain_entries
from app.core.auth.access import SYSTEM_ADMIN, Persona
from app.core.db import Database, utcnow
from app.core.tenancy.context import TenancyContext
from app.modules.admin import service as admin
from app.modules.jobs import scheduler
from app.modules.jobs import service as jobs
from app.modules.jobs import worker as worker_module
from app.modules.jobs.broker import CronRun, JobRef, QueueCounts
from app.modules.jobs.models import Job
from app.modules.jobs.registry import CRON_SCHEDULES, JobContext, JobKind, JobRegistry, QueueName
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import (
    ALICE,
    BOB,
    PASSWORD,
    T1,
    T2,
    auth_app,
    grant_persona,
    make_settings,
)
from tests.auth_support import seed_user as _seed_user
from tests.db_support import database_url, seed_tenants

SYSTEM = "/api/v1/admin/system"
QUEUES = f"{SYSTEM}/queues"
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
CAROL = "carol@example.test"
# A synthetic secret in a job's payload and result: it must never reach a response.
SECRET = "synthetic-secret-7f3a9c"


class FakeMonitor:
    """A ``QueueMonitor`` without Redis: fixed counts, recorded aborts and cron runs."""

    def __init__(self) -> None:
        self.aborted: list[tuple[str, str, str]] = []
        self.runs: dict[str, CronRun] = {}
        self.upcoming: dict[str, datetime] = {}
        self.oldest = utcnow() - timedelta(seconds=90)

    async def counts(self, queue: QueueName) -> QueueCounts:
        if queue == "interactive":
            return QueueCounts(queued=3, active=1, scheduled=2, oldest_queued_at=self.oldest)
        return QueueCounts(queued=0, active=0, scheduled=1, oldest_queued_at=None)

    async def abort(self, queue: QueueName, key: str, reason: str) -> bool:
        self.aborted.append((queue, key, reason))
        return True

    async def record_cron_run(self, name: str, run: CronRun) -> None:
        self.runs[name] = run

    async def cron_runs(self, names: Iterable[str]) -> dict[str, CronRun]:
        return {n: self.runs[n] for n in names if n in self.runs}

    async def next_cron_runs(self, names: Iterable[str]) -> dict[str, datetime]:
        return {n: self.upcoming[n] for n in names if n in self.upcoming}


async def _noop(job: JobContext) -> dict[str, Any]:
    return {}


KIND = JobKind("uploads.monitor_probe", "interactive", _noop)


def _project(db: Database, code: str) -> uuid.UUID:
    with db.unit_of_work(ROOT) as s:
        return projects.create_project(s, ProjectCreate(code=code, name=f"Synthetic {code}")).id


class Api:
    """ALICE: System admin (and, until T-0274, a PER-01 reader of HOME so she reads its jobs).
    BOB: PER-09 of every project. CAROL: no grant at all."""

    def __init__(self, client: TestClient, db: Database) -> None:
        self.client, self.db = client, db
        self.users = {e: _seed_user(db, T1, e) for e in (ALICE, BOB, CAROL)}
        with db.unit_of_work(ROOT) as s:
            assert admin.grant(s, self.users[ALICE], SYSTEM_ADMIN, actor_id=None)
        self.home, self.away = _project(db, "HOME"), _project(db, "AWAY")
        for project in (self.home, self.away):
            grant_persona(db, T1, self.users[BOB], Persona.PROJECT_ADMINISTRATOR.value, project)
        grant_persona(db, T1, self.users[ALICE], Persona.OPERATIONS_MANAGEMENT.value, self.home)
        self._headers: dict[str, dict[str, str]] = {}

    def headers(self, email: str) -> dict[str, str]:
        if email not in self._headers:
            body = {"email": email, "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            self._headers[email] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._headers[email]

    def job(
        self,
        *,
        project: uuid.UUID | None = None,
        status: str = "failed",
        error: str | None = None,
        tenant: uuid.UUID = T1,
    ) -> uuid.UUID:
        """A job row with a secret in its payload and result, then in ``status``."""
        with self.db.unit_of_work(TenancyContext(tenant_id=tenant, project_id=project)) as s:
            job = jobs.create_job(s, KIND, {"file_id": "x", "token": SECRET})
            job.attempts = 2
            if status != "queued":
                job.status = status
            if status == "failed":
                job.error = error if error is not None else "Internal error (ConnectionError)"
                job.finished_at = utcnow()
                job.result = {"leak": SECRET}
            return job.id

    def row(self, job_id: uuid.UUID, project: uuid.UUID | None, tenant: uuid.UUID = T1) -> Job:
        with self.db.unit_of_work(TenancyContext(tenant_id=tenant, project_id=project)) as s:
            return jobs.get_job(s, job_id)

    def chain(self) -> list[dict[str, Any]]:
        with self.db.unit_of_work(ROOT) as s:
            return [e.payload for e in chain_entries(s, f"admin-system:{T1}")]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def dispatched() -> Iterator[list[JobRef]]:
    refs: list[JobRef] = []
    jobs.set_dispatcher(refs.extend)
    yield refs
    jobs.set_dispatcher(None)


@pytest.fixture
def monitor() -> Iterator[FakeMonitor]:
    fake = FakeMonitor()
    yield fake
    jobs.set_queue_monitor(None)


@pytest.fixture
def api(
    tmp_path: Path, db: Database, monitor: FakeMonitor, dispatched: list[JobRef]
) -> Iterator[Api]:
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        jobs.set_queue_monitor(monitor)  # after the lifespan, which installs none without Redis
        yield Api(client, db)


def _calls(job_id: uuid.UUID) -> list[tuple[str, str]]:
    return [
        ("GET", QUEUES),
        ("GET", f"{QUEUES}/failed"),
        ("POST", f"{QUEUES}/failed/{job_id}/retry"),
        ("POST", f"{QUEUES}/{job_id}/abort"),
    ]


# --- access ---------------------------------------------------------------------------------


def test_every_endpoint_needs_a_token(api: Api) -> None:
    codes = [api.client.request(m, u).status_code for m, u in _calls(uuid.uuid4())]
    assert codes == [401] * 4


@pytest.mark.parametrize("email", [BOB, CAROL])
def test_every_endpoint_is_forbidden_to_everyone_but_a_system_admin(api: Api, email: str) -> None:
    # BOB holds PER-09 of every project; CAROL holds nothing. Both get 403 on every endpoint,
    # for an existing failed job too, and nothing changes.
    job_id = api.job(project=api.home)
    for method, url in _calls(job_id):
        response = api.client.request(method, url, headers=api.headers(email))
        assert response.status_code == 403, (method, url, response.text)
        assert SECRET not in response.text
    assert api.row(job_id, api.home).status == "failed"
    assert api.chain() == []


def test_the_saq_web_ui_is_not_mounted(api: Api) -> None:
    app: Any = api.client.app
    for route in app.routes:
        assert not isinstance(route, Mount), route
        endpoint = getattr(route, "endpoint", None)
        assert not getattr(endpoint, "__module__", "").startswith("saq"), route
    for path in ("/saq", "/monitor", "/api/v1/admin/system/saq", "/queues"):
        assert api.client.get(path, headers=api.headers(ALICE)).status_code == 404


# --- overview -------------------------------------------------------------------------------


def test_the_overview_shows_counts_per_queue_and_the_cron_jobs(
    api: Api, monitor: FakeMonitor
) -> None:
    api.job(project=api.home)
    api.job()  # tenant-level
    started = datetime(2026, 11, 21, 3, 17, tzinfo=UTC)
    monitor.runs["hourly_project_jobs"] = CronRun(started, started + timedelta(seconds=4), "failed")
    monitor.upcoming["sweep_cron"] = datetime(2026, 11, 21, 3, 20, tzinfo=UTC)
    response = api.client.get(QUEUES, headers=api.headers(ALICE))
    assert response.status_code == 200, response.text
    body = response.json()
    interactive, batch = body["queues"]
    assert {k: v for k, v in interactive.items() if k != "oldest_queued_age_s"} == {
        "name": "interactive",
        "queued": 3,
        "active": 1,
        "scheduled": 2,
        "failed": 2,
    }
    assert 85 <= interactive["oldest_queued_age_s"] < 600
    assert batch == {
        "name": "batch",
        "queued": 0,
        "active": 0,
        "scheduled": 1,
        "failed": 0,
        "oldest_queued_age_s": None,
    }
    crons = {c["name"]: c for c in body["cron_jobs"]}
    assert set(crons) == set(CRON_SCHEDULES)
    assert crons["sweep_cron"]["schedule"] == "*/5 * * * *"
    assert crons["sweep_cron"]["next_run_at"].startswith("2026-11-21T03:20:00")
    assert crons["sweep_cron"]["last_run_at"] is None
    assert crons["hourly_project_jobs"]["last_outcome"] == "failed"
    assert crons["hourly_project_jobs"]["last_run_at"].startswith("2026-11-21T03:17:00")


def test_the_overview_is_503_without_a_queue(api: Api) -> None:
    jobs.set_queue_monitor(None)
    assert api.client.get(QUEUES, headers=api.headers(ALICE)).status_code == 503


# --- failed jobs ----------------------------------------------------------------------------


def test_failed_jobs_never_show_payloads_results_or_kwargs(api: Api) -> None:
    home_job = api.job(project=api.home)
    long_error = "Bad input " + "x" * 600
    tenant_job = api.job(error=long_error[:500])
    api.job(project=api.home, status="queued")  # not failed: not listed
    response = api.client.get(f"{QUEUES}/failed", headers=api.headers(ALICE))
    assert response.status_code == 200, response.text
    assert SECRET not in response.text
    for word in ("payload", "result", "kwargs", "tenant_id", "request_id", "file_id"):
        assert word not in response.text
    rows = {r["id"]: r for r in response.json()}
    assert set(rows) == {str(home_job), str(tenant_job)}
    home = rows[str(home_job)]
    assert {k: home[k] for k in ("kind", "queue", "project_code", "attempts", "error_class")} == {
        "kind": KIND.kind,
        "queue": "interactive",
        "project_code": "HOME",
        "attempts": 2,
        "error_class": "ConnectionError",
    }
    assert home["message"] == "Internal error (ConnectionError)"
    tenant = rows[str(tenant_job)]
    assert (tenant["project_code"], tenant["error_class"]) == (None, "JobError")
    assert len(tenant["message"]) <= 500


def test_error_classes_are_read_from_the_stored_error() -> None:
    assert jobs.error_class("Internal error (TimeoutError)") == "TimeoutError"
    assert jobs.error_class("Job timed out or was cancelled") == "Cancelled"
    assert jobs.error_class(jobs.ABORTED) == "Aborted"
    assert jobs.error_class("No file to verify") == "JobError"
    assert jobs.error_class(None) == "Unknown"


def test_the_failed_list_pages_and_bounds_its_limit(api: Api) -> None:
    for _ in range(3):
        api.job()
    alice = api.headers(ALICE)
    page = api.client.get(f"{QUEUES}/failed?limit=2&offset=1", headers=alice)
    assert len(page.json()) == 2
    too_many = api.client.get(f"{QUEUES}/failed?limit={jobs.MAX_FAILED_LIST + 1}", headers=alice)
    assert too_many.status_code == 422


# --- retry and abort ------------------------------------------------------------------------


def test_a_system_admin_retries_a_failed_job_audited(
    api: Api, dispatched: list[JobRef]
) -> None:
    job_id = api.job(project=api.home)
    dispatched.clear()
    response = api.client.post(f"{QUEUES}/failed/{job_id}/retry", headers=api.headers(ALICE))
    assert response.status_code == 202, response.text
    assert response.json() == {"id": str(job_id), "kind": KIND.kind, "status": "queued"}
    assert SECRET not in response.text
    row = api.row(job_id, api.home)
    assert (row.status, row.error, row.finished_at, row.attempts) == ("queued", None, None, 2)
    assert row.updated_by == api.users[ALICE]
    assert [(r.job_id, r.project_id) for r in dispatched] == [(job_id, api.home)]
    assert api.chain() == [
        {
            "event": "job_retried",
            "job_id": str(job_id),
            "kind": KIND.kind,
            "queue": "interactive",
            "project_id": str(api.home),
        }
    ]
    with api.db.unit_of_work(ROOT) as s:
        (entry,) = chain_entries(s, f"admin-system:{T1}")
        assert entry.created_by == api.users[ALICE]
    again = api.client.post(f"{QUEUES}/failed/{job_id}/retry", headers=api.headers(ALICE))
    assert again.status_code == 409  # queued now, not failed


def test_retry_and_abort_of_an_unknown_job_are_404(api: Api) -> None:
    alice = api.headers(ALICE)
    unknown = uuid.uuid4()
    assert api.client.post(f"{QUEUES}/failed/{unknown}/retry", headers=alice).status_code == 404
    assert api.client.post(f"{QUEUES}/{unknown}/abort", headers=alice).status_code == 404
    assert api.chain() == []


@pytest.mark.parametrize("state", ["queued", "running"])
def test_a_system_admin_aborts_a_queued_or_running_job_audited(
    api: Api, monitor: FakeMonitor, state: str
) -> None:
    job_id = api.job(project=api.home, status=state)
    response = api.client.post(f"{QUEUES}/{job_id}/abort", headers=api.headers(ALICE))
    assert response.status_code == 200, response.text
    assert response.json() == {"id": str(job_id), "kind": KIND.kind, "status": "failed"}
    row = api.row(job_id, api.home)
    assert (row.status, row.error) == ("failed", jobs.ABORTED)
    assert row.finished_at is not None
    assert monitor.aborted == [("interactive", str(job_id), jobs.ABORTED)]
    assert [(e["event"], e["kind"], e["job_id"]) for e in api.chain()] == [
        ("job_aborted", KIND.kind, str(job_id))
    ]


def test_a_finished_job_cannot_be_aborted(api: Api, monitor: FakeMonitor) -> None:
    job_id = api.job(project=api.home)
    response = api.client.post(f"{QUEUES}/{job_id}/abort", headers=api.headers(ALICE))
    assert response.status_code == 409
    assert monitor.aborted == []
    assert api.chain() == []


def test_an_abort_stands_when_saq_is_unreachable(api: Api, monitor: FakeMonitor) -> None:
    async def broken(queue: QueueName, key: str, reason: str) -> bool:
        raise ConnectionError("redis down")

    monitor.abort = broken  # type: ignore[method-assign]
    job_id = api.job(status="queued")
    response = api.client.post(f"{QUEUES}/{job_id}/abort", headers=api.headers(ALICE))
    assert response.status_code == 200
    assert api.row(job_id, None).status == "failed"


def test_the_worker_never_overwrites_an_aborted_job(db: Database) -> None:
    # The handler finishes after a System admin aborted its row: the abort stands.
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        job_id = jobs.create_job(s, KIND, {}).id
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        assert jobs.start_job(s, job_id) is not None
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        row = jobs.get_job(s, job_id)
        row.status, row.error = "failed", jobs.ABORTED
    worker_module._finish(db, TenancyContext(tenant_id=T1), job_id, {"done": True}, None)
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        row = jobs.get_job(s, job_id)
        assert (row.status, row.error, row.result) == ("failed", jobs.ABORTED, None)


# --- cron runs are recorded for the monitor ------------------------------------------------


def test_tracked_cron_jobs_record_their_runs_under_their_own_name() -> None:
    monitor = FakeMonitor()

    async def ok(ctx: Any) -> None:
        return None

    async def boom(ctx: Any) -> None:
        raise RuntimeError("synthetic")

    asyncio.run(scheduler.tracked(ok)({"broker": monitor}))
    with pytest.raises(RuntimeError):
        asyncio.run(scheduler.tracked(boom)({"broker": monitor}))
    assert scheduler.tracked(ok).__qualname__ == ok.__qualname__  # SAQ's cron key
    assert (monitor.runs["ok"].outcome, monitor.runs["boom"].outcome) == ("succeeded", "failed")
    assert monitor.runs["ok"].finished_at is not None


def test_a_cron_run_survives_a_failed_record() -> None:
    class Broken(FakeMonitor):
        async def record_cron_run(self, name: str, run: CronRun) -> None:
            raise ConnectionError("redis down")

    ran: list[bool] = []

    async def job(ctx: Any) -> None:
        ran.append(True)

    asyncio.run(scheduler.tracked(job)({"broker": Broken()}))
    asyncio.run(scheduler.tracked(job)({}))  # no broker in the context: nothing recorded
    assert ran == [True, True]


def test_cron_schedules_name_the_worker_functions() -> None:
    assert CRON_SCHEDULES[scheduler.sweep_cron.__name__] == scheduler.SWEEP_CRON
    assert CRON_SCHEDULES[scheduler.hourly_project_jobs.__name__] == scheduler.HOURLY_CRON


def test_the_retry_uses_the_kind_s_back_off(api: Api, dispatched: list[JobRef]) -> None:
    registry = JobRegistry()
    registry.add(JobKind(KIND.kind, "interactive", _noop, max_attempts=5, retry_delay_s=2))
    job_id = api.job()
    with api.db.unit_of_work(TenancyContext(tenant_id=T1, user_id=api.users[ALICE])) as s:
        jobs.retry_failed_job(s, api.db, job_id, actor_id=api.users[ALICE], registry=registry)
    assert [(r.max_attempts, r.retry_delay_s) for r in dispatched[-1:]] == [(5, 2)]


# --- invisible jobs: another project, another tenant (review round 1, CLAUDE.md §6) ---------


@pytest.mark.parametrize("where", ["away_project", "other_tenant"])
@pytest.mark.parametrize("state", ["failed", "queued"])
def test_a_job_the_admin_cannot_see_is_404_and_untouched(
    api: Api, dispatched: list[JobRef], monitor: FakeMonitor, where: str, state: str
) -> None:
    # AWAY is outside ALICE's read scope (until T-0274, TD-451); T2 is another tenant, never
    # visible. Retry and abort answer 404, change nothing, enqueue and audit nothing, and
    # the job is neither listed nor counted.
    if where == "other_tenant":
        seed_tenants(api.db, T2)
        tenant, project = T2, None
    else:
        tenant, project = T1, api.away
    job_id = api.job(project=project, status=state, tenant=tenant)
    before = api.row(job_id, project, tenant)
    dispatched.clear()
    alice = api.headers(ALICE)
    retry = api.client.post(f"{QUEUES}/failed/{job_id}/retry", headers=alice)
    abort = api.client.post(f"{QUEUES}/{job_id}/abort", headers=alice)
    assert (retry.status_code, abort.status_code) == (404, 404), (retry.text, abort.text)
    after = api.row(job_id, project, tenant)
    assert (after.status, after.error, after.updated_by, after.finished_at) == (
        before.status,
        before.error,
        before.updated_by,
        before.finished_at,
    )
    assert dispatched == []
    assert monitor.aborted == []
    assert api.chain() == []
    listed = api.client.get(f"{QUEUES}/failed", headers=alice).json()
    assert str(job_id) not in {r["id"] for r in listed}
    overview = api.client.get(QUEUES, headers=alice).json()
    assert [q["failed"] for q in overview["queues"]] == [0, 0]


def test_the_overview_is_503_when_the_queue_is_unreachable(
    api: Api, monitor: FakeMonitor
) -> None:
    async def broken(queue: QueueName) -> QueueCounts:
        raise ConnectionError("redis down")

    monitor.counts = broken  # type: ignore[method-assign]
    response = api.client.get(QUEUES, headers=api.headers(ALICE))
    assert response.status_code == 503
    assert "redis" not in response.text.lower()
