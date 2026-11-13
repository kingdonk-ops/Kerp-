"""Tenancy context, ORM scope, soft-delete filter and cross-project 404 (T-0007, TD-033).

These run on SQLite and on PostgreSQL. On PostgreSQL the scratch tables are also under
RLS, so every assertion holds with both layers active.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.auth.bearer import ANONYMOUS, BearerResult
from app.core.auth.tokens import TokenClaims
from app.core.config import Settings
from app.core.db import Database, DbSession
from app.core.tenancy.context import (
    TenancyContext,
    current_project_id,
    current_tenant_id,
    get_tenancy_context,
    use_tenancy,
)
from app.core.tenancy.rls import (
    ALLOWED_READ_POLICY_NAME,
    ALLOWED_SETTING,
    POLICY_NAME,
    POLICY_NAMES,
    PROJECT_SETTING,
    READ_POLICY_NAME,
    SESSION_INFO_KEY,
    TENANT_SETTING,
    bind_tenancy,
    rls_policy_sql,
    rls_tables,
    session_tenancy,
)
from app.core.tenancy.scope import OutOfScopeError, get_or_404
from app.main import create_app
from tests.db_support import ON_POSTGRES, Gadget, ScratchBase, Widget, database_url
from tests.tenancy_support import (
    CTX_A,
    CTX_C,
    CTX_T1,
    DELETED_IN_A,
    PA,
    PB,
    T1,
    T2,
    VISIBLE_TO_A,
    seeded_database,
    widget_id,
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from seeded_database(tmp_path)


def _names(db: Database, ctx: TenancyContext, **options: object) -> set[str]:
    with db.unit_of_work(ctx) as s:
        return set(s.scalars(select(Widget.name).execution_options(**options)))


# --- context ---------------------------------------------------------------------------


def test_use_tenancy_sets_and_resets_context_vars() -> None:
    assert current_tenant_id.get() is None
    with use_tenancy(CTX_A):
        assert current_tenant_id.get() == T1
        assert current_project_id.get() == PA
    assert current_tenant_id.get() is None
    assert current_project_id.get() is None
    with use_tenancy(CTX_T1):
        assert current_project_id.get() is None


def test_tenancy_seam_comes_from_the_verified_token_only() -> None:
    # No token, or a token that failed verification: the request session stays unbound.
    # No database on the app: the token's scope only (memberships: tests/test_access.py).
    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    assert get_tenancy_context(request, ANONYMOUS) is None
    assert get_tenancy_context(request, BearerResult(claims=None, presented=True)) is None
    user = uuid.uuid4()
    now = datetime.now(UTC)
    claims = TokenClaims("access", user, T1, uuid.uuid4(), "jti", now, now)
    bound = get_tenancy_context(request, BearerResult(claims=claims, presented=True))
    assert bound == TenancyContext(tenant_id=T1, project_id=None, user_id=user)


# --- reads -----------------------------------------------------------------------------


def test_project_session_sees_its_project_and_tenant_level_rows(db: Database) -> None:
    assert _names(db, CTX_A) == VISIBLE_TO_A


def test_tenant_level_session_sees_only_tenant_level_rows(db: Database) -> None:
    assert _names(db, CTX_T1) == {"t1-library"}


def test_other_tenant_sees_only_its_own_rows(db: Database) -> None:
    assert _names(db, CTX_C) == {"c1"}
    with db.unit_of_work(CTX_C) as s:
        assert s.scalars(select(Gadget.label)).all() == ["gc"]


def test_cross_project_lookup_by_id_is_not_found(db: Database) -> None:
    b1 = widget_id(db, TenancyContext(tenant_id=T1, project_id=PB), "b1")
    with db.unit_of_work(CTX_A) as s:
        assert s.get(Widget, b1) is None
        with pytest.raises(OutOfScopeError, match="Widget not found"):
            get_or_404(s, Widget, b1)
        a1 = widget_id(db, CTX_A, "a1")
        assert get_or_404(s, Widget, a1).name == "a1"


def test_unbound_session_on_this_dialect(db: Database) -> None:
    # SQLite: no RLS, the ORM scope needs a binding, so everything is visible.
    # PostgreSQL: RLS fails closed, an unbound session sees nothing.
    with db.sessionmaker() as s:
        count = s.scalar(select(func.count()).select_from(Widget))
    assert count == (0 if ON_POSTGRES else 7)


def test_core_table_select_is_only_guarded_by_rls(db: Database) -> None:
    # Loader criteria apply to ORM entities only; a Core table select relies on RLS.
    with db.unit_of_work(CTX_A) as s:
        count = s.execute(select(func.count()).select_from(Gadget.__table__)).scalar_one()
    assert count == (1 if ON_POSTGRES else 4)


def test_rebinding_a_session_switches_scope(db: Database) -> None:
    with db.sessionmaker() as s:
        bind_tenancy(s, CTX_A)
        assert session_tenancy(s) == CTX_A
        assert set(s.scalars(select(Widget.name))) == VISIBLE_TO_A
        # Mid-transaction rebind is applied to the open connection too.
        bind_tenancy(s, CTX_C)
        assert set(s.scalars(select(Widget.name))) == {"c1"}
        bind_tenancy(s, None)
        assert session_tenancy(s) is None


def test_rebinding_a_session_that_holds_objects_is_refused(db: Database) -> None:
    a1 = widget_id(db, CTX_A, "a1")
    with db.sessionmaker() as s:
        bind_tenancy(s, CTX_A)
        held = get_or_404(s, Widget, a1)  # the identity map keeps it while referenced
        assert held.name == "a1"
        bind_tenancy(s, CTX_A)  # same context: a no-op
        with pytest.raises(RuntimeError, match="cannot rebind"):
            bind_tenancy(s, CTX_C)
        assert session_tenancy(s) == CTX_A
        # The documented way: commit, empty the identity map, then rebind.
        s.commit()
        s.expunge_all()
        bind_tenancy(s, CTX_C)
        with pytest.raises(OutOfScopeError):
            get_or_404(s, Widget, a1)


def test_get_or_404_rechecks_identity_map_hits(db: Database) -> None:
    # session.get answers from the identity map without SQL; the scope must still hold.
    a1 = widget_id(db, CTX_A, "a1")
    with db.sessionmaker() as s:
        bind_tenancy(s, CTX_A)
        row = get_or_404(s, Widget, a1)  # now in the identity map
        s.info[SESSION_INFO_KEY] = CTX_C  # skip the rebind guard to plant a stale identity
        with pytest.raises(OutOfScopeError):
            get_or_404(s, Widget, a1)
        s.info[SESSION_INFO_KEY] = CTX_A
        assert get_or_404(s, Widget, a1) is row
        row.soft_delete()  # soft-deleted earlier in the same session (TD-033)
        with pytest.raises(OutOfScopeError):
            get_or_404(s, Widget, a1)
        assert get_or_404(s, Widget, a1, include_deleted=True) is row


# --- soft delete (TD-033) ----------------------------------------------------------------


def test_soft_deleted_rows_are_hidden_by_default(db: Database) -> None:
    assert DELETED_IN_A not in _names(db, CTX_A)
    deleted = widget_id(db, CTX_A, DELETED_IN_A)
    with db.unit_of_work(CTX_A) as s:
        assert s.get(Widget, deleted) is None


def test_include_deleted_opts_back_in(db: Database) -> None:
    assert _names(db, CTX_A, include_deleted=True) == {*VISIBLE_TO_A, DELETED_IN_A}
    deleted = widget_id(db, CTX_A, DELETED_IN_A)
    with db.unit_of_work(CTX_A) as s:
        row = s.get(Widget, deleted, execution_options={"include_deleted": True})
        assert row is not None
        assert row.is_deleted


def test_include_deleted_does_not_widen_the_project_scope(db: Database) -> None:
    assert not {"b1", "c1"} & _names(db, CTX_A, include_deleted=True)


# --- writes ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tenant", "project"),
    [(T1, PB), (T2, PA), (T2, None)],
    ids=["other-project", "other-tenant", "other-tenant-level"],
)
def test_write_outside_scope_is_refused(
    db: Database, tenant: uuid.UUID, project: uuid.UUID | None
) -> None:
    with pytest.raises(OutOfScopeError), db.unit_of_work(CTX_A) as s:
        s.add(Widget(tenant_id=tenant, project_id=project, name="smuggled"))
    assert "smuggled" not in _names(db, TenancyContext(tenant_id=tenant, project_id=project))


def test_moving_a_row_to_another_project_is_refused(db: Database) -> None:
    a1 = widget_id(db, CTX_A, "a1")
    with pytest.raises(OutOfScopeError), db.unit_of_work(CTX_A) as s:
        s.get_one(Widget, a1).project_id = PB
    assert "a1" in _names(db, CTX_A)


def test_project_session_writes_only_its_project(db: Database) -> None:
    with db.unit_of_work(CTX_A) as s:
        s.add(Widget(tenant_id=T1, project_id=PA, name="a3"))
    assert "a3" in _names(db, CTX_A)
    with pytest.raises(OutOfScopeError), db.unit_of_work(CTX_A) as s:
        s.add(Widget(tenant_id=T1, project_id=None, name="t1-shared"))
    assert "t1-shared" not in _names(db, CTX_T1)


def test_tenant_level_rows_are_written_only_by_a_tenant_level_session(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        s.add(Widget(tenant_id=T1, project_id=None, name="t1-shared"))
    assert "t1-shared" in _names(db, CTX_A)
    library = widget_id(db, CTX_T1, "t1-library")
    with pytest.raises(OutOfScopeError), db.unit_of_work(CTX_A) as s:
        s.get_one(Widget, library).name = "hijacked"
    with pytest.raises(OutOfScopeError), db.unit_of_work(CTX_A) as s:
        s.delete(s.get_one(Widget, library))
    assert "t1-library" in _names(db, CTX_T1)
    with pytest.raises(OutOfScopeError), db.unit_of_work(CTX_T1) as s:
        s.add(Widget(tenant_id=T1, project_id=PA, name="pushed-down"))


def test_demoting_a_project_row_to_tenant_level_is_refused(db: Database) -> None:
    a1 = widget_id(db, CTX_A, "a1")
    with pytest.raises(OutOfScopeError), db.unit_of_work(CTX_A) as s:
        s.get_one(Widget, a1).project_id = None
    assert "a1" not in _names(db, TenancyContext(tenant_id=T1, project_id=PB))


# --- policy helper (dialect-independent parts) --------------------------------------------


def test_rls_tables_cover_every_tenant_table() -> None:
    assert rls_tables(ScratchBase.metadata) == ["test_gadget", "test_widget"]
    assert rls_tables(ScratchBase.metadata, exempt=frozenset({"test_gadget"})) == ["test_widget"]


def test_policy_sql_forces_rls_and_guards_empty_settings() -> None:
    statements = rls_policy_sql("test_widget")
    enable, force, drop, create, drop_read, create_read, drop_allowed, create_allowed = statements
    assert enable == "ALTER TABLE test_widget ENABLE ROW LEVEL SECURITY"
    assert force == "ALTER TABLE test_widget FORCE ROW LEVEL SECURITY"
    assert drop == f"DROP POLICY IF EXISTS {POLICY_NAME} ON test_widget"
    assert drop_read == f"DROP POLICY IF EXISTS {READ_POLICY_NAME} ON test_widget"
    tenant = f"tenant_id = NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid"
    project = (
        f"project_id IS NOT DISTINCT FROM NULLIF(current_setting('{PROJECT_SETTING}', true), '')"
        "::uuid"
    )
    # Writes: exact scope in both USING and WITH CHECK; NULL project is not a wildcard.
    assert create == (
        f"CREATE POLICY {POLICY_NAME} ON test_widget FOR ALL "
        f"USING ({tenant} AND {project}) WITH CHECK ({tenant} AND {project})"
    )
    # Reads only: the tenant's tenant-level rows.
    assert create_read == (
        f"CREATE POLICY {READ_POLICY_NAME} ON test_widget FOR SELECT "
        f"USING ({tenant} AND project_id IS NULL)"
    )
    # Reads only: rows of the allowed projects (T-0010, TD-053); an unset list matches nothing.
    assert drop_allowed == f"DROP POLICY IF EXISTS {ALLOWED_READ_POLICY_NAME} ON test_widget"
    assert create_allowed == (
        f"CREATE POLICY {ALLOWED_READ_POLICY_NAME} ON test_widget FOR SELECT "
        f"USING ({tenant} AND project_id = ANY(string_to_array("
        f"NULLIF(current_setting('{ALLOWED_SETTING}', true), ''), ',')::uuid[]))"
    )
    assert POLICY_NAMES == (POLICY_NAME, READ_POLICY_NAME, ALLOWED_READ_POLICY_NAME)


# --- HTTP: cross-project is a 404 ---------------------------------------------------------


@pytest.fixture
def api(db: Database, tmp_path: Path) -> Iterator[TestClient]:
    app: FastAPI = create_app(Settings(environment="test", database_url=database_url(tmp_path)))
    app.dependency_overrides[get_tenancy_context] = lambda: CTX_A

    @app.get("/_widgets")
    def list_widgets(session: DbSession) -> list[str]:
        return sorted(session.scalars(select(Widget.name)))

    @app.get("/_widgets/{widget}")
    def read_widget(widget: uuid.UUID, session: DbSession) -> dict[str, str]:
        return {"name": get_or_404(session, Widget, widget).name}

    @app.post("/_widgets/{project}")
    def create_widget(project: uuid.UUID, session: DbSession) -> dict[str, bool]:
        session.add(Widget(tenant_id=T1, project_id=project, name="posted"))
        return {"ok": True}

    with TestClient(app) as client:
        yield client


def test_api_lists_only_the_bound_project(api: TestClient) -> None:
    assert set(api.get("/_widgets").json()) == VISIBLE_TO_A


def test_api_cross_project_read_is_404(api: TestClient, db: Database) -> None:
    b1 = widget_id(db, TenancyContext(tenant_id=T1, project_id=PB), "b1")
    response = api.get(f"/_widgets/{b1}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert api.get(f"/_widgets/{widget_id(db, CTX_A, 'a1')}").json() == {"name": "a1"}


def test_api_cross_project_write_is_404_and_writes_nothing(api: TestClient, db: Database) -> None:
    response = api.post(f"/_widgets/{PB}")
    assert response.status_code == 404
    assert "posted" not in _names(db, TenancyContext(tenant_id=T1, project_id=PB))
    assert api.post(f"/_widgets/{PA}").status_code == 200
    assert "posted" in _names(db, CTX_A)
