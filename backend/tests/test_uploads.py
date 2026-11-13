"""Uploads module: record contract, service lifecycle, jobs, API (T-0016).

Object storage is a ``LocalStorage`` under ``tmp_path`` (the client's presigned ``PUT`` is
``storage.write``); the job queue is a recording dispatcher. Runs on SQLite and, in CI, on
PostgreSQL under project RLS. All data is synthetic.
"""

import asyncio
import hashlib
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.auth.access import Persona
from app.core.auth.models import ProjectMembership
from app.core.db import Base, Database, contract_violations, utcnow
from app.core.module_loader.loader import discover_modules
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import RLS_EXEMPT, rls_tables
from app.core.tenancy.scope import OutOfScopeError
from app.modules.jobs import service as jobs
from app.modules.jobs.broker import JobRef
from app.modules.jobs.registry import JOB_REGISTRY, JobContext
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.uploads import jobs as upload_jobs
from app.modules.uploads import service
from app.modules.uploads.models import TABLE, UploadedFile
from app.modules.uploads.scan import set_scanner
from app.modules.uploads.schemas import UploadCreate
from app.modules.uploads.storage import LocalStorage, Storage, set_storage
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, T1, T2, auth_app, make_settings, seed_user
from tests.db_support import database_url

CTX_T1 = TenancyContext(tenant_id=T1)
CTX_T2 = TenancyContext(tenant_id=T2)
CAROL = "carol@example.test"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
MAX = 1024 * 1024


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _body(data: bytes = PNG, **fields: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "module": "documents",
        "filename": "site photo.png",
        "content_type": "image/png",
        "size_bytes": len(data),
        "sha256": _sha(data),
    }
    values.update(fields)
    return values


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[LocalStorage]:
    local = LocalStorage(tmp_path / "vault")
    set_storage(local)
    yield local
    set_storage(None)


@pytest.fixture
def dispatched() -> Iterator[list[JobRef]]:
    refs: list[JobRef] = []
    jobs.set_dispatcher(refs.extend)
    yield refs
    jobs.set_dispatcher(None)


def _project(db: Database, code: str, ctx: TenancyContext = CTX_T1) -> TenancyContext:
    with db.unit_of_work(ctx) as s:
        project_id = projects.create_project(
            s, ProjectCreate.model_validate({"code": code, "name": code})
        ).id
    return TenancyContext(tenant_id=ctx.tenant_id, project_id=project_id)


@pytest.fixture
def home(db: Database) -> TenancyContext:
    return _project(db, "HOME")


def _presign(
    db: Database, ctx: TenancyContext, storage: Storage, data: bytes = PNG, **fields: Any
) -> uuid.UUID:
    with db.unit_of_work(ctx) as s:
        file, _ = service.presign_upload(
            s,
            storage,
            UploadCreate.model_validate(_body(data, **fields)),
            max_bytes=MAX,
            expires_in=900,
        )
        return file.id


def _complete(
    db: Database, ctx: TenancyContext, storage: Storage, file_id: uuid.UUID
) -> UploadedFile:
    with db.unit_of_work(ctx) as s:
        return service.complete_upload(s, storage, file_id)


def _load(db: Database, ctx: TenancyContext, file_id: uuid.UUID) -> UploadedFile:
    with db.unit_of_work(ctx) as s:
        return service.get_file(s, file_id)


def _verify(
    db: Database, ctx: TenancyContext, storage: Storage, file_id: uuid.UUID
) -> dict[str, Any]:
    return service.verify_upload(
        db, ctx, file_id, storage=storage, scanner=upload_jobs.get_scanner()
    )


# --- contract -------------------------------------------------------------------------------


def test_file_table_meets_the_record_contract_and_is_under_rls() -> None:
    assert TABLE in Base.metadata.tables
    assert [p for p in contract_violations(Base.metadata) if p.startswith(TABLE)] == []
    assert TABLE in rls_tables(Base.metadata)
    assert TABLE not in RLS_EXEMPT


def test_uploads_module_is_loaded_and_registers_its_jobs() -> None:
    manifest = discover_modules().get("uploads")
    assert manifest.toggleable is False
    assert set(manifest.depends_on) == {"jobs", "projects"}
    assert JOB_REGISTRY.get(service.VERIFY_KIND).queue == "interactive"
    assert JOB_REGISTRY.get(service.VERIFY_LARGE_KIND).queue == "batch"
    assert JOB_REGISTRY.get(service.SWEEP_KIND).queue == "batch"
    assert not JOB_REGISTRY.get(service.VERIFY_KIND).api


# --- request validation ---------------------------------------------------------------------


def test_upload_request_is_normalised() -> None:
    body = UploadCreate.model_validate(
        _body(filename="../../x/Report.PDF", content_type="Image/PNG", sha256=_sha(PNG).upper())
    )
    assert (body.filename, body.content_type, body.sha256) == ("Report.PDF", "image/png", _sha(PNG))


@pytest.mark.parametrize(
    "fields",
    [
        {"content_type": "text/html"},
        {"content_type": "image/svg+xml"},
        {"sha256": "abc"},
        {"sha256": "g" * 64},
        {"size_bytes": 0},
        {"module": "Documents"},
        {"module": "../x"},
        {"filename": ""},
        {"tenant_id": str(T2)},
    ],
)
def test_bad_upload_requests_are_rejected(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        UploadCreate.model_validate(_body(**fields))


# --- service lifecycle ----------------------------------------------------------------------


def test_presign_complete_verify_download_round_trip(
    db: Database, home: TenancyContext, storage: LocalStorage, dispatched: list[JobRef]
) -> None:
    file_id = _presign(db, home, storage)
    pending = _load(db, home, file_id)
    assert pending.status == "pending"
    assert pending.staging_key == f"staging/{T1}/{home.project_id}/{file_id}"
    assert pending.expires_at is not None

    storage.write(pending.staging_key, PNG)  # the client's presigned PUT
    completed = _complete(db, home, storage, file_id)
    assert completed.status == "verifying"
    assert completed.job_id is not None
    assert [(r.job_id, r.queue, r.project_id) for r in dispatched] == [
        (completed.job_id, "interactive", home.project_id)
    ]

    result = _verify(db, home, storage, file_id)
    assert result == {"file_id": str(file_id), "status": "available"}
    done = _load(db, home, file_id)
    assert done.status == "available"
    assert done.scan_result == "clean"
    assert done.verified_at is not None
    assert done.storage_key == f"{T1}/{home.project_id}/documents/{_sha(PNG)}"
    assert storage.read(done.storage_key) == PNG
    assert storage.head(pending.staging_key) is None  # staging is cleaned up

    with db.unit_of_work(home) as s:
        get = service.download_request(s, storage, file_id, expires_in=60)
    assert get.method == "GET"
    assert get.url.endswith(done.storage_key)

    # Verifying again is a no-op.
    assert _verify(db, home, storage, file_id) == {"file_id": str(file_id), "status": "available"}


def test_a_wrong_sha256_is_rejected_and_nothing_is_published(
    db: Database, home: TenancyContext, storage: LocalStorage, dispatched: list[JobRef]
) -> None:
    file_id = _presign(db, home, storage, sha256=_sha(b"something else"))
    storage.write(_load(db, home, file_id).staging_key, PNG)
    assert _complete(db, home, storage, file_id).status == "verifying"
    assert _verify(db, home, storage, file_id)["reason"] == "sha256_mismatch"
    file = _load(db, home, file_id)
    assert (file.status, file.rejection_reason, file.storage_key) == (
        "rejected",
        "sha256_mismatch",
        None,
    )
    assert list(storage.list("")) == []


def test_an_infected_file_is_rejected(
    db: Database, home: TenancyContext, storage: LocalStorage, dispatched: list[JobRef]
) -> None:
    class Infected:
        def scan(self, storage: Storage, key: str) -> str:
            return "EICAR found"

    file_id = _presign(db, home, storage)
    storage.write(_load(db, home, file_id).staging_key, PNG)
    _complete(db, home, storage, file_id)
    set_scanner(Infected())
    try:
        assert _verify(db, home, storage, file_id)["reason"] == "infected"
    finally:
        set_scanner(None)
    file = _load(db, home, file_id)
    assert (file.status, file.scan_result) == ("rejected", "infected")
    assert list(storage.list("")) == []


# --- check-then-publish races (review round 1) -----------------------------------------------

NOT_PNG = b"MZ" + bytes(len(PNG) - 2)  # same size as PNG, not an image


def test_a_re_put_after_complete_is_not_published(
    db: Database, home: TenancyContext, storage: LocalStorage, dispatched: list[JobRef]
) -> None:
    # The client declares the hash of B, uploads a valid PNG A for ``complete``, then
    # re-PUTs B while the presigned URL is still valid, before the job runs.
    file_id = _presign(db, home, storage, sha256=_sha(NOT_PNG))
    key = _load(db, home, file_id).staging_key
    storage.write(key, PNG)
    assert _complete(db, home, storage, file_id).status == "verifying"
    storage.write(key, NOT_PNG)
    assert _verify(db, home, storage, file_id)["reason"] == "type_mismatch"
    file = _load(db, home, file_id)
    assert (file.status, file.storage_key) == ("rejected", None)
    assert list(storage.list("")) == []  # nothing published, staging and quarantine gone


class RacingStorage(LocalStorage):
    """Re-PUTs ``staging`` with ``evil`` right before every copy (the reviewer's PoC)."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.race: tuple[str, bytes] | None = None

    def copy(self, source: str, destination: str) -> None:
        if self.race is not None:
            self.write(*self.race)
        super().copy(source, destination)


def test_a_racing_write_cannot_change_a_verified_content_object(
    db: Database, home: TenancyContext, tmp_path: Path, dispatched: list[JobRef]
) -> None:
    storage = RacingStorage(tmp_path / "racing")
    victim = _presign(db, home, storage)
    storage.write(_load(db, home, victim).staging_key, PNG)
    _complete(db, home, storage, victim)
    _verify(db, home, storage, victim)
    content = _load(db, home, victim).storage_key
    assert content is not None
    assert storage.read(content) == PNG

    # A member holding a copy of the same document uploads it, and races a re-PUT of
    # different bytes into staging just before each copy.
    attacker = _presign(db, home, storage)
    staging = _load(db, home, attacker).staging_key
    storage.write(staging, PNG)
    _complete(db, home, storage, attacker)
    storage.race = (staging, b"\x89PNG\r\n\x1a\n" + bytes(len(PNG) - 8))
    result = _verify(db, home, storage, attacker)
    storage.race = None
    assert result["status"] == "rejected"
    assert storage.read(content) == PNG
    assert _sha(storage.read(content)) == _load(db, home, victim).sha256


def test_publishing_never_overwrites_an_existing_content_object(
    db: Database, home: TenancyContext, storage: LocalStorage, dispatched: list[JobRef]
) -> None:
    first = _presign(db, home, storage)
    storage.write(_load(db, home, first).staging_key, PNG)
    _complete(db, home, storage, first)
    _verify(db, home, storage, first)
    content = _load(db, home, first).storage_key
    assert content is not None
    copies: list[tuple[str, str]] = []
    original = storage.copy

    def recording_copy(source: str, destination: str) -> None:
        copies.append((source, destination))
        original(source, destination)

    storage.copy = recording_copy  # type: ignore[method-assign]
    second = _presign(db, home, storage)
    storage.write(_load(db, home, second).staging_key, PNG)
    _complete(db, home, storage, second)
    assert _verify(db, home, storage, second)["status"] == "available"
    assert all(destination != content for _, destination in copies)
    assert _load(db, home, second).storage_key == content


def test_a_failed_verify_deletes_the_quarantine_copy(
    db: Database, home: TenancyContext, storage: LocalStorage, dispatched: list[JobRef]
) -> None:
    class Broken:
        def scan(self, storage: Storage, key: str) -> str:
            raise OSError("scanner down")

    file_id = _presign(db, home, storage)
    storage.write(_load(db, home, file_id).staging_key, PNG)
    _complete(db, home, storage, file_id)
    set_scanner(Broken())
    try:
        with pytest.raises(OSError, match="scanner down"):
            _verify(db, home, storage, file_id)
    finally:
        set_scanner(None)
    assert list(storage.list("quarantine/")) == []


def test_an_object_gone_before_verification_is_rejected(
    db: Database, home: TenancyContext, storage: LocalStorage, dispatched: list[JobRef]
) -> None:
    file_id = _presign(db, home, storage)
    key = _load(db, home, file_id).staging_key
    storage.write(key, PNG)
    _complete(db, home, storage, file_id)
    storage.delete(key)
    assert _verify(db, home, storage, file_id)["reason"] == "missing_object"


@pytest.mark.parametrize(
    ("uploaded", "reason"),
    [(PNG[:-1], "size_mismatch"), (b"%PDF-" + PNG[5:], "type_mismatch")],
)
def test_complete_rejects_a_wrong_size_or_type_and_deletes_the_object(
    db: Database,
    home: TenancyContext,
    storage: LocalStorage,
    dispatched: list[JobRef],
    uploaded: bytes,
    reason: str,
) -> None:
    file_id = _presign(db, home, storage)
    key = _load(db, home, file_id).staging_key
    storage.write(key, uploaded)
    file = _complete(db, home, storage, file_id)
    assert (file.status, file.rejection_reason, file.job_id) == ("rejected", reason, None)
    assert storage.head(key) is None
    assert dispatched == []


def test_complete_before_the_upload_or_twice_is_a_state_error(
    db: Database, home: TenancyContext, storage: LocalStorage, dispatched: list[JobRef]
) -> None:
    file_id = _presign(db, home, storage)
    with pytest.raises(service.UploadStateError, match="not been uploaded"):
        _complete(db, home, storage, file_id)
    assert _load(db, home, file_id).status == "pending"  # the client may still PUT
    storage.write(_load(db, home, file_id).staging_key, PNG)
    _complete(db, home, storage, file_id)
    with pytest.raises(service.UploadStateError, match="verifying"):
        _complete(db, home, storage, file_id)
    with db.unit_of_work(home) as s, pytest.raises(service.UploadStateError):
        service.download_request(s, storage, file_id, expires_in=60)


def test_a_rolled_back_complete_queues_no_job(
    db: Database, home: TenancyContext, storage: LocalStorage, dispatched: list[JobRef]
) -> None:
    file_id = _presign(db, home, storage)
    storage.write(_load(db, home, file_id).staging_key, PNG)

    def complete_then_fail() -> None:
        with db.unit_of_work(home) as s:
            service.complete_upload(s, storage, file_id)
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        complete_then_fail()
    assert dispatched == []
    assert _load(db, home, file_id).status == "pending"


def test_the_size_limit_is_enforced_before_signing(
    db: Database, home: TenancyContext, storage: LocalStorage
) -> None:
    with db.unit_of_work(home) as s, pytest.raises(service.FileTooLargeError):
        service.presign_upload(
            s, storage, UploadCreate.model_validate(_body()), max_bytes=10, expires_in=60
        )


def test_the_service_needs_a_project_session(db: Database, storage: LocalStorage) -> None:
    with db.unit_of_work(CTX_T1) as s, pytest.raises(RuntimeError, match="bound to a project"):
        service.presign_upload(
            s, storage, UploadCreate.model_validate(_body()), max_bytes=MAX, expires_in=60
        )
    with pytest.raises(RuntimeError, match="per project"):
        service.sweep_orphans(db, CTX_T1, storage=storage)


def test_files_are_only_visible_in_their_own_project(
    db: Database, home: TenancyContext, storage: LocalStorage
) -> None:
    other = _project(db, "OTHER")
    file_id = _presign(db, home, storage)
    portfolio = TenancyContext(
        tenant_id=T1,
        project_id=other.project_id,
        allowed_project_ids=frozenset({home.project_id}),  # type: ignore[arg-type]
    )
    elsewhere = _project(db, "ELSEWHERE", CTX_T2)
    for ctx in (other, portfolio, elsewhere):
        with db.unit_of_work(ctx) as s, pytest.raises(OutOfScopeError):
            service.get_file(s, file_id)


def test_the_sweep_expires_abandoned_uploads_and_deletes_orphans(
    db: Database, home: TenancyContext, storage: LocalStorage, dispatched: list[JobRef]
) -> None:
    abandoned = _presign(db, home, storage)
    storage.write(_load(db, home, abandoned).staging_key, PNG)  # PUT, never completed
    verifying = _presign(db, home, storage)
    storage.write(_load(db, home, verifying).staging_key, PNG)
    _complete(db, home, storage, verifying)
    stray = f"staging/{T1}/{home.project_id}/{uuid.uuid4()}"  # no row at all
    storage.write(stray, b"x")
    leftover = f"quarantine/{T1}/{home.project_id}/{uuid.uuid4()}"  # a worker died mid-verify
    storage.write(leftover, b"x")
    other = _project(db, "OTHER")
    elsewhere = f"staging/{T1}/{other.project_id}/{uuid.uuid4()}"
    storage.write(elsewhere, b"x")

    # Within the grace period nothing happens.
    assert service.sweep_orphans(db, home, storage=storage) == {"expired": 0, "deleted": 0}

    later = utcnow() + timedelta(hours=2)
    assert service.sweep_orphans(db, home, storage=storage, now=later) == {
        "expired": 1,
        "deleted": 3,
    }
    assert _load(db, home, abandoned).status == "expired"
    assert _load(db, home, verifying).status == "verifying"
    assert storage.head(_load(db, home, verifying).staging_key) is not None
    assert storage.head(stray) is None
    assert storage.head(leftover) is None
    assert storage.head(elsewhere) is not None  # another project's sweep owns it
    with pytest.raises(service.UploadStateError, match="expired"):
        _complete(db, home, storage, abandoned)


def test_large_files_verify_on_the_batch_queue(
    db: Database,
    home: TenancyContext,
    storage: LocalStorage,
    dispatched: list[JobRef],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "LARGE_FILE_BYTES", len(PNG) - 1)
    file_id = _presign(db, home, storage)
    storage.write(_load(db, home, file_id).staging_key, PNG)
    _complete(db, home, storage, file_id)
    assert [r.queue for r in dispatched] == ["batch"]


def test_schedule_sweep_queues_a_batch_job(
    db: Database, home: TenancyContext, dispatched: list[JobRef]
) -> None:
    with db.unit_of_work(home) as s:
        service.schedule_sweep(s)
    assert [(r.queue, r.project_id) for r in dispatched] == [("batch", home.project_id)]


# --- job handlers ---------------------------------------------------------------------------


def _context(db: Database, ctx: TenancyContext, payload: dict[str, Any]) -> JobContext:
    async def update(progress: int, message: str | None = None) -> None:
        return None

    return JobContext(uuid.uuid4(), service.VERIFY_KIND, ctx, payload, None, update, db)


def test_the_job_handlers_run_the_service(
    db: Database, home: TenancyContext, storage: LocalStorage, dispatched: list[JobRef]
) -> None:
    file_id = _presign(db, home, storage)
    storage.write(_load(db, home, file_id).staging_key, PNG)
    _complete(db, home, storage, file_id)
    result = asyncio.run(upload_jobs.verify(_context(db, home, {"file_id": str(file_id)})))
    assert result["status"] == "available"
    swept = asyncio.run(upload_jobs.sweep_orphans(_context(db, home, {})))
    assert swept == {"expired": 0, "deleted": 0}


@pytest.mark.parametrize("payload", [{}, {"file_id": "not-a-uuid"}])
def test_the_verify_job_refuses_a_bad_payload(
    db: Database, home: TenancyContext, payload: dict[str, Any]
) -> None:
    with pytest.raises(jobs.JobError, match="No file"):
        asyncio.run(upload_jobs.verify(_context(db, home, payload)))


# --- HTTP (real tokens, memberships from the store) -----------------------------------------


def _grant(db: Database, user: uuid.UUID, project: uuid.UUID, persona: Persona) -> None:
    with db.unit_of_work(CTX_T1) as s:
        s.add(
            ProjectMembership(
                tenant_id=T1, user_id=user, member_project_id=project, persona=persona.value
            )
        )


class Api:
    def __init__(self, client: TestClient, db: Database) -> None:
        self.client, self.db = client, db
        self.users = {email: seed_user(db, T1, email) for email in (ALICE, BOB, CAROL)}
        self.home = _project(db, "HOME")
        self.other = _project(db, "OTHER")
        assert self.home.project_id is not None
        _grant(db, self.users[ALICE], self.home.project_id, Persona.FIELD_WORKER)
        _grant(db, self.users[BOB], self.home.project_id, Persona.CLIENT_REPRESENTATIVE)

    def headers(self, email: str) -> dict[str, str]:
        body = {"email": email, "password": PASSWORD, "tenant_id": str(T1)}
        response = self.client.post("/api/v1/auth/login", json=body)
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def files(self, ctx: TenancyContext | None = None) -> str:
        return f"/api/v1/uploads/projects/{(ctx or self.home).project_id}/files"


@pytest.fixture
def api(tmp_path: Path, db: Database, storage: LocalStorage) -> Iterator[Api]:
    settings = make_settings(database_url=database_url(tmp_path), upload_max_bytes=MAX)
    app, _runtime = auth_app(settings)
    with TestClient(app) as client:
        yield Api(client, db)


def test_the_api_round_trip(api: Api, storage: LocalStorage, dispatched: list[JobRef]) -> None:
    alice = api.headers(ALICE)
    created = api.client.post(api.files(), json=_body(), headers=alice)
    assert created.status_code == 201, created.text
    ticket = created.json()
    file, upload = ticket["file"], ticket["upload"]
    assert (file["status"], file["filename"], file["project_id"]) == (
        "pending",
        "site photo.png",
        str(api.home.project_id),
    )
    assert upload["method"] == "PUT"
    assert upload["headers"] == {"Content-Type": "image/png"}
    url = f"{api.files()}/{file['id']}"

    assert api.client.post(f"{url}/complete", headers=alice).status_code == 409  # not PUT yet
    storage.write(f"staging/{T1}/{api.home.project_id}/{file['id']}", PNG)
    completed = api.client.post(f"{url}/complete", headers=alice)
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "verifying"
    assert api.client.get(f"{url}/download", headers=alice).status_code == 409
    assert len(dispatched) == 1

    _verify(api.db, api.home, storage, uuid.UUID(file["id"]))
    polled = api.client.get(url, headers=alice).json()
    assert (polled["status"], polled["sha256"]) == ("available", _sha(PNG))
    download = api.client.get(f"{url}/download", headers=alice)
    assert download.status_code == 200
    assert download.json()["method"] == "GET"


def test_the_api_checks_membership_and_capability(api: Api, storage: LocalStorage) -> None:
    alice, bob, carol = (api.headers(e) for e in (ALICE, BOB, CAROL))
    created = api.client.post(api.files(), json=_body(), headers=alice).json()["file"]
    url = f"{api.files()}/{created['id']}"
    # Bob is a client representative (view only): reads, cannot upload.
    assert api.client.post(api.files(), json=_body(), headers=bob).status_code == 403
    assert api.client.post(f"{url}/complete", headers=bob).status_code == 403
    assert api.client.get(url, headers=bob).status_code == 200
    # Carol is no member: the project does not exist for her.
    assert api.client.post(api.files(), json=_body(), headers=carol).status_code == 404
    assert api.client.get(url, headers=carol).status_code == 404
    # Another member who may upload still cannot complete Alice's upload.
    _grant(api.db, api.users[CAROL], api.home.project_id, Persona.FIELD_WORKER)  # type: ignore[arg-type]
    assert api.client.post(f"{url}/complete", headers=carol).status_code == 403
    # A file is only reachable through its own project, even for a member of both.
    _grant(api.db, api.users[ALICE], api.other.project_id, Persona.FIELD_WORKER)  # type: ignore[arg-type]
    other_url = f"{api.files(api.other)}/{created['id']}"
    assert api.client.get(other_url, headers=alice).status_code == 404
    # No token: 401.
    assert api.client.get(url).status_code == 401


def test_the_api_rejects_bad_requests(api: Api) -> None:
    alice = api.headers(ALICE)
    too_big = _body(size_bytes=MAX + 1)
    assert api.client.post(api.files(), json=too_big, headers=alice).status_code == 413
    html = _body(content_type="text/html")
    assert api.client.post(api.files(), json=html, headers=alice).status_code == 422
    missing = f"{api.files()}/{uuid.uuid4()}"
    assert api.client.get(missing, headers=alice).status_code == 404


def test_a_deleted_project_is_404(api: Api) -> None:
    alice = api.headers(ALICE)
    with api.db.unit_of_work(CTX_T1) as s:
        projects.delete_project(s, api.home.project_id)  # type: ignore[arg-type]
    assert api.client.post(api.files(), json=_body(), headers=alice).status_code == 404
