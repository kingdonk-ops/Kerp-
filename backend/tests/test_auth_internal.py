"""Internal staff flag (T-0049, diff §4.1 Internal staff, ADR 0009).

``is_internal`` is derived, never input: true when the account is linked to a member account
of the company Entra tenant (``entra_oid``) or its e-mail domain is on
``KERP_INTERNAL_EMAIL_DOMAINS``; recomputed at account creation and at every sign-in. All
data is synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.auth.access import Persona
from app.core.auth.models import ProjectMembership, User
from app.core.auth.service import (
    create_user,
    derive_is_internal,
    email_domain_is_internal,
    is_internal_user,
)
from app.core.config import Settings
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import (
    PASSWORD,
    T1,
    T2,
    CapturingSender,
    auth_app,
    make_settings,
    seed_user,
    token_body,
)
from tests.db_support import database_url

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
COMPANY = "company.example"
STAFF = "staff@company.example"
CONTRACTOR = "contractor@partner.example"
CTX_T1 = TenancyContext(tenant_id=T1)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def _client(tmp_path: Path, **settings: Any) -> TestClient:
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path), **settings))
    return TestClient(app)


def _flag(db: Database, user_id: uuid.UUID) -> bool:
    with db.unit_of_work(CTX_T1) as session:
        return is_internal_user(session, user_id)


def _login(client: TestClient, email: str) -> dict[str, str]:
    return token_body(client.post(LOGIN, json={"email": email, "password": PASSWORD}))


def _me(client: TestClient, email: str) -> dict[str, Any]:
    token = _login(client, email)["access_token"]
    response = client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# --- configuration --------------------------------------------------------------------------


def test_domains_are_comma_separated_trimmed_lower_cased_and_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERP_INTERNAL_EMAIL_DOMAINS", " Company.Example, eu.company.example,,")
    monkeypatch.setenv("KERP_ENVIRONMENT", "test")
    assert Settings().internal_email_domains == ("company.example", "eu.company.example")
    assert Settings(internal_email_domains=["A.example", "a.example"]).internal_email_domains == (
        "a.example",
    )


def test_no_domains_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KERP_INTERNAL_EMAIL_DOMAINS", raising=False)
    assert Settings(environment="test").internal_email_domains == ()
    assert Settings(environment="test", internal_email_domains="").internal_email_domains == ()


@pytest.mark.parametrize(
    "entry", ["staff@company.example", "@company.example", "*.company.example", "company .example"]
)
def test_a_domain_entry_that_is_not_a_bare_domain_is_refused_at_startup(
    entry: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KERP_INTERNAL_EMAIL_DOMAINS", f"ok.example,{entry}")
    with pytest.raises(ValidationError, match="internal_email_domains"):
        Settings(environment="test")


@pytest.mark.parametrize("value", [None, 5, ["ok.example", 5]])
def test_domains_must_be_a_list_of_strings(value: Any) -> None:
    with pytest.raises(ValidationError, match="internal_email_domains"):
        Settings(environment="test", internal_email_domains=value)


# --- the derivation rule --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("staff@company.example", True),
        ("Staff@COMPANY.Example", True),  # case-insensitive
        ("staff@eu.company.example", False),  # a subdomain is another domain
        ("staff@company.example.evil", False),
        ("staff@notcompany.example", False),
        ("company.example@partner.example", False),  # only the domain part counts
    ],
)
def test_domain_rule_is_an_exact_case_insensitive_match(email: str, expected: bool) -> None:
    assert email_domain_is_internal(email, (COMPANY,)) is expected


def test_an_empty_domain_list_makes_nobody_internal_by_domain() -> None:
    assert email_domain_is_internal(STAFF, ()) is False


def test_an_entra_linked_account_is_internal_whatever_its_domain() -> None:
    linked = User(email=CONTRACTOR, entra_oid=str(uuid.uuid4()), entra_tid=str(uuid.uuid4()))
    unlinked = User(email=CONTRACTOR, entra_oid=None)
    assert derive_is_internal(linked, ()) is True
    assert derive_is_internal(unlinked, ()) is False
    assert derive_is_internal(User(email=STAFF, entra_oid=None), (COMPANY,)) is True


# --- account creation -----------------------------------------------------------------------


def test_the_flag_is_computed_at_account_creation(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as session:
        staff = create_user(
            session, tenant_id=T1, email=STAFF, password=None, internal_domains=(COMPANY,)
        ).id
        outsider = create_user(
            session, tenant_id=T1, email=CONTRACTOR, password=None, internal_domains=(COMPANY,)
        ).id
        unlisted = create_user(
            session, tenant_id=T1, email="other@company.example", password=None, internal_domains=()
        ).id
    assert _flag(db, staff) is True
    assert _flag(db, outsider) is False
    assert _flag(db, unlisted) is False


def test_creation_uses_the_deployment_domains_by_default(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.core.auth.service.get_settings",
        lambda: make_settings(internal_email_domains=COMPANY),
    )
    with db.unit_of_work(CTX_T1) as session:
        staff = create_user(session, tenant_id=T1, email=STAFF, password=None).id
    assert _flag(db, staff) is True


def test_entra_oid_is_unique_per_tenant(db: Database) -> None:
    oid = str(uuid.uuid4())
    seed_user(db, T1, STAFF, password=None, entra_oid=oid)
    seed_user(db, T2, STAFF, password=None, entra_oid=oid)  # another tenant: allowed
    seed_user(db, T1, "a@partner.example", password=None)  # unlinked accounts: NULLs coexist
    seed_user(db, T1, "b@partner.example", password=None)
    with pytest.raises(IntegrityError):
        seed_user(db, T1, CONTRACTOR, password=None, entra_oid=oid)


# --- sign-in --------------------------------------------------------------------------------


def test_password_sign_in_recomputes_the_flag_after_the_list_changes(
    tmp_path: Path, db: Database
) -> None:
    staff = seed_user(db, T1, STAFF)  # created before the domain was listed
    assert _flag(db, staff) is False

    with _client(tmp_path, internal_email_domains=COMPANY) as client:
        assert _me(client, STAFF)["is_internal"] is True
    assert _flag(db, staff) is True

    # The domain is removed from the list: it takes effect at the next sign-in.
    with _client(tmp_path) as client:
        assert _me(client, STAFF)["is_internal"] is False
    assert _flag(db, staff) is False


def test_magic_link_sign_in_recomputes_the_flag(tmp_path: Path, db: Database) -> None:
    staff = seed_user(db, T1, STAFF)
    sender = CapturingSender()
    settings = make_settings(database_url=database_url(tmp_path), internal_email_domains=COMPANY)
    app, _runtime = auth_app(settings, magic_links=sender)
    with TestClient(app) as client:
        assert client.post("/api/v1/auth/magic-link", json={"email": STAFF}).status_code == 202
        redeem = {"token": sender.sent[0].token, "pin": "482916"}
        token_body(client.post("/api/v1/auth/magic-link/redeem", json=redeem))
    assert _flag(db, staff) is True


def test_an_entra_linked_account_signs_in_as_internal_regardless_of_domain(
    tmp_path: Path, db: Database
) -> None:
    linked = seed_user(db, T1, CONTRACTOR, entra_oid=str(uuid.uuid4()), entra_tid=str(T1))
    with _client(tmp_path, internal_email_domains=COMPANY) as client:
        assert _me(client, CONTRACTOR)["is_internal"] is True
    assert _flag(db, linked) is True


def test_a_failed_sign_in_does_not_recompute(tmp_path: Path, db: Database) -> None:
    staff = seed_user(db, T1, STAFF)
    with _client(tmp_path, internal_email_domains=COMPANY) as client:
        response = client.post(LOGIN, json={"email": STAFF, "password": "wrong password"})
        assert response.status_code == 401
    assert _flag(db, staff) is False


# --- never from input or token claims -------------------------------------------------------


@pytest.mark.parametrize("field", ["is_internal", "entra_oid", "entra_tid"])
def test_sign_in_requests_refuse_the_flag_and_the_entra_link(tmp_path: Path, field: str) -> None:
    with _client(tmp_path) as client:
        value: Any = True if field == "is_internal" else str(uuid.uuid4())
        body = {"email": STAFF, "password": PASSWORD, field: value}
        assert client.post(LOGIN, json=body).status_code == 422
        magic = {"email": STAFF, field: value}
        assert client.post("/api/v1/auth/magic-link", json=magic).status_code == 422


def test_tokens_carry_no_internal_claim_and_me_reads_the_account(
    tmp_path: Path, db: Database
) -> None:
    staff = seed_user(db, T1, STAFF)
    with _client(tmp_path, internal_email_domains=COMPANY) as client:
        token = _login(client, STAFF)["access_token"]
        claims = jwt.decode(token, options={"verify_signature": False})
        assert not {"is_internal", "internal", "entra_oid", "oid"} & set(claims)
        # The flag shown is the stored one: changing the row (not the token) changes it.
        with db.unit_of_work(CTX_T1) as session:
            session.get_one(User, staff).is_internal = False
        response = client.get(ME, headers={"Authorization": f"Bearer {token}"})
        assert response.json()["is_internal"] is False


def test_is_internal_user_is_false_for_unknown_and_other_tenant_users(db: Database) -> None:
    other = seed_user(db, T2, STAFF, password=None, is_internal=True)
    assert _flag(db, uuid.uuid4()) is False
    assert _flag(db, other) is False  # bound to T1: T2's user does not exist


# --- the flag grants nothing ----------------------------------------------------------------


def test_an_internal_user_without_a_membership_gets_404_on_a_project(
    tmp_path: Path, db: Database
) -> None:
    staff = seed_user(db, T1, STAFF)
    admin = seed_user(db, T1, "admin@partner.example")
    with db.unit_of_work(CTX_T1) as session:
        project = projects.create_project(
            session, ProjectCreate.model_validate({"code": "SYN-1", "name": "Synthetic"})
        ).id
        session.add(
            ProjectMembership(
                tenant_id=T1,
                user_id=admin,
                member_project_id=project,
                persona=Persona.PROJECT_ADMINISTRATOR.value,
            )
        )
    with _client(tmp_path, internal_email_domains=COMPANY) as client:
        token = _login(client, STAFF)["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get(f"/api/v1/projects/{project}", headers=headers).status_code == 404
        assert client.get("/api/v1/projects", headers=headers).json() == []
    assert _flag(db, staff) is True
    with db.unit_of_work(CTX_T1) as session:
        memberships = session.scalars(
            select(ProjectMembership).where(ProjectMembership.user_id == staff)
        ).all()
    assert memberships == []
