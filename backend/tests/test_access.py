"""Three-tier RBAC/ABAC: persona matrix, SoD, multi-project and Asset-Site scope (T-0010).

Runs on SQLite and PostgreSQL (CI's ``kerp_app`` step); the RLS side of the multi-project
scope is in ``test_rls_multi_project.py``.
"""

import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.auth.access import (
    PERSONAS,
    SOD_BLOCKED,
    TIER_CAPABILITIES,
    Capability,
    Grant,
    HoldKind,
    Persona,
    PersonaSpec,
    ProjectAccess,
    Scope,
    Tier,
    load_grants,
    project_access,
    resolve_request_scope,
    resolve_scope,
    sod_violations,
)
from app.core.auth.models import PERSONA_CODES, ProjectMembership
from app.core.auth.runtime import AuthRuntime
from app.core.db import NAMING_CONVENTION, BaseRecordMixin, Database, DbSession
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import apply_rls_policies, session_tenancy
from app.core.tenancy.scope import OutOfScopeError, SiteScopedMixin, get_or_404
from tests.audit_support import migrated_database
from tests.auth_support import (
    ALICE,
    BOB,
    PASSWORD,
    auth_app,
    make_settings,
    revoke_project_viewers,
    seed_user,
)
from tests.auth_support import T1 as AUTH_T1
from tests.db_support import Widget, database_url
from tests.tenancy_support import CTX_A, PA, PB, PC, T1, seeded_database

S1 = uuid.UUID("00000000-0000-4000-8000-0000000005a1")
S2 = uuid.UUID("00000000-0000-4000-8000-0000000005a2")


def _access(*grants: tuple[Persona, uuid.UUID | None]) -> ProjectAccess:
    return project_access([Grant(PA, persona, site) for persona, site in grants], PA)


# --- the matrix (edge spec §5) ----------------------------------------------------------------


def test_matrix_has_the_sixteen_personas_of_the_store() -> None:
    assert [p.value for p in PERSONAS] == list(PERSONA_CODES)
    assert len(PERSONA_CODES) == 16
    assert PERSONA_CODES[-1] == "PER-16"
    assert PERSONAS[Persona.FIELD_WORKER].scope is Scope.SINGLE
    assert PERSONAS[Persona.CLIENT_REPRESENTATIVE].tier is Tier.PORTAL
    portfolio = {p for p, spec in PERSONAS.items() if spec.scope is Scope.PORTFOLIO}
    assert {p.value for p in portfolio} == {"PER-01", "PER-10", "PER-11", "PER-12", "PER-13"}


def test_capability_tiers() -> None:
    assert PERSONAS[Persona.CLIENT_REPRESENTATIVE].capabilities == {
        Capability.VIEW,
        Capability.SIGN,
    }
    assert Capability.ADMINISTER in PERSONAS[Persona.PROJECT_ADMINISTRATOR].capabilities
    assert Capability.APPROVE in PERSONAS[Persona.PROJECT_MANAGER].capabilities  # commercial A
    assert Capability.APPROVE in PERSONAS[Persona.QAQC_LEAD].capabilities
    assert Capability.APPROVE not in PERSONAS[Persona.STOREKEEPER].capabilities
    assert {Capability.VIEW, Capability.CREATE, Capability.EDIT} <= PERSONAS[
        Persona.FIELD_WORKER
    ].capabilities


def test_project_viewer_is_read_only_on_a_single_project() -> None:
    """PER-16 (edge spec §5.2 amendment, TD-251): Viewer tier, ``V`` only, no authority."""
    spec = PERSONAS[Persona.PROJECT_VIEWER]
    assert Persona.PROJECT_VIEWER.value == "PER-16"
    assert spec == PersonaSpec(Tier.VIEWER, Scope.SINGLE)
    assert TIER_CAPABILITIES[Tier.VIEWER] == {Capability.VIEW}
    assert spec.capabilities == {Capability.VIEW}
    viewer = _access((Persona.PROJECT_VIEWER, None))
    assert viewer.capabilities == {Capability.VIEW}
    for capability in set(Capability) - {Capability.VIEW}:
        assert not viewer.has(capability)
    for kind in HoldKind:
        assert not viewer.can_release_hold(kind)
    assert viewer.site_node_ids is None  # reads the bound project like other single personas
    assert Persona.PROJECT_VIEWER not in SOD_BLOCKED
    assert sod_violations() == []


def test_project_viewer_adds_no_allowed_projects() -> None:
    base = TenancyContext(tenant_id=T1, user_id=uuid.uuid4())
    grants = [Grant(PA, Persona.PROJECT_VIEWER), Grant(PB, Persona.PROJECT_VIEWER)]
    assert resolve_scope(grants, base).allowed_project_ids == frozenset()
    bound = resolve_scope(grants, base, PA)
    assert bound.project_id == PA
    assert bound.readable_project_ids == {PA}
    assert bound.site_node_ids is None
    with pytest.raises(OutOfScopeError):
        resolve_scope(grants, base, PC)


def test_project_administrator_cannot_release_quality_holds() -> None:
    admin = _access((Persona.PROJECT_ADMINISTRATOR, None))
    assert admin.has(Capability.ADMINISTER)
    assert not admin.has(Capability.APPROVE)
    assert not admin.can_release_hold(HoldKind.QUALITY)
    assert not admin.can_release_hold(HoldKind.SAFETY)
    assert _access((Persona.QAQC_LEAD, None)).can_release_hold(HoldKind.QUALITY)
    assert not _access((Persona.QAQC_LEAD, None)).can_release_hold(HoldKind.SAFETY)
    assert _access((Persona.HSE_MANAGER, None)).can_release_hold(HoldKind.SAFETY)
    assert not _access((Persona.CLIENT_REPRESENTATIVE, None)).can_release_hold(HoldKind.QUALITY)


def test_sod_rule_rejects_a_matrix_that_grants_the_admin_hold_authority() -> None:
    assert sod_violations() == []
    tampered = dict(PERSONAS)
    tampered[Persona.PROJECT_ADMINISTRATOR] = PersonaSpec(
        Tier.ADMIN, Scope.SINGLE, holds=frozenset({HoldKind.QUALITY})
    )
    assert sod_violations(tampered) == ["PER-09 must not release quality holds"]


def test_site_inspector_releases_holds_on_their_site_only() -> None:
    inspector = _access((Persona.SITE_INSPECTOR, S1))
    assert inspector.site_node_ids == {S1}
    assert inspector.can_release_hold(HoldKind.QUALITY, site_node_id=S1)
    assert not inspector.can_release_hold(HoldKind.QUALITY, site_node_id=S2)
    assert not inspector.can_release_hold(HoldKind.QUALITY)
    # An unrestricted persona on the same project lifts the site filter.
    assert _access((Persona.SITE_INSPECTOR, S1), (Persona.QAQC_LEAD, None)).site_node_ids is None


# --- resolving a session scope from memberships --------------------------------------------


def test_portfolio_memberships_become_the_allowed_list() -> None:
    base = TenancyContext(tenant_id=T1, user_id=uuid.uuid4())
    grants = [
        Grant(PA, Persona.OPERATIONS_MANAGEMENT),
        Grant(PB, Persona.QUALITY_DIRECTOR),
        Grant(PC, Persona.SITE_SUPERINTENDENT),  # single-project: no cross-project read
    ]
    scope = resolve_scope(grants, base)
    assert scope == replace(
        base, allowed_project_ids=frozenset({PA, PB}), member_project_ids=frozenset({PA, PB, PC})
    )
    assert scope.project_id is None  # writes: tenant-level only until a project is bound
    bound = resolve_scope(grants, base, PC)
    assert bound.project_id == PC
    assert bound.readable_project_ids == {PA, PB, PC}
    assert bound.site_node_ids is None


def test_single_project_personas_get_no_allowed_list() -> None:
    base = TenancyContext(tenant_id=T1)
    grants = [Grant(PA, Persona.SITE_SUPERINTENDENT), Grant(PB, Persona.PROJECT_MANAGER)]
    assert resolve_scope(grants, base).allowed_project_ids == frozenset()


def test_binding_a_project_without_membership_is_not_found() -> None:
    with pytest.raises(OutOfScopeError):
        resolve_scope([Grant(PA, Persona.QAQC_LEAD)], TenancyContext(tenant_id=T1), PB)


def test_a_context_without_user_gets_no_allowed_projects() -> None:
    base = TenancyContext(tenant_id=T1)
    assert resolve_request_scope(cast(Any, None), base) is base  # no lookup at all


def test_site_inspector_scope_carries_the_site() -> None:
    scope = resolve_scope([Grant(PA, Persona.SITE_INSPECTOR, S1)], TenancyContext(T1), PA)
    assert scope.site_node_ids == {S1}


# --- ORM: multi-project reads, single-project writes -------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from seeded_database(tmp_path)


PORTFOLIO_A = TenancyContext(tenant_id=T1, project_id=PA, allowed_project_ids=frozenset({PB}))


def _names(db: Database, ctx: TenancyContext) -> set[str]:
    with db.unit_of_work(ctx) as s:
        return set(s.scalars(select(Widget.name)))


def test_allowed_projects_are_readable_in_one_session(db: Database) -> None:
    assert _names(db, PORTFOLIO_A) == {"a1", "a2", "b1", "b2", "b3", "t1-library"}
    with db.unit_of_work(TenancyContext(tenant_id=T1, project_id=PB)) as s:
        b1 = s.scalars(select(Widget.id).where(Widget.name == "b1")).one()
    with db.unit_of_work(PORTFOLIO_A) as s:
        assert get_or_404(s, Widget, b1).name == "b1"
    # Another tenant's project in the list widens nothing.
    other = TenancyContext(tenant_id=T1, allowed_project_ids=frozenset({PC}))
    assert _names(db, other) == {"t1-library"}


def test_allowed_projects_are_read_only(db: Database) -> None:
    with pytest.raises(OutOfScopeError), db.unit_of_work(PORTFOLIO_A) as s:
        s.add(Widget(tenant_id=T1, project_id=PB, name="smuggled"))
    with db.unit_of_work(PORTFOLIO_A) as s:
        b1 = s.scalars(select(Widget).where(Widget.name == "b1")).one()
        b1.name = "renamed"
        with pytest.raises(OutOfScopeError):
            s.flush()
        s.rollback()
    with db.unit_of_work(PORTFOLIO_A) as s:
        s.add(Widget(tenant_id=T1, project_id=PA, name="a3"))
    assert {"a3", "b1"} <= _names(db, PORTFOLIO_A)
    assert "smuggled" not in _names(db, PORTFOLIO_A)


def test_allowed_list_of_a_only_never_shows_b(db: Database) -> None:
    only_a = TenancyContext(tenant_id=T1, allowed_project_ids=frozenset({PA}))
    assert _names(db, only_a) == {"a1", "a2", "t1-library"}


# --- Asset-Site scoping (PER-07) ------------------------------------------------------------------


class SiteBase(DeclarativeBase):
    pass


SiteBase.metadata.naming_convention = NAMING_CONVENTION


class Inspection(SiteBase, BaseRecordMixin, SiteScopedMixin):
    __tablename__ = "test_site_inspection"

    name: Mapped[str] = mapped_column(String(50), nullable=False)


INSPECTOR = TenancyContext(tenant_id=T1, project_id=PA, site_node_ids=frozenset({S1}))


@pytest.fixture
def site_db(tmp_path: Path) -> Iterator[Database]:
    database = Database(database_url(tmp_path))
    SiteBase.metadata.create_all(database.engine)
    with database.engine.begin() as conn:
        apply_rls_policies(conn, SiteBase.metadata)
    try:
        with database.unit_of_work(CTX_A) as s:
            for name, site in (("on-s1", S1), ("on-s2", S2), ("unplaced", None)):
                s.add(Inspection(tenant_id=T1, project_id=PA, site_node_id=site, name=name))
        yield database
    finally:
        SiteBase.metadata.drop_all(database.engine)
        database.dispose()


def _inspections(db: Database, ctx: TenancyContext) -> dict[str, uuid.UUID]:
    with db.unit_of_work(ctx) as s:
        return {row.name: row.id for row in s.scalars(select(Inspection))}


def test_site_inspector_sees_only_their_sites_records(site_db: Database) -> None:
    every = _inspections(site_db, CTX_A)
    assert set(every) == {"on-s1", "on-s2", "unplaced"}
    assert set(_inspections(site_db, INSPECTOR)) == {"on-s1"}
    with site_db.unit_of_work(INSPECTOR) as s:
        with pytest.raises(OutOfScopeError):
            get_or_404(s, Inspection, every["on-s2"])
        assert get_or_404(s, Inspection, every["on-s1"]).name == "on-s1"


def test_site_inspector_cannot_write_another_sites_record(site_db: Database) -> None:
    with pytest.raises(OutOfScopeError), site_db.unit_of_work(INSPECTOR) as s:
        s.add(Inspection(tenant_id=T1, project_id=PA, site_node_id=S2, name="elsewhere"))
    with site_db.unit_of_work(INSPECTOR) as s:
        s.add(Inspection(tenant_id=T1, project_id=PA, site_node_id=S1, name="mine"))
    assert set(_inspections(site_db, INSPECTOR)) == {"on-s1", "mine"}


# --- HTTP: scope from the membership store, guards ------------------------------------------------


@pytest.fixture
def auth_db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def _grant(db: Database, user: uuid.UUID, project: uuid.UUID, persona: Persona) -> None:
    with db.unit_of_work(TenancyContext(tenant_id=AUTH_T1)) as s:
        site = S1 if persona is Persona.SITE_INSPECTOR else None
        s.add(
            ProjectMembership(
                tenant_id=AUTH_T1,
                user_id=user,
                member_project_id=project,
                persona=persona.value,
                site_node_id=site,
            )
        )


@pytest.fixture
def app_and_users(
    tmp_path: Path, auth_db: Database
) -> tuple[FastAPI, AuthRuntime, dict[str, uuid.UUID]]:
    app, runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    users = {"alice": seed_user(auth_db, AUTH_T1, ALICE), "bob": seed_user(auth_db, AUTH_T1, BOB)}
    _grant(auth_db, users["alice"], PA, Persona.OPERATIONS_MANAGEMENT)
    _grant(auth_db, users["alice"], PB, Persona.HSE_MANAGER)
    _grant(auth_db, users["bob"], PA, Persona.SITE_SUPERINTENDENT)

    @app.get("/_scope")
    def scope(session: DbSession) -> dict[str, list[str]]:
        bound = session_tenancy(session)
        assert bound is not None
        return {"allowed": sorted(map(str, bound.allowed_project_ids))}

    return app, runtime, users


def _bearer(client: TestClient, email: str) -> dict[str, str]:
    body = {"email": email, "password": PASSWORD}
    token = client.post("/api/v1/auth/login", json=body).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_request_binds_allowed_projects_from_memberships_only(
    app_and_users: tuple[FastAPI, AuthRuntime, dict[str, uuid.UUID]],
) -> None:
    with TestClient(app_and_users[0]) as client:
        alice = _bearer(client, ALICE)
        smuggle = {"allowed_project_ids": str(PC), "project_id": str(PC)}
        response = client.get("/_scope", params=smuggle, headers=alice)
        assert response.json() == {"allowed": sorted([str(PA), str(PB)])}
        bob = client.get("/_scope", headers=_bearer(client, BOB))
        assert bob.json() == {"allowed": []}


def _loaded_access(db: Database, user: uuid.UUID, project: uuid.UUID) -> ProjectAccess:
    """The user's access to ``project`` from the membership store (what a request loads)."""
    with db.unit_of_work(TenancyContext(tenant_id=AUTH_T1, user_id=user)) as s:
        return project_access(load_grants(s, AUTH_T1, user), project)


def test_capabilities_and_hold_release_follow_the_matrix_and_sod(
    app_and_users: tuple[FastAPI, AuthRuntime, dict[str, uuid.UUID]], auth_db: Database
) -> None:
    """The decisions the routers make with ``ProjectAccess`` (T-0217 removed the unused
    ``require_capability`` / ``require_hold_release`` guards; T-0010 matrix unchanged)."""
    users = app_and_users[2]
    _grant(auth_db, users["bob"], PB, Persona.PROJECT_ADMINISTRATOR)
    _grant(auth_db, users["alice"], PB, Persona.QAQC_LEAD)
    bob = _loaded_access(auth_db, users["bob"], PB)
    alice = _loaded_access(auth_db, users["alice"], PB)
    assert bob.has(Capability.ADMINISTER)
    assert not bob.can_release_hold(HoldKind.QUALITY)  # SoD
    assert not bob.can_release_hold(HoldKind.SAFETY)
    assert alice.can_release_hold(HoldKind.QUALITY)
    assert alice.can_release_hold(HoldKind.SAFETY)  # PER-11 on B
    assert not alice.has(Capability.ADMINISTER)
    with pytest.raises(OutOfScopeError):  # no membership on C: 404
        _loaded_access(auth_db, users["alice"], PC)


def test_project_viewer_reads_but_holds_no_write_capability(
    app_and_users: tuple[FastAPI, AuthRuntime, dict[str, uuid.UUID]], auth_db: Database
) -> None:
    """Capability matrix row for PER-16: ``V`` only, no hold release."""
    _grant(auth_db, app_and_users[2]["bob"], PB, Persona.PROJECT_VIEWER)
    try:
        viewer = _loaded_access(auth_db, app_and_users[2]["bob"], PB)
        assert {c.value: viewer.has(c) for c in Capability} == {
            "V": True,
            "C": False,
            "E": False,
            "A": False,
            "S": False,
            "admin": False,
        }
        assert not viewer.can_release_hold(HoldKind.QUALITY)
        assert not viewer.can_release_hold(HoldKind.SAFETY)
    finally:
        revoke_project_viewers(auth_db)  # the 0012 downgrade refuses while PER-16 is active


