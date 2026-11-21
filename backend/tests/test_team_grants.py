"""Team-derived project grants in core auth (T-0073, ADR 0008, diff §4.1 Teams).

Membership rows keyed by source (``direct`` | ``team_link``), the sync / revoke / read API
the teams module calls, ``load_grants`` as the union of both sources, separation of duties
across sources, the PER-09 member admin (derived rows read-only), and the startup check.

Runs on SQLite and, in CI's PostgreSQL step, against the migrated schema as ``kerp_app``.
Raw-SQL RLS checks: ``test_rls_team_grants.py``. All data is synthetic.
"""

import importlib.util
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app import main as app_main
from app.core.audit.service import chain_entries
from app.core.auth import access, memberships, team_grants
from app.core.auth.access import (
    HOLD_AUTHORITY,
    SYSTEM_ADMIN,
    Capability,
    Grant,
    Persona,
    load_grants,
    project_access,
    resolve_request_scope,
)
from app.core.auth.models import (
    SOURCE_DIRECT,
    SOURCE_TEAM_LINK,
    ProjectMembership,
)
from app.core.auth.team_grants import (
    LINK_PERSONAS,
    NotSystemAdminError,
    OrphanedTeamGrantsError,
    SodConfirmationRequired,
    check_team_grants_at_startup,
    live_team_link_grant_count,
    revoke_team_link_grants,
    sync_team_link_grants,
    team_link_grants,
)
from app.core.db import Base, Database, utcnow
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import rls_policy_sql, table_policy
from app.core.tenancy.scope import INCLUDE_DELETED, OutOfScopeError
from app.modules.admin import service as admin
from app.modules.admin.schemas import UserUpdate
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import ALEMBIC_INI, migrated_database
from tests.auth_support import (
    ALICE,
    BOB,
    PASSWORD,
    T1,
    auth_app,
    make_settings,
    revoke_project_viewers,
    seed_user,
)
from tests.db_support import ON_POSTGRES, database_url

ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)
CAROL = "carol@example.test"
DAVE = "dave@example.test"
SITE = uuid.UUID("00000000-0000-4000-8000-00000000051e")
P = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
Q = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
LINK = uuid.UUID("00000000-0000-4000-8000-00000000011a")
OTHER_LINK = uuid.UUID("00000000-0000-4000-8000-00000000011b")
TABLE = "auth_project_membership"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    migrated = migrated_database(tmp_path)
    database = next(migrated)
    try:
        yield database
    finally:
        revoke_project_viewers(database)  # the 0012 downgrade refuses while PER-16 is active
        next(migrated, None)


def _users(db: Database, *emails: str) -> list[uuid.UUID]:
    return [seed_user(db, T1, e) for e in emails]


def _direct(
    db: Database, user: uuid.UUID, project: uuid.UUID, persona: Persona, site: uuid.UUID | None
) -> None:
    with db.unit_of_work(ROOT) as s:
        held = memberships.project_grants(s, project).get(user, set())
        memberships.set_user_grants(s, project, user, {*held, Grant(project, persona, site)})


def _sync(
    db: Database,
    users: list[uuid.UUID],
    *,
    persona: Persona | str = Persona.TRADE_SUPERVISOR,
    project: uuid.UUID = P,
    site: uuid.UUID | None = None,
    link: uuid.UUID = LINK,
    confirm_sod: bool = False,
) -> None:
    with db.unit_of_work(ROOT) as s:
        sync_team_link_grants(
            s,
            link_id=link,
            project_id=project,
            persona=persona,
            site_node_id=site,
            user_ids=users,
            confirm_sod=confirm_sod,
        )


def _revoke(db: Database, link: uuid.UUID = LINK) -> None:
    with db.unit_of_work(ROOT) as s:
        revoke_team_link_grants(s, link_id=link)


def _rows(db: Database, *, deleted: bool = False) -> list[tuple[Any, ...]]:
    """Every membership row as (user, project, persona, site, source, link, live)."""
    query = select(ProjectMembership).execution_options(**{INCLUDE_DELETED: deleted})
    with db.unit_of_work(ROOT) as s:
        return sorted(
            (
                (
                    r.user_id,
                    r.member_project_id,
                    r.persona,
                    r.site_node_id,
                    r.source_type,
                    r.source_id,
                    r.deleted_at is None,
                )
                for r in s.scalars(query)
            ),
            key=str,
        )


def _grants(db: Database, user: uuid.UUID) -> list[Grant]:
    with db.unit_of_work(ROOT) as s:
        return load_grants(s, T1, user)


def _chain(db: Database, project: uuid.UUID) -> list[dict[str, Any]]:
    with db.unit_of_work(ROOT) as s:
        return [e.payload for e in chain_entries(s, f"membership:{project}")]


def _team(link: uuid.UUID = LINK) -> tuple[str, uuid.UUID]:
    return SOURCE_TEAM_LINK, link


# --- model, migration, policies -------------------------------------------------------------


def _migration() -> ModuleType:
    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    spec = importlib.util.spec_from_file_location("m0025", next(versions.glob("0025_*.py")))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0025_inlines_exactly_the_live_write_and_census_policies() -> None:
    policy = table_policy(Base.metadata.tables[TABLE])
    assert policy is not None
    assert policy.source_column == "source_type"
    live = {s for s in rls_policy_sql(TABLE, policy) if s.startswith("CREATE")}
    frozen = {s for s in _migration()._policies(sources=True) if s.startswith("CREATE")}
    assert frozen  # insert, update, delete and the census read
    assert frozen <= live
    assert live - frozen == {s for s in live if "membership_read_policy" in s}


def test_link_personas_are_all_but_the_individual_only_ones() -> None:
    assert frozenset(Persona) - LINK_PERSONAS == {
        Persona.PROJECT_ADMINISTRATOR,
        Persona.CLIENT_REPRESENTATIVE,
    }
    assert Persona.PROJECT_VIEWER in LINK_PERSONAS


def test_existing_rows_become_direct_and_the_downgrade_deletes_team_rows(tmp_path: Path) -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=database_url(tmp_path), configure_logger=False)
    command.upgrade(cfg, "0024")
    db = Database(database_url(tmp_path))
    try:
        [alice] = _users(db, ALICE)
        with db.unit_of_work(ROOT) as s:  # a row written before 0025, without the columns
            s.execute(
                text(
                    f"INSERT INTO {TABLE} (id, tenant_id, project_id, user_id, member_project_id,"
                    " persona, created_at, updated_at, custom_fields, source, revision) VALUES"
                    " (:id, :t, NULL, :u, :p, 'PER-04', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,"
                    " '{}', 'system', 1)"
                ),
                {"id": str(uuid.uuid4()), "t": str(T1), "u": str(alice), "p": str(P)},
            )
        command.upgrade(cfg, "head")
        [row] = _rows(db)
        assert row[4:6] == (SOURCE_DIRECT, None)
        _sync(db, [alice])  # the same persona through a team, beside the direct row
        assert len(_rows(db)) == 2
        command.downgrade(cfg, "0024")  # destructive: the team rows go first
        with db.unit_of_work(ROOT) as s:
            assert s.execute(text(f"SELECT persona FROM {TABLE}")).scalars().all() == ["PER-04"]
    finally:
        db.dispose()
        command.downgrade(cfg, "base")


def _raw_insert(
    db: Database,
    user: uuid.UUID,
    source_type: str,
    source_id: uuid.UUID | None,
    persona: str = "PER-04",
    ctx: TenancyContext = ROOT,
) -> None:
    with db.unit_of_work(ctx) as s:
        s.add(
            ProjectMembership(
                tenant_id=T1,
                user_id=user,
                member_project_id=P,
                persona=persona,
                source_type=source_type,
                source_id=source_id,
            )
        )


@pytest.mark.parametrize(
    ("source_type", "source_id"),
    [(SOURCE_DIRECT, LINK), (SOURCE_TEAM_LINK, None), ("access_request", LINK)],
)
def test_the_source_checks_reject_inconsistent_rows(
    db: Database, source_type: str, source_id: uuid.UUID | None
) -> None:
    [alice] = _users(db, ALICE)
    with pytest.raises(IntegrityError):
        _raw_insert(db, alice, source_type, source_id)


def test_one_live_persona_per_user_and_link_and_direct_rows_coexist(db: Database) -> None:
    [alice] = _users(db, ALICE)
    _raw_insert(db, alice, SOURCE_TEAM_LINK, LINK)
    _raw_insert(db, alice, SOURCE_DIRECT, None)  # same persona, direct: allowed beside it
    with pytest.raises(IntegrityError):  # a second live persona for the same user and link
        _raw_insert(db, alice, SOURCE_TEAM_LINK, LINK, persona="PER-05")
    _sync(db, [alice], persona=Persona.AREA_PLANNER)  # re-role: soft-delete, then insert
    assert {(r[2], r[4], r[6]) for r in _rows(db, deleted=True)} == {
        ("PER-04", SOURCE_DIRECT, True),
        ("PER-04", SOURCE_TEAM_LINK, False),
        ("PER-05", SOURCE_TEAM_LINK, True),
    }


@pytest.mark.skipif(not ON_POSTGRES, reason="SQLite treats NULLs as distinct (TD-052 CI step)")
def test_a_second_identical_direct_row_is_rejected_on_postgres(db: Database) -> None:
    [alice] = _users(db, ALICE)
    _raw_insert(db, alice, SOURCE_DIRECT, None)
    with pytest.raises(IntegrityError):
        _raw_insert(db, alice, SOURCE_DIRECT, None)


# --- sync, revoke, read ---------------------------------------------------------------------


def test_sync_adds_removes_re_roles_and_is_idempotent(db: Database) -> None:
    alice, bob, carol = _users(db, ALICE, BOB, CAROL)
    _direct(db, carol, P, Persona.AREA_PLANNER, None)
    _sync(db, [alice, bob])
    live = {(r[0], r[2], r[6]) for r in _rows(db) if r[4] == SOURCE_TEAM_LINK}
    assert live == {(alice, "PER-04", True), (bob, "PER-04", True)}
    _sync(db, [alice, bob])  # unchanged: nothing written
    _sync(db, [bob])  # alice leaves the team
    _sync(db, [bob], persona=Persona.PROJECT_VIEWER)  # the link is re-roled
    _sync(db, [bob], persona=Persona.PROJECT_VIEWER, project=Q)  # and re-pointed
    team = [r for r in _rows(db) if r[4] == SOURCE_TEAM_LINK]
    assert [(r[0], r[1], r[2], r[5]) for r in team] == [(bob, Q, "PER-16", LINK)]
    # The individual row was never touched.
    assert [r[:5] for r in _rows(db) if r[4] == SOURCE_DIRECT] == [
        (carol, P, "PER-05", None, SOURCE_DIRECT)
    ]
    entries = _chain(db, P)
    assert [(e["action"], e["added"], e["removed"]) for e in entries] == [
        ("team_link_sync", sorted([str(alice), str(bob)]), []),
        ("team_link_sync", [], [str(alice)]),
        ("team_link_sync", [str(bob)], [str(bob)]),
        ("team_link_sync", [], [str(bob)]),
    ]
    assert {e["link_id"] for e in entries} == {str(LINK)}
    [moved] = _chain(db, Q)
    assert moved["added"] == [str(bob)]
    assert moved["persona"] == "PER-16"


def test_the_audit_entry_carries_the_actor(db: Database) -> None:
    alice, bob = _users(db, ALICE, BOB)
    admin_ctx = TenancyContext(tenant_id=T1, user_id=alice, is_system_admin=True)
    with db.unit_of_work(admin_ctx) as s:
        sync_team_link_grants(
            s,
            link_id=LINK,
            project_id=P,
            persona="PER-04",
            site_node_id=None,
            user_ids=[bob],
        )
    with db.unit_of_work(ROOT) as s:
        [entry] = chain_entries(s, f"membership:{P}")
        assert entry.created_by == alice


def test_revoke_clears_the_link_and_leaves_individual_rows(db: Database) -> None:
    alice, bob = _users(db, ALICE, BOB)
    _direct(db, alice, P, Persona.TRADE_SUPERVISOR, None)
    _sync(db, [alice, bob])
    _sync(db, [bob], link=OTHER_LINK, persona=Persona.PROJECT_VIEWER, project=Q)
    _revoke(db)
    _revoke(db)  # nothing left: no second entry
    live = _rows(db)
    assert [(r[0], r[4]) for r in live if r[1] == P] == [(alice, SOURCE_DIRECT)]
    assert [(r[0], r[5]) for r in live if r[1] == Q] == [(bob, OTHER_LINK)]
    assert [e["action"] for e in _chain(db, P)] == ["team_link_sync", "team_link_revoke"]
    assert _chain(db, P)[-1]["removed"] == sorted([str(alice), str(bob)])


def test_removing_a_user_and_adding_them_back_restores_the_row(db: Database) -> None:
    alice, bob = _users(db, ALICE, BOB)
    _sync(db, [alice, bob])
    _sync(db, [bob])
    _sync(db, [alice, bob])
    _revoke(db)
    _sync(db, [alice, bob])
    rows = _rows(db, deleted=True)
    assert len(rows) == 2  # one row per user, revived each time
    assert all(r[6] for r in rows)


def test_team_link_grants_returns_every_link_with_live_rows(db: Database) -> None:
    alice, bob = _users(db, ALICE, BOB)
    orphan = uuid.uuid4()  # a link the teams tables would not know any more
    _sync(db, [alice, bob])
    _sync(db, [bob], link=orphan, persona=Persona.SITE_INSPECTOR, project=Q, site=SITE)
    _sync(db, [alice], link=OTHER_LINK)
    _revoke(db, OTHER_LINK)
    _direct(db, alice, Q, Persona.AREA_PLANNER, None)
    with db.unit_of_work(ROOT) as s:
        found = team_link_grants(s)
    assert found == {
        LINK: frozenset({(alice, P, "PER-04", None), (bob, P, "PER-04", None)}),
        orphan: frozenset({(bob, Q, "PER-07", SITE)}),
    }


@pytest.mark.parametrize(
    ("persona", "site", "message"),
    [
        (Persona.PROJECT_ADMINISTRATOR, None, "individual assignment only"),
        (Persona.CLIENT_REPRESENTATIVE, None, "individual assignment only"),
        ("PER-17", None, "PER-17"),
        (Persona.SITE_INSPECTOR, None, "needs a site_node_id"),
        (Persona.TRADE_SUPERVISOR, SITE, "not site-scoped"),
    ],
)
def test_a_link_role_is_validated_like_a_membership(
    db: Database, persona: Persona | str, site: uuid.UUID | None, message: str
) -> None:
    [alice] = _users(db, ALICE)
    with pytest.raises(ValueError, match=message):
        _sync(db, [alice], persona=persona, site=site)
    assert _rows(db) == []


def test_unknown_users_are_refused(db: Database) -> None:
    [alice] = _users(db, ALICE)
    with pytest.raises(ValueError, match="unknown users"):
        _sync(db, [alice, uuid.uuid4()])
    assert _rows(db) == []


def test_a_project_viewer_link_grants_view_only_on_its_project(db: Database) -> None:
    [alice] = _users(db, ALICE)
    _sync(db, [alice], persona=Persona.PROJECT_VIEWER)
    grants = _grants(db, alice)
    assert project_access(grants, P).capabilities == {Capability.VIEW}
    with pytest.raises(OutOfScopeError):
        project_access(grants, Q)


def test_only_system_admin_sessions_use_the_api(db: Database) -> None:
    alice, bob = _users(db, ALICE, BOB)
    _sync(db, [alice])
    for ctx in (
        TenancyContext(tenant_id=T1, user_id=bob),
        TenancyContext(tenant_id=T1, user_id=bob, member_project_ids=frozenset({P})),
    ):
        with db.unit_of_work(ctx) as s:
            with pytest.raises(NotSystemAdminError):
                sync_team_link_grants(
                    s, link_id=LINK, project_id=P, persona="PER-04", site_node_id=None, user_ids=[]
                )
            with pytest.raises(NotSystemAdminError):
                revoke_team_link_grants(s, link_id=LINK)
            with pytest.raises(NotSystemAdminError):
                team_link_grants(s)
    bound = TenancyContext(tenant_id=T1, project_id=P, is_system_admin=True)
    with db.unit_of_work(bound) as s, pytest.raises(RuntimeError, match="tenant-level"):
        team_link_grants(s)
    assert [r[0] for r in _rows(db)] == [alice]


def _change(db: Database, ctx: TenancyContext, user: uuid.UUID, **values: object) -> None:
    with db.unit_of_work(ctx) as s:
        [row] = s.scalars(select(ProjectMembership).where(ProjectMembership.user_id == user))
        for name, value in values.items():
            setattr(row, name, value)


def test_the_write_guard_keeps_other_sessions_off_derived_rows(db: Database) -> None:
    """The ORM mirror of the RLS write rule: a PER-09 session cannot insert, change or delete
    a team-derived row on its own project (SQLite has no RLS; PostgreSQL has both)."""
    alice, bob = _users(db, ALICE, BOB)
    _direct(db, bob, P, Persona.PROJECT_ADMINISTRATOR, None)
    _sync(db, [alice])
    per09 = TenancyContext(tenant_id=T1, user_id=bob, member_project_ids=frozenset({P}))
    with pytest.raises(OutOfScopeError):
        _raw_insert(db, bob, SOURCE_TEAM_LINK, OTHER_LINK, persona="PER-06", ctx=per09)
    with pytest.raises(OutOfScopeError):
        _change(db, per09, alice, persona="PER-05")
    with pytest.raises(OutOfScopeError):
        _change(db, per09, alice, deleted_at=utcnow())
    with pytest.raises(OutOfScopeError):  # nor turn an individual row into a derived one
        _change(db, per09, bob, source_type=SOURCE_TEAM_LINK, source_id=OTHER_LINK)
    with pytest.raises(memberships.TeamGrantsPresentError), db.unit_of_work(per09) as s:
        memberships.revoke_project(s, P)
    assert {(r[0], r[2], r[4]) for r in _rows(db)} == {
        (alice, "PER-04", SOURCE_TEAM_LINK),
        (bob, "PER-09", SOURCE_DIRECT),
    }


# --- separation of duties across sources ---------------------------------------------------


@pytest.mark.parametrize("persona", sorted(HOLD_AUTHORITY))
def test_a_hold_link_for_an_individual_per09_needs_confirmation(
    db: Database, persona: Persona
) -> None:
    alice, bob, carol = _users(db, ALICE, BOB, CAROL)
    _direct(db, alice, P, Persona.PROJECT_ADMINISTRATOR, None)
    _direct(db, carol, Q, Persona.PROJECT_ADMINISTRATOR, None)  # PER-09 elsewhere only
    site = SITE if persona is Persona.SITE_INSPECTOR else None
    with pytest.raises(SodConfirmationRequired) as refused:
        _sync(db, [alice, bob, carol], persona=persona, site=site)
    assert refused.value.user_ids == {alice}
    assert refused.value.project_id == P
    assert [r for r in _rows(db) if r[4] == SOURCE_TEAM_LINK] == []
    assert _chain(db, P) == []
    _sync(db, [alice, bob, carol], persona=persona, site=site, confirm_sod=True)
    [entry] = _chain(db, P)
    assert entry["sod_warning"] is True
    assert entry["sod_user_ids"] == [str(alice)]
    # Re-syncing the confirmed rows (another member added) asks for nothing new.
    [dave] = _users(db, DAVE)
    _sync(db, [alice, bob, carol, dave], persona=persona, site=site)
    assert _chain(db, P)[-1]["sod_warning"] is False


@pytest.mark.parametrize("persona", sorted(HOLD_AUTHORITY))
def test_a_hold_link_for_a_system_admin_needs_confirmation(db: Database, persona: Persona) -> None:
    """ADR 0016, diff §4.1 (T-0275): a derived hold persona reaching a ``system_admin`` (no
    PER-09 anywhere) warns like one reaching an individual PER-09."""
    alice, bob = _users(db, ALICE, BOB)
    with db.unit_of_work(ROOT) as s:
        assert admin.grant(s, alice, SYSTEM_ADMIN, actor_id=None)
    site = SITE if persona is Persona.SITE_INSPECTOR else None
    with pytest.raises(SodConfirmationRequired) as refused:
        _sync(db, [alice, bob], persona=persona, site=site)
    assert (refused.value.user_ids, refused.value.project_id) == ({alice}, P)
    assert [r for r in _rows(db) if r[4] == SOURCE_TEAM_LINK] == []
    assert _chain(db, P) == []
    _sync(db, [alice, bob], persona=persona, site=site, confirm_sod=True)
    [entry] = _chain(db, P)
    assert (entry["sod_warning"], entry["sod_user_ids"]) == (True, [str(alice)])
    # A non-hold link for the same admin warns about nothing.
    _sync(db, [alice], persona=Persona.PROJECT_VIEWER, link=OTHER_LINK)
    assert _chain(db, P)[-1]["sod_warning"] is False


def test_a_link_without_hold_authority_needs_no_confirmation(db: Database) -> None:
    [alice] = _users(db, ALICE)
    _direct(db, alice, P, Persona.PROJECT_ADMINISTRATOR, None)
    _sync(db, [alice], persona=Persona.PROJECT_VIEWER)
    assert _chain(db, P)[0]["sod_warning"] is False


# --- effective access: the union of both sources --------------------------------------------


def test_a_derived_grant_counts_like_an_individual_one(db: Database) -> None:
    alice, bob, carol = _users(db, ALICE, BOB, CAROL)
    _sync(db, [alice])  # PER-04 on P
    _sync(db, [bob], link=OTHER_LINK, persona=Persona.OPERATIONS_MANAGEMENT)  # portfolio
    inspector = uuid.uuid4()
    _sync(db, [carol], link=inspector, persona=Persona.SITE_INSPECTOR, site=SITE)
    mine = project_access(_grants(db, alice), P)
    assert mine.personas == {Persona.TRADE_SUPERVISOR}
    with pytest.raises(OutOfScopeError):
        project_access(_grants(db, alice), Q)
    bob_scope = resolve_request_scope(db, TenancyContext(tenant_id=T1, user_id=bob))
    assert bob_scope.allowed_project_ids == {P}
    assert bob_scope.member_project_ids == {P}
    carol_scope = access.resolve_scope(
        _grants(db, carol), TenancyContext(tenant_id=T1, user_id=carol), P
    )
    assert carol_scope.site_node_ids == {SITE}


def test_individual_and_derived_grants_union_and_outlive_each_other(db: Database) -> None:
    [alice] = _users(db, ALICE)
    _direct(db, alice, P, Persona.TRADE_SUPERVISOR, None)
    _sync(db, [alice])
    assert _grants(db, alice) == [Grant(P, Persona.TRADE_SUPERVISOR)]  # duplicates collapse
    _revoke(db)
    assert project_access(_grants(db, alice), P).personas == {Persona.TRADE_SUPERVISOR}
    _sync(db, [alice])
    with db.unit_of_work(ROOT) as s:
        memberships.set_user_grants(s, P, alice, ())  # the individual row goes
    assert project_access(_grants(db, alice), P).personas == {Persona.TRADE_SUPERVISOR}
    _revoke(db)
    assert _grants(db, alice) == []


def test_deactivated_members_keep_their_rows_and_regain_access_on_reactivation(
    db: Database,
) -> None:
    alice, bob, carol = _users(db, ALICE, BOB, CAROL)
    _sync(db, [bob])
    with db.unit_of_work(ROOT) as s:
        admin.update_user(s, bob, UserUpdate(is_active=False), actor_id=alice)
    _sync(db, [bob, carol])  # the sync stores whom it is given, whatever their status
    assert {r[0] for r in _rows(db)} == {bob, carol}
    with db.unit_of_work(ROOT) as s:
        admin.update_user(s, bob, UserUpdate(is_active=True), actor_id=alice)
    assert project_access(_grants(db, bob), P).personas == {Persona.TRADE_SUPERVISOR}
    assert len(_rows(db, deleted=True)) == 2  # bob's row was never soft-deleted


# --- startup check --------------------------------------------------------------------------


class _WithoutTeams:
    """The module registry as start-up sees it, minus the teams module (T-0013 ships it, so
    a deployment without it is simulated)."""

    def __init__(self, registry: Any) -> None:
        self._names = tuple(n for n in registry.names() if n != team_grants.TEAMS_MODULE)

    def names(self) -> tuple[str, ...]:
        return self._names


def _start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch | None = None) -> None:
    """Start the app; with ``monkeypatch`` as if the teams module were not loaded."""
    if monkeypatch is not None:
        real = app_main.registry_of
        monkeypatch.setattr(app_main, "registry_of", lambda a: _WithoutTeams(real(a)))
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    try:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
    finally:
        app.state.db.dispose()  # a refused start-up never reaches the lifespan's dispose
        if monkeypatch is not None:
            monkeypatch.undo()


def test_startup_refuses_live_derived_rows_without_the_teams_module(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _start(tmp_path, monkeypatch)  # no derived rows: starts
    [alice] = _users(db, ALICE)
    _sync(db, [alice])
    assert live_team_link_grant_count(db) == 1
    with pytest.raises(OrphanedTeamGrantsError, match="revoke_team_link_grants"):
        _start(tmp_path, monkeypatch)
    _start(tmp_path)  # the teams module is loaded (T-0013): it maintains them, so it starts
    check_team_grants_at_startup(db, ("projects", team_grants.TEAMS_MODULE))  # loaded: fine
    check_team_grants_at_startup(None, ())
    _revoke(db)
    assert live_team_link_grant_count(db) == 0
    _start(tmp_path, monkeypatch)


def test_the_count_is_zero_on_a_schema_without_source_columns(tmp_path: Path) -> None:
    db = Database(database_url(tmp_path))
    try:
        assert live_team_link_grant_count(db) == 0  # nothing migrated: no table
    finally:
        db.dispose()


# --- PER-09 member administration (T-0014 API) ---------------------------------------------


class Api:
    """ALICE: System admin. BOB: PER-09 of HOME. CAROL: team-derived PER-06 on HOME.
    DAVE: individual PER-04 and team-derived PER-04 on HOME."""

    def __init__(self, client: TestClient, db: Database) -> None:
        self.client, self.db = client, db
        self.users = dict(
            zip((ALICE, BOB, CAROL, DAVE), _users(db, ALICE, BOB, CAROL, DAVE), strict=True)
        )
        with db.unit_of_work(ROOT) as s:
            assert admin.grant(s, self.users[ALICE], SYSTEM_ADMIN, actor_id=None)
            self.home = projects.create_project(s, ProjectCreate(code="HOME", name="Home")).id
            self.team_only = projects.create_project(s, ProjectCreate(code="TEAM", name="T")).id
        _direct(db, self.users[BOB], self.home, Persona.PROJECT_ADMINISTRATOR, None)
        _direct(db, self.users[DAVE], self.home, Persona.TRADE_SUPERVISOR, None)
        _sync(db, [self.users[CAROL]], persona=Persona.QAQC_LEAD, project=self.home)
        _sync(db, [self.users[DAVE]], link=OTHER_LINK, project=self.home)
        self._headers: dict[str, dict[str, str]] = {}

    def headers(self, email: str) -> dict[str, str]:
        if email not in self._headers:
            body = {"email": email, "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            self._headers[email] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._headers[email]

    def url(self, user: str) -> str:
        return f"/api/v1/admin/projects/{self.home}/members/{self.users[user]}"


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        yield Api(client, db)


def test_the_member_list_marks_team_derived_grants(api: Api) -> None:
    listed = api.client.get(f"/api/v1/admin/projects/{api.home}/members", headers=api.headers(BOB))
    assert listed.status_code == 200
    by_email = {m["email"]: m["grants"] for m in listed.json()}
    assert by_email[CAROL] == [
        {
            "persona": "PER-06",
            "site_node_id": None,
            "source": "team",
            "source_id": str(LINK),
            "team_name": None,
        }
    ]
    assert [(g["source"], g["source_id"]) for g in by_email[DAVE]] == [
        ("direct", None),
        ("team", str(OTHER_LINK)),
    ]


def test_a_project_administrator_cannot_remove_team_derived_access(api: Api) -> None:
    bob = api.headers(BOB)
    refused = api.client.delete(api.url(CAROL), headers=bob)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "managed_through_team"
    assert refused.json()["detail"]["link_ids"] == [str(LINK)]
    # Removing DAVE removes the individual row only; the team row stays.
    assert api.client.delete(api.url(DAVE), headers=bob).status_code == 204
    assert api.client.delete(api.url(DAVE), headers=bob).status_code == 409
    with api.db.unit_of_work(ROOT) as s:
        dave = memberships.project_member_grants(s, api.home)[api.users[DAVE]]
    assert [(g.source_type, g.source_id) for g in dave] == [(SOURCE_TEAM_LINK, OTHER_LINK)]


def test_an_individual_persona_also_held_through_a_team_gets_its_own_row(api: Api) -> None:
    body = {"grants": [{"persona": "PER-06"}]}
    put = api.client.put(api.url(CAROL), json=body, headers=api.headers(BOB))
    assert put.status_code == 200, put.text
    assert [(g["persona"], g["source"]) for g in put.json()["grants"]] == [
        ("PER-06", "direct"),
        ("PER-06", "team"),
    ]
    _revoke(api.db)  # the team goes: the individual row stays
    assert project_access(_grants(api.db, api.users[CAROL]), api.home).personas == {
        Persona.QAQC_LEAD
    }


def test_individual_per09_for_a_team_derived_hold_persona_needs_confirmation(api: Api) -> None:
    bob, body = api.headers(BOB), {"grants": [{"persona": "PER-09"}]}
    refused = api.client.put(api.url(CAROL), json=body, headers=bob)
    assert refused.status_code == 409
    assert refused.json()["detail"]["sod_warning"] is True
    with api.db.unit_of_work(ROOT) as s:
        assert [e.payload for e in chain_entries(s, f"project-members:{api.home}")] == []
    confirmed = api.client.put(api.url(CAROL), json={**body, "confirm_sod": True}, headers=bob)
    assert confirmed.status_code == 200
    assert confirmed.json()["sod_warning"] is True
    with api.db.unit_of_work(ROOT) as s:
        [entry] = [e.payload for e in chain_entries(s, f"project-members:{api.home}")]
    assert entry["sod_warning"] is True
    assert entry["user_id"] == str(api.users[CAROL])


def test_me_projects_includes_projects_reached_only_through_a_team(api: Api) -> None:
    _sync(api.db, [api.users[CAROL]], link=uuid.uuid4(), project=api.team_only)
    mine = api.client.get("/api/v1/me/projects", headers=api.headers(CAROL)).json()
    assert [(p["code"], p["personas"]) for p in mine] == [
        ("HOME", ["PER-06"]),
        ("TEAM", ["PER-04"]),
    ]


def test_a_project_with_team_derived_members_cannot_be_deleted(api: Api) -> None:
    deleted = api.client.delete(f"/api/v1/projects/{api.home}", headers=api.headers(BOB))
    assert deleted.status_code == 409
    assert deleted.json()["detail"]["code"] == "team_links_present"
    with api.db.unit_of_work(ROOT) as s:
        for link in team_link_grants(s):
            revoke_team_link_grants(s, link_id=link)
    deleted = api.client.delete(f"/api/v1/projects/{api.home}", headers=api.headers(BOB))
    assert deleted.status_code == 204
