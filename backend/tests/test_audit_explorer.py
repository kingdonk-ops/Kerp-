"""System console audit explorer (T-0222, diff §4.7).

``/api/v1/admin/system/audit...``: ``system_admin`` only (403 for everyone else, a PER-09 of
every project included); read-only over the hash-chain ledger; filters and keyset paging;
verify one chain synchronously, every readable chain as a ``batch`` job. Runs on SQLite and,
in CI, on PostgreSQL under RLS. All data is synthetic.
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.audit import service as audit
from app.core.audit.hashing import ChainProblem, find_problems, verify_entries
from app.core.auth.access import SYSTEM_ADMIN, Persona
from app.core.db import Database
from app.core.tenancy.context import TenancyContext, current_impersonator_id
from app.modules.admin import audit_explorer, audit_jobs
from app.modules.admin import service as admin
from app.modules.jobs import service as jobs
from app.modules.jobs.broker import JobRef
from app.modules.jobs.models import Job
from app.modules.jobs.registry import JOB_REGISTRY, JobContext
from app.modules.jobs.worker import run_job
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import OWNER_URL, migrated_database, tamper
from tests.auth_support import (
    ALICE,
    BOB,
    PASSWORD,
    T1,
    T2,
    auth_app,
    grant_persona,
    make_settings,
    seed_user,
)
from tests.db_support import ON_POSTGRES, database_url, seed_tenants

AUDIT = "/api/v1/admin/system/audit"
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
CAROL = "carol@example.test"
DEVICE = uuid.UUID("00000000-0000-4000-8000-00000000d001")
START = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
CONSOLE = f"admin-system:{T1}"

needs_tamper_role = pytest.mark.skipif(
    ON_POSTGRES and not OWNER_URL,
    reason="tampering on PostgreSQL needs KERP_TEST_OWNER_URL (set in CI) (TD-052)",
)


class Api:
    """ALICE: System admin with no membership anywhere (ADR 0016). BOB: PER-09 of every
    project. CAROL: no grant at all. Entries are written at ``START`` + n minutes."""

    def __init__(self, client: TestClient, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        self.client, self.db, self._patch = client, db, monkeypatch
        self.users = {e: seed_user(db, T1, e) for e in (ALICE, BOB, CAROL)}
        with db.unit_of_work(ROOT) as s:
            assert admin.grant(s, self.users[ALICE], SYSTEM_ADMIN, actor_id=None)
            self.home, self.away = (
                projects.create_project(s, ProjectCreate(code=c, name=f"Synthetic {c}")).id
                for c in ("HOME", "AWAY")
            )
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

    def entry(
        self,
        key: str,
        payload: dict[str, Any],
        *,
        minute: int = 0,
        project: uuid.UUID | None = None,
        tenant: uuid.UUID = T1,
        actor: uuid.UUID | None = None,
        device: uuid.UUID | None = None,
        impersonator: uuid.UUID | None = None,
    ) -> str:
        """``impersonator`` stands for an impersonation request's context (T-0279)."""
        with self._patch.context() as patch:
            patch.setattr(audit, "utcnow", lambda: START + timedelta(minutes=minute))
            reset = current_impersonator_id.set(impersonator)
            try:
                scope = TenancyContext(tenant_id=tenant, project_id=project)
                with self.db.unit_of_work(scope) as s:
                    return str(audit.append(s, key, payload, actor_id=actor, device_id=device).id)
            finally:
                current_impersonator_id.reset(reset)

    def get(self, **params: Any) -> dict[str, Any]:
        response = self.client.get(AUDIT, params=params, headers=self.headers(ALICE))
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def ids(self, **params: Any) -> list[str]:
        return [item["id"] for item in self.get(**params)["items"]]

    def verify(self, key: str, project: uuid.UUID | None = None) -> Any:
        body = {"chain_key": key, "project_id": str(project) if project else None}
        return self.client.post(f"{AUDIT}/verify", json=body, headers=self.headers(ALICE))

    def console(self) -> list[dict[str, Any]]:
        with self.db.unit_of_work(ROOT) as s:
            return [e.payload for e in audit.chain_entries(s, CONSOLE)]

    def ledger(self) -> list[tuple[Any, ...]]:
        with self.db.unit_of_work(ROOT) as s:
            rows = audit.list_entries(s, audit.EntryFilter(), limit=1000)
        return sorted((str(r.id), r.seq, r.content_hash, r.revision, r.updated_at) for r in rows)


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
def api(
    tmp_path: Path, db: Database, dispatched: list[JobRef], monkeypatch: pytest.MonkeyPatch
) -> Iterator[Api]:
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        jobs.set_dispatcher(dispatched.extend)  # after the lifespan, which installs its own
        yield Api(client, db, monkeypatch)


CALLS = [
    ("GET", AUDIT, None),
    ("POST", f"{AUDIT}/verify", {"chain_key": "t0222:x", "project_id": None}),
    ("POST", f"{AUDIT}/verify-all", None),
]


# --- access ---------------------------------------------------------------------------------


def test_every_endpoint_needs_a_token(api: Api) -> None:
    assert [api.client.request(m, u, json=b).status_code for m, u, b in CALLS] == [401] * 3


@pytest.mark.parametrize("email", [BOB, CAROL])
def test_every_endpoint_is_forbidden_to_everyone_but_a_system_admin(
    api: Api, dispatched: list[JobRef], email: str
) -> None:
    # BOB holds PER-09 of every project; CAROL holds nothing. Both get 403 on every endpoint:
    # no entry is shown, nothing is audited and no job is queued.
    api.entry("t0222:x", {"event": "synthetic-secret-event"}, project=api.home)
    for method, url, body in CALLS:
        response = api.client.request(method, url, json=body, headers=api.headers(email))
        assert response.status_code == 403, (method, url, response.text)
        assert "synthetic-secret-event" not in response.text
    assert api.console() == []
    assert dispatched == []


def test_the_explorer_has_no_endpoint_that_changes_a_ledger_row(api: Api) -> None:
    app: Any = api.client.app
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(AUDIT)
        for method in operations
    }
    assert routes == {(m, u) for m, u, _ in CALLS}
    api.entry("t0222:x", {"event": "created"}, project=api.home)
    before = api.ledger()
    for method, url, body in CALLS:
        api.client.request(method, url, json=body, headers=api.headers(ALICE))
    for method in ("PUT", "PATCH", "DELETE"):
        assert api.client.request(method, AUDIT, headers=api.headers(ALICE)).status_code == 405
    assert [row for row in api.ledger() if row in before] == before  # appended to, never changed


# --- list: scope ----------------------------------------------------------------------------


def test_a_system_admin_without_membership_reads_tenant_and_every_project_chain(
    api: Api,
) -> None:
    seed_tenants(api.db, T2)
    tenant_level = api.entry("t0222:x", {"event": "a"}, minute=1)
    home = api.entry("t0222:x", {"event": "b"}, minute=2, project=api.home)
    away = api.entry("t0222:y", {"event": "c"}, minute=3, project=api.away)
    api.entry("t0222:x", {"event": "other-tenant"}, minute=4, tenant=T2)
    api.entry("t0222:z", {"event": "other-tenant"}, minute=4, tenant=T2, project=uuid.uuid4())
    items = api.get(chain_key_prefix="t0222:")["items"]
    assert [i["id"] for i in items] == [away, home, tenant_level]  # newest first
    assert [i["project_id"] for i in items] == [str(api.away), str(api.home), None]
    assert items[1] | {"prev_hash": "", "content_hash": ""} == {
        "id": home,
        "project_id": str(api.home),
        "chain_key": "t0222:x",
        "seq": 1,
        "created_at": items[1]["created_at"],
        "actor_id": None,
        "device_id": None,
        "impersonated_by_user_id": None,
        "source": "system",
        "payload": {"event": "b"},
        "hash_version": 2,
        "prev_hash": "",
        "content_hash": "",
    }
    assert items[1]["created_at"].startswith("2026-11-01T08:02:00")
    assert "other-tenant" not in api.client.get(AUDIT, headers=api.headers(ALICE)).text


# --- list: filters --------------------------------------------------------------------------


@pytest.fixture
def seeded(api: Api) -> dict[str, str]:
    """Six entries, one per minute, in three chains."""
    alice, bob = api.users[ALICE], api.users[BOB]
    return {
        "a": api.entry("t0222-a:1", {"event": "assign", "sod_warning": True}, minute=1, actor=bob),
        "b": api.entry("t0222-a:1", {"event": "assign", "sod_warning": False}, minute=2),
        "c": api.entry("t0222-a:2", {"event": "remove"}, minute=3, project=api.home, actor=alice),
        "d": api.entry(
            "t0222-b:1", {"event": "signed", "sod_warning": "true"}, minute=4, project=api.home
        ),
        "e": api.entry(
            "t0222-b:1",
            {"event": "assign", "sod_warning": True, "nested": {"event": "remove"}},
            minute=5,
            project=api.away,
            actor=bob,
            device=DEVICE,
        ),
        "f": api.entry("t0222_a:1", {"note": "no event key", "sod_warning": 1}, minute=6),
    }


def _at(minute: int) -> str:
    return (START + timedelta(minutes=minute)).isoformat()


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, "fedcba"),
        ({"chain_key_prefix": "t0222-a:"}, "cba"),
        ({"chain_key_prefix": "t0222_a"}, "f"),  # "_" and "%" are literal, not wildcards
        ({"chain_key_prefix": "t0222%"}, ""),
        ({"actor_id": "BOB"}, "ea"),
        ({"device_id": str(DEVICE)}, "e"),
        ({"event": "assign"}, "eba"),
        ({"event": "remove"}, "c"),
        ({"event": "ASSIGN"}, ""),
        ({"flag": "sod_warning"}, "ea"),  # the boolean true only: not false, "true" or 1
        ({"flag": "event"}, ""),
        ({"created_from": _at(2), "created_to": _at(5)}, "dcb"),  # from inclusive, to exclusive
        ({"created_from": _at(5)}, "fe"),
        ({"created_to": _at(2)}, "a"),
        ({"event": "assign", "flag": "sod_warning", "actor_id": "BOB", "created_to": _at(5)}, "a"),
    ],
)
def test_filters(
    api: Api, seeded: dict[str, str], params: dict[str, str], expected: str
) -> None:
    if params.get("actor_id") == "BOB":
        params = params | {"actor_id": str(api.users[BOB])}
    params = {"chain_key_prefix": "t0222"} | params
    assert api.ids(**params) == [seeded[name] for name in expected]


def test_entries_written_while_impersonating_name_the_admin_and_filter_by_it(api: Api) -> None:
    # T-0279, ADR 0015 §6: who really did it is found through the impersonator filter.
    alice, bob = api.users[ALICE], api.users[BOB]
    plain = api.entry("t0279:1", {"event": "x"}, minute=1, actor=bob, project=api.home)
    acted = api.entry(
        "t0279:1", {"event": "x"}, minute=2, actor=bob, project=api.home, impersonator=alice
    )
    items = api.get(chain_key_prefix="t0279:")["items"]
    seen = [
        (i["id"], i["actor_id"], i["impersonated_by_user_id"], i["hash_version"]) for i in items
    ]
    assert seen == [(acted, str(bob), str(alice), 3), (plain, str(bob), None, 2)]
    assert api.ids(chain_key_prefix="t0279:", impersonated_by_user_id=str(alice)) == [acted]
    assert api.ids(chain_key_prefix="t0279:", impersonated_by_user_id=str(bob)) == []
    assert api.ids(chain_key_prefix="t0279:", actor_id=str(bob)) == [acted, plain]
    assert api.verify("t0279:1", api.home).json()["ok"] is True


@pytest.mark.parametrize(
    "params",
    [
        {"impersonated_by_user_id": "not-a-uuid"},
        {"flag": "sod warning"},
        {"flag": '$."x"'},
        {"actor_id": "not-a-uuid"},
        {"created_from": "2026-11-01T08:00:00"},  # no UTC offset
        {"cursor": "not-a-cursor"},
        {"limit": "0"},
        {"limit": "201"},
        {"event": "x" * 101},
    ],
)
def test_invalid_filters_are_422(api: Api, params: dict[str, str]) -> None:
    response = api.client.get(AUDIT, params=params, headers=api.headers(ALICE))
    assert response.status_code == 422, response.text


# --- list: keyset paging --------------------------------------------------------------------


def test_keyset_paging_walks_every_entry_once_newest_first(api: Api) -> None:
    # Five entries, three of them in the same microsecond (the id breaks the tie).
    made = [api.entry(f"t0222:{n}", {"n": n}, minute=min(n, 2)) for n in range(5)]
    pages, cursor = [], None
    for _ in range(4):
        params = {"chain_key_prefix": "t0222:", "limit": 2} | ({"cursor": cursor} if cursor else {})
        body = api.get(**params)
        pages.append([item["id"] for item in body["items"]])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert [len(p) for p in pages] == [2, 2, 1]
    walked = [i for page in pages for i in page]
    assert sorted(walked) == sorted(made)
    assert walked == api.ids(chain_key_prefix="t0222:", limit=200)
    assert walked[3:] == [made[1], made[0]]
    assert api.get(chain_key_prefix="t0222:", limit=5)["next_cursor"] is None


def test_an_entry_written_while_paging_does_not_shift_the_next_page(api: Api) -> None:
    first, second, third = (api.entry("t0222:p", {"n": n}, minute=n) for n in range(3))
    page = api.get(chain_key_prefix="t0222:", limit=2)
    assert [i["id"] for i in page["items"]] == [third, second]
    api.entry("t0222:p", {"n": 9}, minute=9)
    assert api.ids(chain_key_prefix="t0222:", limit=2, cursor=page["next_cursor"]) == [first]


# --- verify one chain -----------------------------------------------------------------------


def test_verify_reports_an_intact_chain_of_its_own_scope_and_is_audited(api: Api) -> None:
    # The same key in three scopes is three chains: the scope is named, never guessed from
    # what the admin can see (visibility is not scope).
    api.entry("t0222:k", {"n": 1})
    for n in range(2):
        api.entry("t0222:k", {"n": n}, project=api.home)
    for n in range(3):
        api.entry("t0222:k", {"n": n}, project=api.away)
    lengths = []
    for project in (None, api.home, api.away):
        response = api.verify("t0222:k", project)
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["ok"], body["first_broken_seq"], body["problems"]) == (True, None, [])
        assert body["problem_count"] == 0
        assert (body["chain_key"], body["project_id"]) == ("t0222:k", project and str(project))
        assert len(body["head_hash"]) == 64
        lengths.append(body["length"])
    assert lengths == [1, 2, 3]
    assert api.console()[-1] == {
        "event": "audit_chain_verified",
        "chain_key": "t0222:k",
        "project_id": str(api.away),
        "ok": True,
        "length": 3,
        "first_broken_seq": None,
    }
    with api.db.unit_of_work(ROOT) as s:
        assert audit.chain_entries(s, CONSOLE)[-1].created_by == api.users[ALICE]


def test_verify_of_an_unknown_or_invisible_chain_is_404(api: Api) -> None:
    seed_tenants(api.db, T2)
    other = uuid.uuid4()
    api.entry("t0222:k", {"n": 1}, project=api.home)
    api.entry("t0222:t2", {"n": 1}, tenant=T2)
    api.entry("t0222:t2", {"n": 1}, tenant=T2, project=other)
    for key, project in (
        ("t0222:missing", None),
        ("t0222:k", None),  # exists in HOME only
        ("t0222:k", api.away),
        ("t0222:t2", None),
        ("t0222:t2", other),
    ):
        assert api.verify(key, project).status_code == 404, (key, project)
    assert api.console() == []
    bad = {"chain_key": "", "project_id": None}
    extra = {"chain_key": "t0222:k", "tenant_id": str(T2)}
    for body in (bad, extra):
        response = api.client.post(f"{AUDIT}/verify", json=body, headers=api.headers(ALICE))
        assert response.status_code == 422, response.text


def _three(api: Api, key: str, project: uuid.UUID | None) -> None:
    for n in range(1, 4):
        api.entry(key, {"step": n}, project=project)


@needs_tamper_role
def test_verify_reports_the_exact_seq_of_an_edited_entry(api: Api) -> None:
    _three(api, "t0222:tampered", api.home)
    _three(api, "t0222:tampered", api.away)  # the same key in another project stays intact
    cast = "CAST(:p AS jsonb)" if ON_POSTGRES else ":p"
    changed = tamper(
        api.db,
        f"UPDATE audit_ledger SET payload = {cast} WHERE chain_key = 't0222:tampered' "
        "AND seq = 2 AND project_id = :project",
        {"p": '{"step": 2, "by": "forger"}', "project": str(api.home)},
    )
    assert changed == 1
    body = api.verify("t0222:tampered", api.home).json()
    assert (body["ok"], body["length"], body["first_broken_seq"]) == (False, 3, 2)
    assert body["problems"] == ["seq 2: content_hash does not match the entry"]
    assert body["problem_count"] == 1
    assert api.verify("t0222:tampered", api.away).json()["ok"] is True
    assert [(e["ok"], e["first_broken_seq"]) for e in api.console()] == [(False, 2), (True, None)]


@needs_tamper_role
def test_verify_reports_the_seq_after_a_deleted_entry(api: Api) -> None:
    _three(api, "t0222:gap", None)
    assert tamper(api.db, "DELETE FROM audit_ledger WHERE chain_key = 't0222:gap' AND seq = 2") == 1
    body = api.verify("t0222:gap").json()
    assert (body["ok"], body["length"], body["first_broken_seq"]) == (False, 2, 3)
    assert body["problems"] == ["seq 3: expected seq 2 (entry missing or out of order)"]


def test_find_problems_is_structured_and_verify_entries_keeps_its_texts(api: Api) -> None:
    _three(api, "t0222:pure", None)
    with api.db.unit_of_work(ROOT) as s:
        entries = audit.chain_entries(s, "t0222:pure")
        assert find_problems(entries) == []
        assert find_problems(entries[1:]) == [
            ChainProblem(2, "expected seq 1 (entry missing or out of order)")
        ]
        assert verify_entries(entries[1:]) == [
            "seq 2: expected seq 1 (entry missing or out of order)"
        ]
        assert audit.verify_chain(s, "t0222:pure").first_broken_seq is None


# --- verify all: a batch job ----------------------------------------------------------------


def _job(api: Api, job_id: str) -> Job:
    with api.db.unit_of_work(ROOT) as s:
        return jobs.get_job(s, uuid.UUID(job_id))


def _run(api: Api, ref: JobRef) -> None:
    """What the batch worker does: bind the job's own scope and run its handler."""
    worker_ctx: Any = {"db": api.db, "registry": JOB_REGISTRY}
    asyncio.run(run_job(worker_ctx, **ref.kwargs()))  # type: ignore[arg-type]


def test_verify_all_queues_an_audited_batch_job(api: Api, dispatched: list[JobRef]) -> None:
    response = api.client.post(f"{AUDIT}/verify-all", headers=api.headers(ALICE))
    assert response.status_code == 202, response.text
    body = response.json()
    assert (body["kind"], body["status"]) == ("admin.audit_verify_all", "queued")
    assert [(str(r.job_id), r.queue, r.project_id) for r in dispatched] == [
        (body["id"], "batch", None)
    ]
    job = _job(api, body["id"])
    assert (job.queue, job.created_by) == ("batch", api.users[ALICE])
    assert job.payload == {"requested_by": str(api.users[ALICE])}
    assert api.console() == [{"event": "audit_verify_all_requested", "job_id": body["id"]}]


def test_the_job_verifies_every_readable_chain_and_stores_the_report(
    api: Api, dispatched: list[JobRef]
) -> None:
    seed_tenants(api.db, T2)
    _three(api, "t0222:all", None)
    _three(api, "t0222:all", api.home)
    _three(api, "t0222:other", api.away)
    _three(api, "t0222:t2", None)  # read below as T1: counted
    api.entry("t0222:t2", {"n": 1}, tenant=T2)  # another tenant's: never read
    with api.db.unit_of_work(ROOT) as s:
        before = audit.verify_readable_chains(s)
    assert (before.broken, before.first_broken) == (0, None)
    job_id = api.client.post(f"{AUDIT}/verify-all", headers=api.headers(ALICE)).json()["id"]
    _run(api, dispatched[-1])
    polled = api.client.get(f"/api/v1/jobs/{job_id}", headers=api.headers(ALICE)).json()
    assert polled["status"] == "succeeded", polled
    # One more chain entry than before the request: its own audit entry on the console chain.
    assert polled["result"] == {
        "ok": True,
        "chains": before.chains + 1,
        "entries": before.entries + 1,
        "broken_chains": 0,
        "first_broken": None,
    }
    assert api.console()[-1] == {"event": "audit_verify_all_finished", "job_id": job_id} | (
        polled["result"]
    )
    with api.db.unit_of_work(ROOT) as s:
        assert audit.chain_entries(s, CONSOLE)[-1].created_by == api.users[ALICE]


@needs_tamper_role
def test_the_job_reports_the_first_broken_chain_and_seq(
    api: Api, dispatched: list[JobRef]
) -> None:
    _three(api, "t0222:all", None)
    _three(api, "t0222:all", api.home)
    _three(api, "t0222:zz", api.home)
    for key, seq in (("t0222:all", 3), ("t0222:zz", 1)):
        changed = tamper(
            api.db,
            "UPDATE audit_ledger SET source = 'ui' WHERE chain_key = :key AND seq = :seq "
            "AND project_id = :project",
            {"key": key, "seq": seq, "project": str(api.home)},
        )
        assert changed == 1
    job_id = api.client.post(f"{AUDIT}/verify-all", headers=api.headers(ALICE)).json()["id"]
    _run(api, dispatched[-1])
    result = _job(api, job_id).result
    assert result is not None
    assert (result["ok"], result["broken_chains"]) == (False, 2)
    assert result["first_broken"] == {
        "project_id": str(api.home),
        "chain_key": "t0222:all",
        "seq": 3,
    }
    assert api.console()[-1]["first_broken"] == result["first_broken"]


def _context(api: Api, payload: dict[str, Any]) -> JobContext:
    async def update(progress: int, message: str | None = None) -> None:
        return None

    scope = TenancyContext(tenant_id=T1)  # what the worker binds: no admin flag
    kind = audit_explorer.VERIFY_ALL_KIND
    return JobContext(uuid.uuid4(), kind, scope, payload, None, update, api.db)


@pytest.mark.parametrize("requester", [BOB, CAROL, None, "junk"])
def test_the_job_refuses_a_requester_who_is_not_a_system_admin(
    api: Api, requester: str | None
) -> None:
    # The worker binds no admin flag; the job binds it only after re-checking the grant of the
    # user who asked (ADR 0016). A PER-09 of every project is not enough.
    payload: dict[str, Any] = {}
    if requester is not None:
        payload["requested_by"] = str(api.users.get(requester, requester))
    with pytest.raises(jobs.JobError):
        asyncio.run(audit_jobs.verify_all(_context(api, payload)))
    assert api.console() == []
    result = asyncio.run(
        audit_jobs.verify_all(_context(api, {"requested_by": str(api.users[ALICE])}))
    )
    assert result["ok"] is True


def test_the_job_is_tenant_level_and_the_core_refuses_an_unsafe_payload_key(api: Api) -> None:
    alice = {"requested_by": str(api.users[ALICE])}
    in_project = TenancyContext(tenant_id=T1, project_id=api.home)
    with pytest.raises(jobs.JobError, match="tenant-level"):
        audit_explorer.run_verify_all(api.db, in_project, uuid.uuid4(), alice)
    with api.db.unit_of_work(ROOT) as s, pytest.raises(ValueError, match="payload key"):
        audit.list_entries(s, audit.EntryFilter(flag='x"]'), limit=1)
