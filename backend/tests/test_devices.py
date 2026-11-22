"""Device registry (T-0043, diff §4.3): enrolment, session binding, remote revoke with the
``DEVICE_REVOKED`` wipe signal, wipe confirmation, lists, and the device in the audit ledger.

Runs on the real migration (SQLite here, PostgreSQL under RLS in CI). All data synthetic.
"""

import base64
import json
import logging
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.core.audit.service import chain_entries, verify_chain
from app.core.auth import devices
from app.core.auth.access import SYSTEM_ADMIN, Persona, user_has_projects_beyond
from app.core.auth.device_signal import (
    DEVICE_HEADER,
    DEVICE_REVOKED_CODE,
    InvalidDeviceHeaderError,
    parse_device_id,
)
from app.core.auth.field import MAGIC_LINK_TTL
from app.core.auth.lockout import WIPE_CONFIRM_POLICY
from app.core.auth.models import DEVICE_TABLE, Device, ProjectMembership
from app.core.auth.revocation import (
    AUTH_UNAVAILABLE,
    InMemoryRevocationStore,
    RedisRevocationStore,
    RevocationUnavailableError,
)
from app.core.auth.runtime import AuthRuntime
from app.core.db import Base, Database, contract_violations, utcnow
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import POLICY_NAMES, expected_policy_names, rls_tables
from app.core.timestamps import aware_utc
from app.modules.admin import service as admin_service
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import (
    ALICE,
    BOB,
    ORIGIN,
    PASSWORD,
    T1,
    T2,
    CapturingSender,
    auth_app,
    cookie_headers,
    fake_store,
    make_settings,
    refresh,
    revoke_project_viewers,
    seed_field_worker,
    seed_user,
    token_body,
)
from tests.db_support import database_url

CAROL = "carol@example.test"  # PER-02 on HOME: a member without PER-09
DAVE = "dave@example.test"  # no project
FIONA = "fiona@example.test"  # field worker on HOME (and AWAY)
GREG = "greg@example.test"  # field worker on HOME only
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
DEVICES = "/api/v1/devices"
ENROL = f"{DEVICES}/enrol"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
PIN = "482913"  # synthetic


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    migrated = migrated_database(tmp_path)
    database = next(migrated)
    try:
        yield database
    finally:
        revoke_project_viewers(database)
        next(migrated, None)


def _project(db: Database, code: str) -> uuid.UUID:
    with db.unit_of_work(ROOT) as s:
        return projects.create_project(s, ProjectCreate(code=code, name=f"Synthetic {code}")).id


def _grant(db: Database, user: uuid.UUID, project: uuid.UUID, persona: Persona) -> None:
    with db.unit_of_work(ROOT) as s:
        s.add(
            ProjectMembership(
                tenant_id=T1, user_id=user, member_project_id=project, persona=persona.value
            )
        )


class Api:
    """ALICE: System admin. BOB: PER-09 of HOME. CAROL: PER-02 of HOME. DAVE: no project.
    FIONA: PER-14 of HOME and AWAY. GREG: PER-14 of HOME only."""

    def __init__(self, client: TestClient, runtime: AuthRuntime, db: Database) -> None:
        self.client, self.runtime, self.db = client, runtime, db
        self.secrets: dict[uuid.UUID, str] = {}  # the wipe secret each enrolment returned
        self.users = {e: seed_user(db, T1, e) for e in (ALICE, BOB, CAROL, DAVE, FIONA, GREG)}
        with db.unit_of_work(ROOT) as s:
            assert admin_service.grant(s, self.users[ALICE], SYSTEM_ADMIN, actor_id=None)
        self.home, self.away = _project(db, "HOME"), _project(db, "AWAY")
        _grant(db, self.users[BOB], self.home, Persona.PROJECT_ADMINISTRATOR)
        _grant(db, self.users[CAROL], self.home, Persona.PROJECT_MANAGER)
        _grant(db, self.users[FIONA], self.home, Persona.FIELD_WORKER)
        _grant(db, self.users[FIONA], self.away, Persona.FIELD_WORKER)
        _grant(db, self.users[GREG], self.home, Persona.FIELD_WORKER)

    def login(self, email: str, headers: dict[str, str] | None = None) -> dict[str, str]:
        body = {"email": email, "password": PASSWORD}
        return token_body(self.client.post(LOGIN, json=body, headers=headers or {}))

    def bearer(self, tokens: dict[str, str], device: uuid.UUID | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        if device is not None:
            headers[DEVICE_HEADER] = str(device)
        return headers

    def enrol(
        self, tokens: dict[str, str], device: uuid.UUID, platform: str = "android"
    ) -> httpx.Response:
        body = {
            "device_id": str(device),
            "platform": platform,
            "os_version": "Android 15",
            "app_version": "0.9.0",
        }
        return self.client.post(ENROL, json=body, headers=self.bearer(tokens))

    def device_on(self, email: str) -> tuple[dict[str, str], uuid.UUID]:
        """Sign ``email`` in and enrol a fresh device; returns the tokens and the device."""
        tokens, device = self.login(email), uuid.uuid4()
        response = self.enrol(tokens, device)
        assert response.status_code == 201
        self.secrets[device] = response.json()["wipe_secret"]
        return tokens, device

    def revoke(
        self, caller: dict[str, str], device: uuid.UUID, confirm: bool | None = None
    ) -> httpx.Response:
        url = f"{DEVICES}/{device}/revoke"
        if confirm is None:
            return self.client.post(url, headers=self.bearer(caller))
        body = {"confirm_cross_project_wipe": confirm}
        return self.client.post(url, json=body, headers=self.bearer(caller))

    def chain(self, key: str) -> list[dict[str, Any]]:
        with self.db.unit_of_work(ROOT) as s:
            return [e.payload for e in chain_entries(s, key)]

    def row(self, device: uuid.UUID) -> Device:
        with self.db.unit_of_work(ROOT) as s:
            found = devices.find_device(s, device)
            assert found is not None
            s.expunge(found)
            return found


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    app, runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        yield Api(client, runtime, db)


def _revoked(response: httpx.Response) -> bool:
    return response.status_code == 401 and response.json().get("code") == DEVICE_REVOKED_CODE


# --- contract ---------------------------------------------------------------------------------


def test_device_table_meets_the_record_contract_under_the_standard_policy() -> None:
    assert [p for p in contract_violations(Base.metadata) if p.startswith(DEVICE_TABLE)] == []
    assert DEVICE_TABLE in rls_tables(Base.metadata)
    assert expected_policy_names(Base.metadata.tables[DEVICE_TABLE]) == POLICY_NAMES
    columns = set(Base.metadata.tables[DEVICE_TABLE].columns.keys())
    assert {
        "user_id",
        "device_id",
        "platform",
        "os_version",
        "app_version",
        "enrolled_at",
        "last_seen_at",
        "last_sync_at",
        "status",
        "revoked_by",
        "revoked_at",
        "wipe_confirmed_at",
    } <= columns


def test_device_header_must_be_a_uuid() -> None:
    device = uuid.uuid4()
    assert parse_device_id(f" {device} ") == device
    assert parse_device_id(None) is None
    assert parse_device_id("") is None
    with pytest.raises(InvalidDeviceHeaderError):
        parse_device_id("not-a-uuid")


def test_a_malformed_device_header_is_400(api: Api) -> None:
    tokens = api.login(DAVE)
    headers = {**api.bearer(tokens), DEVICE_HEADER: "nope"}
    assert api.client.get(ME, headers=headers).status_code == 400


# --- enrolment --------------------------------------------------------------------------------


def test_every_device_endpoint_but_wipe_confirmation_needs_a_token(api: Api) -> None:
    device = uuid.uuid4()
    calls = [
        ("POST", ENROL),
        ("GET", DEVICES),
        ("GET", f"/api/v1/users/{api.users[DAVE]}/devices"),
        ("POST", f"{DEVICES}/{device}/revoke"),
    ]
    assert [api.client.request(m, u, json={}).status_code for m, u in calls] == [401] * 4


def test_enrolment_registers_the_device_and_binds_the_session(api: Api) -> None:
    tokens = api.login(FIONA)
    device = uuid.uuid4()
    response = api.enrol(tokens, device)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["device_id"] == str(device)
    assert body["user_id"] == str(api.users[FIONA])
    assert (body["platform"], body["os_version"], body["app_version"]) == (
        "android",
        "Android 15",
        "0.9.0",
    )
    assert body["status"] == "active"
    assert body["last_sync_at"] is None
    assert body["enrolled_at"]
    assert body["last_seen_at"]
    sid = api.runtime.tokens.verify(tokens["access_token"], "access").session_id
    assert api.runtime.store.session_device(sid) == device
    # The enrolment is audited, through the device itself.
    entries = api.chain(f"device:{device}")
    assert [e["action"] for e in entries] == ["enrol"]
    with api.db.unit_of_work(ROOT) as s:
        (entry,) = chain_entries(s, f"device:{device}")
        assert (entry.device_id, entry.hash_version) == (device, 2)
        assert verify_chain(s, f"device:{device}").ok


def test_enrolling_again_updates_versions_and_is_200(api: Api) -> None:
    _tokens, device = api.device_on(FIONA)
    again = api.login(FIONA)
    body = {"device_id": str(device), "platform": "android", "app_version": "1.0.0"}
    response = api.client.post(ENROL, json=body, headers=api.bearer(again))
    assert response.status_code == 200
    assert response.json()["app_version"] == "1.0.0"
    assert len(api.chain(f"device:{device}")) == 1  # only the first enrolment is audited


def test_enrolment_refuses_another_accounts_device_and_a_second_device(api: Api) -> None:
    _tokens, device = api.device_on(FIONA)
    assert api.enrol(api.login(GREG), device).status_code == 409
    tokens = api.login(GREG)
    assert api.enrol(tokens, uuid.uuid4()).status_code == 201
    assert api.enrol(tokens, uuid.uuid4()).status_code == 409  # the session has its device


@pytest.mark.parametrize(
    "body",
    [
        {"device_id": "x", "platform": "android"},
        {"device_id": str(uuid.uuid4()), "platform": "symbian"},
        {"device_id": str(uuid.uuid4()), "platform": "web", "os_version": "x" * 65},
        {"device_id": str(uuid.uuid4()), "platform": "web", "tenant_id": str(T1)},
    ],
)
def test_enrolment_validates_its_body(api: Api, body: dict[str, str]) -> None:
    assert api.client.post(ENROL, json=body, headers=api.bearer(api.login(DAVE))).status_code == 422


def test_requests_through_a_device_update_last_seen(api: Api) -> None:
    tokens, device = api.device_on(FIONA)
    with api.db.unit_of_work(ROOT) as s:
        row = devices.find_device(s, device)
        assert row is not None
        row.last_seen_at = utcnow() - timedelta(hours=2)
    assert api.client.get(ME, headers=api.bearer(tokens)).status_code == 200
    assert utcnow() - aware_utc(api.row(device).last_seen_at) < timedelta(minutes=1)


def test_last_seen_is_written_at_most_once_a_minute() -> None:
    now = utcnow()
    device = Device(last_seen_at=now - timedelta(seconds=30))
    assert devices.touch(device, now) is False
    device.last_seen_at = now - devices.SEEN_RESOLUTION
    assert devices.touch(device, now) is True
    assert device.last_seen_at == now


def test_a_header_naming_another_device_than_the_session_is_401(api: Api) -> None:
    tokens, _device = api.device_on(FIONA)
    headers = api.bearer(tokens, uuid.uuid4())
    response = api.client.get(ME, headers=headers)
    assert response.status_code == 401
    assert "code" not in response.json()


def test_record_sync_sets_last_sync_for_the_owner_only(api: Api) -> None:
    _tokens, device = api.device_on(FIONA)
    with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        assert devices.record_sync(s, device, user_id=api.users[GREG]) is None
        assert devices.record_sync(s, uuid.uuid4(), user_id=api.users[FIONA]) is None
        synced = devices.record_sync(s, device, user_id=api.users[FIONA])
        assert synced is not None
        assert synced.last_sync_at is not None
    listed = api.client.get(DEVICES, headers=api.bearer(api.login(FIONA))).json()
    assert listed[0]["last_sync_at"] is not None


def test_another_tenants_devices_do_not_exist(api: Api) -> None:
    _tokens, device = api.device_on(FIONA)
    with api.db.unit_of_work(TenancyContext(tenant_id=T2)) as s:
        assert devices.find_device(s, device) is None
        assert devices.device_status(s, device) is None
        assert devices.devices_of_user(s, api.users[FIONA]) == []
    with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        assert devices.device_status(s, device) == "active"


def test_the_registry_needs_a_tenant_level_session(api: Api) -> None:
    project = TenancyContext(tenant_id=T1, project_id=api.home)
    with api.db.unit_of_work(project) as s, pytest.raises(RuntimeError, match="tenant-level"):
        devices.find_device(s, uuid.uuid4())


# --- ledger device_id through a request -------------------------------------------------------


def test_ledger_entries_written_through_a_device_carry_its_id(api: Api) -> None:
    tokens, device = api.device_on(BOB)
    url = f"/api/v1/admin/projects/{api.home}/members/{api.users[DAVE]}"
    body = {"grants": [{"persona": "PER-05"}]}
    assert api.client.put(url, json=body, headers=api.bearer(tokens)).status_code == 200
    # Without a device: no device id (another session of the same user, not enrolled).
    plain = api.login(BOB)
    body = {"grants": [{"persona": "PER-08"}]}
    assert api.client.put(url, json=body, headers=api.bearer(plain)).status_code == 200
    with api.db.unit_of_work(ROOT) as s:
        entries = chain_entries(s, f"project-members:{api.home}")
        assert [e.device_id for e in entries] == [device, None]
        assert {e.hash_version for e in entries} == {2}
        assert verify_chain(s, f"project-members:{api.home}").ok


def test_a_header_alone_names_only_the_callers_own_enrolled_device(api: Api) -> None:
    _tokens, fionas = api.device_on(FIONA)
    tokens = api.login(BOB)  # not enrolled on any device
    url = f"/api/v1/admin/projects/{api.home}/members/{api.users[DAVE]}"
    body = {"grants": [{"persona": "PER-05"}]}
    response = api.client.put(url, json=body, headers=api.bearer(tokens, fionas))
    assert response.status_code == 200
    with api.db.unit_of_work(ROOT) as s:
        (entry,) = chain_entries(s, f"project-members:{api.home}")
        assert entry.device_id is None


# --- revoke: who may --------------------------------------------------------------------------


def test_the_user_revokes_their_own_device(api: Api) -> None:
    tokens, device = api.device_on(GREG)
    other = api.login(GREG)
    response = api.revoke(other, device)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["role"], body["cross_project_warning"]) == (
        "revoked",
        "self",
        False,
    )
    assert body["revoked_by"] == str(api.users[GREG])
    assert body["revoked_at"] is not None
    (entry,) = [e for e in api.chain(f"device:{device}") if e["action"] == "revoke"]
    assert entry["role"] == "self"
    assert entry["sessions_revoked"] == 1
    # The revoking session was not on the device: it still works.
    assert api.client.get(ME, headers=api.bearer(other)).status_code == 200
    assert _revoked(api.client.get(ME, headers=api.bearer(tokens)))


def test_a_system_admin_revokes_any_device_without_a_warning(api: Api) -> None:
    _tokens, device = api.device_on(FIONA)
    response = api.revoke(api.login(ALICE), device)
    assert response.status_code == 200
    assert (response.json()["role"], response.json()["cross_project_warning"]) == (
        "system_admin",
        False,
    )
    (entry,) = [e for e in api.chain(f"device:{device}") if e["action"] == "revoke"]
    assert (entry["role"], entry["project_ids"], entry["cross_project_warning"]) == (
        "system_admin",
        [],
        False,
    )


def test_a_project_administrator_revokes_a_member_device(api: Api) -> None:
    _tokens, device = api.device_on(GREG)  # GREG is on HOME only: no other projects
    response = api.revoke(api.login(BOB), device)
    assert response.status_code == 200
    assert (response.json()["role"], response.json()["cross_project_warning"]) == (
        "per_09",
        False,
    )
    (entry,) = [e for e in api.chain(f"device:{device}") if e["action"] == "revoke"]
    assert entry["role"] == "per_09"
    assert entry["project_ids"] == [str(api.home)]
    assert (entry["cross_project_warning"], entry["warning_confirmed"]) == (False, False)
    assert entry["sessions_revoked"] == 1


def test_others_get_403_or_404(api: Api) -> None:
    _tokens, device = api.device_on(GREG)
    assert api.revoke(api.login(CAROL), device).status_code == 403  # shares HOME, no PER-09
    assert api.revoke(api.login(DAVE), device).status_code == 404  # shares nothing
    assert api.revoke(api.login(ALICE), uuid.uuid4()).status_code == 404  # unknown device
    assert api.row(device).status == "active"


def test_a_project_administrator_of_another_project_gets_404(api: Api) -> None:
    other = _project(api.db, "ELSE")
    _grant(api.db, api.users[DAVE], other, Persona.PROJECT_ADMINISTRATOR)
    _tokens, device = api.device_on(GREG)
    assert api.revoke(api.login(DAVE), device).status_code == 404


def test_per_09_revoke_of_a_multi_project_user_needs_confirmation(api: Api) -> None:
    _tokens, device = api.device_on(FIONA)  # FIONA is on HOME and AWAY, BOB is PER-09 of HOME
    bob = api.login(BOB)
    for confirm in (None, False):
        refused = api.revoke(bob, device, confirm=confirm)
        assert refused.status_code == 409
        detail = refused.json()["detail"]
        assert detail["code"] == "cross_project_wipe_confirmation_required"
        assert detail["cross_project_warning"] is True
        assert "last_sync_at" in detail
        assert str(api.away) not in refused.text  # the other project is not named
    assert api.row(device).status == "active"
    assert [e["action"] for e in api.chain(f"device:{device}")] == ["enrol"]

    response = api.revoke(bob, device, confirm=True)
    assert response.status_code == 200
    assert (response.json()["role"], response.json()["cross_project_warning"]) == ("per_09", True)
    (entry,) = [e for e in api.chain(f"device:{device}") if e["action"] == "revoke"]
    assert (entry["role"], entry["project_ids"]) == ("per_09", [str(api.home)])
    assert (entry["cross_project_warning"], entry["warning_confirmed"]) == (True, True)


def test_the_user_and_a_system_admin_never_need_the_confirmation(api: Api) -> None:
    _tokens, device = api.device_on(FIONA)
    assert api.revoke(api.login(FIONA), device).status_code == 200
    _tokens, second = api.device_on(FIONA)
    assert api.revoke(api.login(ALICE), second).status_code == 200


def test_revoking_twice_is_idempotent_and_audited_once(api: Api) -> None:
    _tokens, device = api.device_on(GREG)
    first = api.revoke(api.login(ALICE), device).json()
    second = api.revoke(api.login(BOB), device)
    assert second.status_code == 200
    assert second.json()["revoked_at"] == first["revoked_at"]
    assert second.json()["revoked_by"] == str(api.users[ALICE])
    assert [e["action"] for e in api.chain(f"device:{device}")] == ["enrol", "revoke"]


def test_user_has_projects_beyond_answers_yes_or_no_only(api: Api) -> None:
    fiona = api.users[FIONA]
    assert user_has_projects_beyond(api.db, T1, fiona, frozenset({api.home}))
    assert not user_has_projects_beyond(api.db, T1, fiona, frozenset({api.home, api.away}))
    assert not user_has_projects_beyond(api.db, T1, api.users[DAVE], frozenset())


# --- revoke: effect on the device -------------------------------------------------------------


def test_revoke_kills_every_session_of_the_device_at_once(api: Api) -> None:
    first, device = api.device_on(GREG)
    second = api.login(GREG)
    assert api.enrol(second, device).status_code == 200  # a second sign-in on the device
    elsewhere = api.login(GREG)  # another, unenrolled session of the same user
    response = api.revoke(api.login(ALICE), device)
    assert response.status_code == 200
    (entry,) = [e for e in api.chain(f"device:{device}") if e["action"] == "revoke"]
    assert entry["sessions_revoked"] == 2
    for tokens in (first, second):
        call = api.client.get(ME, headers=api.bearer(tokens))
        assert _revoked(call)
        assert call.headers["WWW-Authenticate"].startswith("Bearer")
        assert _revoked(refresh(api.client, tokens["refresh_token"]))
    assert api.client.get(ME, headers=api.bearer(elsewhere)).status_code == 200


def test_a_failed_revoke_commit_leaves_the_device_active_and_its_sessions_alive(
    api: Api,
) -> None:
    tokens, device = api.device_on(GREG)
    admin = api.login(ALICE)

    def fail(session: Session) -> None:
        # Only the commit that would persist the revoke fails.
        held = session.identity_map.values()
        if any(isinstance(o, Device) and o.status == "revoked" for o in held):
            raise RuntimeError("synthetic commit failure")

    event.listen(Session, "before_commit", fail)
    try:
        assert api.revoke(admin, device).status_code == 500
    finally:
        event.remove(Session, "before_commit", fail)
    assert api.row(device).status == "active"
    assert [e["action"] for e in api.chain(f"device:{device}")] == ["enrol"]
    assert api.client.get(ME, headers=api.bearer(tokens)).status_code == 200
    assert refresh(api.client, tokens["refresh_token"]).status_code == 200
    assert api.runtime.store.device_session_count(device) == 1


def test_a_failed_session_kill_after_the_commit_still_refuses_the_device(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens, device = api.device_on(GREG)
    admin = api.login(ALICE)

    def unavailable(device_id: uuid.UUID, ttl: timedelta) -> int:
        raise RevocationUnavailableError("synthetic outage")

    monkeypatch.setattr(api.runtime.store, "revoke_device", unavailable)
    failed = api.revoke(admin, device)
    assert (failed.status_code, failed.json()) == (503, {"detail": AUTH_UNAVAILABLE})  # TD-290
    monkeypatch.undo()
    assert api.row(device).status == "revoked"  # committed before the kill
    (entry,) = [e for e in api.chain(f"device:{device}") if e["action"] == "revoke"]
    assert entry["sessions_revoked"] == 1
    assert _revoked(api.client.get(ME, headers=api.bearer(tokens)))  # per-request re-check
    # Revoking again kills whatever is still bound, without a second audit entry.
    assert api.revoke(admin, device).status_code == 200
    assert api.runtime.store.device_session_count(device) == 0
    assert [e["action"] for e in api.chain(f"device:{device}")] == ["enrol", "revoke"]


def test_enrolment_answers_503_while_the_store_is_down(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TD-290: the device endpoints answer a store outage with the app-wide 503 (T-0349)."""
    tokens = api.login(GREG)

    def unavailable(*_args: Any, **_kwargs: Any) -> None:
        raise RevocationUnavailableError("synthetic outage")

    monkeypatch.setattr(api.runtime.store, "bind_device", unavailable)
    device = uuid.uuid4()
    response = api.enrol(tokens, device)
    monkeypatch.undo()
    assert (response.status_code, response.json()) == (503, {"detail": AUTH_UNAVAILABLE})
    with api.db.unit_of_work(ROOT) as s:
        assert devices.find_device(s, device) is None  # rolled back with the request


def test_resolving_the_request_device_answers_503_while_the_store_is_down(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TD-290: ``resolve_request_device`` reads the session's binding from the store; an
    outage there is the app-wide 503, never a 500 and never "no device" (T-0349)."""
    tokens, device = api.device_on(GREG)

    def unavailable(*_args: Any, **_kwargs: Any) -> None:
        raise RevocationUnavailableError("synthetic outage")

    monkeypatch.setattr(api.runtime.store, "session_device", unavailable)
    response = api.client.get(ME, headers=api.bearer(tokens, device))
    monkeypatch.undo()
    assert (response.status_code, response.json()) == (503, {"detail": AUTH_UNAVAILABLE})
    assert api.client.get(ME, headers=api.bearer(tokens, device)).status_code == 200


def test_any_later_call_from_the_revoked_device_is_device_revoked(api: Api) -> None:
    _tokens, device = api.device_on(GREG)
    assert api.revoke(api.login(ALICE), device).status_code == 200
    on_device = {DEVICE_HEADER: str(device)}
    # Signing in again on the device, or refreshing a session that only names it.
    signed_in = api.client.post(
        LOGIN, json={"email": GREG, "password": PASSWORD}, headers=on_device
    )
    assert _revoked(signed_in)
    unbound = api.login(GREG)
    assert _revoked(api.client.get(ME, headers=api.bearer(unbound, device)))
    # That session is now dead as well, and its refresh tells the device to wipe.
    assert _revoked(api.client.get(ME, headers=api.bearer(unbound)))
    fresh = api.login(GREG)
    assert _revoked(
        api.client.post(
            "/api/v1/auth/refresh",
            headers={**cookie_headers(fresh["refresh_token"]), **on_device},
        )
    )
    # Enrolling the revoked id again is refused, and kills the enrolling session.
    again = api.login(GREG)
    assert _revoked(api.enrol(again, device))
    assert _revoked(api.client.get(ME, headers=api.bearer(again)))


def test_a_plain_logout_is_not_a_device_revoke(api: Api) -> None:
    tokens, _device = api.device_on(GREG)
    assert api.client.post("/api/v1/auth/logout", headers=api.bearer(tokens)).status_code == 204
    response = api.client.get(ME, headers=api.bearer(tokens))
    assert response.status_code == 401
    assert "code" not in response.json()


# --- wipe confirmation ------------------------------------------------------------------------


def _confirm(api: Api, device: uuid.UUID, secret: str | None = None, **kw: Any) -> httpx.Response:
    url = f"{DEVICES}/{device}/wipe-confirmed"
    if secret is None:
        return api.client.post(url, **kw)
    return api.client.post(url, json={"wipe_secret": secret}, **kw)


def _refusal(response: httpx.Response) -> tuple[int, bytes]:
    return response.status_code, response.content


def test_the_revoked_device_confirms_its_wipe_with_its_secret(api: Api) -> None:
    _tokens, device = api.device_on(GREG)
    secret = api.secrets[device]
    assert api.revoke(api.login(ALICE), device).status_code == 200
    assert _confirm(api, device, secret).status_code == 204
    confirmed = api.row(device).wipe_confirmed_at
    assert confirmed is not None
    assert _confirm(api, device, secret).status_code == 204
    assert api.row(device).wipe_confirmed_at == confirmed  # the first confirmation stays
    entries = api.chain(f"device:{device}")
    assert [e["action"] for e in entries] == ["enrol", "revoke", "wipe_confirmed"]
    with api.db.unit_of_work(ROOT) as s:
        assert chain_entries(s, f"device:{device}")[-1].device_id == device
    assert all(secret not in json.dumps(e) for e in entries)


def test_every_refused_wipe_confirmation_gets_the_same_answer(api: Api) -> None:
    """Unknown id, active device, wrong or missing secret: one answer, nothing recorded."""
    _t, active = api.device_on(GREG)
    _t, revoked = api.device_on(FIONA)
    assert api.revoke(api.login(ALICE), revoked).status_code == 200
    wrong = "x" * 43
    answers = {
        "unknown, no secret": _confirm(api, uuid.uuid4()),
        "unknown, some secret": _confirm(api, uuid.uuid4(), wrong),
        "active, no secret": _confirm(api, active),
        "active, its own secret": _confirm(api, active, api.secrets[active]),
        "active, wrong secret": _confirm(api, active, wrong),
        "revoked, no secret": _confirm(api, revoked),
        "revoked, empty body": api.client.post(f"{DEVICES}/{revoked}/wipe-confirmed", json={}),
        "revoked, wrong secret": _confirm(api, revoked, wrong),
        "revoked, another device's secret": _confirm(api, revoked, api.secrets[active]),
    }
    refusals = {name: _refusal(r) for name, r in answers.items()}
    assert set(refusals.values()) == {(404, b'{"detail":"Not Found"}')}, refusals
    assert api.row(active).wipe_confirmed_at is None
    assert api.row(revoked).wipe_confirmed_at is None
    # The right secret still works after the refusals (no lock-out of the device).
    assert _confirm(api, revoked, api.secrets[revoked]).status_code == 204


def test_refused_wipe_confirmations_are_audited_without_the_secret(api: Api) -> None:
    _t, active = api.device_on(GREG)
    _t, revoked = api.device_on(FIONA)
    assert api.revoke(api.login(ALICE), revoked).status_code == 200
    unknown, wrong = uuid.uuid4(), "y" * 43
    _confirm(api, unknown, wrong)
    _confirm(api, active, api.secrets[active])
    _confirm(api, revoked, wrong)
    _confirm(api, revoked)

    def refused(device: uuid.UUID) -> list[dict[str, Any]]:
        return [e for e in api.chain(f"device:{device}") if e["action"] == "wipe_refused"]

    assert [e["reason"] for e in refused(unknown)] == ["unknown_device"]
    assert [e["reason"] for e in refused(active)] == ["not_revoked"]
    assert [e["reason"] for e in refused(revoked)] == ["wrong_secret", "wrong_secret"]
    everything = json.dumps(
        [api.chain(f"device:{d}") for d in (unknown, active, revoked)], default=str
    )
    for secret in (wrong, api.secrets[active], api.secrets[revoked]):
        assert secret not in everything
    with api.db.unit_of_work(ROOT) as s:
        for device in (unknown, active, revoked):
            assert verify_chain(s, f"device:{device}").ok


def test_wipe_confirmation_compares_in_constant_time_whatever_the_device(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Comparable work: every call hashes the presented secret and runs one constant-time
    compare against a stored hash (a dummy one when the device has none or is unknown)."""
    compared: list[tuple[str, str]] = []
    real = devices.hmac.compare_digest

    def spy(a: str, b: str) -> bool:
        compared.append((a, b))
        return real(a, b)

    monkeypatch.setattr(devices.hmac, "compare_digest", spy)
    _t, active = api.device_on(GREG)
    _t, revoked = api.device_on(FIONA)
    assert api.revoke(api.login(ALICE), revoked).status_code == 200
    calls = [
        (uuid.uuid4(), None),
        (uuid.uuid4(), "z" * 43),
        (active, None),
        (active, api.secrets[active]),
        (revoked, "z" * 43),
        (revoked, api.secrets[revoked]),
    ]
    for device, secret in calls:
        before = len(compared)
        _confirm(api, device, secret)
        assert len(compared) == before + 1, device
        assert all(len(side) == 64 for side in compared[-1])


def test_a_device_enrolled_without_a_secret_cannot_confirm_its_wipe(api: Api) -> None:
    """Enrolled before T-0080 (no hash): the device gets the uniform refusal (README)."""
    _t, device = api.device_on(GREG)
    with api.db.unit_of_work(ROOT) as s:
        row = devices.find_device(s, device)
        assert row is not None
        row.wipe_secret_hash = None
    assert api.revoke(api.login(ALICE), device).status_code == 200
    unknown = _refusal(_confirm(api, uuid.uuid4(), api.secrets[device]))
    assert _refusal(_confirm(api, device, api.secrets[device])) == unknown
    assert _refusal(_confirm(api, device)) == unknown
    assert api.row(device).wipe_confirmed_at is None
    reasons = [e.get("reason") for e in api.chain(f"device:{device}")]
    assert reasons[-2:] == ["no_secret", "no_secret"]


def test_wipe_confirmation_openapi_lists_only_the_uniform_refusal(api: Api) -> None:
    spec = api.client.get("/openapi.json").json()
    operation = spec["paths"]["/api/v1/devices/{device_id}/wipe-confirmed"]["post"]
    # 429 (T-0212): the per-client lock-out, decided before the device is looked up.
    assert set(operation["responses"]) == {"204", "404", "422", "429"}
    assert "WipeConfirmRequest" in json.dumps(operation["requestBody"])
    assert "wipe_secret" in spec["components"]["schemas"]["WipeConfirmRequest"]["properties"]
    assert "wipe_secret" in spec["components"]["schemas"]["DeviceEnrolled"]["properties"]
    assert "wipe_secret" not in spec["components"]["schemas"]["DeviceRead"]["properties"]


# --- wipe secret at enrolment -------------------------------------------------------------------


def test_enrolment_issues_the_wipe_secret_once_and_stores_only_its_hash(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    tokens = api.login(FIONA)
    device = uuid.uuid4()
    first = api.enrol(tokens, device)
    assert first.status_code == 201
    secret = first.json()["wipe_secret"]
    assert isinstance(secret, str)
    assert len(base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))) >= 32  # 256 bits
    # Never again: re-enrolling and listing do not return it.
    again = api.enrol(api.login(FIONA), device)
    assert again.status_code == 200
    assert again.json()["wipe_secret"] is None
    listed = api.client.get(DEVICES, headers=api.bearer(tokens)).json()
    assert all("wipe_secret" not in d and "wipe_secret_hash" not in d for d in listed)
    admin = api.client.get(
        f"/api/v1/users/{api.users[FIONA]}/devices", headers=api.bearer(api.login(ALICE))
    )
    assert secret not in admin.text
    assert "wipe_secret" not in admin.text
    # Stored only as a hash: no column of the row, no audit payload, no log line holds it.
    row = api.row(device)
    assert row.wipe_secret_hash is not None
    assert row.wipe_secret_hash == devices.wipe_secret_digest(secret)
    assert row.wipe_secret_hash != secret
    with api.db.unit_of_work(ROOT) as s:
        raw = s.execute(text(f"SELECT * FROM {DEVICE_TABLE}")).all()
    assert all(secret not in str(value) for line in raw for value in line)
    assert secret not in json.dumps(api.chain(f"device:{device}"))
    assert caplog.records  # the request logs were captured
    assert secret not in caplog.text
    # Two devices never share a secret.
    _t, other = api.device_on(GREG)
    assert api.secrets[other] != secret


# --- lists ------------------------------------------------------------------------------------


def test_users_list_their_own_devices_with_last_seen_and_sync(api: Api) -> None:
    tokens, device = api.device_on(FIONA)
    api.device_on(GREG)
    listed = api.client.get(DEVICES, headers=api.bearer(tokens)).json()
    assert [d["device_id"] for d in listed] == [str(device)]
    assert {"last_seen_at", "last_sync_at", "status"} <= set(listed[0])
    assert api.client.get(DEVICES, headers=api.bearer(api.login(DAVE))).json() == []


def test_admins_list_a_users_devices(api: Api) -> None:
    _tokens, device = api.device_on(GREG)
    url = f"/api/v1/users/{api.users[GREG]}/devices"
    for email in (ALICE, BOB, GREG):
        response = api.client.get(url, headers=api.bearer(api.login(email)))
        assert response.status_code == 200
        assert [d["device_id"] for d in response.json()] == [str(device)]
    assert api.client.get(url, headers=api.bearer(api.login(CAROL))).status_code == 403
    assert api.client.get(url, headers=api.bearer(api.login(DAVE))).status_code == 404
    unknown = f"/api/v1/users/{uuid.uuid4()}/devices"
    assert api.client.get(unknown, headers=api.bearer(api.login(ALICE))).status_code == 404


# --- magic-link sign-in from a revoked device -------------------------------------------------


def test_magic_link_redeem_from_a_revoked_device_is_refused(tmp_path: Path, db: Database) -> None:
    sender = CapturingSender()
    settings = make_settings(database_url=database_url(tmp_path))
    app, _runtime = auth_app(settings, magic_links=sender)
    seed_field_worker(db, T1, BOB)
    with TestClient(app) as client:
        api = Api.__new__(Api)
        api.client = client
        tokens = api.login(BOB)
        device = uuid.uuid4()
        assert api.enrol(tokens, device).status_code == 201
        url = f"{DEVICES}/{device}/revoke"
        assert client.post(url, headers=api.bearer(tokens)).status_code == 200
        assert client.post("/api/v1/auth/magic-link", json={"email": BOB}).status_code == 202
        link = sender.sent[-1].token
        redeem = {"token": link}
        on_device = {DEVICE_HEADER: str(device)}
        response = client.post("/api/v1/auth/magic-link/redeem", json=redeem, headers=on_device)
        assert _revoked(response)
        # The link was not spent: the wiped client (new device id) can still use it.
        fresh = {DEVICE_HEADER: str(uuid.uuid4())}
        ok = client.post("/api/v1/auth/magic-link/redeem", json=redeem, headers=fresh)
        assert ok.status_code == 200


def _three_devices(api: Api, email: str) -> dict[str, dict[str, str]]:
    """``X-KERP-Device-Id`` headers for an unknown, an active and a revoked device."""
    _t, active = api.device_on(email)
    _t, revoked = api.device_on(email)
    assert api.revoke(api.login(ALICE), revoked).status_code == 200
    return {
        "unknown": {DEVICE_HEADER: str(uuid.uuid4())},
        "active": {DEVICE_HEADER: str(active)},
        "revoked": {DEVICE_HEADER: str(revoked)},
    }


def test_login_with_wrong_credentials_answers_alike_for_every_device_state(api: Api) -> None:
    """Credentials first (TD-291): no device-status oracle to an unauthenticated caller."""
    headers = _three_devices(api, GREG)
    for body in (
        {"email": GREG, "password": "wrong password, synthetic"},
        {"email": "nobody@example.test", "password": PASSWORD},
    ):
        answers = {
            state: _refusal(api.client.post(LOGIN, json=body, headers=h))
            for state, h in headers.items()
        }
        assert len(set(answers.values())) == 1, answers
        assert answers["revoked"][0] == 401
        assert b"DEVICE_REVOKED" not in answers["revoked"][1]
        refused = api.client.post(LOGIN, json=body, headers=headers["revoked"])
        assert refused.headers.get("www-authenticate") == api.client.post(
            LOGIN, json=body, headers=headers["unknown"]
        ).headers.get("www-authenticate")


def test_login_with_valid_credentials_on_a_revoked_device_is_device_revoked(api: Api) -> None:
    headers = _three_devices(api, GREG)
    body = {"email": GREG, "password": PASSWORD}
    assert _revoked(api.client.post(LOGIN, json=body, headers=headers["revoked"]))
    for state in ("unknown", "active"):
        assert api.client.post(LOGIN, json=body, headers=headers[state]).status_code == 200


def _magic_link_api(tmp_path: Path, db: Database) -> Iterator[tuple[Api, CapturingSender]]:
    sender = CapturingSender()
    settings = make_settings(database_url=database_url(tmp_path))
    app, runtime = auth_app(settings, magic_links=sender)
    with TestClient(app) as client:
        yield Api(client, runtime, db), sender


@pytest.fixture
def field_api(tmp_path: Path, db: Database) -> Iterator[tuple[Api, CapturingSender]]:
    yield from _magic_link_api(tmp_path, db)


REDEEM = "/api/v1/auth/magic-link/redeem"


def _link(api: Api, sender: CapturingSender, email: str) -> str:
    assert api.client.post("/api/v1/auth/magic-link", json={"email": email}).status_code == 202
    return sender.sent[-1].token


def test_an_invalid_magic_link_answers_alike_for_every_device_state(
    field_api: tuple[Api, CapturingSender],
) -> None:
    api, sender = field_api
    headers = _three_devices(api, GREG)
    spent = _link(api, sender, GREG)
    assert api.client.post(REDEEM, json={"token": spent}).status_code == 200
    for token in ("not-a-link-" + "a" * 32, spent):
        answers = {
            state: _refusal(api.client.post(REDEEM, json={"token": token}, headers=h))
            for state, h in headers.items()
        }
        assert len(set(answers.values())) == 1, answers
        assert answers["revoked"][0] == 401
        assert b"DEVICE_REVOKED" not in answers["revoked"][1]


def test_an_expired_magic_link_answers_alike_for_every_device_state(
    tmp_path: Path, db: Database
) -> None:
    clock = [1000.0]
    store = InMemoryRevocationStore(clock=lambda: clock[0])
    sender = CapturingSender()
    settings = make_settings(database_url=database_url(tmp_path))
    app, runtime = auth_app(settings, store=store, magic_links=sender)
    with TestClient(app) as client:
        api = Api(client, runtime, db)
        headers = _three_devices(api, GREG)
        token = _link(api, sender, GREG)
        clock[0] += MAGIC_LINK_TTL.total_seconds() + 1
        answers = {
            state: _refusal(client.post(REDEEM, json={"token": token}, headers=h))
            for state, h in headers.items()
        }
    assert len(set(answers.values())) == 1, answers
    assert answers["revoked"][0] == 401
    assert b"DEVICE_REVOKED" not in answers["revoked"][1]


def test_a_valid_magic_link_on_a_revoked_device_stays_unspent(
    field_api: tuple[Api, CapturingSender],
) -> None:
    api, sender = field_api
    headers = _three_devices(api, GREG)
    token = _link(api, sender, GREG)
    redeem = {"token": token}
    assert _revoked(api.client.post(REDEEM, json=redeem, headers=headers["revoked"]))
    assert _revoked(api.client.post(REDEEM, json=redeem, headers=headers["revoked"]))
    # The link was not spent: the same link then redeems from an active device, once.
    assert api.client.post(REDEEM, json=redeem, headers=headers["active"]).status_code == 200
    assert api.client.post(REDEEM, json=redeem, headers=headers["active"]).status_code == 401


def test_unlock_of_a_session_killed_by_a_device_revoke_is_device_revoked(
    tmp_path: Path, db: Database
) -> None:
    sender = CapturingSender()
    settings = make_settings(database_url=database_url(tmp_path))
    app, _runtime = auth_app(settings, magic_links=sender)
    seed_field_worker(db, T1, BOB)
    with TestClient(app) as client:
        assert client.post("/api/v1/auth/magic-link", json={"email": BOB}).status_code == 202
        redeem = {"token": sender.sent[-1].token}
        tokens = token_body(client.post("/api/v1/auth/magic-link/redeem", json=redeem))
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        device = uuid.uuid4()
        enrol = {"device_id": str(device), "platform": "android", "pin": PIN}
        assert client.post(ENROL, json=enrol, headers=headers).status_code == 201
        assert client.post(f"{DEVICES}/{device}/revoke", headers=headers).status_code == 200
        unlock = client.post(
            "/api/v1/auth/pin/unlock",
            json={"pin": PIN},
            headers=cookie_headers(tokens["refresh_token"], ORIGIN),
        )
        assert _revoked(unlock)


# --- revocation store -------------------------------------------------------------------------


@pytest.mark.parametrize("make_store", [InMemoryRevocationStore, fake_store])
def test_store_binds_sessions_to_a_device_and_revokes_them_together(make_store: Any) -> None:
    store: InMemoryRevocationStore | RedisRevocationStore = make_store()
    device, other = uuid.uuid4(), uuid.uuid4()
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ttl = timedelta(hours=12)
    store.bind_device(a, device, ttl)
    store.bind_device(b, device, ttl)
    store.bind_device(c, other, ttl)
    assert (store.session_device(a), store.session_device(c)) == (device, other)
    assert store.session_device(uuid.uuid4()) is None
    assert store.device_session_count(device) == 2
    assert store.revoke_device(device, ttl) == 2
    assert store.device_session_count(device) == 0
    assert store.is_revoked("jti", a)
    assert store.is_revoked("jti", b)
    assert store.revoked_by_device(a)
    assert store.revoked_by_device(b)
    assert not store.is_revoked("jti", c)
    assert not store.revoked_by_device(c)
    assert store.revoke_device(device, ttl) == 0  # nothing bound any more
    store.revoke_session(c, ttl)  # a plain revoke is not a device revoke
    assert store.is_revoked("jti", c)
    assert not store.revoked_by_device(c)


# --- one request-pool connection per request (T-0210) -----------------------------------------
# ``get_current_user`` used to load the user on the request session (which then keeps its
# connection until the request ends) and only then open a second unit of work for the
# ``X-KERP-Device-Id`` device: under load every request held one connection and waited for a
# second, and all of them timed out. With a request pool of exactly one connection, any request
# that needs two at once fails with a pool timeout.


@pytest.fixture
def one_connection(api: Api) -> Iterator[Api]:
    """``api`` whose app runs on a request pool of exactly one connection."""
    state = api.client.app.state  # type: ignore[attr-defined]
    original: Database = state.db
    small = Database(original.url, pool_size=1, max_overflow=0, pool_timeout=2)
    state.db = small
    try:
        yield api
    finally:
        state.db = original
        small.dispose()


def test_a_request_through_an_enrolled_device_needs_one_connection(one_connection: Api) -> None:
    tokens, device = one_connection.device_on(FIONA)
    response = one_connection.client.get(ME, headers=one_connection.bearer(tokens, device))
    assert response.status_code == 200


def test_a_request_naming_an_unknown_device_needs_one_connection(one_connection: Api) -> None:
    tokens = one_connection.login(FIONA)
    headers = one_connection.bearer(tokens, uuid.uuid4())
    assert one_connection.client.get(ME, headers=headers).status_code == 200


def test_a_per09_revoke_with_the_cross_project_check_needs_one_connection(
    one_connection: Api,
) -> None:
    _tokens, device = one_connection.device_on(FIONA)  # FIONA also works on AWAY
    response = one_connection.revoke(one_connection.login(BOB), device, confirm=True)
    assert response.status_code == 200
    assert response.json()["cross_project_warning"] is True


# --- wipe confirmation is rate-limited per client (T-0212, TD-298) ----------------------------


def _wipe_rows(api: Api) -> int:
    with api.db.unit_of_work(ROOT) as s:
        query = "SELECT count(*) FROM audit_ledger WHERE chain_key LIKE 'device:%'"
        return int(s.execute(text(query)).scalar_one())


def test_wipe_confirmation_locks_a_client_out_before_any_device_lookup(api: Api) -> None:
    free = WIPE_CONFIRM_POLICY.free_attempts + 1  # attempts that run back to back
    for _ in range(free):
        assert _confirm(api, uuid.uuid4(), "w" * 43).status_code == 404
    before = _wipe_rows(api)
    locked = _confirm(api, uuid.uuid4(), "w" * 43)
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    assert _wipe_rows(api) == before  # a locked call writes no audit entry


def test_another_client_still_confirms_its_wipe(api: Api) -> None:
    _tokens, device = api.device_on(GREG)
    assert api.revoke(api.login(ALICE), device).status_code == 200
    # Another client address, as uvicorn reports it in ``request.client``.
    with TestClient(api.client.app, client=("203.0.113.9", 40000)) as noisy:
        url = f"{DEVICES}/{uuid.uuid4()}/wipe-confirmed"
        for _ in range(WIPE_CONFIRM_POLICY.free_attempts + 1):
            noisy.post(url, json={"wipe_secret": "w" * 43})
        assert noisy.post(url, json={"wipe_secret": "w" * 43}).status_code == 429
    assert _confirm(api, device, api.secrets[device]).status_code == 204


def test_an_overlong_wipe_secret_is_422_whatever_the_device(api: Api) -> None:
    long = "s" * 129
    _t, active = api.device_on(GREG)
    assert _confirm(api, uuid.uuid4(), long).status_code == 422
    assert _confirm(api, active, long).status_code == 422
    assert _confirm(api, uuid.uuid4(), "s" * 128).status_code == 404



def test_replaying_a_valid_wipe_secret_does_not_reset_the_lock(api: Api) -> None:
    """T-0212 review: refusals, then the same correct confirmation, again and again from one
    client must still reach 429; the audit ledger stops growing once locked."""
    _tokens, device = api.device_on(GREG)
    assert api.revoke(api.login(ALICE), device).status_code == 200
    secret = api.secrets[device]
    assert _confirm(api, device, secret).status_code == 204  # recorded: clears the counter
    statuses: list[int] = []
    for _ in range(3):
        for _ in range(WIPE_CONFIRM_POLICY.free_attempts):
            statuses.append(_confirm(api, uuid.uuid4(), "w" * 43).status_code)
        statuses.append(_confirm(api, device, secret).status_code)  # a replay: no reset
    assert 429 in statuses
    before = _wipe_rows(api)
    assert _confirm(api, uuid.uuid4(), "w" * 43).status_code == 429
    assert _wipe_rows(api) == before


def test_the_first_confirmation_clears_the_clients_earlier_refusals(api: Api) -> None:
    _tokens, device = api.device_on(GREG)
    assert api.revoke(api.login(ALICE), device).status_code == 200
    for _ in range(WIPE_CONFIRM_POLICY.free_attempts):
        assert _confirm(api, uuid.uuid4(), "w" * 43).status_code == 404
    assert _confirm(api, device, api.secrets[device]).status_code == 204
    # The counter starts over: a whole new run of free attempts before any lock.
    for _ in range(WIPE_CONFIRM_POLICY.free_attempts + 1):
        assert _confirm(api, uuid.uuid4(), "w" * 43).status_code == 404


def test_wipe_confirmation_answers_503_while_the_store_is_down(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tokens, revoked = api.device_on(GREG)
    assert api.revoke(api.login(ALICE), revoked).status_code == 200

    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise RevocationUnavailableError("synthetic outage")

    before = _wipe_rows(api)
    monkeypatch.setattr(api.runtime.store, "begin_attempt", unavailable)
    unknown = _confirm(api, uuid.uuid4(), "w" * 43)
    real = _confirm(api, revoked, api.secrets[revoked])
    monkeypatch.undo()
    assert (unknown.status_code, real.status_code) == (503, 503)
    assert unknown.content == real.content  # the same answer whatever the device
    assert _wipe_rows(api) == before
    assert api.row(revoked).wipe_confirmed_at is None


def test_a_store_outage_after_the_wipe_keeps_the_recorded_confirmation(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tokens, revoked = api.device_on(GREG)
    assert api.revoke(api.login(ALICE), revoked).status_code == 200

    def unavailable(*_args: Any, **_kwargs: Any) -> None:
        raise RevocationUnavailableError("synthetic outage")

    monkeypatch.setattr(api.runtime.store, "clear_failures", unavailable)
    assert _confirm(api, revoked, api.secrets[revoked]).status_code == 204
    monkeypatch.undo()
    assert api.row(revoked).wipe_confirmed_at is not None
