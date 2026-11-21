"""Access-request lifecycle: file, follow, withdraw, approve, reject, cooldown, lookup tickets
(T-0050, diff §4.1 access-request lifecycle; edge spec §6 amendments).

Runs on SQLite (the ORM scope mirrors the table's RLS policy) and, in CI, on PostgreSQL under
RLS against the migrated schema. Raw-SQL policy checks as ``kerp_app``:
``test_rls_access_requests.py``. All data is synthetic.
"""

import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.audit.service import chain_entries
from app.core.auth.access import SYSTEM_ADMIN, Grant, Persona, resolve_scope
from app.core.auth.models import ProjectMembership, User
from app.core.auth.tokens import TokenService
from app.core.db import Base, BaseRecordMixin, Database
from app.core.mail.models import MailSend
from app.core.mail.queue import QueuedMail, mail_enqueuer, set_mail_enqueuer
from app.core.mail.service import Delivery, deliver
from app.core.mail.templates import TEMPLATES, render
from app.core.mail.transport import FakeTransport, PermanentMailError
from app.core.module_loader.loader import discover_modules
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import (
    ACCESS_REQUEST_POLICY_NAMES,
    expected_policy_names,
    rls_policy_sql,
    setting_values,
    table_policy,
)
from app.core.tenancy.scope import OutOfScopeError
from app.modules.access_requests import service
from app.modules.access_requests.models import TABLE, AccessRequest
from app.modules.admin import service as admin
from app.modules.lobby import service as lobby
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import PASSWORD, T1, auth_app, make_settings, seed_user
from tests.db_support import database_url

BASE = "/api/v1/access-requests"
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
STAFF = "staff.example.test"
# ROOT_ADMIN: System admin (external, no project). PAM: PER-09 of VIS and HID. PETE: PER-09 of
# OTH. IVAN: internal, PER-02 of VIS. INES: internal, no project. EXT, EXT2: external.
ROOT_ADMIN = "root@example.test"
PAM = "pam@example.test"
PETE = "pete@example.test"
IVAN = f"ivan@{STAFF}"
INES = f"ines@{STAFF}"
EXT = "ext@example.test"
EXT2 = "ext2@example.test"
DAY = timedelta(hours=24)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    migrated = migrated_database(tmp_path)
    database = next(migrated)
    try:
        yield database
    finally:
        next(migrated, None)


def _project(db: Database, code: str, name: str) -> uuid.UUID:
    with db.unit_of_work(ROOT) as s:
        return projects.create_project(s, ProjectCreate(code=code, name=name)).id


def _grant(db: Database, user: uuid.UUID, project: uuid.UUID, persona: Persona) -> None:
    with db.unit_of_work(ROOT) as s:
        s.add(
            ProjectMembership(
                tenant_id=T1, user_id=user, member_project_id=project, persona=persona.value
            )
        )


class Api:
    def __init__(self, client: TestClient, db: Database, tokens: TokenService) -> None:
        self.client, self.db, self.tokens = client, db, tokens
        self.users = {
            e: seed_user(db, T1, e, is_internal=e.endswith(f"@{STAFF}"))
            for e in (ROOT_ADMIN, PAM, PETE, IVAN, INES, EXT, EXT2)
        }
        with db.unit_of_work(ROOT) as s:
            admin.grant(s, self.users[ROOT_ADMIN], SYSTEM_ADMIN, actor_id=None)
        self.vis = _project(db, "VIS", "Visible synthetic site")
        self.hid = _project(db, "HID", "Hidden synthetic site")
        self.oth = _project(db, "OTH", "Other synthetic site")
        self.gone = _project(db, "GONE", "Deleted synthetic site")
        _grant(db, self.users[PAM], self.vis, Persona.PROJECT_ADMINISTRATOR)
        _grant(db, self.users[PAM], self.hid, Persona.PROJECT_ADMINISTRATOR)
        _grant(db, self.users[PAM], self.gone, Persona.PROJECT_ADMINISTRATOR)
        _grant(db, self.users[PETE], self.oth, Persona.PROJECT_ADMINISTRATOR)
        _grant(db, self.users[IVAN], self.vis, Persona.PROJECT_MANAGER)
        self._headers: dict[str, dict[str, str]] = {}
        url = f"/api/v1/projects/{self.vis}/lobby"
        shown = client.put(url, json={"visible_in_lobby": True}, headers=self.headers(PAM))
        assert shown.status_code == 200, shown.text

    def headers(self, email: str) -> dict[str, str]:
        if email not in self._headers:
            body = {"email": email, "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            self._headers[email] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._headers[email]

    def ticket(self, code: str, email: str) -> str:
        found = self.client.get(
            f"/api/v1/lobby/projects/by-code/{code}", headers=self.headers(email)
        )
        assert found.status_code == 200, found.text
        ticket: str = found.json()["lookup_ticket"]
        return ticket

    def file(self, email: str, project: uuid.UUID, ticket: str | None = None, **extra: Any) -> Any:
        body: dict[str, Any] = {"project_id": str(project), **extra}
        if ticket is not None:
            body["lookup_ticket"] = ticket
        return self.client.post(BASE, json=body, headers=self.headers(email))

    def filed(self, email: str, project: uuid.UUID, ticket: str | None = None) -> str:
        response = self.file(email, project, ticket)
        assert response.status_code == 201, response.text
        request_id: str = response.json()["id"]
        return request_id

    def mine(self, email: str) -> list[dict[str, Any]]:
        response = self.client.get(f"{BASE}/mine", headers=self.headers(email))
        assert response.status_code == 200, response.text
        found: list[dict[str, Any]] = response.json()
        return found

    def act(self, email: str, request_id: str, action: str, body: Any = None) -> Any:
        url = f"{BASE}/{request_id}/{action}"
        return self.client.post(url, json=body, headers=self.headers(email))

    def approve(self, email: str, request_id: str, *personas: str, **extra: Any) -> Any:
        grants = [{"persona": p} for p in personas or ("PER-04",)]
        return self.act(email, request_id, "approve", {"grants": grants, **extra})

    def pending(self, email: str, project: uuid.UUID | None = None) -> Any:
        params = {"project_id": str(project)} if project else None
        return self.client.get(f"{BASE}/pending", params=params, headers=self.headers(email))

    def as_user(self, email: str) -> TenancyContext:
        return TenancyContext(tenant_id=T1, user_id=self.users[email])


def _api(tmp_path: Path, db: Database, **settings: Any) -> Iterator[Api]:
    values = make_settings(
        database_url=database_url(tmp_path), internal_email_domains=STAFF, **settings
    )
    app, runtime = auth_app(values)
    with TestClient(app) as client:
        yield Api(client, db, runtime.tokens)


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    yield from _api(tmp_path, db)


@pytest.fixture
def api_no_cooldown(tmp_path: Path, db: Database) -> Iterator[Api]:
    yield from _api(tmp_path, db, access_request_cooldown_hours=0)


# --- contract -------------------------------------------------------------------------------


def test_access_requests_is_a_platform_module() -> None:
    manifest = discover_modules().get("access_requests")
    assert manifest.toggleable is False
    assert set(manifest.depends_on) == {"projects", "lobby", "admin", "notifications"}
    assert manifest.route_prefix == "/access-requests"


def test_the_table_is_a_base_record_under_its_own_policy() -> None:
    assert issubclass(AccessRequest, BaseRecordMixin)
    table = Base.metadata.tables[TABLE]
    policy = table_policy(table)
    assert policy is not None
    assert policy.kind == "access_request"
    assert expected_policy_names(table) == ACCESS_REQUEST_POLICY_NAMES
    ddl = " ".join(rls_policy_sql(TABLE, policy))
    assert "FORCE ROW LEVEL SECURITY" in ddl
    assert "app.admin_project_ids" in ddl
    assert "FOR DELETE" not in ddl  # nobody deletes a request


def test_admin_projects_are_bound_from_per09_memberships_only() -> None:
    user, admin_of, member_of = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    grants = [
        Grant(admin_of, Persona.PROJECT_ADMINISTRATOR),
        Grant(member_of, Persona.PROJECT_MANAGER),
    ]
    context = resolve_scope(grants, TenancyContext(tenant_id=T1, user_id=user))
    assert context.admin_project_ids == frozenset({admin_of})
    assert setting_values(context)["app.admin_project_ids"] == str(admin_of)
    assert setting_values(TenancyContext(tenant_id=T1))["app.admin_project_ids"] == ""


def test_every_endpoint_needs_a_token(api: Api) -> None:
    some = uuid.uuid4()
    calls = [
        ("POST", BASE),
        ("GET", f"{BASE}/mine"),
        ("GET", f"{BASE}/pending"),
        ("POST", f"{BASE}/{some}/withdraw"),
        ("POST", f"{BASE}/{some}/approve"),
        ("POST", f"{BASE}/{some}/reject"),
    ]
    assert [api.client.request(m, u, json={}).status_code for m, u in calls] == [401] * 6


def test_bodies_refuse_unknown_fields_and_long_texts(api: Api) -> None:
    ticket = api.ticket("HID", EXT)
    assert api.file(EXT, api.hid, ticket, tenant_id=str(uuid.uuid4())).status_code == 422
    assert api.file(EXT, api.hid, ticket, status="approved").status_code == 422
    assert api.file(EXT, api.hid, ticket, message="x" * 1001).status_code == 422
    request_id = api.filed(EXT, api.hid, ticket)
    too_long = api.act(PAM, request_id, "reject", {"reason": "y" * 501})
    assert too_long.status_code == 422
    assert api.act(PAM, request_id, "reject", {"reason": "n", "x": 1}).status_code == 422


# --- filing: discovery ----------------------------------------------------------------------


def test_an_external_first_request_needs_a_valid_ticket(api: Api) -> None:
    assert api.file(EXT, api.hid).status_code == 404  # a leaked id alone is not enough
    assert api.file(EXT, api.vis).status_code == 404  # lobby-visible, but EXT cannot list it
    assert api.file(EXT, api.hid, "not-a-ticket").status_code == 404
    other_user = api.ticket("HID", EXT2)
    assert api.file(EXT, api.hid, other_user).status_code == 404
    other_project = api.ticket("OTH", EXT)
    assert api.file(EXT, api.hid, other_project).status_code == 404
    assert api.mine(EXT) == []  # nothing was filed
    created = api.file(EXT, api.hid, api.ticket("HID", EXT), message="  Synthetic note  ")
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["project_id"], body["project_code"], body["status"]) == (
        str(api.hid),
        "HID",
        "pending",
    )
    assert body["message"] == "Synthetic note"
    assert body["request_again_at"] is None


def test_an_expired_ticket_is_refused(api: Api) -> None:
    past = datetime.now(UTC) - timedelta(minutes=20)
    old = TokenService(
        api.tokens.keys,
        issuer=api.tokens.issuer,
        audience=api.tokens.audience,
        access_ttl=timedelta(minutes=15),
        refresh_ttl=timedelta(hours=1),
        clock=lambda: past,
    )
    expired = lobby.issue_lookup_ticket(
        old, user_id=api.users[EXT], tenant_id=T1, project_id=api.hid
    )
    assert api.file(EXT, api.hid, expired.token).status_code == 404


def test_an_internal_user_needs_a_ticket_for_a_project_not_in_the_lobby(api: Api) -> None:
    assert api.file(INES, api.hid).status_code == 404
    assert api.file(INES, api.vis).status_code == 201  # lobby-visible: no ticket needed
    assert api.file(INES, api.hid, api.ticket("HID", INES)).status_code == 201


@pytest.mark.parametrize("which", ["unknown", "deleted"])
def test_an_unknown_or_deleted_project_is_404(api: Api, which: str) -> None:
    if which == "unknown":
        project, ticket = uuid.uuid4(), None
    else:
        project, ticket = api.gone, api.ticket("GONE", EXT)
        deleted = api.client.delete(f"/api/v1/projects/{api.gone}", headers=api.headers(PAM))
        assert deleted.status_code == 204
    assert api.file(EXT, project, ticket).status_code == 404
    assert api.file(ROOT_ADMIN, project, ticket).status_code == 404


def test_a_member_or_a_second_pending_request_is_409(api: Api) -> None:
    member = api.file(IVAN, api.vis)
    assert member.status_code == 409
    assert member.json()["code"] == "ACCESS_REQUEST_ALREADY_MEMBER"
    api.filed(EXT, api.hid, api.ticket("HID", EXT))
    again = api.file(EXT, api.hid, api.ticket("HID", EXT))
    assert again.status_code == 409
    assert again.json()["code"] == "ACCESS_REQUEST_PENDING"
    assert len(api.mine(EXT)) == 1


# --- the full lifecycle ---------------------------------------------------------------------


def test_approval_creates_the_membership_and_the_project_appears(api: Api) -> None:
    request_id = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    # PAM (PER-09 of HID) is notified in-app; the requester is not (no member).
    inbox = api.client.get(
        f"/api/v1/notifications/projects/{api.hid}/notifications", headers=api.headers(PAM)
    ).json()
    assert [(n["kind"], n["subject_id"]) for n in inbox] == [
        ("access_requests.requested", request_id)
    ]
    assert inbox[0]["params"] == {"project_code": "HID", "requester": EXT}
    listed = api.pending(PAM).json()
    assert [(p["id"], p["project_code"], p["requester"]["email"]) for p in listed] == [
        (request_id, "HID", EXT)
    ]
    approved = api.approve(PAM, request_id, "PER-04", "PER-05")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["decided_by"] == str(api.users[PAM])
    assert sorted(g["persona"] for g in body["granted"]) == ["PER-04", "PER-05"]
    assert body["sod_warning"] is False
    projects_now = api.client.get("/api/v1/me/projects", headers=api.headers(EXT)).json()
    assert [(p["code"], p["personas"]) for p in projects_now] == [("HID", ["PER-04", "PER-05"])]
    mine = api.mine(EXT)
    assert [(m["id"], m["status"], m["request_again_at"]) for m in mine] == [
        (request_id, "approved", None)
    ]
    assert api.pending(PAM).json() == []
    assert api.file(EXT, api.hid).status_code == 409  # a member now
    with api.db.unit_of_work(ROOT) as s:
        trail = [e.payload for e in chain_entries(s, f"access-requests:{api.hid}")]
        members = [e.payload for e in chain_entries(s, f"project-members:{api.hid}")]
    ext = str(api.users[EXT])
    assert [(e["action"], e["user_id"], e["request_id"]) for e in trail] == [
        ("request", ext, request_id),
        ("approve", ext, request_id),
    ]
    assert trail[1]["sod_warning"] is False
    assert [g["persona"] for g in trail[1]["grants"]] == ["PER-04", "PER-05"]
    assert members[-1]["action"] == "assign"
    assert members[-1]["user_id"] == ext


def test_approval_keeps_grants_the_requester_holds_already(api: Api) -> None:
    request_id = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    _grant(api.db, api.users[EXT], api.oth, Persona.FIELD_WORKER)  # another project
    assert api.approve(ROOT_ADMIN, request_id, "PER-14").status_code == 200
    projects_now = api.client.get("/api/v1/me/projects", headers=api.headers(EXT)).json()
    assert sorted(p["code"] for p in projects_now) == ["HID", "OTH"]


def test_a_site_inspector_approval_names_its_site(api: Api) -> None:
    request_id = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    assert api.approve(PAM, request_id, "PER-07").status_code == 422
    site = str(uuid.uuid4())
    body = {"grants": [{"persona": "PER-07", "site_node_id": site}]}
    approved = api.act(PAM, request_id, "approve", body)
    assert approved.status_code == 200, approved.text
    assert approved.json()["granted"] == [{"persona": "PER-07", "site_node_id": site}]


def test_per09_with_hold_authority_needs_confirmation(api: Api) -> None:
    request_id = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    refused = api.approve(PAM, request_id, "PER-09", "PER-02")
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "sod_confirmation_required"
    assert api.mine(EXT)[0]["status"] == "pending"  # nothing changed
    confirmed = api.approve(PAM, request_id, "PER-09", "PER-02", confirm_sod=True)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["sod_warning"] is True
    with api.db.unit_of_work(ROOT) as s:
        trail = [e.payload for e in chain_entries(s, f"access-requests:{api.hid}")]
        members = [e.payload for e in chain_entries(s, f"project-members:{api.hid}")]
    assert trail[-1]["sod_warning"] is True
    assert members[-1]["sod_warning"] is True


def test_rejection_reason_cooldown_and_request_again(api: Api) -> None:
    request_id = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    rejected = api.act(PAM, request_id, "reject", {"reason": "  Synthetic: not on this job  "})
    assert rejected.status_code == 200, rejected.text
    decided_at = datetime.fromisoformat(rejected.json()["decided_at"])
    (entry,) = api.mine(EXT)
    assert (entry["status"], entry["rejection_reason"]) == (
        "rejected",
        "Synthetic: not on this job",
    )
    again_at = datetime.fromisoformat(entry["request_again_at"])
    assert again_at == decided_at + DAY
    blocked = api.file(EXT, api.hid)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "ACCESS_REQUEST_COOLDOWN"
    assert datetime.fromisoformat(blocked.json()["retry_after"]) == again_at
    # Only pending requests are decided: a second decision is 409.
    assert api.approve(PAM, request_id).status_code == 409
    assert api.act(PAM, request_id, "reject", {}).status_code == 409
    assert api.act(EXT, request_id, "withdraw").status_code == 409
    with api.db.unit_of_work(ROOT) as s:
        trail = [e.payload for e in chain_entries(s, f"access-requests:{api.hid}")]
    assert trail[-1]["action"] == "reject"
    assert trail[-1]["reason"] == "Synthetic: not on this job"


def test_the_cooldown_boundary_and_request_again_without_a_ticket(api: Api) -> None:
    request_id = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    assert api.act(PAM, request_id, "reject", None).status_code == 200
    with api.db.unit_of_work(ROOT) as s:
        row = s.get(AccessRequest, uuid.UUID(request_id))
        assert row is not None
        assert row.decided_at is not None
        decided_at = row.decided_at
    if decided_at.tzinfo is None:  # SQLite hands TIMESTAMPTZ back naive (UTC)
        decided_at = decided_at.replace(tzinfo=UTC)
    retry_after = decided_at + DAY

    def again(now: datetime) -> AccessRequest:
        with api.db.unit_of_work(api.as_user(EXT)) as s:
            request, _project = service.file_request(
                s,
                api.hid,
                message=None,
                lookup_ticket=None,
                caller_grants=[],
                is_internal=False,
                tokens=api.tokens,
                cooldown=DAY,
                now=now,
            )
            return request

    with pytest.raises(service.CooldownError) as info:
        again(retry_after - timedelta(seconds=1))
    assert info.value.retry_after == retry_after
    filed = again(retry_after)  # no ticket: the rejected row proves the discovery
    assert filed.status == "pending"


def test_a_zero_cooldown_disables_it(api_no_cooldown: Api) -> None:
    api = api_no_cooldown
    request_id = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    assert api.act(PAM, request_id, "reject", {"reason": "Synthetic"}).status_code == 200
    assert api.mine(EXT)[0]["request_again_at"] is None
    again = api.file(EXT, api.hid)  # at once, and without a ticket
    assert again.status_code == 201, again.text
    statuses = [m["status"] for m in api.mine(EXT)]
    assert statuses == ["pending"]  # the latest request per project


def test_withdraw_then_request_again_at_once_without_a_ticket(api: Api) -> None:
    request_id = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    assert api.act(INES, request_id, "withdraw").status_code == 404  # someone else's
    assert api.act(PAM, request_id, "withdraw").status_code == 404  # the decider's view
    assert api.act(EXT, str(uuid.uuid4()), "withdraw").status_code == 404
    withdrawn = api.act(EXT, request_id, "withdraw")
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "withdrawn"
    assert withdrawn.json()["withdrawn_at"] is not None
    assert api.act(EXT, request_id, "withdraw").status_code == 409  # not pending
    assert api.approve(PAM, request_id).status_code == 409
    assert api.mine(EXT)[0]["request_again_at"] is None  # no cooldown after a withdrawal
    again = api.file(EXT, api.hid)
    assert again.status_code == 201, again.text
    with api.db.unit_of_work(ROOT) as s:
        actions = [e.payload["action"] for e in chain_entries(s, f"access-requests:{api.hid}")]
    assert actions == ["request", "withdraw", "request"]


# --- who decides ----------------------------------------------------------------------------


def test_only_the_projects_per09_or_a_system_admin_decides(api: Api) -> None:
    request_id = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    assert api.approve(IVAN, request_id).status_code == 404  # no PER-09 of HID
    assert api.approve(PETE, request_id).status_code == 404  # PER-09 of another project
    assert api.approve(INES, request_id).status_code == 404
    assert api.approve(EXT, request_id).status_code == 403  # sees it: their own
    assert api.act(EXT, request_id, "reject", {}).status_code == 403
    assert api.approve(PAM, str(uuid.uuid4())).status_code == 404
    rejected = api.act(ROOT_ADMIN, request_id, "reject", {})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["decided_by"] == str(api.users[ROOT_ADMIN])
    assert rejected.json()["rejection_reason"] is None


def test_a_system_admin_never_decides_their_own_request(api: Api) -> None:
    request_id = api.filed(ROOT_ADMIN, api.hid, api.ticket("HID", ROOT_ADMIN))
    assert api.approve(ROOT_ADMIN, request_id).status_code == 403
    assert [p["id"] for p in api.pending(ROOT_ADMIN).json()] == [request_id]
    assert api.approve(PAM, request_id).status_code == 200


def test_the_pending_list_is_for_project_administrators(api: Api) -> None:
    hid_request = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    vis_request = api.filed(INES, api.vis)
    oth_request = api.filed(EXT2, api.oth, api.ticket("OTH", EXT2))
    assert [p["id"] for p in api.pending(PAM).json()] == [hid_request, vis_request]
    assert [p["id"] for p in api.pending(PAM, api.vis).json()] == [vis_request]
    assert [p["id"] for p in api.pending(PETE).json()] == [oth_request]
    everything = api.pending(ROOT_ADMIN).json()
    assert {p["id"] for p in everything} == {hid_request, vis_request, oth_request}
    assert [p["id"] for p in api.pending(ROOT_ADMIN, api.oth).json()] == [oth_request]
    assert api.pending(ROOT_ADMIN, api.gone).json() == []
    assert api.pending(IVAN, api.vis).status_code == 403  # member without PER-09
    assert api.pending(IVAN).status_code == 403
    assert api.pending(INES, api.vis).status_code == 404  # not a member
    assert api.pending(PETE, api.hid).status_code == 404
    assert api.pending(ROOT_ADMIN, uuid.uuid4()).status_code == 404
    first = api.pending(PAM, api.hid).json()[0]
    assert first["requester"] == {"id": str(api.users[EXT]), "email": EXT, "display_name": ""}


# --- the lobby panel ------------------------------------------------------------------------


def test_the_panel_shows_pending_and_recent_decisions(api: Api) -> None:
    old = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    assert api.act(PAM, old, "reject", {"reason": "Synthetic"}).status_code == 200
    pending = api.filed(EXT, api.oth, api.ticket("OTH", EXT))
    now = datetime.now(UTC)
    with api.db.unit_of_work(api.as_user(EXT)) as s:
        shown = service.list_mine(s, cooldown=DAY, now=now)
        assert {str(e.request.id) for e in shown} == {old, pending}
        later = service.list_mine(s, cooldown=DAY, now=now + timedelta(days=31))
        assert [str(e.request.id) for e in later] == [pending]  # the decision aged out
    # Another user sees none of it.
    assert api.mine(EXT2) == []


def test_requests_of_a_deleted_project_leave_the_panel(api: Api) -> None:
    request_id = api.filed(EXT, api.gone, api.ticket("GONE", EXT))
    assert (
        api.client.delete(f"/api/v1/projects/{api.gone}", headers=api.headers(PAM)).status_code
        == 204
    )
    assert api.mine(EXT) == []
    assert api.act(EXT, request_id, "withdraw").status_code == 404


# --- visibility (ORM scope; PostgreSQL RLS: test_rls_access_requests.py) -----------------------


def test_requests_are_visible_to_requester_per09_and_system_admin_only(api: Api) -> None:
    request_id = uuid.UUID(api.filed(EXT, api.hid, api.ticket("HID", EXT)))

    def sees(context: TenancyContext) -> bool:
        with api.db.unit_of_work(context) as s:
            return request_id in set(s.scalars(select(AccessRequest.id)))

    pam = resolve_scope([Grant(api.hid, Persona.PROJECT_ADMINISTRATOR)], api.as_user(PAM))
    pete = resolve_scope([Grant(api.oth, Persona.PROJECT_ADMINISTRATOR)], api.as_user(PETE))
    ivan = resolve_scope([Grant(api.hid, Persona.PROJECT_MANAGER)], api.as_user(IVAN))
    assert sees(api.as_user(EXT)) is True
    assert sees(pam) is True
    assert sees(ROOT) is True
    assert sees(api.as_user(EXT2)) is False
    assert sees(pete) is False
    assert sees(ivan) is False  # a member of the project without PER-09


def test_only_the_requester_files_a_request(api: Api) -> None:
    forged = AccessRequest(
        tenant_id=T1, requester_id=api.users[EXT2], requested_project_id=api.hid, status="pending"
    )
    with api.db.unit_of_work(api.as_user(EXT)) as s:
        s.add(forged)
        with pytest.raises(OutOfScopeError):
            s.flush()
        s.rollback()
    with api.db.unit_of_work(api.as_user(EXT)) as s:
        assert s.scalars(select(AccessRequest)).all() == []


# --- decision e-mails (T-0063, from T-0056; diff §4.1, §4.6) ---------------------------------

WEB = "https://kerp.example.test"


class Outbox:
    """The mail enqueuer: what core hands to the queue once a request has committed."""

    def __init__(self) -> None:
        self.mails: list[QueuedMail] = []

    def __call__(self, mails: Sequence[QueuedMail]) -> None:
        self.mails.extend(mails)

    def decisions(self) -> list[QueuedMail]:
        return [m for m in self.mails if m.template_key.startswith("access_requests.")]


@pytest.fixture
def outbox() -> Iterator[Outbox]:
    earlier, box = mail_enqueuer(), Outbox()
    set_mail_enqueuer(box)
    yield box
    set_mail_enqueuer(earlier)


def _deliver(db: Database, mail: QueuedMail, transport: FakeTransport) -> str:
    """The worker's side (``kerp_send_mail``) with the fake transport."""
    delivery = Delivery(db, transport, make_settings(web_origin=WEB))
    return deliver(delivery, mail, final_attempt=True)


def _send_log(db: Database, mail: QueuedMail) -> MailSend:
    with db.unit_of_work(ROOT) as s:
        row = s.get(MailSend, mail.send_id)
        assert row is not None
        s.expunge(row)
        return row


def test_approval_e_mails_the_requester_after_the_commit(api: Api, outbox: Outbox) -> None:
    request_id = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    assert outbox.decisions() == []  # filing mails nobody
    assert api.approve(PAM, request_id, "PER-04").status_code == 200
    [mail] = outbox.decisions()
    assert (mail.template_key, mail.to, mail.locale) == ("access_requests.approved", EXT, None)
    assert mail.context == {"project_code": "HID", "project_name": "Hidden synthetic site"}
    assert (mail.tenant_id, mail.project_id) == (T1, None)
    row = _send_log(api.db, mail)
    assert (row.template_key, row.status, row.recipient_masked) == (
        "access_requests.approved",
        "queued",
        "e***@example.test",
    )
    transport = FakeTransport()
    assert _deliver(api.db, mail, transport) == "sent"
    [message] = transport.sent
    assert message.to == EXT
    assert "HID" in message.rendered.subject
    for part in ("HID", "Hidden synthetic site", "approved", f"{WEB}/\n"):
        assert part in message.rendered.text
    # Code and name only: no personas, no request or project ids, nobody's name.
    for leak in ("PER-04", request_id, str(api.hid), PAM, "pam"):
        assert leak not in message.rendered.text
        assert leak not in message.rendered.html
    assert _send_log(api.db, mail).status == "sent"


def test_rejection_e_mails_the_reason_on_one_line(api_no_cooldown: Api, outbox: Outbox) -> None:
    api = api_no_cooldown
    first = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    reason = {"reason": "Synthetic: not on\nthis   job"}
    assert api.act(PAM, first, "reject", reason).status_code == 200
    second = api.filed(EXT, api.hid)  # request again, no cooldown
    assert api.act(PAM, second, "reject", {}).status_code == 200
    with_reason, without = outbox.decisions()
    assert with_reason.template_key == "access_requests.rejected"
    assert with_reason.context == {
        "project_code": "HID",
        "project_name": "Hidden synthetic site",
        "reason": "Synthetic: not on this job",
    }
    assert without.template_key == "access_requests.rejected_no_reason"
    assert without.context == {"project_code": "HID", "project_name": "Hidden synthetic site"}
    transport = FakeTransport()
    assert [_deliver(api.db, m, transport) for m in (with_reason, without)] == ["sent", "sent"]
    texts = [m.rendered.text for m in transport.sent]
    assert "Synthetic: not on this job" in texts[0]
    assert "Synthetic" not in texts[1]
    assert all(f"{WEB}/\n" in text and "HID" in text for text in texts)
    assert [m.to for m in transport.sent] == [EXT, EXT]


def test_a_refused_decision_queues_no_e_mail(api: Api, outbox: Outbox) -> None:
    request_id = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    assert api.approve(PAM, request_id, "PER-09", "PER-02").status_code == 409  # SoD, rolled back
    assert api.approve(PETE, request_id).status_code == 404  # not PER-09 of HID: hidden
    assert outbox.decisions() == []
    with api.db.unit_of_work(ROOT) as s:
        keys = [r.template_key for r in s.scalars(select(MailSend))]
    assert [k for k in keys if k.startswith("access_requests.")] == []


def test_a_mail_the_service_refuses_never_undoes_the_decision(
    api: Api, outbox: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> uuid.UUID:
        raise service.InvalidAddressError("not a single e-mail address")

    monkeypatch.setattr(service, "send_mail", refuse)
    request_id = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    approved = api.approve(PAM, request_id)
    assert approved.status_code == 200, approved.text
    assert api.mine(EXT)[0]["status"] == "approved"
    assert outbox.decisions() == []


def test_a_failed_send_never_undoes_the_decision(api: Api, outbox: Outbox) -> None:
    request_id = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    assert api.act(PAM, request_id, "reject", {"reason": "Synthetic"}).status_code == 200
    [mail] = outbox.decisions()
    transport = FakeTransport()
    transport.fail_with(PermanentMailError("ses:MessageRejected"))
    assert _deliver(api.db, mail, transport) == "failed"
    assert transport.sent == []
    row = _send_log(api.db, mail)
    assert (row.status, row.error_code) == ("failed", "ses:MessageRejected")
    (entry,) = api.mine(EXT)
    assert (entry["status"], entry["rejection_reason"]) == ("rejected", "Synthetic")


def test_an_inactive_requester_gets_no_e_mail(api: Api, outbox: Outbox) -> None:
    request_id = api.filed(EXT, api.hid, api.ticket("HID", EXT))
    with api.db.unit_of_work(ROOT) as s:
        account = s.get(User, api.users[EXT])
        assert account is not None
        account.is_active = False
    assert api.act(PAM, request_id, "reject", {}).status_code == 200
    assert outbox.decisions() == []


def test_every_decision_template_renders_from_the_catalog() -> None:
    for template in (
        service.APPROVED_TEMPLATE,
        service.REJECTED_TEMPLATE,
        service.REJECTED_NO_REASON_TEMPLATE,
    ):
        assert TEMPLATES.get(template.key) == template
        assert (template.link_path, template.secret, template.public_link) == ("/", None, True)
        context: dict[str, str | int] = dict.fromkeys(template.params, "SYN")
        rendered = render(template, context, locale="en-AU", app_name="KERP", web_origin=WEB)
        assert "{" not in rendered.text
        assert f"mail.{template.key}" not in rendered.text
