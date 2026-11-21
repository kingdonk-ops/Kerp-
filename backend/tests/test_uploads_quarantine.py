"""System console upload quarantine monitor (T-0221; diff §4.7).

``/api/v1/admin/system/uploads...``: ``system_admin`` only (403 for everyone else, a PER-09 of
every project included). Rows and counts of every project of the tenant under the normal
project RLS (ADR 0016), audited re-check and sweep queued as jobs. Object storage is a
``LocalStorage``; the job queue a recording dispatcher. Runs on SQLite and, in CI, on
PostgreSQL as the ``NOSUPERUSER NOBYPASSRLS`` role ``kerp_app``. All data is synthetic.
"""

import hashlib
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.audit.service import chain_entries
from app.core.auth.access import SYSTEM_ADMIN, Persona
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import POLICY_NAMES
from app.modules.admin import service as admin
from app.modules.jobs import service as jobs
from app.modules.jobs.broker import JobRef
from app.modules.jobs.models import TABLE as JOBS_TABLE
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.uploads import service
from app.modules.uploads.models import TABLE, UploadedFile
from app.modules.uploads.schemas import UploadCreate
from app.modules.uploads.storage import LocalStorage, set_storage
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
from tests.db_support import ON_POSTGRES, database_url, seed_tenants

UPLOADS = "/api/v1/admin/system/uploads"
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
CAROL = "carol@example.test"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
SIGNATURE = "Synthetic.Test.Marker-0"


def _project(db: Database, code: str, tenant: uuid.UUID = T1) -> uuid.UUID:
    ctx = TenancyContext(tenant_id=tenant, is_system_admin=True)
    with db.unit_of_work(ctx) as s:
        return projects.create_project(s, ProjectCreate(code=code, name=f"Synthetic {code}")).id


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


class Api:
    """ALICE: System admin with no membership anywhere (ADR 0016). BOB: PER-09 of every
    project. CAROL: no grant at all."""

    def __init__(self, client: TestClient, db: Database, storage: LocalStorage) -> None:
        self.client, self.db, self.storage = client, db, storage
        self.users = {e: _seed_user(db, T1, e) for e in (ALICE, BOB, CAROL)}
        with db.unit_of_work(ROOT) as s:
            assert admin.grant(s, self.users[ALICE], SYSTEM_ADMIN, actor_id=None)
        self.home, self.away = _project(db, "HOME"), _project(db, "AWAY")
        for project in (self.home, self.away):
            grant_persona(db, T1, self.users[BOB], Persona.PROJECT_ADMINISTRATOR.value, project)
        self._headers: dict[str, dict[str, str]] = {}

    def headers(self, email: str) -> dict[str, str]:
        if email not in self._headers:
            body = {"email": email, "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            self._headers[email] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._headers[email]

    def file(
        self,
        project: uuid.UUID,
        state: str,
        *,
        reason: str | None = None,
        signature: str | None = None,
        staged: bool = True,
        tenant: uuid.UUID = T1,
        filename: str = "site photo.png",
    ) -> uuid.UUID:
        """An upload of ``project`` in ``state``; its staging object exists if ``staged``."""
        ctx = TenancyContext(tenant_id=tenant, project_id=project)
        body = UploadCreate.model_validate(
            {
                "module": "documents",
                "filename": filename,
                "content_type": "image/png",
                "size_bytes": len(PNG),
                "sha256": hashlib.sha256(PNG).hexdigest(),
            }
        )
        with self.db.unit_of_work(ctx) as s:
            file, _ = service.presign_upload(
                s, self.storage, body, max_bytes=1 << 20, expires_in=900, actor_id=self.users[CAROL]
            )
            file.status, file.rejection_reason, file.scan_signature = state, reason, signature
            if staged:
                self.storage.write(file.staging_key, PNG)
            return file.id

    def row(self, file_id: uuid.UUID, project: uuid.UUID) -> UploadedFile:
        with self.db.unit_of_work(TenancyContext(tenant_id=T1, project_id=project)) as s:
            return service.get_file(s, file_id)

    def chain(self) -> list[dict[str, Any]]:
        with self.db.unit_of_work(ROOT) as s:
            return [e.payload for e in chain_entries(s, f"admin-system:{T1}")]

    def get(self, url: str, email: str = ALICE, **params: Any) -> Any:
        response = self.client.get(url, params=params, headers=self.headers(email))
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def api(
    tmp_path: Path, db: Database, storage: LocalStorage, dispatched: list[JobRef]
) -> Iterator[Api]:
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        yield Api(client, db, storage)


def _calls(file_id: uuid.UUID, project: uuid.UUID) -> list[tuple[str, str]]:
    return [
        ("GET", UPLOADS),
        ("GET", f"{UPLOADS}/counts"),
        ("POST", f"{UPLOADS}/{file_id}/recheck"),
        ("POST", f"{UPLOADS}/sweep?project={project}"),
    ]


# --- access ---------------------------------------------------------------------------------


def test_every_endpoint_needs_a_token(api: Api) -> None:
    codes = [api.client.request(m, u).status_code for m, u in _calls(uuid.uuid4(), api.home)]
    assert codes == [401] * 4


@pytest.mark.parametrize("email", [BOB, CAROL])
def test_every_endpoint_is_forbidden_to_everyone_but_a_system_admin(
    api: Api, dispatched: list[JobRef], email: str
) -> None:
    # BOB holds PER-09 of every project; CAROL holds nothing (she uploaded the file). Both get
    # 403 on every endpoint, for an existing stuck file too, and nothing is queued or audited.
    file_id = api.file(api.home, "verifying")
    for method, url in _calls(file_id, api.home):
        response = api.client.request(method, url, headers=api.headers(email))
        assert response.status_code == 403, (method, url, response.text)
        assert "site photo" not in response.text
    row = api.row(file_id, api.home)
    assert (row.status, row.job_id) == ("verifying", None)
    assert dispatched == []
    assert api.chain() == []


# --- rows -----------------------------------------------------------------------------------


def test_the_list_shows_monitored_uploads_of_every_project(api: Api) -> None:
    # ALICE has no membership in either project: her system_admin grant alone shows the rows.
    stuck = api.file(api.home, "verifying")
    infected = api.file(api.away, "rejected", reason="infected", signature=SIGNATURE, staged=False)
    expired = api.file(api.away, "expired", staged=False)
    api.file(api.home, "pending")  # neither in the check nor refused: not listed
    api.file(api.home, "available")
    page = api.get(UPLOADS)
    assert (page["total"], page["limit"], page["offset"]) == (3, 50, 0)
    rows = {r["id"]: r for r in page["items"]}
    assert set(rows) == {str(stuck), str(infected), str(expired)}
    bad = rows[str(infected)]
    assert bad == {
        "id": str(infected),
        "project_id": str(api.away),
        "project_code": "AWAY",
        "module": "documents",
        "filename": "site photo.png",
        "content_type": "image/png",
        "size_bytes": len(PNG),
        "sha256": hashlib.sha256(PNG).hexdigest(),
        "status": "rejected",
        "reason": "infected",
        "scan_signature": SIGNATURE,
        "uploader_id": str(api.users[CAROL]),
        "object_deleted": True,
        "created_at": bad["created_at"],
        "updated_at": bad["updated_at"],
    }
    assert (rows[str(stuck)]["project_code"], rows[str(stuck)]["object_deleted"]) == (
        "HOME",
        False,
    )
    assert rows[str(expired)]["object_deleted"] is True
    # Metadata only: never a presigned URL or a storage key.
    text_ = str(page)
    for word in ("staging", "quarantine", "storage_key", "url"):
        assert word not in text_


def test_the_list_filters_by_status_reason_and_project_and_pages(api: Api) -> None:
    api.file(api.home, "verifying")
    size = api.file(api.home, "rejected", reason="size_mismatch", staged=False)
    probe = api.file(api.away, "rejected", reason="media_probe_failed", staged=False)
    api.file(api.away, "expired", staged=False)
    ids = lambda page: {r["id"] for r in page["items"]}  # noqa: E731
    assert ids(api.get(UPLOADS, status="rejected")) == {str(size), str(probe)}
    # Reason codes are passed through, not enumerated (T-0169's media-probe codes too).
    assert ids(api.get(UPLOADS, reason="media_probe_failed")) == {str(probe)}
    assert ids(api.get(UPLOADS, status="rejected", project=str(api.home))) == {str(size)}
    assert api.get(UPLOADS, project=str(uuid.uuid4()))["total"] == 0
    first = api.get(UPLOADS, limit=3)
    rest = api.get(UPLOADS, limit=3, offset=3)
    assert (first["total"], len(first["items"]), len(rest["items"])) == (4, 3, 1)
    assert ids(first) | ids(rest) == ids(api.get(UPLOADS))
    alice = api.headers(ALICE)
    for bad in (
        "status=pending",
        "status=available",
        f"limit={service.MAX_QUARANTINE_LIST + 1}",
        "project=not-a-uuid",
        "offset=-1",
    ):
        assert api.client.get(f"{UPLOADS}?{bad}", headers=alice).status_code == 422, bad


# --- counts ---------------------------------------------------------------------------------


def test_counts_group_by_project_status_and_reason(api: Api) -> None:
    api.file(api.home, "verifying")
    api.file(api.home, "verifying")
    api.file(api.home, "rejected", reason="infected", signature=SIGNATURE, staged=False)
    api.file(api.away, "rejected", reason="infected", signature=SIGNATURE, staged=False)
    api.file(api.away, "rejected", reason="sha256_mismatch", staged=False)
    api.file(api.away, "expired", staged=False)
    api.file(api.away, "available")
    counts = [
        (c["project_code"], c["status"], c["reason"], c["count"])
        for c in api.get(f"{UPLOADS}/counts")
    ]
    assert counts == [
        ("AWAY", "expired", None, 1),
        ("AWAY", "rejected", "infected", 1),
        ("AWAY", "rejected", "sha256_mismatch", 1),
        ("HOME", "rejected", "infected", 1),
        ("HOME", "verifying", None, 2),
    ]
    infected = api.get(f"{UPLOADS}/counts", reason="infected", project=str(api.home))
    assert [(c["project_id"], c["count"]) for c in infected] == [(str(api.home), 1)]
    assert [c["status"] for c in api.get(f"{UPLOADS}/counts", status="expired")] == ["expired"]


# --- re-check -------------------------------------------------------------------------------


def test_a_system_admin_rechecks_a_stuck_file_of_any_project_audited(
    api: Api, dispatched: list[JobRef]
) -> None:
    file_id = api.file(api.away, "verifying")
    before = api.row(file_id, api.away)
    response = api.client.post(f"{UPLOADS}/{file_id}/recheck", headers=api.headers(ALICE))
    assert response.status_code == 202, response.text
    body = response.json()
    assert (body["project_id"], body["kind"], body["status"]) == (
        str(api.away),
        service.VERIFY_KIND,
        "queued",
    )
    row = api.row(file_id, api.away)
    assert (row.status, str(row.job_id), row.updated_by) == (
        "verifying",
        body["job_id"],
        api.users[ALICE],
    )
    assert row.updated_at >= before.updated_at  # the stuck clock restarts
    assert [(r.job_id, r.project_id) for r in dispatched] == [(row.job_id, api.away)]
    assert api.chain() == [
        {
            "event": "upload_rechecked",
            "file_id": str(file_id),
            "project_id": str(api.away),
            "job_id": body["job_id"],
        }
    ]
    with api.db.unit_of_work(ROOT) as s:
        (entry,) = chain_entries(s, f"admin-system:{T1}")
        assert entry.created_by == api.users[ALICE]


def test_a_recheck_finds_the_quarantine_copy_kept_after_a_scanner_outage(
    api: Api, storage: LocalStorage
) -> None:
    file_id = api.file(api.home, "verifying", staged=False)
    row = api.row(file_id, api.home)
    storage.write(row.quarantine_key, PNG)
    response = api.client.post(f"{UPLOADS}/{file_id}/recheck", headers=api.headers(ALICE))
    assert response.status_code == 202, response.text


def test_a_recheck_of_a_file_whose_object_is_gone_is_409(
    api: Api, dispatched: list[JobRef]
) -> None:
    file_id = api.file(api.home, "verifying", staged=False)
    response = api.client.post(f"{UPLOADS}/{file_id}/recheck", headers=api.headers(ALICE))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "OBJECT_GONE"
    assert api.row(file_id, api.home).job_id is None
    assert dispatched == []
    assert api.chain() == []


@pytest.mark.parametrize("state", ["pending", "available", "rejected", "expired"])
def test_only_a_verifying_file_is_rechecked(api: Api, dispatched: list[JobRef], state: str) -> None:
    file_id = api.file(api.home, state)
    response = api.client.post(f"{UPLOADS}/{file_id}/recheck", headers=api.headers(ALICE))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "NOT_VERIFYING"
    assert dispatched == []
    assert api.chain() == []


def test_a_large_stuck_file_is_rechecked_on_the_batch_queue(api: Api) -> None:
    file_id = api.file(api.home, "verifying")
    with api.db.unit_of_work(TenancyContext(tenant_id=T1, project_id=api.home)) as s:
        service.get_file(s, file_id).size_bytes = service.LARGE_FILE_BYTES + 1
    response = api.client.post(f"{UPLOADS}/{file_id}/recheck", headers=api.headers(ALICE))
    assert response.json()["kind"] == service.VERIFY_LARGE_KIND


# --- sweep ----------------------------------------------------------------------------------


def test_a_system_admin_queues_the_orphan_sweep_of_any_project_audited(
    api: Api, dispatched: list[JobRef]
) -> None:
    response = api.client.post(f"{UPLOADS}/sweep?project={api.away}", headers=api.headers(ALICE))
    assert response.status_code == 202, response.text
    body = response.json()
    assert (body["project_id"], body["kind"], body["status"]) == (
        str(api.away),
        service.SWEEP_KIND,
        "queued",
    )
    assert [(str(r.job_id), r.project_id, r.queue) for r in dispatched] == [
        (body["job_id"], api.away, "batch")
    ]
    assert api.chain() == [
        {"event": "upload_sweep_queued", "project_id": str(api.away), "job_id": body["job_id"]}
    ]


def test_the_sweep_needs_a_live_project(api: Api, dispatched: list[JobRef]) -> None:
    alice = api.headers(ALICE)
    assert api.client.post(f"{UPLOADS}/sweep", headers=alice).status_code == 422
    unknown = api.client.post(f"{UPLOADS}/sweep?project={uuid.uuid4()}", headers=alice)
    assert unknown.status_code == 404
    with api.db.unit_of_work(ROOT) as s:
        projects.delete_project(s, api.away)
    gone = api.client.post(f"{UPLOADS}/sweep?project={api.away}", headers=alice)
    assert gone.status_code == 404
    assert dispatched == []
    assert api.chain() == []


# --- never across tenants (ADR 0016) --------------------------------------------------------


def test_another_tenants_uploads_are_invisible_and_untouchable(
    api: Api, dispatched: list[JobRef]
) -> None:
    seed_tenants(api.db, T2)
    foreign_project = _project(api.db, "FOREIGN", tenant=T2)
    foreign = api.file(foreign_project, "verifying", tenant=T2, filename="foreign.png")
    api.file(api.home, "verifying")
    assert [r["project_code"] for r in api.get(UPLOADS)["items"]] == ["HOME"]
    assert [c["project_code"] for c in api.get(f"{UPLOADS}/counts")] == ["HOME"]
    assert api.get(UPLOADS, project=str(foreign_project))["total"] == 0
    alice = api.headers(ALICE)
    recheck = api.client.post(f"{UPLOADS}/{foreign}/recheck", headers=alice)
    sweep = api.client.post(f"{UPLOADS}/sweep?project={foreign_project}", headers=alice)
    assert (recheck.status_code, sweep.status_code) == (404, 404)
    assert dispatched == []
    assert api.chain() == []
    with api.db.unit_of_work(TenancyContext(tenant_id=T2, project_id=foreign_project)) as s:
        assert service.get_file(s, foreign).job_id is None


def test_the_monitor_needs_a_tenant_level_session(db: Database) -> None:
    project = _project(db, "HOME")
    with db.unit_of_work(TenancyContext(tenant_id=T1, project_id=project)) as s:
        with pytest.raises(RuntimeError, match="tenant-level"):
            service.quarantine_files(s)
        with pytest.raises(RuntimeError, match="tenant-level"):
            service.quarantine_counts(s)


# --- PostgreSQL contract: as kerp_app, normal RLS, no bypass (ADR 0016) ---------------------


@pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL RLS; runs in CI's PostgreSQL step (TD-052)"
)
def test_postgres_contract_the_admin_reads_b_through_normal_rls_without_a_bypass(
    api: Api, dispatched: list[JobRef]
) -> None:
    # The runtime role is the NOSUPERUSER NOBYPASSRLS kerp_app; oe_uploads_file keeps exactly
    # the standard project policies (no uploads-specific policy), and no SECURITY DEFINER
    # function touches it.
    with api.db.engine.connect() as conn:
        role = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        assert tuple(role) == (False, False)
        policies = {
            table: set(
                conn.execute(
                    text("SELECT policyname FROM pg_policies WHERE tablename = :t"), {"t": table}
                ).scalars()
            )
            for table in (TABLE, JOBS_TABLE)
        }
        definers = conn.execute(
            text("SELECT proname FROM pg_proc WHERE prosecdef AND prosrc ILIKE :t"),
            {"t": f"%{TABLE}%"},
        ).scalars()
        assert list(definers) == []
    assert policies[TABLE] == set(POLICY_NAMES) == policies[JOBS_TABLE]
    # ALICE (system_admin, no membership on B = AWAY) sees B's rows and counts and re-checks
    # a B file; BOB, PER-09 of B, gets 403 on every endpoint.
    stuck = api.file(api.away, "verifying")
    api.file(api.away, "rejected", reason="infected", signature=SIGNATURE, staged=False)
    assert {r["id"] for r in api.get(UPLOADS, project=str(api.away))["items"]} >= {str(stuck)}
    assert sum(c["count"] for c in api.get(f"{UPLOADS}/counts", project=str(api.away))) == 2
    recheck = api.client.post(f"{UPLOADS}/{stuck}/recheck", headers=api.headers(ALICE))
    assert recheck.status_code == 202, recheck.text
    assert api.row(stuck, api.away).job_id is not None
    for method, url in _calls(stuck, api.away):
        response = api.client.request(method, url, headers=api.headers(BOB))
        assert response.status_code == 403, (method, url)
