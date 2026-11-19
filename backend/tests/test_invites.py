"""Project invites: create, list, revoke, re-send, preview, accept, the carry-through of an
``invite_token`` over password login, magic link and Entra ID SSO, the send cap, single use
under concurrency and log scrubbing (T-0052, diff §4.1 Invites, §4.5; edge spec §6 amendments).

Runs on SQLite (the ORM scope mirrors the table's RLS policy) and, in CI, on PostgreSQL under
RLS against the migrated schema. Raw-SQL policy checks as ``kerp_app``: ``test_rls_invites.py``.
All data is synthetic. The magic link is "delivered" by the capturing sender of the auth tests
(the delivery seam in front of the mail transport); Entra ID is the fake OIDC provider of
T-0054.
"""

import hashlib
import json
import logging
import sys
import threading
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.audit.service import chain_entries
from app.core.auth import entra, post_auth
from app.core.auth.access import SYSTEM_ADMIN, Persona
from app.core.auth.magic_link import MagicLinkCandidate, magic_link_eligible
from app.core.auth.models import ProjectMembership, User
from app.core.auth.revocation import RedisRevocationStore
from app.core.auth.sso_router import PREAUTH_COOKIE
from app.core.db import Base, BaseRecordMixin, Database, utcnow
from app.core.logging import REDACTED, JsonFormatter, scrub, scrub_text
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import (
    PROJECT_INVITE_POLICY_NAMES,
    expected_policy_names,
    rls_policy_sql,
    table_policy,
)
from app.core.tenancy.scope import OutOfScopeError
from app.modules.admin import invites
from app.modules.admin import service as admin
from app.modules.admin.models import INVITE_TABLE, ProjectInvite
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import PASSWORD, T1, CapturingSender, auth_app, make_settings, seed_user
from tests.db_support import database_url
from tests.sso_support import ENTRA_CLIENT, ENTRA_TENANT, FakeEntra

ADMIN = "/api/v1/admin"
PREVIEW = f"{ADMIN}/invites/preview"
ACCEPT = f"{ADMIN}/invites/accept"
LOGIN = "/api/v1/auth/login"
MAGIC = "/api/v1/auth/magic-link"
REDEEM = "/api/v1/auth/magic-link/redeem"
SSO_START = "/api/v1/auth/sso/entra/start"
SSO_CALLBACK = "/api/v1/auth/sso/entra/callback"
SSO_EXCHANGE = "/api/v1/auth/sso/entra/exchange"
DEPLOYMENT = "https://kerp.example.test"
NATIVE_ORIGIN = "https://localhost"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}
VERIFIER = "v" * 43 + "-synthetic-app-verifier"
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
# ROOT_ADMIN: System admin. PAM: PER-09 of P1. PETE: PER-09 of P2. IVAN: PER-02 of P1 (no
# PER-09). EXT, WORKER: accounts with a password and no membership. STAFF: no account until
# invited (a company member, signs in through Entra ID). NEW: no account until invited.
ROOT_ADMIN = "root@example.test"
PAM = "pam@example.test"
PETE = "pete@example.test"
IVAN = "ivan@example.test"
EXT = "ext@example.test"
WORKER = "worker@example.test"
STAFF = "staff@company.example"
NEW = "new.person@example.test"
MINUTE = timedelta(minutes=1)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


class Api:
    def __init__(
        self,
        client: TestClient,
        db: Database,
        redis: fakeredis.FakeRedis,
        sender: CapturingSender,
        fake: FakeEntra,
    ) -> None:
        self.client, self.db, self.redis, self.sender, self.fake = client, db, redis, sender, fake
        self.users = {
            e: seed_user(db, T1, e, display_name=e.split("@")[0].title())
            for e in (ROOT_ADMIN, PAM, PETE, IVAN, EXT, WORKER)
        }
        with db.unit_of_work(ROOT) as s:
            admin.grant(s, self.users[ROOT_ADMIN], SYSTEM_ADMIN, actor_id=None)
            self.p1 = projects.create_project(s, ProjectCreate(code="P1", name="Synthetic one")).id
            self.p2 = projects.create_project(s, ProjectCreate(code="P2", name="Synthetic two")).id
            for email, project, persona in (
                (PAM, self.p1, Persona.PROJECT_ADMINISTRATOR),
                (PETE, self.p2, Persona.PROJECT_ADMINISTRATOR),
                (IVAN, self.p1, Persona.PROJECT_MANAGER),
            ):
                s.add(
                    ProjectMembership(
                        tenant_id=T1,
                        user_id=self.users[email],
                        member_project_id=project,
                        persona=persona.value,
                    )
                )
        self._headers: dict[str, dict[str, str]] = {}

    # --- sessions ---------------------------------------------------------------------

    def headers(self, email: str) -> dict[str, str]:
        if email not in self._headers:
            response = self.client.post(LOGIN, json={"email": email, "password": PASSWORD})
            assert response.status_code == 200, response.text
            self._headers[email] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._headers[email]

    def login(self, email: str, token: str | None = None, password: str = PASSWORD) -> Any:
        body = {"email": email, "password": password}
        if token is not None:
            body["invite_token"] = token
        return self.client.post(LOGIN, json=body)

    # --- the administrators' side -------------------------------------------------------

    def url(self, project: uuid.UUID, *parts: object) -> str:
        return "/".join([f"{ADMIN}/projects/{project}/invites", *map(str, parts)])

    def invite(
        self,
        email: str,
        *personas: str,
        by: str = PAM,
        project: uuid.UUID | None = None,
        **extra: Any,
    ) -> Any:
        grants = [{"persona": p} for p in personas or ("PER-03",)]
        body = {"email": email, "grants": grants, **extra}
        return self.client.post(self.url(project or self.p1), json=body, headers=self.headers(by))

    def invited(self, email: str, *personas: str, **extra: Any) -> dict[str, Any]:
        response = self.invite(email, *personas, **extra)
        assert response.status_code == 201, response.text
        body: dict[str, Any] = response.json()
        return body

    def listed(self, by: str = PAM, project: uuid.UUID | None = None) -> list[dict[str, Any]]:
        response = self.client.get(self.url(project or self.p1), headers=self.headers(by))
        assert response.status_code == 200, response.text
        found: list[dict[str, Any]] = response.json()
        return found

    def revoke(self, invite_id: str, by: str = PAM, project: uuid.UUID | None = None) -> Any:
        return self.client.delete(self.url(project or self.p1, invite_id), headers=self.headers(by))

    def resend(self, invite_id: str, by: str = PAM, project: uuid.UUID | None = None) -> Any:
        url = self.url(project or self.p1, invite_id, "resend")
        return self.client.post(url, headers=self.headers(by))

    # --- the invitee's side -------------------------------------------------------------

    def preview(self, email: str, token: str) -> Any:
        return self.client.post(PREVIEW, json={"invite_token": token}, headers=self.headers(email))

    def accept(self, email: str, token: str) -> Any:
        return self.client.post(ACCEPT, json={"invite_token": token}, headers=self.headers(email))

    # --- the database behind the API ------------------------------------------------------

    def rows(self, email: str | None = None) -> list[ProjectInvite]:
        with self.db.unit_of_work(ROOT) as s:
            query = select(ProjectInvite).order_by(ProjectInvite.created_at)
            if email is not None:
                query = query.where(ProjectInvite.invited_email == email)
            return list(s.scalars(query))

    def age(self, email: str, by: timedelta = 2 * MINUTE) -> None:
        """Make every send to ``email`` ``by`` older (the clock the send cap reads)."""
        with self.db.unit_of_work(ROOT) as s:
            for row in s.scalars(select(ProjectInvite).where(ProjectInvite.invited_email == email)):
                row.sent_at = [(datetime.fromisoformat(t) - by).isoformat() for t in row.sent_at]
                row.last_sent_at = _aware(row.last_sent_at) - by

    def expire(self, invite_id: str) -> None:
        with self.db.unit_of_work(ROOT) as s:
            row = s.get(ProjectInvite, uuid.UUID(invite_id))
            assert row is not None
            row.expires_at = utcnow() - MINUTE

    def personas(self, email: str, project: uuid.UUID | None = None) -> set[str]:
        with self.db.unit_of_work(ROOT) as s:
            user = s.scalars(select(User).where(User.email == email)).one()
            rows = s.scalars(
                select(ProjectMembership).where(
                    ProjectMembership.user_id == user.id,
                    ProjectMembership.member_project_id == (project or self.p1),
                )
            )
            return {row.persona for row in rows}

    def account(self, email: str) -> User | None:
        with self.db.unit_of_work(ROOT) as s:
            return s.scalars(select(User).where(User.email == email)).one_or_none()

    def audit(
        self, project: uuid.UUID | None = None, chain: str | None = None
    ) -> list[dict[str, Any]]:
        key = chain or f"project-invites:{project or self.p1}"
        with self.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
            return [dict(entry.payload) for entry in chain_entries(s, key)]

    def actions(self, project: uuid.UUID | None = None) -> list[str]:
        return [
            f"{e['action']}:{e['reason']}" if "reason" in e else e["action"]
            for e in self.audit(project)
        ]

    # --- Entra ID SSO (the fake provider of T-0054) ---------------------------------------

    def sso_start(self, **form: str) -> tuple[str, str]:
        response = self.client.post(SSO_START, data=form, headers=FORM, follow_redirects=False)
        assert response.status_code == 303, response.text
        return response.headers["location"], response.cookies[PREAUTH_COOKIE]

    def sso_callback(self, location: str, cookie: str, **claims: Any) -> dict[str, str]:
        code, state = self.fake.authorize(location, **claims)
        self.client.cookies.clear()
        response = self.client.get(
            SSO_CALLBACK,
            params={"code": code, "state": state},
            headers={"Cookie": f"{PREAUTH_COOKIE}={cookie}"},
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text
        query = parse_qs(urlsplit(response.headers["location"]).query)
        return {k: v[0] for k, v in query.items()}


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    redis = fakeredis.FakeRedis()
    settings = make_settings(
        database_url=database_url(tmp_path),
        web_origin=DEPLOYMENT,
        native_app_origins=NATIVE_ORIGIN,
        entra_tenant_id=ENTRA_TENANT,
        entra_client_id=ENTRA_CLIENT,
        entra_client_secret="synthetic-client-secret",
        entra_redirect_uri=f"{DEPLOYMENT}{SSO_CALLBACK}",
    )
    sender, fake = CapturingSender(), FakeEntra()
    app, _runtime = auth_app(
        settings, store=RedisRevocationStore(redis), magic_links=sender, entra_transport=fake
    )
    with TestClient(app) as client:
        yield Api(client, db, redis, sender, fake)


# --- contract -------------------------------------------------------------------------------


def test_the_table_is_a_base_record_under_its_own_policy() -> None:
    assert issubclass(ProjectInvite, BaseRecordMixin)
    table = Base.metadata.tables[INVITE_TABLE]
    policy = table_policy(table)
    assert policy is not None
    assert policy.kind == "project_invite"
    assert expected_policy_names(table) == PROJECT_INVITE_POLICY_NAMES
    ddl = " ".join(rls_policy_sql(INVITE_TABLE, policy))
    assert "FORCE ROW LEVEL SECURITY" in ddl
    assert "app.admin_project_ids" in ddl
    assert "app.current_user_id" not in ddl  # never the invitee
    assert "FOR DELETE" not in ddl  # nobody deletes an invite
    assert make_settings().invite_ttl_hours == 168


def test_every_endpoint_needs_a_token(api: Api) -> None:
    some = uuid.uuid4()
    calls = [
        ("POST", api.url(api.p1)),
        ("GET", api.url(api.p1)),
        ("DELETE", api.url(api.p1, some)),
        ("POST", api.url(api.p1, some, "resend")),
        ("POST", PREVIEW),
        ("POST", ACCEPT),
    ]
    assert [api.client.request(m, u, json={}).status_code for m, u in calls] == [401] * 6


def test_only_the_projects_administrators_see_and_write_invites(api: Api) -> None:
    """The ORM scope (SQLite) and RLS (PostgreSQL) agree: PER-09 of the project and System
    admins; not the invitee, not a member, not another project's PER-09. Nobody deletes."""
    invite_id = uuid.UUID(api.invited(EXT)["id"])
    p1 = frozenset({api.p1})

    def seen(context: TenancyContext) -> list[uuid.UUID]:
        with api.db.unit_of_work(context) as s:
            return list(s.scalars(select(ProjectInvite.id)))

    def as_user(email: str, **extra: Any) -> TenancyContext:
        return TenancyContext(tenant_id=T1, user_id=api.users[email], **extra)

    pam = as_user(PAM, member_project_ids=p1, admin_project_ids=p1)
    assert seen(pam) == [invite_id]
    assert seen(ROOT) == [invite_id]
    assert seen(as_user(EXT)) == []
    assert seen(as_user(IVAN, member_project_ids=p1)) == []
    p2 = frozenset({api.p2})
    pete = as_user(PETE, member_project_ids=p2, admin_project_ids=p2)
    assert seen(pete) == []
    forged = ProjectInvite(
        tenant_id=T1,
        invited_project_id=api.p1,
        invited_email=PETE,
        grants=[{"persona": "PER-09", "site_node_id": None}],
        inviter_id=api.users[PETE],
        token_hash="0" * 64,
        expires_at=utcnow() + MINUTE,
        last_sent_at=utcnow(),
        sent_at=[],
    )
    with api.db.unit_of_work(pete) as s:
        s.add(forged)
        with pytest.raises(OutOfScopeError):
            s.flush()
        s.rollback()
    with api.db.unit_of_work(pam) as s:
        row = s.get(ProjectInvite, invite_id)
        assert row is not None
        s.delete(row)
        with pytest.raises(OutOfScopeError):
            s.flush()
        s.rollback()
    assert seen(ROOT) == [invite_id]


# --- create ---------------------------------------------------------------------------------


def test_create_returns_the_link_once_and_stores_only_the_hash(api: Api) -> None:
    response = api.invite(NEW, "PER-03")
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    token = body["invite_token"]
    assert len(token) >= 43  # 256 random bits, URL-safe
    assert body["invite_link"] == f"{DEPLOYMENT}/invite#{token}"  # fragment: never sent to servers
    assert body["email"] == NEW
    assert body["status"] == "pending"
    assert body["grants"] == [{"persona": "PER-03", "site_node_id": None}]
    assert body["sod_warning"] is False
    sent, expires = (datetime.fromisoformat(body[k]) for k in ("last_sent_at", "expires_at"))
    assert expires - sent == timedelta(hours=168)
    assert abs(utcnow() - _aware(sent)) < MINUTE
    assert datetime.fromisoformat(body["resend_available_at"]) - sent == timedelta(seconds=60)
    (row,) = api.rows(NEW)
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.inviter_id == api.users[PAM]
    stored = json.dumps({c.name: str(getattr(row, c.name)) for c in row.__table__.columns})
    assert token not in stored
    # The account exists without credentials; no membership yet.
    account = api.account(NEW)
    assert account is not None
    assert account.password_hash is None
    assert account.is_active is True
    assert api.personas(NEW) == set()
    (entry,) = api.audit()
    assert entry["action"] == "create"
    assert entry["invite_id"] == body["id"]
    assert entry["user_id"] == str(account.id)
    assert entry["sod_warning"] is False
    audited = json.dumps(api.audit())
    assert token not in audited
    assert row.token_hash not in audited
    assert NEW not in audited


def test_who_may_invite(api: Api) -> None:
    assert api.invite(EXT, by=IVAN).status_code == 403  # a member without PER-09
    assert api.invite(EXT, by=PETE).status_code == 404  # PER-09 of another project
    assert api.invite(EXT, by=EXT).status_code == 404
    assert api.invite(EXT, by=PAM, project=uuid.uuid4()).status_code == 404
    assert api.rows() == []
    assert api.invite(EXT, by=ROOT_ADMIN).status_code == 201
    assert api.invite(WORKER, by=PAM).status_code == 201
    assert [i["email"] for i in api.listed()] == [EXT, WORKER]
    for reader, status in ((IVAN, 403), (PETE, 404), (EXT, 404)):
        got = api.client.get(api.url(api.p1), headers=api.headers(reader))
        assert got.status_code == status


def test_bodies_are_validated(api: Api) -> None:
    headers = api.headers(PAM)
    url = api.url(api.p1)
    grants = [{"persona": "PER-03"}]
    bad_bodies = [
        {"email": "not-an-address", "grants": grants},
        {"email": EXT, "grants": []},
        {"email": EXT, "grants": [{"persona": "PER-07"}]},  # a site persona needs its site
        {"email": EXT, "grants": [{"persona": "PER-99"}]},
        {"email": EXT, "grants": [*grants, *grants]},
        {"email": EXT, "grants": grants, "tenant_id": str(T1)},
        {"email": EXT, "grants": grants, "expires_at": "2099-01-01T00:00:00Z"},
    ]
    assert [api.client.post(url, json=b, headers=headers).status_code for b in bad_bodies] == [
        422
    ] * len(bad_bodies)
    for path in (PREVIEW, ACCEPT):
        bad_tokens: list[dict[str, str]] = [
            {},
            {"invite_token": "short"},
            {"invite_token": "x" * 513},
            {"token": "x" * 40},
        ]
        for bad in bad_tokens:
            assert api.client.post(path, json=bad, headers=headers).status_code == 422
    assert api.rows() == []


def test_per09_with_hold_authority_needs_confirmation(api: Api) -> None:
    refused = api.invite(NEW, "PER-09", "PER-02")
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "sod_confirmation_required"
    assert refused.json()["detail"]["sod_warning"] is True
    assert api.rows() == []
    assert api.account(NEW) is None  # nothing was created
    confirmed = api.invite(NEW, "PER-09", "PER-02", confirm_sod=True)
    assert confirmed.status_code == 201
    assert confirmed.json()["sod_warning"] is True
    assert api.audit()[-1]["sod_warning"] is True
    # The grants the account already holds on the project count too: IVAN is PER-02 there.
    assert api.invite(IVAN, "PER-09").status_code == 409
    assert api.invite(IVAN, "PER-09", confirm_sod=True).json()["sod_warning"] is True


# --- preview and accept (signed in) ---------------------------------------------------------


def test_a_signed_in_user_joins_with_one_click(api: Api) -> None:
    token = api.invited(EXT, "PER-03")["invite_token"]
    preview = api.preview(EXT, token)
    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "project_id": str(api.p1),
        "project_code": "P1",
        "project_name": "Synthetic one",
        "inviter_name": "Pam",
    }
    assert api.personas(EXT) == set()  # a preview grants nothing
    accepted = api.accept(EXT, token)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == {"joined_project_id": str(api.p1)}
    assert api.personas(EXT) == {"PER-03"}
    (row,) = api.rows(EXT)
    assert row.status == "accepted"
    assert row.accepted_by == api.users[EXT]
    assert row.accepted_at is not None
    assert api.listed() == []  # accepted invites are not listed
    assert api.actions() == ["create", "accept"]
    assert api.audit()[-1]["user_id"] == str(api.users[EXT])
    # The project now opens for the new member.
    mine = api.client.get("/api/v1/me/projects", headers=api.headers(EXT))
    assert [p["code"] for p in mine.json()] == ["P1"]


def test_a_used_token_is_gone(api: Api) -> None:
    token = api.invited(EXT)["invite_token"]
    assert api.accept(EXT, token).status_code == 200
    for again in (api.accept(EXT, token), api.preview(EXT, token)):
        assert again.status_code == 410
        assert again.json() == {"detail": "This invite is no longer valid", "code": "INVITE_GONE"}
    assert api.actions() == ["create", "accept", "refuse:used", "refuse:used"]
    assert api.personas(EXT) == {"PER-03"}


def test_accepting_adds_to_the_grants_already_held(api: Api) -> None:
    token = api.invited(IVAN, "PER-03")["invite_token"]
    assert api.accept(IVAN, token).status_code == 200
    assert api.personas(IVAN) == {"PER-02", "PER-03"}


def test_the_invite_is_bound_to_the_invited_e_mail(api: Api) -> None:
    token = api.invited(EXT)["invite_token"]
    for refused in (api.preview(PETE, token), api.accept(PETE, token)):
        assert refused.status_code == 403
        assert refused.json()["code"] == "INVITE_WRONG_USER"
        assert "P1" not in refused.text
        assert "Synthetic" not in refused.text
    assert api.personas(PETE) == set()
    assert api.personas(EXT) == set()
    refusals = [e for e in api.audit() if e["action"] == "refuse"]
    assert [(e["stage"], e["reason"], e["user_id"]) for e in refusals] == [
        ("preview", "wrong_user", str(api.users[PETE])),
        ("accept", "wrong_user", str(api.users[PETE])),
    ]
    # A stranger learns nothing about its state either: expired is still 403 for them.
    api.expire(str(api.rows(EXT)[0].id))
    assert api.accept(PETE, token).status_code == 403


def test_the_invited_user_still_accepts_after_a_wrong_user_tried(api: Api) -> None:
    token = api.invited(EXT)["invite_token"]
    assert api.accept(PETE, token).status_code == 403
    assert api.accept(EXT, token).status_code == 200


def test_an_expired_token_is_gone_and_listed_as_expired(api: Api) -> None:
    invite = api.invited(EXT)
    api.expire(invite["id"])
    assert [i["status"] for i in api.listed()] == ["expired"]
    token = invite["invite_token"]
    for refused in (api.preview(EXT, token), api.accept(EXT, token)):
        assert refused.status_code == 410
        assert refused.json()["code"] == "INVITE_GONE"
    assert api.personas(EXT) == set()
    assert api.actions() == ["create", "refuse:expired", "refuse:expired"]
    assert api.rows(EXT)[0].status == "expired"  # stored lazily
    assert [i["status"] for i in api.listed()] == ["expired"]


def test_a_revoked_token_is_gone(api: Api) -> None:
    invite = api.invited(EXT)
    assert api.revoke(invite["id"], by=IVAN).status_code == 403
    assert api.revoke(invite["id"], by=PETE).status_code == 404
    assert api.revoke(invite["id"], by=PETE, project=api.p2).status_code == 404  # not P2's invite
    assert api.revoke(str(uuid.uuid4())).status_code == 404
    assert api.revoke(invite["id"]).status_code == 204
    assert api.listed() == []
    refused = api.accept(EXT, invite["invite_token"])
    assert refused.status_code == 410
    assert refused.json() == {"detail": "This invite is no longer valid", "code": "INVITE_GONE"}
    assert api.personas(EXT) == set()
    again = api.revoke(invite["id"])
    assert again.status_code == 409
    assert again.json()["code"] == "INVITE_NOT_RESENDABLE"
    assert api.actions() == ["create", "revoke", "refuse:revoked"]


def test_an_unknown_token_is_gone_and_audited(api: Api) -> None:
    refused = api.accept(EXT, "x" * 43)
    assert refused.status_code == 410
    assert refused.json()["code"] == "INVITE_GONE"
    assert api.audit(chain="project-invites") == [
        {
            "action": "refuse",
            "stage": "accept",
            "reason": "unmatched_token",
            "user_id": str(api.users[EXT]),
        }
    ]


def test_an_invite_to_a_deleted_project_is_gone(api: Api) -> None:
    with api.db.unit_of_work(ROOT) as s:
        gone = projects.create_project(s, ProjectCreate(code="GONE", name="Synthetic gone")).id
    token = api.invited(EXT, by=ROOT_ADMIN, project=gone)["invite_token"]
    with api.db.unit_of_work(ROOT) as s:
        projects.delete_project(s, gone)
    assert api.preview(EXT, token).status_code == 410
    assert api.accept(EXT, token).status_code == 410
    assert api.personas(EXT, gone) == set()
    assert api.actions(gone) == ["create", "refuse:project_deleted", "refuse:project_deleted"]


def test_a_re_invite_revokes_the_earlier_token(api: Api) -> None:
    first = api.invited(EXT, "PER-03")
    api.age(EXT)
    second = api.invited(EXT, "PER-04")
    assert second["invite_token"] != first["invite_token"]
    assert [i["id"] for i in api.listed()] == [second["id"]]
    assert api.accept(EXT, first["invite_token"]).status_code == 410
    assert api.accept(EXT, second["invite_token"]).status_code == 200
    assert api.personas(EXT) == {"PER-04"}
    assert api.actions() == ["create", "revoke:reinvited", "create", "refuse:revoked", "accept"]


# --- the list -------------------------------------------------------------------------------


def test_the_list_shows_open_invites_only(api: Api) -> None:
    accepted = api.invited(IVAN)
    revoked = api.invited(WORKER)
    expired = api.invited(NEW)
    api.invited(EXT, "PER-04")  # stays pending
    assert api.accept(IVAN, accepted["invite_token"]).status_code == 200
    assert api.revoke(revoked["id"]).status_code == 204
    api.expire(expired["id"])
    listed = api.listed()
    assert [(i["email"], i["status"]) for i in listed] == [(EXT, "pending"), (NEW, "expired")]
    for item in listed:
        assert set(item) == {
            "id",
            "email",
            "grants",
            "status",
            "expires_at",
            "last_sent_at",
            "resend_available_at",
        }  # never a token
        assert datetime.fromisoformat(item["resend_available_at"]) - datetime.fromisoformat(
            item["last_sent_at"]
        ) == timedelta(seconds=60)
    assert listed[0]["grants"] == [{"persona": "PER-04", "site_node_id": None}]
    assert listed == api.listed(by=ROOT_ADMIN)
    api.age(EXT)
    assert api.listed()[0]["resend_available_at"] is None  # a re-send is allowed now
    assert api.listed(by=PETE, project=api.p2) == []


# --- re-send --------------------------------------------------------------------------------


def test_a_re_send_rotates_the_token(api: Api) -> None:
    first = api.invited(EXT, "PER-03")
    api.age(EXT, timedelta(hours=1))
    response = api.resend(first["id"])
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    second = response.json()
    assert second["id"] == first["id"]
    assert second["email"] == EXT
    assert second["grants"] == first["grants"]
    assert second["status"] == "pending"
    assert second["invite_token"] != first["invite_token"]
    assert second["invite_link"] == f"{DEPLOYMENT}/invite#{second['invite_token']}"
    assert "sod_warning" not in second
    sent, expires = (datetime.fromisoformat(second[k]) for k in ("last_sent_at", "expires_at"))
    assert expires - sent == timedelta(hours=168)
    assert abs(utcnow() - _aware(sent)) < MINUTE
    old = api.accept(EXT, first["invite_token"])
    assert old.status_code == 410
    assert old.json() == {"detail": "This invite is no longer valid", "code": "INVITE_GONE"}
    assert api.accept(EXT, second["invite_token"]).status_code == 200
    assert api.personas(EXT) == {"PER-03"}
    assert api.actions() == ["create", "resend", "accept"]
    # The old token matches no invite any more: its refusal is on the tenant chain.
    assert [e["reason"] for e in api.audit(chain="project-invites")] == ["unmatched_token"]
    resent = api.audit()[1]
    assert resent["invite_id"] == first["id"]
    # Actor, invite, whether the e-mail was queued (T-0059); no token.
    assert set(resent) == {"action", "invite_id", "expires_at", "email_queued"}


def test_an_expired_invite_is_re_sent_as_pending(api: Api) -> None:
    invite = api.invited(EXT)
    api.expire(invite["id"])
    api.age(EXT)
    assert api.accept(EXT, invite["invite_token"]).status_code == 410  # stored as expired now
    response = api.resend(invite["id"], by=ROOT_ADMIN)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert _aware(datetime.fromisoformat(body["expires_at"])) > utcnow() + timedelta(hours=167)
    assert [i["status"] for i in api.listed()] == ["pending"]
    assert api.accept(EXT, body["invite_token"]).status_code == 200


def test_accepted_and_revoked_invites_are_not_re_sent(api: Api) -> None:
    accepted = api.invited(EXT)
    revoked = api.invited(WORKER)
    assert api.accept(EXT, accepted["invite_token"]).status_code == 200
    assert api.revoke(revoked["id"]).status_code == 204
    api.age(EXT)
    api.age(WORKER)
    for invite in (accepted, revoked):
        response = api.resend(invite["id"])
        assert response.status_code == 409
        assert response.json()["code"] == "INVITE_NOT_RESENDABLE"
    assert "resend" not in api.actions()


def test_who_may_re_send(api: Api) -> None:
    invite = api.invited(EXT)
    api.age(EXT)
    assert api.resend(invite["id"], by=PETE).status_code == 404  # PER-09 of another project
    assert api.resend(invite["id"], by=PETE, project=api.p2).status_code == 404
    assert api.resend(invite["id"], by=IVAN).status_code == 403
    assert api.resend(invite["id"], by=EXT).status_code == 404
    assert api.resend(str(uuid.uuid4())).status_code == 404
    assert api.accept(EXT, invite["invite_token"]).status_code == 200  # untouched


def test_sends_are_capped_at_one_per_minute(api: Api) -> None:
    invite = api.invited(EXT)
    limited = api.resend(invite["id"])
    assert limited.status_code == 429
    assert 0 < int(limited.headers["retry-after"]) <= 60
    # Revoking and re-creating does not reset it, and the refused create changes nothing.
    assert api.revoke(invite["id"]).status_code == 204
    again = api.invite(EXT)
    assert again.status_code == 429
    assert 0 < int(again.headers["retry-after"]) <= 60
    assert len(api.rows(EXT)) == 1
    # Another address, and the same address on another project, have their own caps.
    assert api.invite(WORKER).status_code == 201
    assert api.invite(EXT, by=PETE, project=api.p2).status_code == 201
    api.age(EXT)
    assert api.invite(EXT).status_code == 201


def test_sends_are_capped_at_five_per_day_across_create_and_re_send(api: Api) -> None:
    invite = api.invited(EXT)  # 1
    for _ in range(2):  # 2, 3
        api.age(EXT)
        assert api.resend(invite["id"]).status_code == 200
    api.age(EXT)
    assert api.revoke(invite["id"]).status_code == 204
    second = api.invited(EXT)  # 4: revoke-and-recreate counts on
    api.age(EXT)
    live = api.resend(second["id"])  # 5
    assert live.status_code == 200
    api.age(EXT)
    for limited in (api.resend(second["id"]), api.invite(EXT)):
        assert limited.status_code == 429
        assert 23 * 3600 < int(limited.headers["retry-after"]) <= 24 * 3600
    # Nothing was created or revoked by the refused create: the live invite still works.
    assert [i["id"] for i in api.listed()] == [second["id"]]
    (listed,) = api.listed()
    assert _aware(datetime.fromisoformat(listed["resend_available_at"])) > utcnow() + timedelta(
        hours=23
    )
    assert api.accept(EXT, live.json()["invite_token"]).status_code == 200
    # A day later the cap has moved on.
    api.age(EXT, timedelta(hours=24))
    assert api.invite(EXT).status_code == 201


# --- carry-through: password login -----------------------------------------------------------


def test_password_login_accepts_the_invite(api: Api) -> None:
    token = api.invited(EXT, "PER-03")["invite_token"]
    plain = api.login(EXT)
    assert "joined_project_id" not in plain.json()
    assert "invite_error" not in plain.json()
    wrong_password = api.login(EXT, token, password="not the password")
    assert wrong_password.status_code == 401
    assert api.personas(EXT) == set()
    response = api.login(EXT, token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["joined_project_id"] == str(api.p1)
    assert "invite_error" not in body
    assert body["access_token"]
    assert api.personas(EXT) == {"PER-03"}
    assert api.actions() == ["create", "accept"]
    # The new session opens the project at once.
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    mine = api.client.get("/api/v1/me/projects", headers=headers)
    assert [p["code"] for p in mine.json()] == ["P1"]


def test_a_bad_invite_never_blocks_the_sign_in(api: Api) -> None:
    token = api.invited(EXT)["invite_token"]
    stranger = api.login(PETE, token)
    assert stranger.status_code == 200
    assert stranger.json()["invite_error"] == "INVITE_WRONG_USER"
    assert "joined_project_id" not in stranger.json()
    assert api.personas(PETE) == set()
    unknown = api.login(EXT, "y" * 43)
    assert unknown.status_code == 200
    assert unknown.json()["invite_error"] == "INVITE_GONE"
    assert api.login(EXT, token).json()["joined_project_id"] == str(api.p1)
    used = api.login(EXT, token)
    assert used.status_code == 200
    assert used.json()["invite_error"] == "INVITE_GONE"
    assert api.login(EXT, "z" * 513).status_code == 422
    assert api.actions() == ["create", "refuse:wrong_user", "accept", "refuse:used"]


def test_without_a_hook_the_invite_token_is_ignored(api: Api) -> None:
    token = api.invited(EXT)["invite_token"]
    post_auth.unregister_post_authentication_hook(invites.HOOK)
    try:
        assert post_auth.invite_reference(token) is None
        response = api.login(EXT, token)
    finally:
        post_auth.register_post_authentication_hook(invites.HOOK)
    assert response.status_code == 200
    assert "joined_project_id" not in response.json()
    assert "invite_error" not in response.json()
    assert api.personas(EXT) == set()
    assert api.accept(EXT, token).status_code == 200  # still pending


def test_a_failing_hook_never_blocks_the_sign_in(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    token = api.invited(EXT)["invite_token"]

    class Broken(invites.InviteHook):
        def after_authentication(self, *_args: Any) -> post_auth.PostAuthOutcome:
            raise RuntimeError("synthetic failure")

    post_auth.register_post_authentication_hook(Broken())
    try:
        with caplog.at_level(logging.DEBUG):
            response = api.login(EXT, token)
    finally:
        post_auth.register_post_authentication_hook(invites.HOOK)
    assert response.status_code == 200
    assert response.json()["invite_error"] == "INVITE_UNAVAILABLE"
    assert "post-authentication hook failed" in caplog.text
    assert token not in caplog.text
    assert invites.token_digest(token) not in caplog.text
    assert api.accept(EXT, token).status_code == 200


# --- carry-through: magic link (PER-14 invites only) -----------------------------------------


def test_a_field_worker_invitee_joins_through_a_magic_link(api: Api) -> None:
    token = api.invited(WORKER, "PER-14")["invite_token"]
    response = api.client.post(MAGIC, json={"email": WORKER, "invite_token": token})
    assert response.status_code == 202
    (link,) = api.sender.sent
    assert link.email == WORKER
    assert token not in link.token  # the invite token is never in the magic-link URL
    stored = b" ".join(api.redis.get(key) or b"" for key in api.redis.keys("*magic*"))
    assert token.encode() not in stored  # only its reference is kept with the link record
    assert invites.token_digest(token).encode() in stored
    redeemed = api.client.post(REDEEM, json={"token": link.token})
    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json()["joined_project_id"] == str(api.p1)
    assert api.personas(WORKER) == {"PER-14"}
    assert api.actions() == ["create", "accept"]
    # A member now: the next link needs no invite.
    assert api.client.post(MAGIC, json={"email": WORKER}).status_code == 202
    assert len(api.sender.sent) == 2


def test_a_non_field_worker_invite_gets_the_neutral_answer_and_no_link(api: Api) -> None:
    token = api.invited(EXT, "PER-03", "PER-04")["invite_token"]
    invited = api.client.post(MAGIC, json={"email": EXT, "invite_token": token})
    unknown = api.client.post(MAGIC, json={"email": "nobody@example.test", "invite_token": token})

    def shape(response: httpx.Response) -> tuple[int, bytes, dict[str, str]]:
        headers = {k: v for k, v in response.headers.items() if k.lower() != "x-request-id"}
        return response.status_code, response.content, headers

    assert shape(invited) == shape(unknown)
    assert invited.status_code == 202
    assert api.sender.sent == []
    assert api.personas(EXT) == set()
    assert api.accept(EXT, token).status_code == 200  # joins through password sign-in instead


def test_only_a_valid_pending_per14_invite_makes_an_account_eligible(api: Api) -> None:
    candidate = MagicLinkCandidate(tenant_id=T1, user_id=api.users[WORKER], email=WORKER)
    own = TenancyContext(tenant_id=T1, user_id=api.users[WORKER])

    def eligible() -> bool:
        with api.db.unit_of_work(own) as s:
            return magic_link_eligible(s, candidate)

    assert eligible() is False
    invite = api.invited(WORKER, "PER-14", "PER-03")
    assert eligible() is True
    api.expire(invite["id"])
    assert eligible() is False
    api.age(WORKER)
    renewed = api.resend(invite["id"]).json()
    assert eligible() is True
    assert api.revoke(invite["id"]).status_code == 204
    assert eligible() is False
    assert api.client.post(MAGIC, json={"email": WORKER}).status_code == 202
    assert api.sender.sent == []
    assert api.accept(WORKER, renewed["invite_token"]).status_code == 410


def test_redeem_re_checks_the_invite(api: Api) -> None:
    invite = api.invited(WORKER, "PER-14")
    body = {"email": WORKER, "invite_token": invite["invite_token"]}
    assert api.client.post(MAGIC, json=body).status_code == 202
    (link,) = api.sender.sent
    assert api.revoke(invite["id"]).status_code == 204
    assert api.client.post(REDEEM, json={"token": link.token}).status_code == 401
    assert api.personas(WORKER) == set()


# --- carry-through: Entra ID SSO --------------------------------------------------------------


def test_a_brand_new_company_member_joins_through_sso(api: Api) -> None:
    token = api.invited(STAFF, "PER-03")["invite_token"]
    account = api.account(STAFF)
    assert account is not None
    assert account.password_hash is None  # brand new: no credentials
    assert account.entra_oid is None
    location, cookie = api.sso_start(invite_token=token)
    assert token not in location
    stored = b" ".join(api.redis.get(key) or b"" for key in api.redis.keys("*sso:entra:state:*"))
    assert token.encode() not in stored  # the state keeps the reference, never the token
    assert invites.token_digest(token).encode() in stored
    landed = api.sso_callback(location, cookie)
    assert landed == {"status": "signed_in", "joined_project_id": str(api.p1)}
    assert api.personas(STAFF) == {"PER-03"}
    linked = api.account(STAFF)
    assert linked is not None
    assert linked.entra_oid is not None
    assert api.actions() == ["create", "accept"]


def test_sso_reports_a_refused_invite_and_still_signs_in(api: Api) -> None:
    seed_user(api.db, T1, STAFF, password=None)
    token = api.invited(EXT)["invite_token"]  # someone else's invite
    location, cookie = api.sso_start(invite_token=token)
    assert api.sso_callback(location, cookie) == {
        "status": "signed_in",
        "invite_error": "INVITE_WRONG_USER",
    }
    assert api.personas(STAFF) == set()
    assert api.personas(EXT) == set()
    # Without an invite the landing is unchanged.
    location, cookie = api.sso_start()
    assert api.sso_callback(location, cookie) == {"status": "signed_in"}


def test_the_invite_token_is_never_taken_from_a_url(api: Api) -> None:
    token = api.invited(STAFF)["invite_token"]
    response = api.client.get(SSO_START, params={"invite_token": token}, follow_redirects=False)
    assert response.status_code == 302
    landed = api.sso_callback(response.headers["location"], response.cookies[PREAUTH_COOKIE])
    assert landed == {"status": "signed_in"}  # the query parameter is ignored
    assert api.personas(STAFF) == set()
    spec = api.client.app.openapi()  # type: ignore[attr-defined]
    for path, item in spec["paths"].items():
        assert "invite_token" not in path
        assert "{token}" not in path
        for operation in item.values():
            for parameter in operation.get("parameters", []):
                assert "token" not in parameter["name"], (path, parameter)


def test_the_sso_start_form_is_validated(api: Api) -> None:
    def start(content: bytes | str, headers: dict[str, str] = FORM) -> int:
        return api.client.post(
            SSO_START, content=content, headers=headers, follow_redirects=False
        ).status_code

    assert start("") == 303
    assert start('{"invite_token": "x"}', {"Content-Type": "application/json"}) == 415
    assert start("invite_token=" + "x" * 513) == 422
    assert start("invite_token=a&invite_token=b") == 422
    assert start("device_id=not-a-uuid") == 422
    assert start("app_challenge=short&device_id=" + str(uuid.uuid4())) == 422
    assert start("app_challenge=" + entra.pkce_challenge(VERIFIER)) == 422  # needs device_id
    assert start("nonsense") == 422
    assert start("invite_token=" + "é" * 4) == 422
    assert start("x=" + "y" * 3000) == 413


def test_the_native_sso_handoff_accepts_the_invite_at_the_exchange(api: Api) -> None:
    token = api.invited(STAFF, "PER-14")["invite_token"]
    device = uuid.uuid4()
    challenge = entra.pkce_challenge(VERIFIER)
    location, cookie = api.sso_start(
        invite_token=token, device_id=str(device), app_challenge=challenge
    )
    landed = api.sso_callback(location, cookie)
    assert set(landed) == {"code"}
    assert api.personas(STAFF) == set()  # not before the app has redeemed its code
    body = {"code": landed["code"], "verifier": VERIFIER, "device_id": str(device)}
    response = api.client.post(SSO_EXCHANGE, json=body, headers={"Origin": NATIVE_ORIGIN})
    assert response.status_code == 200, response.text
    assert response.json()["joined_project_id"] == str(api.p1)
    assert api.personas(STAFF) == {"PER-14"}


# --- single use under concurrency -------------------------------------------------------------


def _together(
    monkeypatch: pytest.MonkeyPatch, *calls: Callable[[], httpx.Response]
) -> list[httpx.Response]:
    """Run the calls in parallel, each on its own thread. Every request is held at its first
    state change of an invite (``invites._transition``) until all of them have read the invite
    and reached theirs, so the statements really race in the database."""
    gate = threading.Barrier(len(calls))
    arrivals = iter(range(len(calls)))
    real = invites._transition

    def gated(*args: Any, **kwargs: Any) -> bool:
        if next(arrivals, None) is not None:  # the first state change of each request
            gate.wait(timeout=20)
        return real(*args, **kwargs)

    monkeypatch.setattr(invites, "_transition", gated)
    barrier = threading.Barrier(len(calls))
    results: dict[int, httpx.Response] = {}

    def run(index: int, call: Callable[[], httpx.Response]) -> None:
        barrier.wait(timeout=10)
        results[index] = call()

    threads = [threading.Thread(target=run, args=item) for item in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(results) == len(calls)
    return [results[i] for i in range(len(calls))]


class _Pause:
    """Holds the first state change of an invite (``invites._transition``) until released: a
    request that has read the invite but not yet written, the window every race needs."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.reached, self.release = threading.Event(), threading.Event()
        self._armed = True
        real = invites._transition

        def held(*args: Any, **kwargs: Any) -> bool:
            if self._armed:
                self._armed = False
                self.reached.set()
                assert self.release.wait(timeout=20)
            return real(*args, **kwargs)

        monkeypatch.setattr(invites, "_transition", held)

    def race(
        self, first: Callable[[], httpx.Response], second: Callable[[], httpx.Response]
    ) -> tuple[httpx.Response, httpx.Response]:
        """``first`` reads the invite and stops; ``second`` runs to completion; ``first``
        then goes on with what it read."""
        box: list[httpx.Response] = []
        thread = threading.Thread(target=lambda: box.append(first()))
        thread.start()
        assert self.reached.wait(timeout=20)
        try:
            other = second()
        finally:
            self.release.set()
            thread.join(timeout=30)
        return box[0], other


def test_two_parallel_accepts_one_joins_one_conflicts(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = api.invited(EXT, "PER-03")["invite_token"]
    api.headers(EXT)  # signed in before the race
    responses = _together(monkeypatch, *[lambda: api.accept(EXT, token)] * 2)
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    loser = next(r for r in responses if r.status_code == 409)
    assert loser.json()["code"] == "INVITE_CONFLICT"
    assert api.personas(EXT) == {"PER-03"}
    assert api.rows(EXT)[0].status == "accepted"
    assert api.actions() == ["create", "accept", "refuse:race_used"]


def test_an_accept_that_read_the_invite_before_another_accept_conflicts(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = api.invited(EXT)["invite_token"]
    api.headers(EXT)
    first, second = _Pause(monkeypatch).race(
        lambda: api.accept(EXT, token), lambda: api.accept(EXT, token)
    )
    assert (first.status_code, second.status_code) == (409, 200)
    assert first.json()["code"] == "INVITE_CONFLICT"
    assert api.personas(EXT) == {"PER-03"}


def test_a_re_send_racing_an_accept_has_exactly_one_winner(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    invite = api.invited(EXT, "PER-03")
    api.age(EXT)
    api.headers(EXT), api.headers(PAM)
    accepted, resent = _together(
        monkeypatch,
        lambda: api.accept(EXT, invite["invite_token"]),
        lambda: api.resend(invite["id"]),
    )
    monkeypatch.undo()
    outcome = (accepted.status_code, resent.status_code)
    assert outcome in ((200, 409), (410, 200)), (accepted.text, resent.text)
    (row,) = api.rows(EXT)
    if outcome == (200, 409):  # the accept won: nothing was rotated
        assert resent.json()["code"] == "INVITE_NOT_RESENDABLE"
        assert row.status == "accepted"
        assert api.personas(EXT) == {"PER-03"}
    else:  # the re-send won: the old token never works again, the new one does
        assert row.status == "pending"
        assert api.personas(EXT) == set()
        assert api.accept(EXT, invite["invite_token"]).status_code == 410
        assert api.accept(EXT, resent.json()["invite_token"]).status_code == 200


def test_the_old_token_fails_when_the_re_send_commits_first(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The accept has already read the invite as pending when the re-send rotates it."""
    invite = api.invited(EXT, "PER-03")
    api.age(EXT)
    api.headers(EXT), api.headers(PAM)
    accepted, resent = _Pause(monkeypatch).race(
        lambda: api.accept(EXT, invite["invite_token"]), lambda: api.resend(invite["id"])
    )
    assert (accepted.status_code, resent.status_code) == (410, 200)
    assert accepted.json()["code"] == "INVITE_GONE"
    assert api.personas(EXT) == set()
    assert api.rows(EXT)[0].status == "pending"
    assert api.actions() == ["create", "resend", "refuse:race_rotated"]
    assert api.accept(EXT, invite["invite_token"]).status_code == 410  # after the rotation
    assert api.accept(EXT, resent.json()["invite_token"]).status_code == 200


def test_the_re_send_fails_when_the_accept_commits_first(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-send has already read the invite as pending when the accept uses it."""
    invite = api.invited(EXT, "PER-03")
    api.age(EXT)
    api.headers(EXT), api.headers(PAM)
    resent, accepted = _Pause(monkeypatch).race(
        lambda: api.resend(invite["id"]), lambda: api.accept(EXT, invite["invite_token"])
    )
    assert (accepted.status_code, resent.status_code) == (200, 409)
    assert resent.json()["code"] == "INVITE_NOT_RESENDABLE"
    (row,) = api.rows(EXT)
    assert row.status == "accepted"
    assert row.token_hash == invites.token_digest(invite["invite_token"])  # never rotated
    assert api.personas(EXT) == {"PER-03"}


def test_two_re_sends_at_once_send_once(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    invite = api.invited(EXT)
    api.age(EXT)
    first, second = _Pause(monkeypatch).race(
        lambda: api.resend(invite["id"]), lambda: api.resend(invite["id"])
    )
    assert (first.status_code, second.status_code) == (429, 200)
    assert 0 < int(first.headers["retry-after"]) <= 60
    assert api.accept(EXT, second.json()["invite_token"]).status_code == 200


def test_a_revoke_or_re_invite_that_lost_a_race_conflicts(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    invite = api.invited(EXT)
    api.age(EXT)
    api.headers(EXT)
    revoked, accepted = _Pause(monkeypatch).race(
        lambda: api.revoke(invite["id"]), lambda: api.accept(EXT, invite["invite_token"])
    )
    assert (revoked.status_code, accepted.status_code) == (409, 200)
    assert revoked.json()["code"] == "INVITE_CONFLICT"
    other = api.invited(WORKER)
    api.age(WORKER)
    api.headers(WORKER)
    reinvited, accepted = _Pause(monkeypatch).race(
        lambda: api.invite(WORKER), lambda: api.accept(WORKER, other["invite_token"])
    )
    assert (reinvited.status_code, accepted.status_code) == (409, 200)
    assert reinvited.json()["code"] == "INVITE_CONFLICT"
    assert [r.status for r in api.rows(WORKER)] == ["accepted"]  # nothing created


def test_a_login_carrying_the_invite_that_lost_a_race_still_signs_in(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = api.invited(EXT)["invite_token"]
    api.headers(EXT)
    signed_in, accepted = _Pause(monkeypatch).race(
        lambda: api.login(EXT, token), lambda: api.accept(EXT, token)
    )
    assert (signed_in.status_code, accepted.status_code) == (200, 200)
    assert signed_in.json()["invite_error"] == "INVITE_CONFLICT"
    assert api.personas(EXT) == {"PER-03"}


def test_a_colliding_write_is_a_conflict_and_leaves_nothing_behind(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unique indexes are the last line against a concurrent duplicate: the request that
    hits one answers 409 and its unit of work (the new account included) is rolled back."""
    first = api.invited(EXT)
    monkeypatch.setattr(invites.secrets, "token_urlsafe", lambda _n: first["invite_token"])
    collided = api.invite(NEW)
    assert collided.status_code == 409
    assert collided.json()["code"] == "INVITE_CONFLICT"
    assert api.account(NEW) is None
    other = api.rows(EXT)[0]
    monkeypatch.undo()
    second = api.invited(WORKER)
    api.age(WORKER)
    monkeypatch.setattr(invites.secrets, "token_urlsafe", lambda _n: first["invite_token"])
    resent = api.resend(second["id"])
    assert resent.status_code == 409
    assert resent.json()["code"] == "INVITE_CONFLICT"
    assert api.rows(EXT)[0].token_hash == other.token_hash
    assert api.accept(WORKER, second["invite_token"]).status_code == 200  # not rotated

    def raced(*_args: Any, **_kwargs: Any) -> None:
        raise admin.DuplicateUserError("synthetic race")

    monkeypatch.setattr(invites.admin, "create_user", raced)
    assert api.invite(NEW).json()["code"] == "INVITE_CONFLICT"
    assert api.rows(NEW) == []


def test_the_invited_address_is_normalised(api: Api) -> None:
    invite = api.invited("  Ext@Example.TEST ")
    assert invite["email"] == EXT
    assert len(api.rows(EXT)) == 1
    assert api.accept(EXT, invite["invite_token"]).status_code == 200


# --- the token never reaches a URL or a log ---------------------------------------------------


def test_no_log_record_contains_an_invite_token(api: Api, caplog: pytest.LogCaptureFixture) -> None:
    api.headers(PAM), api.headers(EXT), api.headers(PETE)
    formatter = JsonFormatter()
    with caplog.at_level(logging.DEBUG):
        first = api.invited(EXT)  # the response carries a token
        api.age(EXT)
        second = api.resend(first["id"]).json()  # so does the re-send's
        tokens = [first["invite_token"], second["invite_token"]]
        assert api.preview(EXT, tokens[1]).status_code == 200
        assert api.preview(PETE, tokens[1]).status_code == 403
        assert api.accept(EXT, tokens[0]).status_code == 410
        assert api.login(PETE, tokens[1]).json()["invite_error"] == "INVITE_WRONG_USER"
        magic = {"email": EXT, "invite_token": tokens[1]}
        assert api.client.post(MAGIC, json=magic).status_code == 202
        api.sso_start(invite_token=tokens[1])
        assert api.login(EXT, tokens[1]).json()["joined_project_id"] == str(api.p1)
        assert api.accept(EXT, tokens[1]).status_code == 410
    access = [r for r in caplog.records if r.name == "kerp.access"]
    paths = {r.__dict__["path"] for r in access}
    assert {PREVIEW, ACCEPT, LOGIN, MAGIC, SSO_START, api.url(api.p1)} <= paths
    assert api.url(api.p1, first["id"], "resend") in paths
    assert len(caplog.records) > len(access)  # SQL, audit and auth loggers were captured too
    for record in caplog.records:
        rendered = formatter.format(record) + record.getMessage() + repr(record.__dict__)
        for token in tokens:
            assert token not in rendered, record.name


def test_the_log_formatter_scrubs_invite_token_fields() -> None:
    secret = "s3cret-Token_value.123"
    message = 'body {"invite_token": "%s"} rejected'
    record = logging.LogRecord("kerp.test", logging.INFO, __file__, 1, message, (secret,), None)
    record.__dict__.update(
        invite_token=secret,
        body={"invite_token": secret, "nested": [{"setup_token": secret, "email": "a@b.test"}]},
        note=f"retry with invite_token={secret}&next=1",
    )
    line = JsonFormatter().format(record)
    assert secret not in line
    payload = json.loads(line)
    assert payload["invite_token"] == REDACTED
    assert payload["body"] == {
        "invite_token": REDACTED,
        "nested": [{"setup_token": REDACTED, "email": "a@b.test"}],
    }
    assert payload["note"] == f"retry with invite_token={REDACTED}&next=1"
    assert payload["message"] == f'body {{"invite_token": "{REDACTED}"}} rejected'
    # ``token=...`` itself is a credential pair since T-0077 (reset tokens): redacted too.
    assert scrub_text("invite_tokens=5 and token=abc") == f"invite_tokens=5 and token={REDACTED}"
    assert scrub((1, "setup_token: abc")) == [1, f"setup_token: {REDACTED}"]
    try:
        raise ValueError(f"invite_token='{secret}'")
    except ValueError:
        failed = logging.LogRecord("kerp.test", logging.ERROR, __file__, 1, "failed", (), None)
        failed.exc_info = sys.exc_info()
        assert secret not in JsonFormatter().format(failed)
