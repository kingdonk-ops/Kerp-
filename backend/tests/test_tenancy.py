"""Tenancy context, ORM scope, soft-delete filter and cross-project 404 (T-0007, TD-033).

These run on SQLite and on PostgreSQL. On PostgreSQL the scratch tables are also under
RLS, so every assertion holds with both layers active.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.db import Database, DbSession
from app.core.tenancy.context import (
    TenancyContext,
    current_project_id,
    current_tenant_id,
    get_tenancy_context,
    require_project_id,
    require_tenant_id,
    use_tenancy,
)
from app.core.tenancy.rls import (
    POLICY_NAME,
    PROJECT_SETTING,
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
    with pytest.raises(RuntimeError, match="no tenant"):
        require_tenant_id()
    with use_tenancy(CTX_A):
        assert require_tenant_id() == T1
        assert require_project_id() == PA
    assert current_tenant_id.get() is None
    assert current_project_id.get() is None
    with use_tenancy(CTX_T1), pytest.raises(RuntimeError, match="no project"):
        require_project_id()


def test_auth_seam_is_unbound_until_t0008() -> None:
    assert get_tenancy_context() is None


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


def test_project_session_may_write_its_project_and_tenant_level_rows(db: Database) -> None:
    with db.unit_of_work(CTX_A) as s:
        s.add(Widget(tenant_id=T1, project_id=PA, name="a3"))
        s.add(Widget(tenant_id=T1, project_id=None, name="t1-shared"))
    assert {"a3", "t1-shared"} <= _names(db, CTX_A)


# --- policy helper (dialect-independent parts) --------------------------------------------


def test_rls_tables_cover_every_tenant_table() -> None:
    assert rls_tables(ScratchBase.metadata) == ["test_gadget", "test_widget"]
    assert rls_tables(ScratchBase.metadata, exempt=frozenset({"test_gadget"})) == ["test_widget"]


def test_policy_sql_forces_rls_and_guards_empty_settings() -> None:
    enable, force, drop, create = rls_policy_sql("test_widget")
    assert enable == "ALTER TABLE test_widget ENABLE ROW LEVEL SECURITY"
    assert force == "ALTER TABLE test_widget FORCE ROW LEVEL SECURITY"
    assert drop == f"DROP POLICY IF EXISTS {POLICY_NAME} ON test_widget"
    assert f"CREATE POLICY {POLICY_NAME} ON test_widget FOR ALL" in create
    for setting in (TENANT_SETTING, PROJECT_SETTING):
        # Both in USING and in WITH CHECK, both behind the NULLIF empty-setting guard.
        assert create.count(f"NULLIF(current_setting('{setting}', true), '')::uuid") == 2
    assert "WITH CHECK" in create


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
