"""Teams reconciliation (T-0075, ADR 0008 "Reconciliation", diff §4.1 Teams).

``teams.reconcile`` compares the expected team-derived tuples (every live link of a live team
x its live members, deactivated accounts included) with core's ``team_link_grants`` and
reports missing, excess and orphan drift; ``fix=True`` repairs it through the T-0073 API, with
the SoD rule that a missing hold-authority tuple over an individual PER-09 stays
``sod_pending`` until ``confirm_sod``. Also the ``python -m app.modules.teams.reconcile``
command. Runs on SQLite and, in CI's PostgreSQL step, against the migrated schema as
``kerp_app``. All data is synthetic.
"""

import io
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.core.audit.service import chain_entries
from app.core.auth import memberships
from app.core.auth.access import SYSTEM_ADMIN, Grant, Persona
from app.core.auth.models import SOURCE_TEAM_LINK, ProjectMembership
from app.core.auth.team_grants import NotSystemAdminError, sync_team_link_grants, team_link_grants
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.modules.admin import service as admin
from app.modules.admin.schemas import UserUpdate
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.teams import reconcile as cli
from app.modules.teams import service as teams
from app.modules.teams.models import TeamMember, TeamProjectLink
from app.modules.teams.schemas import TeamCreate
from app.modules.teams.service import ReconcileReport
from tests.audit_support import migrated_database
from tests.auth_support import T1, make_settings, revoke_project_viewers, seed_user
from tests.db_support import database_url

ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    migrated = migrated_database(tmp_path)
    database = next(migrated)
    try:
        yield database
    finally:
        revoke_project_viewers(database)  # the 0012 downgrade refuses while PER-16 is active
        next(migrated, None)


@dataclass
class World:
    """A team with ERIN and FRANK, linked to P as PER-04 (no drift yet)."""

    db: Database
    p: uuid.UUID
    q: uuid.UUID
    erin: uuid.UUID
    frank: uuid.UUID
    team: uuid.UUID
    link: uuid.UUID

    def reconcile(self, *, fix: bool = False, confirm_sod: bool = False) -> ReconcileReport:
        with self.db.unit_of_work(ROOT) as s:
            return teams.reconcile(s, fix=fix, confirm_sod=confirm_sod)

    def stored(self) -> dict[uuid.UUID, frozenset[Any]]:
        with self.db.unit_of_work(ROOT) as s:
            return dict(team_link_grants(s))

    def membership_chain(self, project: uuid.UUID) -> list[dict[str, Any]]:
        with self.db.unit_of_work(ROOT) as s:
            return [e.payload for e in chain_entries(s, f"membership:{project}")]

    def edit_row(self, user: uuid.UUID, **fields: Any) -> None:
        """Change the user's derived row of the link behind the sync's back."""
        with self.db.unit_of_work(ROOT) as s:
            row = s.scalars(
                select(ProjectMembership).where(
                    ProjectMembership.source_type == SOURCE_TEAM_LINK,
                    ProjectMembership.user_id == user,
                )
            ).one()
            for name, value in fields.items():
                setattr(row, name, value)

    def drop_row(self, user: uuid.UUID) -> None:
        with self.db.unit_of_work(ROOT) as s:
            row = s.scalars(
                select(ProjectMembership).where(
                    ProjectMembership.source_type == SOURCE_TEAM_LINK,
                    ProjectMembership.user_id == user,
                )
            ).one()
            row.soft_delete()

    def tuple(self, user: uuid.UUID, persona: str = "PER-04") -> tuple[Any, ...]:
        return (user, self.p, persona, None)


@pytest.fixture
def world(db: Database) -> World:
    erin = seed_user(db, T1, "erin@example.test")
    frank = seed_user(db, T1, "frank@example.test")
    with db.unit_of_work(ROOT) as s:
        p = projects.create_project(s, ProjectCreate(code="RC-P", name="Synthetic P")).id
        q = projects.create_project(s, ProjectCreate(code="RC-Q", name="Synthetic Q")).id
        team = teams.create_team(s, TeamCreate(name="Reconcilers"), actor_id=None).id
        teams.add_member(s, team, erin, confirm_sod=False, actor_id=None)
        teams.add_member(s, team, frank, confirm_sod=False, actor_id=None)
        link, _ = teams.set_link(
            s, team, p, Persona.TRADE_SUPERVISOR, None, confirm_sod=False, actor_id=None
        )
        link_id = link.id
    return World(db, p, q, erin, frank, team, link_id)


def test_a_clean_state_reports_no_drift(world: World) -> None:
    report = world.reconcile()
    assert not report.drift
    assert report.links == ()
    assert not report.unresolved
    assert world.reconcile(fix=True).links == ()


def test_a_persona_mismatch_is_excess_plus_missing_and_fix_restores_the_persona(
    world: World,
) -> None:
    world.edit_row(world.erin, persona=Persona.PROJECT_MANAGER.value)  # PER-02 behind its back
    report = world.reconcile()
    (drift,) = report.links
    assert drift.link_id == world.link
    assert not drift.orphan
    assert drift.missing == {world.tuple(world.erin)}
    assert drift.excess == {world.tuple(world.erin, "PER-02")}
    assert drift.sod_pending == frozenset()
    assert not drift.repaired
    assert report.unresolved
    assert world.stored()[world.link] == {  # report-only changes nothing
        world.tuple(world.erin, "PER-02"),
        world.tuple(world.frank),
    }

    fixed = world.reconcile(fix=True)
    (drift,) = fixed.links
    assert drift.repaired
    assert drift.added == {world.tuple(world.erin)}
    assert drift.removed == {world.tuple(world.erin, "PER-02")}
    assert not fixed.unresolved
    assert world.stored()[world.link] == {world.tuple(world.erin), world.tuple(world.frank)}
    entry = world.membership_chain(world.p)[-1]
    assert entry["action"] == "team_link_sync"
    assert entry["reason"] == "reconcile"
    assert entry["link_id"] == str(world.link)
    assert entry["sod_warning"] is False


def test_a_wrong_project_or_site_is_excess(world: World) -> None:
    world.edit_row(world.frank, member_project_id=world.q)
    (drift,) = world.reconcile().links
    assert drift.excess == {(world.frank, world.q, "PER-04", None)}
    assert drift.missing == {world.tuple(world.frank)}
    world.reconcile(fix=True)
    assert world.stored()[world.link] == {world.tuple(world.erin), world.tuple(world.frank)}


def test_a_removed_member_still_holding_a_row_is_excess(world: World) -> None:
    with world.db.unit_of_work(ROOT) as s:
        member = s.scalars(select(TeamMember).where(TeamMember.user_id == world.frank)).one()
        member.soft_delete()  # removed without a sync
    (drift,) = world.reconcile().links
    assert drift.excess == {world.tuple(world.frank)}
    assert drift.missing == frozenset()
    world.reconcile(fix=True)
    assert world.stored()[world.link] == {world.tuple(world.erin)}


@pytest.mark.parametrize("kind", ["unknown_link", "deleted_link", "deleted_team"])
def test_an_orphan_link_id_is_reported_and_fix_revokes_it(world: World, kind: str) -> None:
    orphan = uuid.UUID("00000000-0000-4000-8000-00000000099a")
    with world.db.unit_of_work(ROOT) as s:
        if kind == "unknown_link":
            sync_team_link_grants(
                s,
                link_id=orphan,
                project_id=world.q,
                persona=Persona.AREA_PLANNER,
                site_node_id=None,
                user_ids=[world.erin],
            )
        elif kind == "deleted_link":
            orphan = world.link
            s.get_one(TeamProjectLink, world.link).soft_delete()  # rows left behind
        else:
            orphan = world.link
            team = teams.get_team(s, world.team)
            team.soft_delete()  # the link row stays live, its team is gone
    report = world.reconcile()
    assert report.orphan_link_ids == (orphan,)
    (drift,) = [d for d in report.links if d.link_id == orphan]
    assert drift.orphan
    assert drift.missing == frozenset()
    assert drift.excess == world.stored()[orphan]
    assert report.unresolved

    fixed = world.reconcile(fix=True)
    (drift,) = [d for d in fixed.links if d.link_id == orphan]
    assert drift.repaired
    assert drift.removed == drift.excess
    assert orphan not in world.stored()
    assert not fixed.unresolved
    project = world.q if kind == "unknown_link" else world.p
    with world.db.unit_of_work(ROOT) as s:  # the user lost that access
        assert world.erin not in memberships.project_member_grants(s, project)
    entry = world.membership_chain(project)[-1]
    assert entry["action"] == "team_link_revoke"
    assert entry["reason"] == "reconcile"
    assert world.reconcile().links == ()


def test_a_missing_tuple_is_reported_and_fixed(world: World) -> None:
    dave = seed_user(world.db, T1, "dave@example.test")
    with world.db.unit_of_work(ROOT) as s:
        s.add(TeamMember(tenant_id=T1, project_id=None, team_id=world.team, user_id=dave))
    (drift,) = world.reconcile().links
    assert drift.missing == {world.tuple(dave)}
    assert drift.excess == frozenset()
    fixed = world.reconcile(fix=True)
    assert fixed.links[0].added == {world.tuple(dave)}
    assert world.tuple(dave) in world.stored()[world.link]


def test_running_twice_gives_the_same_result_and_a_second_fix_finds_nothing(
    world: World,
) -> None:
    world.drop_row(world.erin)
    first, second = world.reconcile(), world.reconcile()
    assert first == second
    world.reconcile(fix=True)
    assert world.reconcile(fix=True).links == ()
    assert world.reconcile().links == ()


def test_a_non_system_admin_session_is_refused(world: World) -> None:
    plain = TenancyContext(tenant_id=T1, user_id=world.erin)
    with world.db.unit_of_work(plain) as s, pytest.raises(NotSystemAdminError):
        teams.reconcile(s)
    bound = TenancyContext(tenant_id=T1, project_id=world.p, is_system_admin=True)
    with world.db.unit_of_work(bound) as s, pytest.raises(RuntimeError, match="tenant-level"):
        teams.reconcile(s, fix=True)


def test_an_unconfirmed_sod_combination_stays_pending_until_confirm_sod(world: World) -> None:
    with world.db.unit_of_work(ROOT) as s:  # the link becomes PER-06 (hold authority)
        teams.set_link(
            s, world.team, world.p, Persona.QAQC_LEAD, None, confirm_sod=False, actor_id=None
        )
    world.drop_row(world.erin)  # her derived row goes missing ...
    world.drop_row(world.frank)
    with world.db.unit_of_work(ROOT) as s:  # ... while she gets individual PER-09 on P
        memberships.set_user_grants(
            s, world.p, world.erin, {Grant(world.p, Persona.PROJECT_ADMINISTRATOR, None)}
        )
    erin_t, frank_t = world.tuple(world.erin, "PER-06"), world.tuple(world.frank, "PER-06")

    (drift,) = world.reconcile().links
    assert drift.missing == {erin_t, frank_t}
    assert drift.sod_pending == {erin_t}

    fixed = world.reconcile(fix=True)  # the rest of the link is still repaired
    (drift,) = fixed.links
    assert drift.sod_pending == {erin_t}
    assert drift.added == {frank_t}
    assert drift.remaining == {erin_t}
    assert fixed.unresolved
    assert world.stored()[world.link] == {frank_t}
    assert world.membership_chain(world.p)[-1]["sod_warning"] is False

    confirmed = world.reconcile(fix=True, confirm_sod=True)
    (drift,) = confirmed.links
    assert drift.added == {erin_t}
    assert not confirmed.unresolved
    assert world.stored()[world.link] == {erin_t, frank_t}
    entry = world.membership_chain(world.p)[-1]
    assert entry["sod_warning"] is True
    assert entry["sod_user_ids"] == [str(world.erin)]
    assert entry["reason"] == "reconcile"
    assert world.reconcile().links == ()


def test_a_missing_hold_row_of_a_system_admin_stays_pending_until_confirm_sod(
    world: World,
) -> None:
    """ADR 0016 (T-0275): a missing derived hold persona for a ``system_admin`` (no PER-09
    anywhere) is ``sod_pending`` like one for an individual PER-09."""
    with world.db.unit_of_work(ROOT) as s:  # the link becomes PER-06 (hold authority)
        teams.set_link(
            s, world.team, world.p, Persona.QAQC_LEAD, None, confirm_sod=False, actor_id=None
        )
    world.drop_row(world.erin)  # her derived row goes missing ...
    with world.db.unit_of_work(ROOT) as s:  # ... while she becomes a System admin
        assert admin.grant(s, world.erin, SYSTEM_ADMIN, actor_id=None)
    erin_t, frank_t = world.tuple(world.erin, "PER-06"), world.tuple(world.frank, "PER-06")

    (drift,) = world.reconcile().links
    assert drift.missing == {erin_t}
    assert drift.sod_pending == {erin_t}
    fixed = world.reconcile(fix=True)
    (drift,) = fixed.links
    assert (drift.sod_pending, drift.remaining, drift.added) == ({erin_t}, {erin_t}, frozenset())
    assert fixed.unresolved
    assert world.stored()[world.link] == {frank_t}  # not written without confirm_sod

    confirmed = world.reconcile(fix=True, confirm_sod=True)
    assert confirmed.links[0].added == {erin_t}
    entry = world.membership_chain(world.p)[-1]
    assert (entry["sod_warning"], entry["sod_user_ids"]) == (True, [str(world.erin)])


def test_a_deactivated_members_row_is_neither_excess_nor_removed(world: World) -> None:
    with world.db.unit_of_work(ROOT) as s:
        admin.update_user(s, world.erin, UserUpdate(is_active=False), actor_id=uuid.uuid4())
    assert world.reconcile().links == ()
    assert world.reconcile(fix=True).links == ()
    assert world.tuple(world.erin) in world.stored()[world.link]
    world.drop_row(world.erin)  # and a deactivated member's missing row is restored
    (drift,) = world.reconcile(fix=True).links
    assert drift.added == {world.tuple(world.erin)}


def test_a_link_to_a_deleted_project_expects_nothing(world: World) -> None:
    """Like the sync (README, "Links to a deleted project"), such a link confers nothing:
    rows it still holds are excess and the fix revokes them."""
    with world.db.unit_of_work(ROOT) as s:
        link, _ = teams.set_link(
            s, world.team, world.q, Persona.AREA_PLANNER, None, confirm_sod=False, actor_id=None
        )
        q_link = link.id
        rows = s.scalars(
            select(ProjectMembership).where(ProjectMembership.source_id == q_link)
        ).all()
        for row in rows:
            row.soft_delete()  # let the project delete through, then bring the rows back
        s.flush()
        projects.delete_project(s, world.q, actor_id=None)
        for row in rows:
            row.deleted_at = None
    (drift,) = world.reconcile().links
    assert drift.link_id == q_link
    assert not drift.orphan
    assert drift.excess == {
        (world.erin, world.q, "PER-05", None),
        (world.frank, world.q, "PER-05", None),
    }
    assert drift.missing == frozenset()
    world.reconcile(fix=True)
    assert q_link not in world.stored()
    assert world.reconcile().links == ()


# --- command line -----------------------------------------------------------------------------


def _main(tmp_path: Path, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    settings = make_settings(database_url=database_url(tmp_path))
    code = cli.main(list(argv), settings=settings, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_the_command_reports_drift_and_exits_non_zero_until_fixed(
    world: World, tmp_path: Path
) -> None:
    code, out, _err = _main(tmp_path)
    assert (code, out.strip()) == (0, "no drift")
    world.edit_row(world.erin, persona=Persona.PROJECT_MANAGER.value)
    code, out, _err = _main(tmp_path)
    assert code == 1
    assert f"link {world.link}" in out
    assert f"excess {world.erin} {world.p} PER-02" in out
    assert f"missing {world.erin} {world.p} PER-04" in out
    assert "not fixed" in out
    code, out, _err = _main(tmp_path, "--fix")
    assert code == 0
    assert "repaired" in out
    assert _main(tmp_path)[:2] == (0, "no drift\n")


def test_the_command_leaves_sod_pending_without_confirm_sod(world: World, tmp_path: Path) -> None:
    with world.db.unit_of_work(ROOT) as s:
        teams.set_link(
            s, world.team, world.p, Persona.QAQC_LEAD, None, confirm_sod=False, actor_id=None
        )
    world.drop_row(world.erin)
    with world.db.unit_of_work(ROOT) as s:
        memberships.set_user_grants(
            s, world.p, world.erin, {Grant(world.p, Persona.PROJECT_ADMINISTRATOR, None)}
        )
    code, out, _err = _main(tmp_path, "--fix")
    assert code == 1
    assert f"sod_pending {world.erin} {world.p} PER-06" in out
    assert "--confirm-sod" in out
    code, out, _err = _main(tmp_path, "--fix", "--confirm-sod")
    assert code == 0


def test_the_command_needs_a_database_and_a_tenant(tmp_path: Path) -> None:
    err = io.StringIO()
    code = cli.main([], settings=make_settings(database_url=None), out=io.StringIO(), err=err)
    assert code == 2
    assert "KERP_DATABASE_URL" in err.getvalue()
    settings = make_settings(database_url=database_url(tmp_path), default_tenant_id=None)
    err = io.StringIO()
    assert cli.main([], settings=settings, out=io.StringIO(), err=err) == 2
    assert "--tenant-id" in err.getvalue()
    with pytest.raises(SystemExit):
        cli.main(["--confirm-sod"], settings=settings, out=io.StringIO(), err=io.StringIO())
