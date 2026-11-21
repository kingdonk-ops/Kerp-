"""Per-project legal hold (T-0223; diff §4.7 Legal hold).

A System admin sets or clears ``legal_hold`` with a reason through
``PUT /api/v1/admin/system/projects/{id}/legal-hold`` (403 for everyone else, a PER-09
included); each change is written to the tenant-level console chain ``admin-system:<tenant>``.
While it is on, soft-deleting the project answers 409 ``LEGAL_HOLD_ACTIVE`` and changes
nothing. Other modules ask ``projects.service.is_on_legal_hold``. Runs on SQLite and, in CI, on
PostgreSQL as the ``NOSUPERUSER NOBYPASSRLS`` role ``kerp_app``. All data is synthetic.
"""

import importlib.util
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.audit.service import chain_entries
from app.core.auth.access import SYSTEM_ADMIN, Persona
from app.core.auth.models import ProjectMembership
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.scope import OutOfScopeError
from app.modules.admin import service as admin
from app.modules.jobs import service as jobs
from app.modules.projects import service
from app.modules.projects.models import Project
from app.modules.projects.schemas import (
    MAX_LEGAL_HOLD_REASON,
    LegalHoldChange,
    ProjectCreate,
    ProjectRead,
)
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
from tests.db_support import ON_POSTGRES, database_url

PROJECTS = "/api/v1/projects"
HOLD = "/api/v1/admin/system/projects/{}/legal-hold"
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
CAROL = "carol@example.test"
CONSOLE = f"admin-system:{T1}"


def _project(db: Database, code: str, tenant: uuid.UUID = T1) -> uuid.UUID:
    ctx = TenancyContext(tenant_id=tenant, is_system_admin=True)
    with db.unit_of_work(ctx) as s:
        return service.create_project(s, ProjectCreate(code=code, name=f"Synthetic {code}")).id


def _console(db: Database) -> list[dict[str, Any]]:
    with db.unit_of_work(ROOT) as s:
        return [e.payload for e in chain_entries(s, CONSOLE)]


def _project_chain(db: Database, project_id: uuid.UUID) -> list[dict[str, Any]]:
    with db.unit_of_work(ROOT) as s:
        return [e.payload for e in chain_entries(s, f"project:{project_id}")]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


# --- model, schema, chain name --------------------------------------------------------------


def test_a_new_project_is_not_on_hold(db: Database) -> None:
    project_id = _project(db, "HOME")
    with db.unit_of_work(ROOT) as s:
        project = service.get_project(s, project_id)
        assert project.legal_hold is False
        assert (project.legal_hold_reason, project.legal_hold_set_by) == (None, None)
        assert project.legal_hold_set_at is None
        assert service.is_on_legal_hold(s, project_id) is False


def test_project_read_shows_the_flag_but_not_the_reason() -> None:
    assert "legal_hold" in ProjectRead.model_fields
    assert "legal_hold_reason" not in ProjectRead.model_fields


def test_the_hold_is_audited_in_the_system_console_chain() -> None:
    assert service.LEGAL_HOLD_CHAIN.format(tenant_id=T1) == jobs.CONSOLE_CHAIN.format(tenant_id=T1)


@pytest.mark.parametrize("reason", ["", "   ", "x" * (MAX_LEGAL_HOLD_REASON + 1)])
@pytest.mark.parametrize("on", [True, False])
def test_a_reason_of_1_to_500_characters_is_required_both_ways(on: bool, reason: str) -> None:
    with pytest.raises(ValidationError, match="reason"):
        LegalHoldChange.model_validate({"on": on, "reason": reason})
    with pytest.raises(ValidationError, match="reason"):
        LegalHoldChange.model_validate({"on": on})
    assert LegalHoldChange.model_validate({"on": on, "reason": " ok "}).reason == "ok"
    assert len(LegalHoldChange(on=on, reason="x" * MAX_LEGAL_HOLD_REASON).reason) == 500


# --- service --------------------------------------------------------------------------------


def test_setting_and_clearing_the_hold_is_audited_on_the_tenant_chain(db: Database) -> None:
    project_id = _project(db, "HOME")
    actor = uuid.uuid4()
    before = _project_chain(db, project_id)
    with db.unit_of_work(ROOT) as s:
        project = service.set_legal_hold(s, project_id, True, "Synthetic dispute", actor_id=actor)
        assert project.legal_hold is True
        assert (project.legal_hold_reason, project.legal_hold_set_by) == (
            "Synthetic dispute",
            actor,
        )
        assert project.legal_hold_set_at is not None
    with db.unit_of_work(ROOT) as s:
        assert service.is_on_legal_hold(s, project_id) is True
        service.set_legal_hold(s, project_id, False, "Synthetic settlement", actor_id=actor)
    with db.unit_of_work(ROOT) as s:
        project = service.get_project(s, project_id)
        assert project.legal_hold is False
        assert project.legal_hold_reason is None
        assert (project.legal_hold_set_by, project.legal_hold_set_at) == (None, None)
        assert service.is_on_legal_hold(s, project_id) is False
        entries = chain_entries(s, CONSOLE)
    assert [e.payload for e in entries] == [
        {
            "event": "legal_hold_set",
            "project_id": str(project_id),
            "code": "HOME",
            "reason": "Synthetic dispute",
        },
        {
            "event": "legal_hold_cleared",
            "project_id": str(project_id),
            "code": "HOME",
            "reason": "Synthetic settlement",
        },
    ]
    assert {e.created_by for e in entries} == {actor}
    assert {e.project_id for e in entries} == {None}  # tenant level
    # A System admin need not be a member: nothing goes to the project's own chain.
    assert _project_chain(db, project_id) == before


def test_a_call_that_changes_nothing_writes_nothing(db: Database) -> None:
    project_id = _project(db, "HOME")
    with db.unit_of_work(ROOT) as s:
        service.set_legal_hold(s, project_id, False, "Not on anyway", actor_id=None)
    assert _console(db) == []
    with db.unit_of_work(ROOT) as s:
        service.set_legal_hold(s, project_id, True, "Synthetic dispute", actor_id=None)
        service.set_legal_hold(s, project_id, True, "Synthetic dispute", actor_id=None)
    assert [p["event"] for p in _console(db)] == ["legal_hold_set"]
    # A new reason while on is a change: recorded with the new reason.
    with db.unit_of_work(ROOT) as s:
        project = service.set_legal_hold(s, project_id, True, "Second dispute", actor_id=None)
        assert project.legal_hold_reason == "Second dispute"
    assert [p["reason"] for p in _console(db)] == ["Synthetic dispute", "Second dispute"]


def test_unknown_deleted_and_other_tenant_projects_are_not_found(db: Database) -> None:
    other = _project(db, "AWAY", tenant=T2)
    gone = _project(db, "GONE")
    with db.unit_of_work(ROOT) as s:
        service.delete_project(s, gone)
    for project_id in (uuid.uuid4(), other, gone):
        with db.unit_of_work(ROOT) as s, pytest.raises(OutOfScopeError):
            service.set_legal_hold(s, project_id, True, "Synthetic", actor_id=None)
        with db.unit_of_work(ROOT) as s, pytest.raises(OutOfScopeError):
            service.is_on_legal_hold(s, project_id)
    assert _console(db) == []


def test_the_hold_needs_a_tenant_level_session(db: Database) -> None:
    project_id = _project(db, "HOME")
    with db.unit_of_work(TenancyContext(tenant_id=T1, project_id=project_id)) as s:
        assert service.is_on_legal_hold(s, project_id) is False  # readable in the project
        with pytest.raises(RuntimeError, match="tenant-level"):
            service.set_legal_hold(s, project_id, True, "Synthetic", actor_id=None)


def test_a_project_under_hold_cannot_be_soft_deleted(db: Database) -> None:
    project_id = _project(db, "HOME")
    user = _seed_user(db, T1, BOB)
    grant_persona(db, T1, user, Persona.PROJECT_ADMINISTRATOR.value, project_id)
    with db.unit_of_work(ROOT) as s:
        service.set_legal_hold(s, project_id, True, "Synthetic dispute", actor_id=None)
    chain = _project_chain(db, project_id)
    with db.unit_of_work(ROOT) as s, pytest.raises(service.LegalHoldActiveError):
        service.delete_project(s, project_id)
    with db.unit_of_work(ROOT) as s:
        assert service.get_project(s, project_id).deleted_at is None
        members = s.scalars(select(ProjectMembership.user_id)).all()
    assert members == [user]
    assert _project_chain(db, project_id) == chain
    with db.unit_of_work(ROOT) as s:
        service.set_legal_hold(s, project_id, False, "Synthetic settlement", actor_id=None)
        service.delete_project(s, project_id)


@pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL row locks; runs in CI's PostgreSQL step (TD-052)"
)
def test_a_delete_waits_for_a_concurrent_hold_change(db: Database) -> None:
    # The hold change locks the row until it commits, so a delete cannot pass its check on the
    # row as it was before the hold (and the other way round).
    project_id = _project(db, "HOME")

    def delete_while_held() -> None:
        with db.unit_of_work(ROOT) as deleter:
            deleter.execute(text("SET LOCAL lock_timeout = '200ms'"))
            service.delete_project(deleter, project_id)

    with db.unit_of_work(ROOT) as holder:
        service.set_legal_hold(holder, project_id, True, "Synthetic dispute", actor_id=None)
        # It waits at its check (the locking read), not later at its own UPDATE.
        with pytest.raises(OperationalError, match=r"lock timeout(?s:.*)FOR UPDATE"):
            delete_while_held()
    with db.unit_of_work(ROOT) as s, pytest.raises(service.LegalHoldActiveError):
        service.delete_project(s, project_id)


def test_the_database_refuses_a_hold_without_a_reason(db: Database) -> None:
    project_id = _project(db, "HOME")
    with pytest.raises(IntegrityError), db.unit_of_work(ROOT) as s:
        s.execute(update(Project).where(Project.id == project_id).values(legal_hold=True))


def test_migration_0037_freezes_the_table_it_copies() -> None:
    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    spec = importlib.util.spec_from_file_location("m0037", next(versions.glob("0037_*.py")))
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    # The SQLite copy at 0037 is the live table: same columns, constraints and indexes.
    frozen = migration._frozen_project(with_hold=True)
    live = Project.__table__
    assert {c.name for c in frozen.columns} == {c.name for c in live.columns}
    assert {c.name for c in frozen.constraints} == {c.name for c in live.constraints}
    assert {i.name for i in frozen.indexes} == {i.name for i in live.indexes}
    before = migration._frozen_project(with_hold=False)
    assert {c.name for c in live.columns} - {c.name for c in before.columns} == set(
        migration.HOLD_COLUMNS
    )


# --- HTTP -----------------------------------------------------------------------------------


class Api:
    """ALICE: System admin with no membership (ADR 0016). BOB: PER-09 of HOME. CAROL: no
    grant at all."""

    def __init__(self, client: TestClient, db: Database) -> None:
        self.client, self.db = client, db
        self.users = {e: _seed_user(db, T1, e) for e in (ALICE, BOB, CAROL)}
        with db.unit_of_work(ROOT) as s:
            assert admin.grant(s, self.users[ALICE], SYSTEM_ADMIN, actor_id=None)
        self.home = _project(db, "HOME")
        grant_persona(db, T1, self.users[BOB], Persona.PROJECT_ADMINISTRATOR.value, self.home)
        self._headers: dict[str, dict[str, str]] = {}

    def headers(self, email: str) -> dict[str, str]:
        if email not in self._headers:
            body = {"email": email, "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            self._headers[email] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._headers[email]

    def hold(self, project_id: uuid.UUID, body: dict[str, Any], email: str = ALICE) -> Any:
        return self.client.put(HOLD.format(project_id), json=body, headers=self.headers(email))


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        yield Api(client, db)


def test_a_system_admin_sets_and_clears_the_hold_without_a_membership(api: Api) -> None:
    response = api.hold(api.home, {"on": True, "reason": "Synthetic dispute"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == str(api.home)
    assert (body["legal_hold"], body["legal_hold_reason"]) == (True, "Synthetic dispute")
    assert body["legal_hold_set_by"] == str(api.users[ALICE])
    assert body["legal_hold_set_at"] is not None
    read = api.client.get(f"{PROJECTS}/{api.home}", headers=api.headers(BOB))
    assert read.json()["legal_hold"] is True
    assert "legal_hold_reason" not in read.json()
    cleared = api.hold(api.home, {"on": False, "reason": "Synthetic settlement"})
    assert cleared.status_code == 200, cleared.text
    assert (cleared.json()["legal_hold"], cleared.json()["legal_hold_reason"]) == (False, None)
    assert [p["event"] for p in _console(api.db)] == ["legal_hold_set", "legal_hold_cleared"]


@pytest.mark.parametrize("email", [BOB, CAROL])
def test_everyone_but_a_system_admin_gets_403(api: Api, email: str) -> None:
    # BOB holds PER-09 of the project: still 403 (an admin surface, not a project record).
    for body in ({"on": True, "reason": "Synthetic"}, {"on": True}):
        assert api.hold(api.home, body, email).status_code == 403
    assert api.hold(uuid.uuid4(), {"on": True, "reason": "Synthetic"}, email).status_code == 403
    assert _console(api.db) == []
    unauthenticated = api.client.put(HOLD.format(api.home), json={"on": True, "reason": "x"})
    assert unauthenticated.status_code == 401


@pytest.mark.parametrize(
    "body", [{"on": True}, {"on": False}, {"on": True, "reason": ""}, {"reason": "x"}]
)
def test_a_missing_or_empty_reason_is_422(api: Api, body: dict[str, Any]) -> None:
    assert api.hold(api.home, body).status_code == 422
    long = {"on": False, "reason": "x" * (MAX_LEGAL_HOLD_REASON + 1)}
    assert api.hold(api.home, long).status_code == 422
    assert _console(api.db) == []


def test_an_unknown_other_tenant_or_deleted_project_is_404(api: Api) -> None:
    other = _project(api.db, "AWAY", tenant=T2)
    gone = _project(api.db, "GONE")
    with api.db.unit_of_work(ROOT) as s:
        service.delete_project(s, gone)
    for project_id in (uuid.uuid4(), other, gone):
        response = api.hold(project_id, {"on": True, "reason": "Synthetic"})
        assert response.status_code == 404, response.text
    assert _console(api.db) == []


def test_deleting_a_project_under_hold_is_409_and_changes_nothing(api: Api) -> None:
    assert api.hold(api.home, {"on": True, "reason": "Synthetic dispute"}).status_code == 200
    for email in (BOB, ALICE):  # its PER-09 and a System admin alike
        response = api.client.delete(f"{PROJECTS}/{api.home}", headers=api.headers(email))
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "LEGAL_HOLD_ACTIVE"
    read = api.client.get(f"{PROJECTS}/{api.home}", headers=api.headers(BOB))
    assert read.status_code == 200
    assert api.hold(api.home, {"on": False, "reason": "Settled"}).status_code == 200
    deleted = api.client.delete(f"{PROJECTS}/{api.home}", headers=api.headers(BOB))
    assert deleted.status_code == 204
