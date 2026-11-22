"""System admin impersonation (T-0277, ADR 0015 option (a); diff §4.2 *Support impersonation*).

A ``system_admin`` acts as a non-admin user for a time-boxed, audited session: the target's
exact RLS scope (``is_system_admin`` false), records attributed to the target, a single-session
impersonation token without refresh or device binding, ended by exit, expiry or revocation of
the admin (logout, grant removal, deactivation), each end ledgered once and told to the target
by one e-mail. Runs on SQLite and, in CI, PostgreSQL under RLS. All data is synthetic.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.audit.models import AuditEntry
from app.core.audit.service import chain_entries, verify_chain
from app.core.auth import impersonation
from app.core.auth.access import CREATE_PROJECTS, SYSTEM_ADMIN, Persona
from app.core.auth.deps import CurrentPrincipal
from app.core.auth.keys import KeySet, generate_signing_key
from app.core.auth.models import IMPERSONATION_TABLE, ImpersonationSession, User
from app.core.auth.revocation import InMemoryRevocationStore
from app.core.auth.runtime import AuthRuntime
from app.core.auth.service import AuthenticationError, set_user_password
from app.core.auth.tokens import IMPERSONATION_TYP, InvalidTokenError, TokenService
from app.core.db import Base, Database, contract_violations
from app.core.mail import service as mail_service
from app.core.mail.models import MailSend
from app.core.mail.queue import QueuedMail, set_mail_enqueuer
from app.core.mail.templates import TEMPLATES, render
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import RLS_EXEMPT, rls_tables, table_policy
from app.modules.admin import service as admin
from app.modules.admin.setting_defs import IMPERSONATION_MAX_MINUTES
from app.modules.asset_management.schemas import LevelDefCreate
from app.modules.asset_management.service import create_level_def
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.saved_views.models import SavedView
from app.modules.settings.registry import REGISTRY
from tests.audit_support import migrated_database
from tests.auth_support import (
    ALICE,
    BOB,
    ORIGIN,
    PASSWORD,
    T1,
    auth_app,
    cookie_headers,
    fake_store,
    make_settings,
    revoke_project_viewers,
    seed_user,
)
from tests.db_support import database_url

ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
CAROL = "carol@example.test"  # PER-15 Client Representative on AWAY
DAVE = "dave@example.test"  # create_projects, no membership
ERIN = "erin@example.test"  # a second System admin
FRED = "fred@example.test"  # deactivated
REASON = "Ticket 4711: user cannot see the HOME register (synthetic)"
CHAIN = f"admin-impersonation:{T1}"
START = "/api/v1/admin/users/{user_id}/impersonate"
EXIT = "/api/v1/auth/impersonation/exit"
ME = "/api/v1/auth/me"
NOTICE = "auth.impersonation_ended"
PRINCIPAL_PROBE = "/test/principal"


class Outbox:
    """The mail enqueuer: what core hands to the queue after a commit."""

    def __init__(self) -> None:
        self.mails: list[QueuedMail] = []

    def __call__(self, mails: Sequence[QueuedMail]) -> None:
        self.mails.extend(mails)

    def of(self, template: str = NOTICE) -> list[QueuedMail]:
        return [m for m in self.mails if m.template_key == template]


@dataclass
class Api:
    client: TestClient
    db: Database
    runtime: AuthRuntime
    outbox: Outbox
    users: dict[str, uuid.UUID]
    home: uuid.UUID
    away: uuid.UUID
    _logins: dict[str, dict[str, Any]] = field(default_factory=dict)

    def login(self, email: str) -> dict[str, Any]:
        response = self.client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        return {**response.json(), "refresh_token": response.cookies["kerp_refresh"]}

    def headers(self, email: str) -> dict[str, str]:
        if email not in self._logins:
            self._logins[email] = self.login(email)
        return bearer(self._logins[email]["access_token"])

    def start(
        self, target: str | uuid.UUID, caller: str = ALICE, reason: str = REASON, **kw: Any
    ) -> Any:
        user_id = self.users[target] if isinstance(target, str) else target
        headers = kw.pop("headers", None) or self.headers(caller)
        return self.client.post(
            START.format(user_id=user_id), json={"reason": reason}, headers=headers, **kw
        )

    def impersonate(self, target: str = BOB, caller: str = ALICE) -> dict[str, Any]:
        response = self.start(target, caller)
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def chain(self) -> list[dict[str, Any]]:
        with self.db.unit_of_work(ROOT) as s:
            return [e.payload for e in chain_entries(s, CHAIN)]

    def ended(self) -> list[dict[str, Any]]:
        return [p for p in self.chain() if p["action"] == impersonation.ENDED]

    def row(self, session_id: str) -> ImpersonationSession:
        with self.db.unit_of_work(ROOT) as s:
            row = s.get(ImpersonationSession, uuid.UUID(session_id))
            assert row is not None
            s.expunge(row)
            return row


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _project(db: Database, code: str) -> uuid.UUID:
    with db.unit_of_work(ROOT) as s:
        return projects.create_project(s, ProjectCreate(code=code, name=f"Synthetic {code}")).id


def _member(db: Database, user: uuid.UUID, project: uuid.UUID, persona: Persona) -> None:
    from app.core.auth.models import ProjectMembership

    with db.unit_of_work(ROOT) as s:
        s.add(
            ProjectMembership(
                tenant_id=T1, user_id=user, member_project_id=project, persona=persona.value
            )
        )


def _probe(app: FastAPI) -> None:
    """A test-only route that shows what the request authenticated as."""

    def probe(principal: CurrentPrincipal) -> dict[str, Any]:
        return {
            "user_id": str(principal.user_id),
            "impersonator_user_id": str(principal.impersonator_user_id or ""),
            "read_only": principal.impersonation_read_only,
            "device_id": str(principal.device_id or ""),
        }

    app.add_api_route(PRINCIPAL_PROBE, probe, methods=["GET"])


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    migrated = migrated_database(tmp_path)
    database = next(migrated)
    try:
        yield database
    finally:
        revoke_project_viewers(database)
        next(migrated, None)


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    users = {e: seed_user(db, T1, e) for e in (ALICE, BOB, CAROL, DAVE, ERIN, FRED)}
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        s.get_one(User, users[ALICE]).display_name = "Alice Admin"
        s.get_one(User, users[FRED]).is_active = False
    with db.unit_of_work(ROOT) as s:
        assert admin.grant(s, users[ALICE], SYSTEM_ADMIN, actor_id=None)
        assert admin.grant(s, users[ERIN], SYSTEM_ADMIN, actor_id=None)
        assert admin.grant(s, users[DAVE], CREATE_PROJECTS, actor_id=None)
    home, away = _project(db, "HOME"), _project(db, "AWAY")
    _member(db, users[BOB], home, Persona.PROJECT_MANAGER)
    _member(db, users[CAROL], away, Persona.CLIENT_REPRESENTATIVE)
    outbox = Outbox()
    set_mail_enqueuer(outbox)
    app, runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    _probe(app)
    try:
        with TestClient(app) as client:
            yield Api(client, db, runtime, outbox, users, home, away)
    finally:
        set_mail_enqueuer(None)


# --- the table --------------------------------------------------------------------------------


def test_the_session_table_meets_the_record_contract_under_the_standard_policy() -> None:
    assert [
        p for p in contract_violations(Base.metadata) if p.startswith(IMPERSONATION_TABLE)
    ] == []
    assert IMPERSONATION_TABLE in rls_tables(Base.metadata)
    assert IMPERSONATION_TABLE not in RLS_EXEMPT
    assert table_policy(Base.metadata.tables[IMPERSONATION_TABLE]) is None


# --- the registry entry -------------------------------------------------------------------------


def test_the_maximum_length_is_a_registry_entry_with_its_bounds() -> None:
    entry = REGISTRY.get("impersonation_max_minutes")
    assert entry is IMPERSONATION_MAX_MINUTES
    assert (entry.type, entry.default, entry.minimum, entry.maximum) == ("int", 30, 5, 60)
    assert entry.client_visible is False


@pytest.mark.parametrize("value", [4, 61, 30.5, "30"])
def test_an_out_of_bounds_maximum_is_422(api: Api, value: Any) -> None:
    response = api.client.patch(
        "/api/v1/settings/registry/impersonation_max_minutes",
        json={"value": value},
        headers=api.headers(ALICE),
    )
    assert response.status_code == 422, response.text


def test_the_registry_maximum_sets_the_expiry(api: Api) -> None:
    response = api.client.patch(
        "/api/v1/settings/registry/impersonation_max_minutes",
        json={"value": 5},
        headers=api.headers(ALICE),
    )
    assert response.status_code == 200, response.text
    assert api.impersonate()["expires_in"] == 300


# --- tokens -------------------------------------------------------------------------------------


def _tokens(clock: datetime | None = None) -> TokenService:
    return TokenService(
        KeySet(generate_signing_key("EdDSA")),
        issuer="kerp-test",
        audience="kerp-test",
        access_ttl=timedelta(minutes=15),
        refresh_ttl=timedelta(hours=12),
        clock=(lambda: clock) if clock is not None else (lambda: datetime.now(UTC)),
    )


def test_an_impersonation_token_has_its_own_type_and_claims() -> None:
    tokens = _tokens()
    target, admin_id, sid, admin_sid = (uuid.uuid4() for _ in range(4))
    expires = datetime.now(UTC) + timedelta(minutes=30)
    issued = tokens.issue_impersonation(
        user_id=target,
        tenant_id=T1,
        session_id=sid,
        impersonator_id=admin_id,
        impersonator_session_id=admin_sid,
        expires_at=expires,
    )
    assert jwt.get_unverified_header(issued.token)["typ"] == IMPERSONATION_TYP
    payload = jwt.decode(issued.token, options={"verify_signature": False})
    assert payload["type"] == "impersonation"
    assert (payload["sub"], payload["tid"], payload["sid"]) == (str(target), str(T1), str(sid))
    assert (payload["imp"], payload["imp_sid"]) == (str(admin_id), str(admin_sid))
    assert "did" not in payload
    claims = tokens.verify(issued.token, "impersonation")
    assert claims == issued.claims
    assert claims.is_impersonation
    assert claims.impersonator_id == admin_id
    for other in ("access", "refresh"):
        with pytest.raises(InvalidTokenError):
            tokens.verify(issued.token, other)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="issue_impersonation"):
        tokens.issue("impersonation", user_id=target, tenant_id=T1, session_id=sid)


def test_an_access_token_carries_its_session_end_and_refuses_impersonation_claims() -> None:
    tokens = _tokens()
    end = datetime.now(UTC) + timedelta(hours=11)
    access = tokens.issue(
        "access", user_id=uuid.uuid4(), tenant_id=T1, session_id=uuid.uuid4(), not_after=end
    )
    assert tokens.verify(access.token, "access").session_expires_at == end
    with pytest.raises(InvalidTokenError):
        tokens.verify(access.token, "impersonation")
    # Forged: an access token with impersonation claims, or an impersonation one with a device.
    signing = tokens.keys.signing
    now = datetime.now(UTC).timestamp()
    base = {
        "iss": "kerp-test",
        "aud": "kerp-test",
        "sub": str(uuid.uuid4()),
        "tid": str(T1),
        "sid": str(uuid.uuid4()),
        "jti": "x" * 32,
        "iat": now,
        "nbf": int(now),
        "exp": now + 600,
    }
    for payload, typ, kind in (
        (
            {**base, "type": "access", "imp": str(uuid.uuid4()), "imp_sid": str(uuid.uuid4())},
            "at+jwt",
            "access",
        ),
        (
            {**base, "type": "impersonation", "imp": str(uuid.uuid4())},
            IMPERSONATION_TYP,
            "impersonation",
        ),
        (
            {
                **base,
                "type": "impersonation",
                "imp": str(uuid.uuid4()),
                "imp_sid": str(uuid.uuid4()),
                "did": str(uuid.uuid4()),
            },
            IMPERSONATION_TYP,
            "impersonation",
        ),
        (
            {**base, "type": "impersonation", "imp": base["sub"], "imp_sid": str(uuid.uuid4())},
            IMPERSONATION_TYP,
            "impersonation",
        ),
        # The right ``type`` claim under the access header, and the other way round.
        (
            {
                **base,
                "type": "impersonation",
                "imp": str(uuid.uuid4()),
                "imp_sid": str(uuid.uuid4()),
            },
            "at+jwt",
            "impersonation",
        ),
        ({**base, "type": "access"}, IMPERSONATION_TYP, "access"),
    ):
        forged = jwt.encode(
            payload,
            signing.private_key,
            algorithm=signing.algorithm,
            headers={"kid": signing.kid, "typ": typ},
        )
        with pytest.raises(InvalidTokenError):
            tokens.verify(forged, kind)  # type: ignore[arg-type]


def test_the_expiry_is_capped_by_the_admin_session(db: Database) -> None:
    admin_id, target = seed_user(db, T1, ALICE), seed_user(db, T1, BOB)
    now = datetime.now(UTC)
    tokens = _tokens(now)
    admin_access = tokens.issue(
        "access",
        user_id=admin_id,
        tenant_id=T1,
        session_id=uuid.uuid4(),
        not_after=now + timedelta(minutes=10),
    ).claims
    with db.unit_of_work(TenancyContext(tenant_id=T1, user_id=admin_id)) as s:
        started = impersonation.start(
            s,
            tokens,
            admin=admin_access,
            admin_device_id=None,
            target_id=target,
            reason=REASON,
            max_minutes=30,
            client_ip=None,
            user_agent=None,
        )
    assert started.expires_at == now + timedelta(minutes=10)
    assert started.token.claims.expires_at == started.expires_at
    # No ``sxp`` (a token from before it existed): only its own expiry is promised.
    legacy = tokens.issue("access", user_id=admin_id, tenant_id=T1, session_id=uuid.uuid4())
    assert impersonation.admin_session_end(legacy.claims) == legacy.claims.expires_at
    assert legacy.claims.session_expires_at is None


# --- start --------------------------------------------------------------------------------------


def test_a_system_admin_starts_an_impersonation(api: Api) -> None:
    admin_login = api.login(ALICE)
    response = api.start(
        BOB, headers={**bearer(admin_login["access_token"]), "User-Agent": "synthetic-agent/1.0"}
    )
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["target_user_id"] == str(api.users[BOB])
    assert "refresh_token" not in body
    assert "kerp_refresh" not in response.cookies
    assert body["expires_in"] == 1800  # the default 30 minutes
    token = body["access_token"]
    payload = jwt.decode(token, options={"verify_signature": False})
    admin_claims = jwt.decode(admin_login["access_token"], options={"verify_signature": False})
    assert payload["sub"] == str(api.users[BOB])
    assert payload["imp"] == str(api.users[ALICE])
    assert payload["imp_sid"] == admin_claims["sid"]
    assert payload["sid"] == body["impersonation_session_id"] != admin_claims["sid"]
    assert payload["jti"] != admin_claims["jti"]
    assert "did" not in payload

    (started,) = api.chain()
    assert started == {
        "action": "impersonation.started",
        "session_id": body["impersonation_session_id"],
        "admin_user_id": str(api.users[ALICE]),
        "target_user_id": str(api.users[BOB]),
        "reason": REASON,
        "client_ip": "testclient",
        "user_agent": "synthetic-agent/1.0",
        "started_at": started["started_at"],
        "expires_at": started["expires_at"],
        "admin_device_id": None,
    }
    assert datetime.fromisoformat(started["expires_at"]) == datetime.fromisoformat(
        body["expires_at"]
    )
    assert token not in str(api.chain())  # never the token
    row = api.row(body["impersonation_session_id"])
    assert (row.admin_user_id, row.target_user_id, row.ended_at) == (
        api.users[ALICE],
        api.users[BOB],
        None,
    )
    me = api.client.get(ME, headers=bearer(token))
    assert (me.status_code, me.json()["id"]) == (200, str(api.users[BOB]))
    # The admin's own session is untouched and still the admin's.
    assert api.client.get(ME, headers=bearer(admin_login["access_token"])).json()["id"] == str(
        api.users[ALICE]
    )
    assert api.outbox.of() == []  # no notice while the session is live


@pytest.mark.parametrize(
    ("caller", "target", "reason", "status", "code"),
    [
        (BOB, CAROL, REASON, 403, None),  # not a System admin
        (DAVE, CAROL, REASON, 403, None),  # create_projects is not system_admin
        (ALICE, BOB, "   ", 422, None),
        (ALICE, BOB, "", 422, None),
        (ALICE, BOB, "x" * 501, 422, None),
        (ALICE, FRED, REASON, 422, "USER_DEACTIVATED"),
        (ALICE, ERIN, REASON, 403, "CANNOT_IMPERSONATE_SYSTEM_ADMIN"),
        (ALICE, ALICE, REASON, 422, "CANNOT_IMPERSONATE_SELF"),
    ],
)
def test_refused_starts(
    api: Api, caller: str, target: str, reason: str, status: int, code: str | None
) -> None:
    response = api.start(target, caller, reason)
    assert response.status_code == status, response.text
    assert response.json().get("code") == code
    assert api.chain() == []


def test_an_unknown_user_is_404_and_a_500_character_reason_is_fine(api: Api) -> None:
    assert api.start(uuid.uuid4()).status_code == 404
    assert api.start(BOB, reason="y" * 500).status_code == 200


def test_no_nested_impersonation(api: Api) -> None:
    token = api.impersonate(BOB)["access_token"]
    response = api.start(CAROL, headers=bearer(token))
    assert response.status_code == 403
    assert response.json()["code"] == "IMPERSONATION_ACTION_FORBIDDEN"


# --- scope ---------------------------------------------------------------------------------------


def test_the_target_scope_is_exact(api: Api) -> None:
    """Target member of HOME only: the admin sees AWAY normally, as the target it is 404."""
    admin = api.headers(ALICE)
    for project in (api.home, api.away):
        assert api.client.get(f"/api/v1/projects/{project}", headers=admin).status_code == 200
    as_bob = bearer(api.impersonate(BOB)["access_token"])
    assert api.client.get(f"/api/v1/projects/{api.home}", headers=as_bob).status_code == 200
    assert api.client.get(f"/api/v1/projects/{api.away}", headers=as_bob).status_code == 404
    listed = api.client.get("/api/v1/projects", headers=as_bob).json()
    assert [p["id"] for p in listed] == [str(api.home)]
    # Admin-only endpoints answer as for the target.
    assert api.client.get("/api/v1/admin/users", headers=as_bob).status_code == 403


def test_the_scope_binds_the_target_without_system_admin(api: Api) -> None:
    from starlette.datastructures import State

    from app.core.auth.access import GRANTS_STATE, resolve_request_scope
    from app.core.auth.deps import IMPERSONATION_STATE

    body = api.impersonate(BOB)
    claims = api.runtime.tokens.verify(body["access_token"], "impersonation")
    state = State()
    base = TenancyContext(tenant_id=T1, user_id=api.users[BOB])
    scope = resolve_request_scope(api.db, base, state=state, impersonation=claims)
    assert scope.user_id == api.users[BOB]
    assert scope.is_system_admin is False
    assert scope.member_project_ids == {api.home}
    assert getattr(state, GRANTS_STATE)[1] == api.users[BOB]
    marker = getattr(state, IMPERSONATION_STATE)
    assert (marker.impersonator_user_id, marker.read_only) == (api.users[ALICE], False)


def test_records_written_while_impersonating_are_attributed_to_the_target(api: Api) -> None:
    as_bob = bearer(api.impersonate(BOB)["access_token"])
    url = f"/api/v1/saved-views/projects/{api.home}/views"
    created = api.client.post(
        url, json={"register_key": "assets.equipment", "name": "Mine"}, headers=as_bob
    )
    assert created.status_code == 201, created.text
    view_id = created.json()["id"]
    renamed = api.client.patch(f"{url}/{view_id}", json={"name": "Renamed"}, headers=as_bob)
    assert renamed.status_code == 200, renamed.text
    with api.db.unit_of_work(ROOT) as s:
        view = s.get_one(SavedView, uuid.UUID(view_id))
        assert (view.created_by, view.updated_by) == (api.users[BOB], api.users[BOB])
        assert api.users[ALICE] not in (view.created_by, view.updated_by)


def _node_entry(api: Api, headers: dict[str, str], level_id: str, code: str) -> AuditEntry:
    """Create a node over the API and return its module ledger entry (asset hierarchy)."""
    url = f"/api/v1/asset-management/projects/{api.home}/nodes"
    created = api.client.post(url, json={"level_def_id": level_id, "code": code}, headers=headers)
    assert created.status_code == 201, created.text
    key = f"asset_node:{created.json()['id']}"
    with api.db.unit_of_work(TenancyContext(tenant_id=T1, project_id=api.home)) as s:
        [entry] = chain_entries(s, key)
        assert verify_chain(s, key).ok
        s.expunge(entry)
        return entry


def test_a_mutation_while_impersonating_names_both_identities_in_its_ledger_entry(
    api: Api,
) -> None:
    # Option (a) (TD-426, ADR 0015 §6): an allowed mutation is attributed to the target, and
    # its module ledger entry carries the admin as ``impersonated_by_user_id`` at hash v3.
    with api.db.unit_of_work(
        TenancyContext(tenant_id=T1, project_id=api.home, is_system_admin=True)
    ) as s:
        level = create_level_def(
            s,
            LevelDefCreate(key="site", depth=0, label_key="level.site", roles={"work_anchor"}),
            actor_id=None,
        )
        level_id = str(level.id)
    alice, bob = api.users[ALICE], api.users[BOB]
    as_bob = bearer(api.impersonate(BOB)["access_token"])
    impersonated = _node_entry(api, as_bob, level_id, "S1")
    assert (impersonated.created_by, impersonated.impersonated_by_user_id) == (bob, alice)
    assert impersonated.hash_version == 3
    # The same mutation by the target itself: v2, no impersonator.
    own = _node_entry(api, api.headers(BOB), level_id, "S2")
    assert (own.created_by, own.impersonated_by_user_id, own.hash_version) == (bob, None, 2)
    # The start is the admin's own request (v2, no impersonator); the exit is made under the
    # impersonation token with the admin as actor, so it names the admin in both (v3).
    assert api.client.post(EXIT, headers=as_bob).status_code == 204
    with api.db.unit_of_work(ROOT) as s:
        started, ended = chain_entries(s, CHAIN)
        assert (started.created_by, started.impersonated_by_user_id) == (alice, None)
        assert started.hash_version == 2
        assert (ended.created_by, ended.impersonated_by_user_id) == (alice, alice)
        assert ended.hash_version == 3
        assert verify_chain(s, CHAIN).ok


def test_a_create_projects_holder_is_impersonated_without_the_grant(api: Api) -> None:
    body = {"code": "NEWP", "name": "Synthetic new"}
    assert (
        api.client.post("/api/v1/projects", json=body, headers=api.headers(DAVE)).status_code == 201
    )
    as_dave = bearer(api.impersonate(DAVE)["access_token"])
    refused = api.client.post("/api/v1/projects", json={**body, "code": "NEWQ"}, headers=as_dave)
    assert refused.status_code == 403, refused.text


def test_a_client_representative_session_is_read_only(api: Api) -> None:
    as_carol = bearer(api.impersonate(CAROL)["access_token"])
    probe = api.client.get(PRINCIPAL_PROBE, headers=as_carol).json()
    assert probe == {
        "user_id": str(api.users[CAROL]),
        "impersonator_user_id": str(api.users[ALICE]),
        "read_only": True,
        "device_id": "",
    }
    as_bob = bearer(api.impersonate(BOB)["access_token"])
    assert api.client.get(PRINCIPAL_PROBE, headers=as_bob).json()["read_only"] is False
    admin = api.client.get(PRINCIPAL_PROBE, headers=api.headers(ALICE)).json()
    assert (admin["impersonator_user_id"], admin["read_only"]) == ("", False)


def test_an_impersonation_is_never_bound_to_a_device(api: Api) -> None:
    token = api.impersonate(BOB)["access_token"]
    headers = {**bearer(token), "X-KERP-Device-Id": str(uuid.uuid4())}
    # A device header does not bind the request: the principal has no device.
    assert api.client.get(PRINCIPAL_PROBE, headers=headers).json()["device_id"] == ""
    # Nor does the token: no ``did`` claim, and a token that carries one never verifies (the
    # forged ``did`` case of test_an_access_token_carries_its_session_end_and_refuses_...).
    assert "did" not in jwt.decode(token, options={"verify_signature": False})


# --- no refresh ---------------------------------------------------------------------------------


def test_refresh_refuses_an_impersonation_token(api: Api) -> None:
    admin_login = api.login(ALICE)
    token = api.start(BOB, headers=bearer(admin_login["access_token"])).json()["access_token"]
    assert api.client.post("/api/v1/auth/refresh", headers=cookie_headers(token)).status_code == 401
    # The admin's own refresh cookie still renews the admin's session.
    renewed = api.client.post(
        "/api/v1/auth/refresh", headers=cookie_headers(admin_login["refresh_token"])
    )
    assert renewed.status_code == 200, renewed.text
    again = jwt.decode(renewed.json()["access_token"], options={"verify_signature": False})
    assert again["sub"] == str(api.users[ALICE])
    assert "imp" not in again


# --- ending -------------------------------------------------------------------------------------


def _assert_one_notice(api: Api, target: str = BOB) -> QueuedMail:
    (mail,) = api.outbox.of()
    with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        email = s.get_one(User, api.users[target]).email
    assert mail.to == email
    # The delivery ledger knows whose address it is (T-0226, TD-577).
    with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        assert s.get_one(MailSend, mail.send_id).recipient_user_id == api.users[target]
    return mail


def test_exit_ends_the_session_once_and_resumes_the_admin(api: Api) -> None:
    admin_login = api.login(ALICE)
    body = api.start(BOB, headers=bearer(admin_login["access_token"])).json()
    token = body["access_token"]
    assert api.client.post(EXIT, headers=bearer(token)).status_code == 204
    assert api.client.get(ME, headers=bearer(token)).status_code == 401
    assert api.client.post(EXIT, headers=bearer(token)).status_code == 401
    (ended,) = api.ended()
    assert ended["reason"] == "exit"
    assert (ended["session_id"], ended["target_user_id"]) == (
        body["impersonation_session_id"],
        str(api.users[BOB]),
    )
    assert api.row(body["impersonation_session_id"]).end_reason == "exit"
    _assert_one_notice(api)
    # The admin's tokens were never touched.
    assert api.client.get(ME, headers=bearer(admin_login["access_token"])).status_code == 200
    refreshed = api.client.post(
        "/api/v1/auth/refresh", headers=cookie_headers(admin_login["refresh_token"])
    )
    assert refreshed.status_code == 200


def test_exit_needs_an_impersonation_token(api: Api) -> None:
    response = api.client.post(EXIT, headers=api.headers(ALICE))
    assert (response.status_code, response.json()["code"]) == (403, "NOT_IMPERSONATING")
    assert api.client.post(EXIT).status_code == 401


def test_logout_with_the_impersonation_token_ends_only_the_impersonation(api: Api) -> None:
    admin_login = api.login(ALICE)
    token = api.start(BOB, headers=bearer(admin_login["access_token"])).json()["access_token"]
    response = api.client.post("/api/v1/auth/logout", headers={**bearer(token), "Origin": ORIGIN})
    assert response.status_code == 204
    assert "kerp_refresh" not in response.headers.get("set-cookie", "")
    assert api.client.get(ME, headers=bearer(token)).status_code == 401
    assert [e["reason"] for e in api.ended()] == ["exit"]
    assert api.client.get(ME, headers=bearer(admin_login["access_token"])).status_code == 200


def test_admin_logout_revokes_the_impersonation(api: Api) -> None:
    admin_login = api.login(ALICE)
    token = api.start(BOB, headers=bearer(admin_login["access_token"])).json()["access_token"]
    assert api.client.get(ME, headers=bearer(token)).status_code == 200
    logout = api.client.post("/api/v1/auth/logout", headers=bearer(admin_login["access_token"]))
    assert logout.status_code == 204
    assert api.client.get(ME, headers=bearer(token)).status_code == 401
    assert api.client.get(ME, headers=bearer(token)).status_code == 401
    assert [e["reason"] for e in api.ended()] == ["revoked"]
    _assert_one_notice(api)


def test_removing_the_admin_grant_revokes_the_impersonation(api: Api) -> None:
    token = api.impersonate(BOB)["access_token"]
    removed = api.client.delete(
        f"/api/v1/admin/grants/{api.users[ALICE]}/system_admin", headers=api.headers(ERIN)
    )
    assert removed.status_code == 204, removed.text
    assert api.client.get(ME, headers=bearer(token)).status_code == 401
    assert api.client.get(ME, headers=bearer(token)).status_code == 401
    assert [e["reason"] for e in api.ended()] == ["revoked"]
    _assert_one_notice(api)


def test_deactivating_the_admin_revokes_the_impersonation(api: Api) -> None:
    token = api.impersonate(BOB)["access_token"]
    patched = api.client.patch(
        f"/api/v1/admin/users/{api.users[ALICE]}",
        json={"is_active": False},
        headers=api.headers(ERIN),
    )
    assert patched.status_code == 200, patched.text
    assert api.client.get(ME, headers=bearer(token)).status_code == 401
    assert [e["reason"] for e in api.ended()] == ["revoked"]
    _assert_one_notice(api)


NEW_PASSWORD = "plum-Radio 41 kettle"  # synthetic


def _change_password(api: Api, email: str) -> None:
    """An admin-side password set or reset of ``email`` (stamps ``password_changed_at``)."""
    with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        set_user_password(s, api.users[email], NEW_PASSWORD, settings=make_settings())


def test_an_admin_password_change_revokes_the_impersonation(api: Api) -> None:
    # The "admin's account compromised: reset the password" case (ADR 0015 §2-3): the change
    # kills the admin's tokens through ``password_changed_at``, not a session revocation, and
    # must end the impersonation started from them as well.
    admin_login = api.login(ALICE)
    token = api.start(BOB, headers=bearer(admin_login["access_token"])).json()["access_token"]
    assert api.client.get(ME, headers=bearer(token)).status_code == 200
    _change_password(api, ALICE)
    assert api.client.get(ME, headers=bearer(admin_login["access_token"])).status_code == 401
    assert api.client.get(ME, headers=bearer(token)).status_code == 401
    assert api.client.get(ME, headers=bearer(token)).status_code == 401
    assert [e["reason"] for e in api.ended()] == ["revoked"]
    _assert_one_notice(api)
    # An impersonation started after the change (a new sign-in) is not refused by it.
    relogin = api.client.post("/api/v1/auth/login", json={"email": ALICE, "password": NEW_PASSWORD})
    assert relogin.status_code == 200, relogin.text
    fresh = api.start(BOB, headers=bearer(relogin.json()["access_token"]))
    assert fresh.status_code == 200, fresh.text
    assert api.client.get(ME, headers=bearer(fresh.json()["access_token"])).status_code == 200


def test_the_sweep_revokes_after_an_admin_password_change(api: Api) -> None:
    store = api.runtime.store
    live = api.impersonate(BOB)
    assert impersonation.sweep(api.db, store, T1) == {"expired": 0, "revoked": 0}
    _change_password(api, ALICE)
    assert impersonation.sweep(api.db, store, T1) == {"expired": 0, "revoked": 1}
    assert impersonation.sweep(api.db, store, T1) == {"expired": 0, "revoked": 0}
    assert api.row(live["impersonation_session_id"]).end_reason == "revoked"
    assert api.client.get(ME, headers=bearer(live["access_token"])).status_code == 401
    assert [e["reason"] for e in api.ended()] == ["revoked"]
    _assert_one_notice(api)
    # A password change of the target or another user does not touch the admin's sessions.
    other = api.impersonate(CAROL, caller=ERIN)
    _change_password(api, DAVE)
    assert impersonation.sweep(api.db, store, T1) == {"expired": 0, "revoked": 0}
    assert api.client.get(ME, headers=bearer(other["access_token"])).status_code == 200


def test_no_impersonation_starts_from_a_token_older_than_the_admin_password(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A change committed between the request's authentication and the start: the admin's row
    # is re-checked under its lock before the impersonation token is issued (T-0077).
    admin_token = api.login(ALICE)["access_token"]
    claims = api.runtime.tokens.verify(admin_token, "access")
    _change_password(api, ALICE)
    with (
        api.db.unit_of_work(TenancyContext(tenant_id=T1, user_id=api.users[ALICE])) as s,
        pytest.raises(AuthenticationError),
    ):
        impersonation.start(
            s,
            api.runtime.tokens,
            admin=claims,
            admin_device_id=None,
            target_id=api.users[BOB],
            reason=REASON,
            max_minutes=30,
            client_ip=None,
            user_agent=None,
        )
    assert api.chain() == []
    # Through the endpoint the same refusal is a 401 (the caller's own token is dead too).
    monkeypatch.setattr(impersonation, "load_active_user", _refuse_authentication)
    refused = api.start(BOB, caller=ERIN)
    assert refused.status_code == 401
    assert refused.headers["WWW-Authenticate"] == 'Bearer error="invalid_token"'
    assert api.chain() == []


def _refuse_authentication(*_args: Any, **_kwargs: Any) -> User:
    raise AuthenticationError("user not active")


def test_an_expired_token_is_refused_and_ends_the_session_lazily(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = api.headers(ALICE)
    # Issue it in the past: it expired in real time before it is first used.
    past = datetime.now(UTC) - timedelta(hours=1)
    monkeypatch.setattr(api.runtime.tokens, "_clock", lambda: past)
    body = api.start(BOB, headers=admin).json()
    monkeypatch.undo()
    token = body["access_token"]
    assert api.client.get(ME, headers=bearer(token)).status_code == 401
    assert api.client.get(ME, headers=bearer(token)).status_code == 401
    assert [e["reason"] for e in api.ended()] == ["expired"]
    _assert_one_notice(api)


def test_the_sweep_ends_expired_and_revoked_sessions_once(api: Api) -> None:
    store = api.runtime.store
    live = api.impersonate(BOB)
    assert impersonation.sweep(api.db, store, T1) == {"expired": 0, "revoked": 0}
    assert api.outbox.of() == []  # nothing while the session is live
    later = datetime.now(UTC) + timedelta(minutes=31)
    assert impersonation.sweep(api.db, store, T1, now=later) == {"expired": 1, "revoked": 0}
    assert impersonation.sweep(api.db, store, T1, now=later) == {"expired": 0, "revoked": 0}
    assert api.row(live["impersonation_session_id"]).end_reason == "expired"
    _assert_one_notice(api)
    # The admin's session revoked without the token being used again.
    admin_login = api.login(ALICE)
    other = api.start(CAROL, headers=bearer(admin_login["access_token"])).json()
    api.client.post("/api/v1/auth/logout", headers=bearer(admin_login["access_token"]))
    assert impersonation.sweep(api.db, store, T1) == {"expired": 0, "revoked": 1}
    assert api.row(other["impersonation_session_id"]).end_reason == "revoked"
    assert api.client.get(ME, headers=bearer(other["access_token"])).status_code == 401
    assert [e["reason"] for e in api.ended()] == ["expired", "revoked"]
    assert len(api.outbox.of()) == 2


def test_the_notice_names_the_admin_and_the_times_never_the_reason_or_token(api: Api) -> None:
    body = api.impersonate(BOB)
    api.client.post(EXIT, headers=bearer(body["access_token"]))
    mail = _assert_one_notice(api)
    assert set(mail.context) == {"admin_name", "started_at", "ended_at"}
    assert mail.context["admin_name"] == "Alice Admin"
    rendered = render(
        TEMPLATES.get(NOTICE),
        mail.context,
        locale=None,
        default_locale="en-AU",
        app_name="KERP",
        web_origin="https://kerp.example.test",
    )
    text = rendered.subject + rendered.text + rendered.html
    assert "Alice Admin" in text
    assert str(mail.context["started_at"]) in text
    assert str(mail.context["ended_at"]) in text
    assert "recorded under your name" in text
    assert "audited as impersonated" in text
    assert REASON not in text
    assert body["access_token"] not in text
    assert "http" not in rendered.text.replace("https://kerp.example.test", "")
    assert "<a " not in rendered.html


def test_a_failed_notice_never_affects_the_end(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> uuid.UUID:
        raise ValueError("synthetic: address refused")

    monkeypatch.setattr(mail_service, "send_mail", refuse)
    token = api.impersonate(BOB)["access_token"]
    assert api.client.post(EXIT, headers=bearer(token)).status_code == 204
    assert api.client.get(ME, headers=bearer(token)).status_code == 401
    assert [e["reason"] for e in api.ended()] == ["exit"]
    assert api.outbox.of() == []


def test_ending_is_recorded_once_whatever_path_comes_second(api: Api) -> None:
    body = api.impersonate(BOB)
    session_id = uuid.UUID(body["impersonation_session_id"])
    with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        assert impersonation.end(s, session_id, "exit")
    with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        assert not impersonation.end(s, session_id, "revoked")
    with (
        pytest.raises(ValueError, match="reason must be one of"),
        api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s,
    ):
        impersonation.end(s, session_id, "other")
    assert [e["reason"] for e in api.ended()] == ["exit"]
    assert len(api.outbox.of()) == 1


def test_impersonation_leaves_the_target_rows_alone(api: Api) -> None:
    """No side effects on the target (ADR 0015 §7): its own row is not written."""
    with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        before = s.execute(
            sa.select(User.updated_at, User.revision).where(User.id == api.users[BOB])
        ).one()
    token = api.impersonate(BOB)["access_token"]
    for _ in range(2):
        assert api.client.get(ME, headers=bearer(token)).status_code == 200
    api.client.post(EXIT, headers=bearer(token))
    with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        after = s.execute(
            sa.select(User.updated_at, User.revision).where(User.id == api.users[BOB])
        ).one()
    assert after == before


def test_the_batch_cron_runs_the_sweep_for_the_deployment_tenant(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.jobs import scheduler

    api.impersonate(BOB)
    later = datetime.now(UTC) + timedelta(minutes=31)
    monkeypatch.setattr(impersonation, "utcnow", lambda: later)
    ctx: dict[str, Any] = {"db": api.db, "settings": make_settings(default_tenant_id=None)}
    asyncio.run(scheduler.impersonation_sweep(ctx))  # no deployment tenant: nothing
    assert api.ended() == []
    ctx["settings"] = make_settings()
    asyncio.run(scheduler.impersonation_sweep(ctx))
    assert [e["reason"] for e in api.ended()] == ["expired"]
    _assert_one_notice(api)


def test_a_deactivated_target_is_refused_like_any_deactivated_user(api: Api) -> None:
    token = api.impersonate(BOB)["access_token"]
    with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        s.get_one(User, api.users[BOB]).is_active = False
    assert api.client.get(ME, headers=bearer(token)).status_code == 401


@pytest.mark.parametrize("store", [InMemoryRevocationStore(), fake_store()])
def test_a_revoked_session_is_told_apart_from_a_revoked_token(store: Any) -> None:
    session_id = uuid.uuid4()
    store.revoke_token("a-token", timedelta(minutes=5))
    assert not store.is_session_revoked(session_id)
    store.revoke_session(session_id, timedelta(minutes=5))
    assert store.is_session_revoked(session_id)
