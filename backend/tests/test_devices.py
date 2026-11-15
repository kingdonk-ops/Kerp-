"""Device registry (T-0043, diff §4.3): enrolment, session binding, remote revoke with the
``DEVICE_REVOKED`` wipe signal, wipe confirmation, lists, and the device in the audit ledger.

Runs on the real migration (SQLite here, PostgreSQL under RLS in CI). All data synthetic.
"""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.audit.service import chain_entries, verify_chain
from app.core.auth import devices
from app.core.auth.access import SYSTEM_ADMIN, Persona, user_project_ids
from app.core.auth.device_signal import (
    DEVICE_HEADER,
    DEVICE_REVOKED_CODE,
    InvalidDeviceHeaderError,
    parse_device_id,
)
from app.core.auth.models import DEVICE_TABLE, Device, ProjectMembership
from app.core.auth.revocation import InMemoryRevocationStore, RedisRevocationStore
from app.core.auth.runtime import AuthRuntime
from app.core.db import Base, Database, contract_violations, utcnow
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import POLICY_NAMES, expected_policy_names, rls_tables
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
        assert self.enrol(tokens, device).status_code == 201
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
    assert utcnow() - devices._aware(api.row(device).last_seen_at) < timedelta(minutes=1)


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


def test_user_project_ids_reads_every_project_of_the_user(api: Api) -> None:
    found = user_project_ids(api.db, T1, api.users[FIONA])
    assert found == {api.home, api.away}
    assert user_project_ids(api.db, T1, api.users[DAVE]) == frozenset()


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


def test_the_revoked_device_confirms_its_wipe(api: Api) -> None:
    _tokens, device = api.device_on(GREG)
    url = f"{DEVICES}/{device}/wipe-confirmed"
    assert api.client.post(url).status_code == 409  # still active
    assert api.client.post(f"{DEVICES}/{uuid.uuid4()}/wipe-confirmed").status_code == 404
    assert api.revoke(api.login(ALICE), device).status_code == 200
    assert api.client.post(url).status_code == 204
    confirmed = api.row(device).wipe_confirmed_at
    assert confirmed is not None
    assert api.client.post(url).status_code == 204
    assert api.row(device).wipe_confirmed_at == confirmed  # the first confirmation stays
    entries = api.chain(f"device:{device}")
    assert [e["action"] for e in entries] == ["enrol", "revoke", "wipe_confirmed"]
    with api.db.unit_of_work(ROOT) as s:
        assert chain_entries(s, f"device:{device}")[-1].device_id == device


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
    seed_user(db, T1, BOB)
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
        redeem = {"token": link, "pin": PIN}
        on_device = {DEVICE_HEADER: str(device)}
        response = client.post("/api/v1/auth/magic-link/redeem", json=redeem, headers=on_device)
        assert _revoked(response)
        # The link was not spent: the wiped client (new device id) can still use it.
        fresh = {DEVICE_HEADER: str(uuid.uuid4())}
        ok = client.post("/api/v1/auth/magic-link/redeem", json=redeem, headers=fresh)
        assert ok.status_code == 200


def test_unlock_of_a_session_killed_by_a_device_revoke_is_device_revoked(
    tmp_path: Path, db: Database
) -> None:
    sender = CapturingSender()
    settings = make_settings(database_url=database_url(tmp_path))
    app, _runtime = auth_app(settings, magic_links=sender)
    seed_user(db, T1, BOB)
    with TestClient(app) as client:
        assert client.post("/api/v1/auth/magic-link", json={"email": BOB}).status_code == 202
        redeem = {"token": sender.sent[-1].token, "pin": PIN}
        tokens = token_body(client.post("/api/v1/auth/magic-link/redeem", json=redeem))
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        device = uuid.uuid4()
        enrol = {"device_id": str(device), "platform": "android"}
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
    assert store.revoke_device(device, ttl) == 2
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
