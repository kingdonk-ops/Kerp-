"""Lobby discovery: ``visible_in_lobby``, the lobby list for internal users, code lookup and
lookup tickets (T-0040, diff §4.1, edge spec §6 amendments, TD-202).

Runs on SQLite (the narrow read mirrors the PostgreSQL functions) and, in CI, on PostgreSQL
under RLS against the migrated schema. Raw-SQL checks of the narrow read as ``kerp_app``:
``test_rls_lobby.py``. All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.audit.service import chain_entries
from app.core.auth.access import SYSTEM_ADMIN, Persona
from app.core.auth.keys import KeySet, generate_signing_key
from app.core.auth.lockout import LockedOutError, LockoutPolicy
from app.core.auth.models import ProjectMembership
from app.core.auth.revocation import InMemoryRevocationStore
from app.core.auth.tokens import InvalidTokenError, TokenService
from app.core.db import Database
from app.core.module_loader.loader import discover_modules
from app.core.tenancy.context import TenancyContext
from app.modules.admin import service as admin
from app.modules.admin.schemas import UserUpdate
from app.modules.lobby import service as lobby
from app.modules.projects import service as projects
from app.modules.projects.models import Project
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import PASSWORD, T1, auth_app, make_settings, seed_user
from tests.db_support import database_url

LOBBY = "/api/v1/lobby/projects"
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
STAFF = "staff.example.test"
# ROOT_ADMIN: System admin (external). PAM: PER-09 of VIS. IVAN: internal, PER-02 of VIS.
# INES: internal, no project. EXT: external, no project.
ROOT_ADMIN = "root@example.test"
PAM = "pam@example.test"
IVAN = f"ivan@{STAFF}"
INES = f"ines@{STAFF}"
EXT = "ext@example.test"


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


def _memberships(db: Database) -> int:
    with db.unit_of_work(ROOT) as s:
        return s.scalar(select(func.count()).select_from(ProjectMembership)) or 0


class Api:
    def __init__(self, client: TestClient, db: Database, tokens: TokenService) -> None:
        self.client, self.db, self.tokens = client, db, tokens
        # ``is_internal`` as sign-in derives it from KERP_INTERNAL_EMAIL_DOMAINS (T-0049).
        self.users = {
            e: seed_user(db, T1, e, is_internal=e.endswith(f"@{STAFF}"))
            for e in (ROOT_ADMIN, PAM, IVAN, INES, EXT)
        }
        with db.unit_of_work(ROOT) as s:
            admin.grant(s, self.users[ROOT_ADMIN], SYSTEM_ADMIN, actor_id=None)
        # Names sort the other way round from codes: the list is by name.
        self.vis = _project(db, "VIS", "Beta synthetic site")
        self.vis2 = _project(db, "VIS2", "Alpha synthetic site")
        self.hid = _project(db, "HID", "Hidden synthetic site")
        self.gone = _project(db, "GONE", "Deleted synthetic site")
        _grant(db, self.users[PAM], self.vis, Persona.PROJECT_ADMINISTRATOR)
        _grant(db, self.users[PAM], self.vis2, Persona.PROJECT_ADMINISTRATOR)
        _grant(db, self.users[PAM], self.gone, Persona.PROJECT_ADMINISTRATOR)
        _grant(db, self.users[IVAN], self.vis, Persona.PROJECT_MANAGER)
        self._headers: dict[str, dict[str, str]] = {}

    def headers(self, email: str) -> dict[str, str]:
        if email not in self._headers:
            body = {"email": email, "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            self._headers[email] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._headers[email]

    def set_lobby(self, project: uuid.UUID, email: str, visible: bool) -> Any:
        url = f"/api/v1/projects/{project}/lobby"
        return self.client.put(url, json={"visible_in_lobby": visible}, headers=self.headers(email))

    def code(self, code: str, email: str) -> Any:
        return self.client.get(f"{LOBBY}/by-code/{code}", headers=self.headers(email))

    def publish(self) -> None:
        """VIS, VIS2 and GONE visible in the lobby; GONE then deleted; HID stays hidden."""
        for project in (self.vis, self.vis2, self.gone):
            assert self.set_lobby(project, PAM, True).status_code == 200
        url = f"/api/v1/projects/{self.gone}"
        assert self.client.delete(url, headers=self.headers(PAM)).status_code == 204


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    settings = make_settings(database_url=database_url(tmp_path), internal_email_domains=STAFF)
    app, runtime = auth_app(settings)
    with TestClient(app) as client:
        yield Api(client, db, runtime.tokens)


# --- contract -------------------------------------------------------------------------------


def test_lobby_is_a_platform_module_without_a_table() -> None:
    manifest = discover_modules().get("lobby")
    assert manifest.toggleable is False
    assert manifest.depends_on == ("projects",)


def test_projects_are_not_in_the_lobby_by_default(db: Database) -> None:
    project = _project(db, "NEW", "Synthetic new")
    with db.unit_of_work(ROOT) as s:
        assert projects.get_project(s, project).visible_in_lobby is False
    assert Project.__table__.c.visible_in_lobby.nullable is False


# --- visible_in_lobby -----------------------------------------------------------------------


def test_every_lobby_endpoint_needs_a_token(api: Api) -> None:
    calls = [
        ("GET", LOBBY),
        ("GET", f"{LOBBY}/by-code/VIS"),
        ("PUT", f"/api/v1/projects/{api.vis}/lobby"),
    ]
    assert [api.client.request(m, u, json={}).status_code for m, u in calls] == [401] * 3


def test_only_per09_or_a_system_admin_changes_the_lobby_flag(api: Api) -> None:
    read = api.client.get(f"/api/v1/projects/{api.vis}", headers=api.headers(PAM)).json()
    assert read["visible_in_lobby"] is False
    shown = api.set_lobby(api.vis, PAM, True)
    assert shown.status_code == 200, shown.text
    assert shown.json()["visible_in_lobby"] is True
    assert api.set_lobby(api.vis, IVAN, False).status_code == 403  # member without PER-09
    assert api.set_lobby(api.hid, INES, True).status_code == 404  # not a member
    assert api.set_lobby(api.hid, PAM, True).status_code == 404  # PER-09 of other projects
    hidden = api.set_lobby(api.hid, ROOT_ADMIN, True)  # System admin, no membership
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["visible_in_lobby"] is True
    assert api.set_lobby(api.hid, ROOT_ADMIN, False).json()["visible_in_lobby"] is False
    assert api.set_lobby(api.hid, ROOT_ADMIN, False).status_code == 200  # no change: no entry
    with api.db.unit_of_work(ROOT) as s:
        updates = [e.payload for e in chain_entries(s, f"project:{api.hid}")][1:]
    assert updates == [
        {"action": "update", "code": "HID", "fields": ["visible_in_lobby"]},
        {"action": "update", "code": "HID", "fields": ["visible_in_lobby"]},
    ]


def test_patch_does_not_take_the_lobby_flag(api: Api) -> None:
    url = f"/api/v1/projects/{api.vis}"
    response = api.client.patch(url, json={"visible_in_lobby": True}, headers=api.headers(PAM))
    assert response.status_code == 422
    bad = api.client.put(
        f"{url}/lobby", json={"visible_in_lobby": True, "x": 1}, headers=api.headers(PAM)
    )
    assert bad.status_code == 422


# --- lobby list -----------------------------------------------------------------------------


def test_an_internal_user_lists_lobby_visible_projects_only(api: Api) -> None:
    api.publish()
    listed = api.client.get(LOBBY, headers=api.headers(INES))
    assert listed.status_code == 200, listed.text
    assert listed.json() == [
        {"id": str(api.vis2), "code": "VIS2", "name": "Alpha synthetic site", "member": False},
        {"id": str(api.vis), "code": "VIS", "name": "Beta synthetic site", "member": False},
    ]
    mine = api.client.get(LOBBY, headers=api.headers(IVAN)).json()
    assert [(p["code"], p["member"]) for p in mine] == [("VIS2", False), ("VIS", True)]


@pytest.mark.parametrize("email", [EXT, PAM, ROOT_ADMIN])
def test_an_external_user_gets_403_and_no_project_data(api: Api, email: str) -> None:
    api.publish()
    response = api.client.get(LOBBY, headers=api.headers(email))
    assert response.status_code == 403
    assert response.json() == {
        "detail": "The lobby list is for internal staff",
        "code": "LOBBY_INTERNAL_ONLY",
    }


def test_the_narrow_read_itself_returns_nothing_to_a_non_internal_caller(api: Api) -> None:
    api.publish()
    for email, expected in ((INES, ["VIS2", "VIS"]), (EXT, []), (ROOT_ADMIN, [])):
        context = TenancyContext(tenant_id=T1, user_id=api.users[email])
        with api.db.unit_of_work(context) as s:
            assert [p.code for p in projects.lobby_projects(s)] == expected
    with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:  # no user bound
        assert projects.lobby_projects(s) == []
        assert projects.project_by_code(s, "HID") is None
    with api.db.unit_of_work(ROOT) as s:
        off = UserUpdate(is_active=False)
        admin.update_user(s, api.users[INES], off, actor_id=None)
    with api.db.unit_of_work(TenancyContext(tenant_id=T1, user_id=api.users[INES])) as s:
        assert projects.lobby_projects(s) == []  # deactivated
        assert projects.project_by_code(s, "VIS") is None


# --- code lookup ----------------------------------------------------------------------------


def test_an_external_user_finds_a_hidden_project_by_code(api: Api) -> None:
    api.publish()
    before = _memberships(api.db)
    found = api.code("HID", EXT)
    assert found.status_code == 200, found.text
    body = found.json()
    ticket_fields = {"lookup_ticket", "lookup_ticket_expires_at"}
    assert set(body) == {"id", "code", "name", "member", *ticket_fields}
    assert (body["id"], body["code"], body["name"], body["member"]) == (
        str(api.hid),
        "HID",
        "Hidden synthetic site",
        False,
    )
    assert api.code("HID", INES).status_code == 200  # internal users too
    assert api.code("VIS", IVAN).json()["member"] is True
    # A code lookup never creates a membership, and the project stays unreadable.
    assert _memberships(api.db) == before
    assert api.client.get("/api/v1/me/projects", headers=api.headers(EXT)).json() == []
    url = f"/api/v1/projects/{api.hid}"
    assert api.client.get(url, headers=api.headers(EXT)).status_code == 404


@pytest.mark.parametrize("code", ["NOPE", "hid", "HID ", "GONE", "%", "H_D"])
def test_an_unknown_code_is_404(api: Api, code: str) -> None:
    api.publish()
    assert api.code(code, EXT).status_code == 404


def test_the_lookup_ticket_is_bound_to_user_and_project(api: Api) -> None:
    body = api.code("HID", EXT).json()
    ticket, ext = body["lookup_ticket"], api.users[EXT]
    expires = datetime.fromisoformat(body["lookup_ticket_expires_at"])
    assert timedelta(minutes=14) < expires - datetime.now(UTC) <= timedelta(minutes=15)
    lobby.verify_lookup_ticket(api.tokens, ticket, user_id=ext, tenant_id=T1, project_id=api.hid)
    wrong = [
        {"user_id": api.users[INES], "tenant_id": T1, "project_id": api.hid},
        {"user_id": ext, "tenant_id": uuid.uuid4(), "project_id": api.hid},
        {"user_id": ext, "tenant_id": T1, "project_id": api.vis},
    ]
    for bound in wrong:
        with pytest.raises(lobby.InvalidLookupTicketError):
            lobby.verify_lookup_ticket(api.tokens, ticket, **bound)
    # Not an access token, and an access token is not a ticket.
    with pytest.raises(InvalidTokenError):
        api.tokens.verify(ticket, "access")
    access = api.headers(EXT)["Authorization"].removeprefix("Bearer ")
    with pytest.raises(lobby.InvalidLookupTicketError):
        lobby.verify_lookup_ticket(
            api.tokens, access, user_id=ext, tenant_id=T1, project_id=api.hid
        )
    response = api.client.get(LOBBY, headers={"Authorization": f"Bearer {ticket}"})
    assert response.status_code == 401


def _tokens(keys: KeySet, at: datetime) -> TokenService:
    return TokenService(
        keys,
        issuer="kerp-test",
        audience="kerp-test",
        access_ttl=timedelta(minutes=15),
        refresh_ttl=timedelta(hours=1),
        clock=lambda: at,
    )


def test_a_lookup_ticket_expires_after_15_minutes() -> None:
    keys = KeySet(generate_signing_key("EdDSA"))
    issued_at = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)
    user, project = uuid.uuid4(), uuid.uuid4()
    ticket = lobby.issue_lookup_ticket(
        _tokens(keys, issued_at), user_id=user, tenant_id=T1, project_id=project
    )
    assert ticket.expires_at == issued_at + timedelta(minutes=15)
    bound = {"user_id": user, "tenant_id": T1, "project_id": project}
    in_time = _tokens(keys, issued_at + timedelta(minutes=14))
    lobby.verify_lookup_ticket(in_time, ticket.token, **bound)
    for late in (timedelta(minutes=15, seconds=10), timedelta(hours=2)):
        with pytest.raises(lobby.InvalidLookupTicketError):
            lobby.verify_lookup_ticket(_tokens(keys, issued_at + late), ticket.token, **bound)
    early = _tokens(keys, issued_at - timedelta(minutes=1))
    with pytest.raises(lobby.InvalidLookupTicketError):  # not yet valid
        lobby.verify_lookup_ticket(early, ticket.token, **bound)
    other = KeySet(generate_signing_key("EdDSA"))  # another deployment's key
    with pytest.raises(lobby.InvalidLookupTicketError):
        lobby.verify_lookup_ticket(_tokens(other, issued_at), ticket.token, **bound)
    forged = jwt.encode(
        {**jwt.decode(ticket.token, options={"verify_signature": False})},
        key=None,
        algorithm="none",
    )
    with pytest.raises(lobby.InvalidLookupTicketError):
        lobby.verify_lookup_ticket(_tokens(keys, issued_at), forged, **bound)
    with pytest.raises(lobby.InvalidLookupTicketError):
        lobby.verify_lookup_ticket(_tokens(keys, issued_at), "not-a-jwt", **bound)


# --- throttling -----------------------------------------------------------------------------

_TIGHT = LockoutPolicy(
    free_attempts=2,
    base_delay=timedelta(minutes=1),
    max_delay=timedelta(minutes=15),
    window=timedelta(hours=1),
)


def test_code_lookup_is_limited_per_user(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lobby, "USER_LOOKUP_POLICY", _TIGHT)
    assert [api.code(c, EXT).status_code for c in ("HID", "NOPE", "VIS")] == [200, 404, 200]
    locked = api.code("HID", EXT)
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    assert api.code("HID", INES).status_code == 200  # another user is not affected


def test_code_lookup_is_limited_deployment_wide(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lobby, "DEPLOYMENT_LOOKUP_POLICY", _TIGHT)
    assert [api.code("HID", e).status_code for e in (EXT, INES, IVAN)] == [200, 200, 200]
    assert api.code("HID", PAM).status_code == 429  # a fourth user: the deployment is locked


def test_a_locked_user_does_not_spend_the_deployment_budget() -> None:
    store = InMemoryRevocationStore()
    deployment = LockoutPolicy(
        free_attempts=4,
        base_delay=timedelta(minutes=1),
        max_delay=timedelta(minutes=15),
        window=timedelta(hours=1),
    )
    limits = {"user_policy": _TIGHT, "deployment_policy": deployment}
    noisy, quiet = uuid.uuid4(), uuid.uuid4()
    for _ in range(3):
        lobby.throttle_code_lookup(store, T1, noisy, **limits)
    for _ in range(20):
        with pytest.raises(LockedOutError):
            lobby.throttle_code_lookup(store, T1, noisy, **limits)
    lobby.throttle_code_lookup(store, T1, quiet, **limits)  # 4th and 5th deployment attempts
    lobby.throttle_code_lookup(store, T1, quiet, **limits)
