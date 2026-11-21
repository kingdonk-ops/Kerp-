"""System console: deployment stats and health probes (T-0219, diff §4.7, ADR 0016).

``/api/v1/admin/system/stats``, ``.../stats/object-store/refresh`` and ``/system/health``:
``system_admin`` only (403 for everyone else, a PER-09 of every project included), numbers
only, counted under the caller's normal RLS (a System admin without any membership counts
every project of its tenant and nothing of another tenant), every call audited in
``admin-system:<tenant>``. Runs on SQLite and, in CI, on PostgreSQL under RLS. All data is
synthetic.
"""

import ast
import asyncio
import re
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.audit.service import chain_entries
from app.core.auth.access import SYSTEM_ADMIN, Persona
from app.core.auth.models import DEVICE_ACTIVE, DEVICE_REVOKED, Device
from app.core.db import Base, Database, utcnow
from app.core.stats.registry import STATS_REGISTRY, StatsRegistry, register_stats_provider
from app.core.tenancy.context import TenancyContext
from app.modules.admin import health
from app.modules.admin import jobs as admin_jobs
from app.modules.admin import service as admin
from app.modules.asset_management import service as assets
from app.modules.asset_management.schemas import LevelDefCreate, NodeCreate
from app.modules.jobs import service as jobs
from app.modules.jobs.broker import JobRef
from app.modules.jobs.models import Job
from app.modules.jobs.registry import JobContext
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.uploads import service as uploads
from app.modules.uploads.schemas import UploadCreate
from app.modules.uploads.storage import LocalStorage, ObjectInfo, set_storage
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, T1, T2, auth_app, grant_persona, make_settings
from tests.auth_support import seed_user as _seed_user
from tests.db_support import database_url, seed_tenants

BACKEND = Path(__file__).resolve().parents[1]
SYSTEM = "/api/v1/admin/system"
STATS = f"{SYSTEM}/stats"
REFRESH = f"{STATS}/object-store/refresh"
HEALTH = f"{SYSTEM}/health"
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
CAROL = "carol@example.test"
DAVE = "dave@example.test"
# Synthetic connection details: none of them may ever reach a response.
SECRET_HOST = "redis-synthetic-host.internal"
SECRET_PASSWORD = "synthetic-pass-41c9"
REDIS_URL = f"redis://kerp:{SECRET_PASSWORD}@{SECRET_HOST}:6379/0"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _project(db: Database, code: str, tenant: uuid.UUID = T1) -> uuid.UUID:
    with db.unit_of_work(TenancyContext(tenant_id=tenant, is_system_admin=True)) as s:
        return projects.create_project(s, ProjectCreate(code=code, name=f"Synthetic {code}")).id


def _device(db: Database, tenant: uuid.UUID, user_id: uuid.UUID, *, revoked: bool) -> None:
    with db.unit_of_work(TenancyContext(tenant_id=tenant)) as s:
        now = utcnow()
        s.add(
            Device(
                tenant_id=tenant,
                project_id=None,
                user_id=user_id,
                device_id=uuid.uuid4(),
                platform="android",
                enrolled_at=now,
                last_seen_at=now,
                status=DEVICE_REVOKED if revoked else DEVICE_ACTIVE,
                revoked_at=now if revoked else None,
                created_by=user_id,
            )
        )


def _nodes(db: Database, scope: TenancyContext, count: int) -> None:
    with db.unit_of_work(scope) as s:
        level = assets.create_level_def(
            s,
            LevelDefCreate.model_validate(
                {"key": "site", "depth": 0, "label_key": "level.site", "roles": ["grouping"]}
            ),
        )
        for n in range(count):
            assets.create_node(s, NodeCreate(level_def_id=level.id, code=f"SI-{n:03d}"))


def _upload(db: Database, scope: TenancyContext, storage: LocalStorage) -> None:
    body = {
        "module": "documents",
        "filename": "synthetic.png",
        "content_type": "image/png",
        "size_bytes": len(PNG),
        "sha256": "0" * 64,
    }
    with db.unit_of_work(scope) as s:
        uploads.presign_upload(
            s, storage, UploadCreate.model_validate(body), max_bytes=1024, expires_in=900
        )


class Api:
    """ALICE: System admin with no membership anywhere (ADR 0016). BOB: PER-09 of every
    project. CAROL: no grant at all. DAVE: deactivated. Tenant T2 has data of its own."""

    def __init__(self, client: TestClient, db: Database, storage: LocalStorage) -> None:
        self.client, self.db, self.storage = client, db, storage
        self.users = {e: _seed_user(db, T1, e) for e in (ALICE, BOB, CAROL)}
        self.users[DAVE] = _seed_user(db, T1, DAVE, is_active=False)
        with db.unit_of_work(ROOT) as s:
            assert admin.grant(s, self.users[ALICE], SYSTEM_ADMIN, actor_id=None)
        self.home, self.away = _project(db, "HOME"), _project(db, "AWAY")
        gone = _project(db, "GONE")
        with db.unit_of_work(ROOT) as s:
            projects.delete_project(s, gone, actor_id=None)
        for project in (self.home, self.away):
            grant_persona(db, T1, self.users[BOB], Persona.PROJECT_ADMINISTRATOR.value, project)
        self._headers: dict[str, dict[str, str]] = {}

    def scope(self, project: uuid.UUID, tenant: uuid.UUID = T1) -> TenancyContext:
        return TenancyContext(tenant_id=tenant, project_id=project)

    def headers(self, email: str) -> dict[str, str]:
        if email not in self._headers:
            body = {"email": email, "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            self._headers[email] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._headers[email]

    def chain(self) -> list[dict[str, Any]]:
        with self.db.unit_of_work(ROOT) as s:
            return [dict(e.payload) for e in chain_entries(s, f"admin-system:{T1}")]

    def actors(self) -> list[uuid.UUID | None]:
        with self.db.unit_of_work(ROOT) as s:
            return [e.created_by for e in chain_entries(s, f"admin-system:{T1}")]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[LocalStorage]:
    local = LocalStorage(tmp_path / "vault")
    local.root.mkdir(parents=True)
    set_storage(local)
    yield local
    set_storage(None)


@pytest.fixture
def dispatched() -> Iterator[list[JobRef]]:
    refs: list[JobRef] = []
    jobs.set_dispatcher(refs.extend)
    yield refs
    jobs.set_dispatcher(None)


@pytest.fixture
def api(
    tmp_path: Path, db: Database, storage: LocalStorage, dispatched: list[JobRef]
) -> Iterator[Api]:
    settings = make_settings(database_url=database_url(tmp_path))
    app, _runtime = auth_app(settings)
    with TestClient(app) as client:
        yield Api(client, db, storage)


def _calls() -> list[tuple[str, str]]:
    return [("GET", STATS), ("POST", REFRESH), ("GET", HEALTH)]


@pytest.fixture
def quick_probes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Every probe answers at once (no Redis, no S3, no clamd here); a test may change one."""
    answers: dict[str, Any] = {"redis": True, "object_store": True, "malware_scanner": True}

    def answer(name: str) -> bool | None:
        value = answers[name]
        return value() if callable(value) else value

    monkeypatch.setattr(health, "ping_redis", lambda url, timeout=2.0: answer("redis"))
    monkeypatch.setattr(health, "ping_object_store", lambda: answer("object_store"))
    monkeypatch.setattr(health, "ping_malware_scanner", lambda s: answer("malware_scanner"))
    return answers


# --- access ---------------------------------------------------------------------------------


def test_every_endpoint_needs_a_token(api: Api) -> None:
    assert [api.client.request(m, u).status_code for m, u in _calls()] == [401] * 3


@pytest.mark.parametrize("email", [BOB, CAROL])
def test_every_endpoint_is_forbidden_to_everyone_but_a_system_admin(
    api: Api, email: str, dispatched: list[JobRef], quick_probes: dict[str, Any]
) -> None:
    # BOB holds PER-09 of every project; CAROL holds nothing. Both get 403 everywhere, no job
    # is queued and nothing is audited.
    for method, url in _calls():
        response = api.client.request(method, url, headers=api.headers(email))
        assert response.status_code == 403, (method, url, response.text)
    assert dispatched == []
    assert api.chain() == []


# --- stats ----------------------------------------------------------------------------------


def _seed_counts(api: Api) -> None:
    db = api.db
    for email, revoked in ((ALICE, False), (BOB, False), (BOB, True)):
        _device(db, T1, api.users[email], revoked=revoked)
    _nodes(db, api.scope(api.home), 2)
    _nodes(db, api.scope(api.away), 3)
    _upload(db, api.scope(api.home), api.storage)
    _upload(db, api.scope(api.away), api.storage)
    with db.unit_of_work(api.scope(api.away)) as s:
        jobs.create_job(s, jobs.JOB_REGISTRY.get(admin_jobs.OBJECT_STORE_KIND))
    # Another tenant: a project, a user, a device, nodes, an upload. None of it may count.
    seed_tenants(db, T2)
    other = _project(db, "OTHER", T2)
    stranger = _seed_user(db, T2, "stranger@example.test")
    _device(db, T2, stranger, revoked=False)
    _nodes(db, api.scope(other, T2), 4)
    _upload(db, api.scope(other, T2), api.storage)


def test_a_system_admin_without_memberships_counts_every_project_of_its_tenant(
    api: Api,
) -> None:
    _seed_counts(api)
    response = api.client.get(STATS, headers=api.headers(ALICE))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["projects"] == {"active": 2, "soft_deleted": 1}
    assert body["users"] == {"active": 3, "deactivated": 1}
    assert body["devices"] == {"active": 2, "revoked": 1}
    modules = body["modules"]
    assert modules["asset_management"] == {"asset_nodes": 5, "level_defs": 2}
    assert modules["uploads"]["files_pending"] == 2
    assert modules["uploads"]["available_bytes"] == 0
    assert modules["jobs"]["jobs_queued"] == 1
    assert modules["teams"] == {"teams": 0}
    assert body["object_store"] == {"as_of": None, "refreshing": False, "storage_classes": []}


def test_stats_are_numbers_only_never_ids_names_or_rows(api: Api) -> None:
    _seed_counts(api)
    response = api.client.get(STATS, headers=api.headers(ALICE))
    assert response.status_code == 200
    text = response.text
    assert UUID_TEXT.search(text) is None
    for word in ("HOME", "AWAY", "GONE", "Synthetic", "example.test", "synthetic.png", "SI-0"):
        assert word not in text

    def leaves(value: Any) -> Iterator[Any]:
        if isinstance(value, dict):
            for item in value.values():
                yield from leaves(item)
        elif isinstance(value, list):
            for item in value:
                yield from leaves(item)
        else:
            yield value

    counts = list(leaves(response.json()["modules"]))
    assert counts
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in counts)


def test_every_stats_call_is_audited_in_the_console_chain(api: Api) -> None:
    for _ in range(2):
        assert api.client.get(STATS, headers=api.headers(ALICE)).status_code == 200
    assert [e["event"] for e in api.chain()] == ["stats_viewed", "stats_viewed"]
    assert api.actors() == [api.users[ALICE]] * 2


# --- the stats-provider registry ------------------------------------------------------------


def test_each_shipped_provider_is_registered_from_its_own_modules_service() -> None:
    assert {"asset_management", "jobs", "teams", "uploads"} <= set(STATS_REGISTRY.modules())
    for module in STATS_REGISTRY.modules():
        provider = STATS_REGISTRY._providers[module]
        assert provider.__module__ == f"app.modules.{module}.service", module


def test_the_registry_accepts_counts_only() -> None:
    registry = StatsRegistry()
    registry.add("good", lambda s: {"things": 3})
    with pytest.raises(ValueError, match="module name"):
        registry.add("Bad-Name", lambda s: {})
    bad_values: list[dict[str, Any]] = [
        {"ids": "00000000-0000-0000-0000-000000000000"},
        {"flag": True},
        {"negative": -1},
        {"rows": [1, 2]},
        {"Not A Name": 1},
    ]
    for values in bad_values:
        broken = StatsRegistry()
        broken.add("broken", lambda s, v=values: v)
        with pytest.raises(ValueError, match="is not a"):
            broken.collect(None)  # type: ignore[arg-type]
    assert registry.collect(None) == {"good": {"things": 3}}  # type: ignore[arg-type]
    assert registry.collect(None, modules=()) == {}  # type: ignore[arg-type]


def test_a_module_has_one_provider() -> None:
    registry = StatsRegistry()

    def first(session: Any) -> dict[str, int]:
        return {}

    def second(session: Any) -> dict[str, int]:
        return {}

    registry.add("same", first)
    registry.add("same", first)  # a re-imported module replaces its own provider
    with pytest.raises(ValueError, match="already has"):
        registry.add("same", second)


def test_only_loaded_modules_are_collected(api: Api) -> None:
    @register_stats_provider("not_loaded_module")
    def provider(session: Any) -> dict[str, int]:
        raise AssertionError("a provider of a module the app did not load must not run")

    try:
        response = api.client.get(STATS, headers=api.headers(ALICE))
        assert response.status_code == 200
        assert "not_loaded_module" not in response.json()["modules"]
    finally:
        STATS_REGISTRY._providers.pop("not_loaded_module")


def _imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            # ``from app.modules.x import service``: the imported name is the submodule.
            if node.module.count(".") == 2:
                names += [f"{node.module}.{alias.name}" for alias in node.names]
            else:
                names.append(node.module)
    return names


def test_the_admin_module_never_reads_another_modules_tables() -> None:
    # Import boundary (CLAUDE.md §6): the admin module reaches other modules through their
    # ``service.py`` (and the jobs module's registry seam), never their models or tables.
    admin_dir = BACKEND / "app" / "modules" / "admin"
    allowed = {"service", "registry", "schemas"}
    offenders: list[str] = []
    for path in sorted(admin_dir.glob("*.py")):
        for name in _imports(path):
            parts = name.split(".")
            if parts[:2] != ["app", "modules"] or len(parts) < 3 or parts[2] == "admin":
                continue
            if len(parts) < 4 or parts[3] not in allowed:
                offenders.append(f"{path.name}: {name}")
    assert offenders == []
    own = {t.name for t in Base.metadata.tables.values() if t.name.startswith("oe_admin_")}
    foreign = [
        t.name
        for t in Base.metadata.tables.values()
        if t.name.startswith("oe_") and t.name not in own
    ]
    for path in sorted(admin_dir.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert [t for t in foreign if t in source] == [], path.name


# --- object-store totals --------------------------------------------------------------------


class ListingStorage:
    """A storage whose listing carries S3 storage classes (``LocalStorage`` has none)."""

    def __init__(self, objects: list[ObjectInfo]) -> None:
        self.objects, self.prefixes = objects, list[str]()

    def list(self, prefix: str) -> Iterator[ObjectInfo]:
        self.prefixes.append(prefix)
        yield from (o for o in self.objects if o.key.startswith(prefix))


def test_object_store_totals_by_storage_class_for_the_tenants_keys_only() -> None:
    now = utcnow()
    p = uuid.uuid4()
    objects = [
        ObjectInfo(f"{T1}/{p}/documents/aa", 10, now, "STANDARD"),
        ObjectInfo(f"{T1}/{p}/documents/bb", 20, now, "GLACIER_IR"),
        ObjectInfo(f"{T1}/{p}/documents/cc", 30, now, "DEEP_ARCHIVE"),
        ObjectInfo(f"{T1}/{p}/documents/dd", 40, now, "GLACIER"),
        ObjectInfo(f"staging/{T1}/{p}/x", 5, now),
        ObjectInfo(f"quarantine/{T1}/{p}/y", 6, now),
        ObjectInfo(f"{T2}/{p}/documents/ee", 1000, now),
        ObjectInfo(f"staging/{T2}/{p}/z", 1000, now),
    ]
    store = ListingStorage(objects)
    totals = uploads.object_store_totals(T1, store)  # type: ignore[arg-type]
    assert totals == {
        "STANDARD": {"objects": 3, "bytes": 21},
        "GLACIER_IR": {"objects": 1, "bytes": 20},
        "DEEP_ARCHIVE": {"objects": 1, "bytes": 30},
        "other": {"objects": 1, "bytes": 40},
    }
    assert all(str(T1) in prefix for prefix in store.prefixes)


def test_the_batch_job_lists_the_store_of_its_own_tenant(storage: LocalStorage) -> None:
    kind = jobs.JOB_REGISTRY.get(admin_jobs.OBJECT_STORE_KIND)
    assert kind.queue == "batch"
    storage.write(f"{T1}/{uuid.uuid4()}/documents/{'a' * 64}", b"12345")
    storage.write(f"{T2}/{uuid.uuid4()}/documents/{'b' * 64}", b"1234567890")

    async def update(progress: int, message: str | None = None) -> None:
        return None

    def run(tenancy: TenancyContext) -> dict[str, Any] | None:
        context = JobContext(
            job_id=uuid.uuid4(),
            kind=kind.kind,
            tenancy=tenancy,
            payload={},
            request_id=None,
            update=update,
            db=None,  # type: ignore[arg-type]  (the handler opens no unit of work)
        )
        return asyncio.run(kind.handler(context))

    result = run(TenancyContext(tenant_id=T1))
    assert result is not None
    assert result["storage_classes"]["STANDARD"] == {"objects": 1, "bytes": 5}
    with pytest.raises(jobs.JobError):
        run(TenancyContext(tenant_id=T1, project_id=uuid.uuid4()))


def _finish(
    api: Api, job_id: uuid.UUID, result: dict[str, Any], project: uuid.UUID | None = None
) -> None:
    with api.db.unit_of_work(TenancyContext(tenant_id=T1, project_id=project)) as s:
        job = s.get(Job, job_id)
        assert job is not None
        job.status, job.result, job.finished_at = "succeeded", result, utcnow()


def test_refresh_queues_one_batch_run_at_a_time_and_stats_show_the_cache(
    api: Api, dispatched: list[JobRef]
) -> None:
    alice = api.headers(ALICE)
    first = api.client.post(REFRESH, headers=alice)
    assert first.status_code == 202, first.text
    job_id = uuid.UUID(first.json()["job_id"])
    assert first.json()["status"] == "queued"
    assert [(r.job_id, r.queue) for r in dispatched] == [(job_id, "batch")]

    again = api.client.post(REFRESH, headers=alice)
    assert again.status_code == 409
    assert again.json() == {"job_id": str(job_id), "status": "queued"}
    assert len(dispatched) == 1
    assert api.client.get(STATS, headers=alice).json()["object_store"]["refreshing"] is True

    totals = {
        "STANDARD": {"objects": 3, "bytes": 300},
        "GLACIER_IR": {"objects": 1, "bytes": 10},
        "DEEP_ARCHIVE": {"objects": 0, "bytes": 0},
        "other": {"objects": 2, "bytes": "not-a-number"},
    }
    _finish(api, job_id, {"storage_classes": totals, "leak": str(uuid.uuid4())})
    store = api.client.get(STATS, headers=alice).json()["object_store"]
    assert store["refreshing"] is False
    assert store["as_of"] is not None
    assert store["storage_classes"] == [
        {"storage_class": "STANDARD", "objects": 3, "bytes": 300},
        {"storage_class": "GLACIER_IR", "objects": 1, "bytes": 10},
        {"storage_class": "DEEP_ARCHIVE", "objects": 0, "bytes": 0},
        {"storage_class": "other", "objects": 2, "bytes": 0},
    ]
    # Finished: the next refresh queues a new run.
    assert api.client.post(REFRESH, headers=alice).status_code == 202
    events = [(e["event"], e.get("job_id")) for e in api.chain()]
    assert events[:2] == [
        ("object_store_refresh_queued", str(job_id)),
        ("object_store_refresh_running", str(job_id)),
    ]
    assert [e for e, _ in events].count("object_store_refresh_queued") == 2


def test_a_project_job_of_the_same_kind_neither_blocks_nor_feeds_the_cache(api: Api) -> None:
    # Visibility is not scope: the admin sees every project's job rows (ADR 0016), but only the
    # tenant-level run is the deployment's.
    kind = jobs.JOB_REGISTRY.get(admin_jobs.OBJECT_STORE_KIND)
    with api.db.unit_of_work(api.scope(api.home)) as s:
        stray = jobs.create_job(s, kind)
        stray_id = stray.id
    _finish(api, stray_id, {"storage_classes": {"STANDARD": {"objects": 9, "bytes": 9}}}, api.home)
    with api.db.unit_of_work(api.scope(api.away)) as s:
        jobs.create_job(s, kind)
    alice = api.headers(ALICE)
    store = api.client.get(STATS, headers=alice).json()["object_store"]
    assert store == {"as_of": None, "refreshing": False, "storage_classes": []}
    assert api.client.post(REFRESH, headers=alice).status_code == 202


# --- health ---------------------------------------------------------------------------------


def test_health_probes_answer_status_and_latency_only(
    api: Api, quick_probes: dict[str, Any]
) -> None:
    response = api.client.get(HEALTH, headers=api.headers(ALICE))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert [p["name"] for p in body["probes"]] == [
        "postgresql",
        "redis",
        "object_store",
        "malware_scanner",
    ]
    for probe in body["probes"]:
        assert set(probe) == {"name", "status", "latency_ms"}
        assert probe["status"] == "ok"
        assert isinstance(probe["latency_ms"], float)


def test_health_never_shows_a_host_connection_string_or_credential(
    api: Api, quick_probes: dict[str, Any]
) -> None:
    def leaky() -> bool:
        raise ConnectionError(f"cannot reach {REDIS_URL} ({SECRET_HOST}, {SECRET_PASSWORD})")

    quick_probes["redis"] = leaky
    quick_probes["object_store"] = leaky
    response = api.client.get(HEALTH, headers=api.headers(ALICE))
    assert response.status_code == 200
    by_name = {p["name"]: p["status"] for p in response.json()["probes"]}
    assert by_name["redis"] == by_name["object_store"] == "down"
    assert response.json()["status"] == "down"
    for secret in (SECRET_HOST, SECRET_PASSWORD, "redis://", "kerp-vault", "sqlite", "6379"):
        assert secret not in response.text
    chain = api.chain()
    assert chain[-1]["event"] == "health_checked"
    assert chain[-1]["statuses"]["redis"] == "down"
    assert SECRET_HOST not in str(chain)


def test_a_hung_probe_is_down_after_its_deadline(
    api: Api, quick_probes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert health.PROBE_TIMEOUT_S <= 2.0
    monkeypatch.setattr(health, "PROBE_TIMEOUT_S", 0.2)
    release = threading.Event()
    quick_probes["malware_scanner"] = lambda: release.wait(10) and True
    try:
        response = api.client.get(HEALTH, headers=api.headers(ALICE))
        # Answered while the probe still hangs: the request never waits for it.
        assert not release.is_set()
    finally:
        release.set()
    probes = {p["name"]: p for p in response.json()["probes"]}
    assert probes["malware_scanner"]["status"] == "down"
    assert probes["malware_scanner"]["latency_ms"] >= 200
    assert probes["postgresql"]["status"] == "ok"


def test_a_slow_answer_is_degraded_and_a_stub_scanner_too(
    api: Api, quick_probes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "DEGRADED_AFTER_S", 0.05)
    quick_probes["object_store"] = lambda: time.sleep(0.1) or True
    quick_probes["malware_scanner"] = None  # the always-clean stub: nothing to ping
    body = api.client.get(HEALTH, headers=api.headers(ALICE)).json()
    probes = {p["name"]: p for p in body["probes"]}
    assert probes["object_store"]["status"] == "degraded"
    assert probes["malware_scanner"] == {
        "name": "malware_scanner",
        "status": "degraded",
        "latency_ms": None,
    }
    assert body["status"] == "degraded"


def test_the_real_probes_of_this_deployment(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    # No mocks but Redis: the database answers, the local store exists, the stub scanner has
    # nothing to ping, and an unreachable Redis is down without naming it.
    monkeypatch.setattr(health, "ping_redis", lambda url, timeout=2.0: False)
    body = api.client.get(HEALTH, headers=api.headers(ALICE)).json()
    statuses = {p["name"]: p["status"] for p in body["probes"]}
    assert statuses == {
        "postgresql": "ok",
        "redis": "down",
        "object_store": "ok",
        "malware_scanner": "degraded",
    }


def test_redis_probe_without_a_url_is_down() -> None:
    assert health.ping_redis(None) is False


def test_storage_probes() -> None:
    assert uploads.ping_storage(ListingStorage([])) is None  # type: ignore[arg-type]

    class Broken:
        def ping(self) -> bool:
            raise OSError("synthetic")

    assert uploads.ping_storage(Broken()) is False  # type: ignore[arg-type]


def test_a_cache_older_than_a_day_still_shows_its_as_of(api: Api) -> None:
    with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        job = jobs.create_job(s, jobs.JOB_REGISTRY.get(admin_jobs.OBJECT_STORE_KIND))
        job.status, job.finished_at = "succeeded", utcnow() - timedelta(days=2)
        job.result = {"storage_classes": {}}
    store = api.client.get(STATS, headers=api.headers(ALICE)).json()["object_store"]
    assert store["as_of"] is not None
    assert [c["objects"] for c in store["storage_classes"]] == [0, 0, 0, 0]
