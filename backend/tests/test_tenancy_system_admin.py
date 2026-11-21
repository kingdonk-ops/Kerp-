"""system_admin global project visibility in the ORM scope and the policy helper (ADR 0016,
T-0274; diff §4.1, edge spec §6).

These run on SQLite (the ORM scope is the only filter) and on PostgreSQL (the scratch tables
are under the live RLS policies too, so every assertion holds with both layers). Raw-SQL and
worker paths under RLS alone are in ``test_rls_bypass.py``; the real tables in
``test_rls_system_admin.py``. All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth.access import SYSTEM_ADMIN
from app.core.db import Database, DbSession
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import (
    POLICY_NAME,
    PROJECT_ISOLATION_SQL,
    PROJECT_SETTING,
    SCOPE_ROW_SQL,
    SYSTEM_ADMIN_SETTING,
    SYSTEM_ADMIN_SQL,
    TENANT_SETTING,
    rls_policy_sql,
    session_tenancy,
)
from app.core.tenancy.scope import OutOfScopeError, get_or_404, is_visible, site_filter
from app.modules.admin import service as admin
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, auth_app, make_settings, seed_user
from tests.auth_support import T1 as AUTH_T1
from tests.db_support import Widget, database_url
from tests.tenancy_support import (
    ADMIN_A,
    ADMIN_T1,
    ADMIN_T2,
    CTX_A,
    CTX_B,
    CTX_C,
    CTX_T1,
    PA,
    PB,
    PC,
    T1,
    T2,
    VISIBLE_TO_A,
    VISIBLE_TO_T1_ADMIN,
    seeded_database,
    widget_id,
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from seeded_database(tmp_path)


def _names(db: Database, ctx: TenancyContext) -> set[str]:
    with db.unit_of_work(ctx) as s:
        return set(s.scalars(select(Widget.name)))


# --- the policy helper ------------------------------------------------------------------------


def test_project_isolation_policy_admits_a_system_admin_for_project_rows_only() -> None:
    tenant = f"tenant_id = NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid"
    exact = (
        f"project_id IS NOT DISTINCT FROM NULLIF(current_setting('{PROJECT_SETTING}', true), '')"
        "::uuid"
    )
    flag = f"COALESCE(NULLIF(current_setting('{SYSTEM_ADMIN_SETTING}', true), '')::boolean, false)"
    assert flag == SYSTEM_ADMIN_SQL
    rule = f"{tenant} AND ({exact} OR (project_id IS NOT NULL AND {flag}))"
    assert rule == PROJECT_ISOLATION_SQL
    create = rls_policy_sql("test_widget")[3]
    # USING and WITH CHECK alike: the admin reads and writes project rows of its tenant.
    assert create == (
        f"CREATE POLICY {POLICY_NAME} ON test_widget FOR ALL USING ({rule}) WITH CHECK ({rule})"
    )
    # The special policies keep the exact scope for their writes (unchanged by ADR 0016).
    assert f"{tenant} AND {exact}" == SCOPE_ROW_SQL


# --- reads --------------------------------------------------------------------------------------


@pytest.mark.parametrize("ctx", [ADMIN_A, ADMIN_T1], ids=["project-bound", "tenant-level"])
def test_a_system_admin_reads_every_project_of_its_tenant(
    db: Database, ctx: TenancyContext
) -> None:
    assert _names(db, ctx) == VISIBLE_TO_T1_ADMIN
    b1 = widget_id(db, CTX_B, "b1")
    with db.unit_of_work(ctx) as s:
        record = get_or_404(s, Widget, b1)
        assert record.name == "b1"
        assert is_visible(s, record)


def test_a_non_admin_still_reads_only_its_project(db: Database) -> None:
    assert _names(db, CTX_A) == VISIBLE_TO_A
    assert _names(db, TenancyContext(tenant_id=T1, project_id=PA, is_system_admin=False)) == (
        VISIBLE_TO_A
    )
    b1 = widget_id(db, CTX_B, "b1")
    with pytest.raises(OutOfScopeError), db.unit_of_work(CTX_A) as s:
        get_or_404(s, Widget, b1)


def test_the_flag_never_crosses_tenants(db: Database) -> None:
    # An admin of T1 sees nothing of T2; an admin of T2 sees nothing of T1.
    c1 = widget_id(db, CTX_C, "c1")
    with pytest.raises(OutOfScopeError), db.unit_of_work(ADMIN_T1) as s:
        get_or_404(s, Widget, c1)
    assert _names(db, ADMIN_T2) == {"c1"}
    a1 = widget_id(db, CTX_A, "a1")
    with pytest.raises(OutOfScopeError), db.unit_of_work(ADMIN_T2) as s:
        get_or_404(s, Widget, a1)


# --- writes -------------------------------------------------------------------------------------


@pytest.mark.parametrize("ctx", [ADMIN_A, ADMIN_T1], ids=["project-bound", "tenant-level"])
def test_a_system_admin_writes_project_rows_of_every_project(
    db: Database, ctx: TenancyContext
) -> None:
    b1 = widget_id(db, CTX_B, "b1")
    with db.unit_of_work(ctx) as s:
        s.add(Widget(tenant_id=T1, project_id=PB, name="b-by-admin"))
        s.get_one(Widget, b1).name = "b1-renamed"
        s.delete(s.get_one(Widget, widget_id(db, CTX_B, "b2")))
    assert _names(db, CTX_B) == {"b-by-admin", "b1-renamed", "b3", "t1-library"}


def test_a_project_bound_admin_never_writes_a_tenant_level_row(db: Database) -> None:
    library = widget_id(db, CTX_T1, "t1-library")
    with pytest.raises(OutOfScopeError), db.unit_of_work(ADMIN_A) as s:
        s.add(Widget(tenant_id=T1, project_id=None, name="t1-smuggled"))
    with pytest.raises(OutOfScopeError), db.unit_of_work(ADMIN_A) as s:
        s.get_one(Widget, library).name = "hijacked"
    with pytest.raises(OutOfScopeError), db.unit_of_work(ADMIN_A) as s:
        s.delete(s.get_one(Widget, library))
    # Nor demotes a project row (of its own project or another) to tenant level.
    for project, name in ((CTX_A, "a1"), (CTX_B, "b1")):
        row = widget_id(db, project, name)
        with pytest.raises(OutOfScopeError), db.unit_of_work(ADMIN_A) as s:
            s.get_one(Widget, row).project_id = None
    # Nor promotes a tenant-level row into a project (the old row is not writable).
    with pytest.raises(OutOfScopeError), db.unit_of_work(ADMIN_A) as s:
        s.get_one(Widget, library).project_id = PB
    assert _names(db, CTX_T1) == {"t1-library"}
    assert _names(db, CTX_A) == VISIBLE_TO_A


def test_a_tenant_level_admin_keeps_the_tenant_level_write_rule(db: Database) -> None:
    # A tenant-level session (admin or not) writes tenant-level rows: unchanged.
    with db.unit_of_work(ADMIN_T1) as s:
        s.add(Widget(tenant_id=T1, project_id=None, name="t1-shared"))
    assert "t1-shared" in _names(db, CTX_A)


def test_a_system_admin_moves_a_row_between_projects_of_its_tenant(db: Database) -> None:
    a1 = widget_id(db, CTX_A, "a1")
    with db.unit_of_work(ADMIN_T1) as s:
        s.get_one(Widget, a1).project_id = PB
    assert "a1" in _names(db, CTX_B)
    # A non-admin portfolio reader of B cannot move B's row into its own project A.
    portfolio = TenancyContext(tenant_id=T1, project_id=PA, allowed_project_ids=frozenset({PB}))
    with pytest.raises(OutOfScopeError), db.unit_of_work(portfolio) as s:
        s.get_one(Widget, a1).project_id = PA
    assert "a1" in _names(db, CTX_B)


@pytest.mark.parametrize(
    ("ctx", "tenant", "project"),
    [(ADMIN_A, T2, PC), (ADMIN_T1, T2, PC), (ADMIN_T1, T2, None), (ADMIN_T2, T1, PA)],
    ids=["t1-bound-into-t2", "t1-into-t2", "t1-into-t2-tenant-level", "t2-into-t1"],
)
def test_a_system_admin_never_writes_another_tenant(
    db: Database, ctx: TenancyContext, tenant: uuid.UUID, project: uuid.UUID | None
) -> None:
    with pytest.raises(OutOfScopeError), db.unit_of_work(ctx) as s:
        s.add(Widget(tenant_id=tenant, project_id=project, name="smuggled"))
    assert "smuggled" not in _names(db, TenancyContext(tenant_id=tenant, project_id=project))


# --- site scope ---------------------------------------------------------------------------------


def test_a_system_admin_context_is_never_site_filtered() -> None:
    sites = frozenset({uuid.uuid4()})
    inspector = TenancyContext(tenant_id=T1, project_id=PA, site_node_ids=sites)
    assert site_filter(inspector) == sites
    assert site_filter(TenancyContext(tenant_id=T1, project_id=PA)) is None
    flagged = TenancyContext(tenant_id=T1, project_id=PA, site_node_ids=sites, is_system_admin=True)
    assert site_filter(flagged) is None


# --- the flag comes from the grants table only ----------------------------------------------


@pytest.fixture
def scope_api(tmp_path: Path) -> Iterator[tuple[TestClient, Any, dict[str, uuid.UUID]]]:
    """The real auth stack on a migrated database, with a probe of the request session's flag."""
    migrated = migrated_database(tmp_path)
    auth_db = next(migrated)
    app, runtime = auth_app(make_settings(database_url=database_url(tmp_path)))

    @app.get("/_scope")
    def scope(session: DbSession) -> dict[str, Any]:
        context = session_tenancy(session)
        return {"admin": context is not None and context.is_system_admin}

    @app.post("/_scope")
    def scope_with_body(body: dict[str, Any], session: DbSession) -> dict[str, Any]:
        context = session_tenancy(session)
        return {"admin": context is not None and context.is_system_admin}

    users = {"alice": seed_user(auth_db, AUTH_T1, ALICE), "bob": seed_user(auth_db, AUTH_T1, BOB)}
    with auth_db.unit_of_work(TenancyContext(tenant_id=AUTH_T1, is_system_admin=True)) as s:
        admin.grant(s, users["alice"], SYSTEM_ADMIN, actor_id=None)
    try:
        with TestClient(app) as client:
            yield client, runtime, users
    finally:
        next(migrated, None)


_FORGED_HEADERS = {
    "X-KERP-System-Admin": "true",
    "X-Is-System-Admin": "true",
    "X-KERP-Is-System-Admin": "true",
    "X-App-Is-System-Admin": "true",
    "X-KERP-Admin": "system_admin",
}
_FORGED_QUERY = "?is_system_admin=true&system_admin=true&app.is_system_admin=true"


def _login(client: TestClient, email: str) -> str:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


def test_a_forged_claim_header_query_or_body_never_sets_the_flag(
    scope_api: tuple[TestClient, Any, dict[str, uuid.UUID]],
) -> None:
    client, runtime, _users = scope_api
    token = _login(client, BOB)
    # The same token re-signed with the deployment key plus admin-looking claims: still valid,
    # still BOB, still no flag (only resolve_request_scope's grant lookup sets it).
    claims = jwt.decode(token, options={"verify_signature": False})
    claims.update(
        is_system_admin=True,
        system_admin=True,
        admin_grants=[SYSTEM_ADMIN],
        roles=[SYSTEM_ADMIN],
        app={"is_system_admin": "true"},
    )
    signing = runtime.tokens.keys.signing
    forged = jwt.encode(
        claims,
        signing.private_key,
        algorithm=signing.algorithm,
        headers={**jwt.get_unverified_header(token), "kid": signing.kid},
    )
    for bearer in (token, forged):
        headers = {"Authorization": f"Bearer {bearer}", **_FORGED_HEADERS}
        response = client.get(f"/_scope{_FORGED_QUERY}", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"admin": False}
        body = {"is_system_admin": True, "app.is_system_admin": "true", "tenant_id": str(AUTH_T1)}
        posted = client.post(f"/_scope{_FORGED_QUERY}", headers=headers, json=body)
        assert posted.status_code == 200, posted.text
        assert posted.json() == {"admin": False}
    # The positive control: ALICE holds the grant row, so her plain token binds the flag.
    alice = {"Authorization": f"Bearer {_login(client, ALICE)}"}
    assert client.get("/_scope", headers=alice).json() == {"admin": True}
