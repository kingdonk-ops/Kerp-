"""Teams service (T-0013, ADR 0008, diff §4.1 Teams). Authorisation is the router's; this
service writes, keeps the derived grants in step and audits.

**Writes** need a tenant-level ``system_admin`` session (the ``team`` RLS policy and the ORM
write guard refuse anyone else). Every member add/remove, link create/re-role/remove and team
delete re-syncs the derived project grants of each affected link through
``app.core.auth.team_grants`` (T-0073) in the same transaction. The sync is always fed
**every live member of the team, whatever their account status**: a deactivated member keeps
their derived rows (they grant nothing while the account cannot sign in) and gets the access
back on reactivation with no re-sync. A link whose project has been deleted (an empty team's
link does not stop the delete) is never synced: any rows it holds are revoked, and it stays
removable by ``unlink`` (README, "Derived access"). Each change is audited on chain
``team:<id>`` with the actor; an idempotent no-op writes nothing. The derived-row changes
are audited by core on ``membership:<project>``.

**Separation of duties** (ADR 0008): a change that would give a hold-authority persona
(PER-02, PER-06, PER-07, PER-11) on a linked project to a user who holds individual PER-09
there raises ``SodConfirmationError`` (409 ``sod_warning``) before anything is written,
unless ``confirm_sod``; a confirmed change is audited with ``sod_warning: true``.

**Read contract** (the module's public API for other modules; see ``README.md``): soft-deleted
teams, members and links are ignored, and a member counts only while their account is active.
It answers what the session may see (RLS / ORM scope):

* ``project_team_ids(session, project_id)``: teams linked to the project.
* ``user_team_ids(session, user_id)``: the user's teams linked to the session's bound project
  (empty without a bound project).
* ``is_member(session, team_id, user_id)``, ``team_member_ids(session, team_id)``,
  ``team_exists(session, team_id)``.
* ``link_team_names(session, link_ids)``: team names of link ids (the T-0014 member list).

**Reconciliation** (T-0075): ``reconcile(session, fix=False, confirm_sod=False)`` compares the
expected derived tuples of every live link with core's ``team_link_grants`` and reports
missing, excess and orphan drift; ``fix`` repairs it (see ``reconcile``).
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth import memberships, team_grants
from app.core.auth import service as users
from app.core.auth.access import HOLD_AUTHORITY, Persona
from app.core.auth.models import User
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import get_or_404
from app.modules.projects import service as projects
from app.modules.teams.models import Team, TeamMember, TeamProjectLink, normalize_name
from app.modules.teams.schemas import TeamCreate, TeamUpdate


class DuplicateTeamNameError(ValueError):
    """A live team of the tenant has this name already, ignoring case (409)."""


class InvalidMemberError(ValueError):
    """The user is unknown, deactivated or of another tenant (422, always the same body)."""


class SodConfirmationError(ValueError):
    """Individual PER-09 would meet a hold-authority persona from this team (409). Nothing
    has been written; retry with ``confirm_sod``."""

    def __init__(self, user_ids: Iterable[uuid.UUID], project_ids: Iterable[uuid.UUID]) -> None:
        self.user_ids = sorted(set(user_ids), key=str)
        self.project_ids = sorted(set(project_ids), key=str)
        super().__init__(
            "individual PER-09 together with a hold-authority persona: confirm with confirm_sod"
        )


@dataclass(frozen=True, slots=True)
class TeamSummary:
    team: Team
    member_count: int  # live members with an active account
    project_count: int


@dataclass(frozen=True, slots=True)
class TeamDetail:
    team: Team
    members: list[User]  # every live member, deactivated accounts included
    links: list[TeamProjectLink]


@dataclass(frozen=True, slots=True)
class ProjectTeam:
    team: Team
    link: TeamProjectLink
    member_count: int


def _tenant_id(session: Session) -> uuid.UUID:
    context = session_tenancy(session)
    if context is None:
        raise RuntimeError("the teams service needs a bound session")
    return context.tenant_id


def _audit(
    session: Session, team_id: uuid.UUID, payload: dict[str, Any], actor_id: uuid.UUID | None
) -> None:
    audit.append(session, f"team:{team_id}", payload, actor_id=actor_id, source="api")


def _ids(values: Iterable[uuid.UUID]) -> list[str]:
    return sorted(str(v) for v in values)


# --- reads ----------------------------------------------------------------------------------


def get_team(session: Session, team_id: uuid.UUID) -> Team:
    """The live team if the session can see it, else ``OutOfScopeError`` (404)."""
    return get_or_404(session, Team, team_id)


def _live_members(
    session: Session, team_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Every live member of each team, whatever the account status (the sync input)."""
    found: dict[uuid.UUID, set[uuid.UUID]] = {t: set() for t in team_ids}
    if not team_ids:
        return found
    query = select(TeamMember.team_id, TeamMember.user_id).where(
        TeamMember.team_id.in_(list(team_ids))
    )
    for team_id, user_id in session.execute(query):
        found[team_id].add(user_id)
    return found


def _active_members(
    session: Session, team_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, frozenset[uuid.UUID]]:
    """The live members of each team whose account is active (the read contract)."""
    members = _live_members(session, team_ids)
    active = users.active_user_ids(session, set().union(*members.values()))
    return {t: frozenset(u & active) for t, u in members.items()}


def _links(session: Session, team_ids: Collection[uuid.UUID]) -> list[TeamProjectLink]:
    if not team_ids:
        return []
    query = select(TeamProjectLink).where(TeamProjectLink.team_id.in_(list(team_ids)))
    return list(session.scalars(query))


def _summaries(session: Session, teams: list[Team]) -> list[TeamSummary]:
    ids = [t.id for t in teams]
    members = _active_members(session, ids)
    projects_of: dict[uuid.UUID, set[uuid.UUID]] = {t: set() for t in ids}
    for link in _links(session, ids):
        projects_of[link.team_id].add(link.linked_project_id)
    return [TeamSummary(t, len(members[t.id]), len(projects_of[t.id])) for t in teams]


def list_teams(session: Session) -> list[TeamSummary]:
    """The teams the session can see, by name."""
    return _summaries(session, list(session.scalars(select(Team).order_by(Team.name_key))))


def team_summary(session: Session, team_id: uuid.UUID) -> TeamSummary:
    """One visible team with its counts (404 when not visible)."""
    return _summaries(session, [get_team(session, team_id)])[0]


def team_detail(session: Session, team_id: uuid.UUID) -> TeamDetail:
    """The team with every live member (by e-mail) and link (404 when not visible)."""
    team = get_team(session, team_id)
    member_ids = _live_members(session, [team.id])[team.id]
    accounts = users.users_by_id(session, member_ids)
    members = sorted(accounts.values(), key=lambda u: u.email)
    links = sorted(_links(session, [team.id]), key=lambda link: str(link.linked_project_id))
    return TeamDetail(team, members, links)


def project_teams(session: Session, project_id: uuid.UUID) -> list[ProjectTeam]:
    """The live teams linked to ``project_id`` with their link and active member count."""
    query = (
        select(Team, TeamProjectLink)
        .join(TeamProjectLink, TeamProjectLink.team_id == Team.id)
        .where(TeamProjectLink.linked_project_id == project_id)
        .order_by(Team.name_key)
    )
    rows = session.execute(query).all()
    members = _active_members(session, [team.id for team, _link in rows])
    return [ProjectTeam(team, link, len(members[team.id])) for team, link in rows]


# --- read contract (public API) -------------------------------------------------------------


def project_team_ids(session: Session, project_id: uuid.UUID) -> frozenset[uuid.UUID]:
    """The live teams linked to the project."""
    query = (
        select(Team.id)
        .join(TeamProjectLink, TeamProjectLink.team_id == Team.id)
        .where(TeamProjectLink.linked_project_id == project_id)
    )
    return frozenset(session.scalars(query))


def user_team_ids(session: Session, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
    """The user's live teams linked to the session's bound project (empty without one, or
    while the account is not active)."""
    context = session_tenancy(session)
    if context is None or context.project_id is None:
        return frozenset()
    if not users.active_user_ids(session, {user_id}):
        return frozenset()
    query = (
        select(Team.id)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .join(TeamProjectLink, TeamProjectLink.team_id == Team.id)
        .where(
            TeamMember.user_id == user_id,
            TeamProjectLink.linked_project_id == context.project_id,
        )
    )
    return frozenset(session.scalars(query))


def team_exists(session: Session, team_id: uuid.UUID) -> bool:
    """Whether a live team with this id is visible to the session."""
    return session.scalar(select(Team.id).where(Team.id == team_id)) is not None


def team_member_ids(session: Session, team_id: uuid.UUID) -> frozenset[uuid.UUID]:
    """The live members of a live team whose account is active (empty for an unknown team)."""
    if not team_exists(session, team_id):
        return frozenset()
    return _active_members(session, [team_id])[team_id]


def is_member(session: Session, team_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Whether the user is an active, live member of the live team (False when unknown)."""
    return user_id in team_member_ids(session, team_id)


def link_team_names(session: Session, link_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, str]:
    """The team name of each live link id the session can see (others are left out)."""
    if not link_ids:
        return {}
    query = (
        select(TeamProjectLink.id, Team.name)
        .join(Team, Team.id == TeamProjectLink.team_id)
        .where(TeamProjectLink.id.in_(list(link_ids)))
    )
    return {row[0]: row[1] for row in session.execute(query)}


# --- writes (system_admin sessions) ---------------------------------------------------------


def _name_taken(session: Session, key: str, *, other_than: uuid.UUID | None = None) -> bool:
    query = select(Team.id).where(Team.tenant_id == _tenant_id(session), Team.name_key == key)
    if other_than is not None:
        query = query.where(Team.id != other_than)
    return session.scalar(query) is not None


def _flush_name(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        # A concurrent create or rename took the name after the check (live unique index).
        raise DuplicateTeamNameError("a team with this name exists already") from exc


def create_team(session: Session, data: TeamCreate, *, actor_id: uuid.UUID | None) -> Team:
    key = normalize_name(data.name)
    if _name_taken(session, key):
        raise DuplicateTeamNameError("a team with this name exists already")
    team = Team(
        tenant_id=_tenant_id(session),
        project_id=None,
        name=data.name,
        name_key=key,
        description=data.description,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(team)
    _flush_name(session)
    _audit(session, team.id, {"action": "create", "name": team.name}, actor_id)
    return team


def update_team(
    session: Session, team_id: uuid.UUID, data: TeamUpdate, *, actor_id: uuid.UUID | None
) -> Team:
    team = get_team(session, team_id)
    changed = sorted(n for n in data.model_fields_set if getattr(team, n) != getattr(data, n))
    if not changed:
        return team
    if "name" in changed and data.name is not None:
        key = normalize_name(data.name)
        if _name_taken(session, key, other_than=team.id):
            raise DuplicateTeamNameError("a team with this name exists already")
        team.name_key = key
    for name in changed:
        setattr(team, name, getattr(data, name))
    team.updated_by = actor_id
    _flush_name(session)
    payload: dict[str, Any] = {"action": "update", "fields": changed}
    if "name" in changed:
        payload["name"] = team.name
    _audit(session, team.id, payload, actor_id)
    return team


def _live_project_links(
    session: Session, links: Collection[TeamProjectLink]
) -> list[TeamProjectLink]:
    """The links whose project is live. Writes run in ``system_admin`` sessions, which see
    every live project of the tenant, so a link missing here points at a deleted project."""
    live = {p.id for p in projects.list_projects(session, {k.linked_project_id for k in links})}
    return [link for link in links if link.linked_project_id in live]


def _sync_links(session: Session, links: Collection[TeamProjectLink], *, confirm_sod: bool) -> None:
    """Re-sync ``links``: a link to a live project from its team's members, a link to a
    deleted project (an empty team's link does not stop the delete) only has whatever rows it
    still holds revoked. Such a link confers nothing; it stays until unlinked or the team is
    deleted (both still work)."""
    live = {link.id for link in _live_project_links(session, links)}
    for link in links:
        if link.id in live:
            _sync(session, link, confirm_sod=confirm_sod)
        else:
            team_grants.revoke_team_link_grants(session, link_id=link.id)


def _sync(session: Session, link: TeamProjectLink, *, confirm_sod: bool) -> None:
    """Re-sync the derived grants of ``link`` (to a live project) from every live member of
    its team, whatever their account status (ADR 0008). Accounts that no longer exist cannot
    hold access."""
    members = _live_members(session, [link.team_id])[link.team_id]
    existing = set(users.users_by_id(session, members))
    team_grants.sync_team_link_grants(
        session,
        link_id=link.id,
        project_id=link.linked_project_id,
        persona=link.persona,
        site_node_id=link.site_node_id,
        user_ids=existing,
        confirm_sod=confirm_sod,
    )


def _individual_admins(session: Session, project_id: uuid.UUID) -> set[uuid.UUID]:
    """Users holding individual (direct) PER-09 on the project."""
    grants = memberships.project_grants(session, project_id)
    return {
        user_id
        for user_id, held in grants.items()
        if any(g.persona is Persona.PROJECT_ADMINISTRATOR for g in held)
    }


def _is_hold(persona: str) -> bool:
    return Persona(persona) in HOLD_AUTHORITY


def delete_team(session: Session, team_id: uuid.UUID, *, actor_id: uuid.UUID | None) -> None:
    """Soft-delete the team, end its memberships and links and revoke their derived grants."""
    team = get_team(session, team_id)
    links = _links(session, [team.id])
    for link in links:
        team_grants.revoke_team_link_grants(session, link_id=link.id)
        link.soft_delete()
        link.updated_by = actor_id
    members = list(session.scalars(select(TeamMember).where(TeamMember.team_id == team.id)))
    for member in members:
        member.soft_delete()
        member.updated_by = actor_id
    team.soft_delete()
    team.updated_by = actor_id
    session.flush()
    payload = {
        "action": "delete",
        "unlinked_project_ids": _ids(link.linked_project_id for link in links),
        "removed_user_ids": _ids(member.user_id for member in members),
    }
    _audit(session, team.id, payload, actor_id)


def add_member(
    session: Session,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    confirm_sod: bool,
    actor_id: uuid.UUID | None,
) -> bool:
    """Add an active account of the tenant to the team; ``False`` when it is a member already.

    ``InvalidMemberError`` for an unknown, deactivated or other-tenant account (one answer for
    all three). ``SodConfirmationError`` (nothing written) when the user holds individual
    PER-09 on a linked project whose link persona has hold authority, unless ``confirm_sod``.
    """
    team = get_team(session, team_id)
    if not users.active_user_ids(session, {user_id}):
        raise InvalidMemberError("this user cannot be added to a team")
    if user_id in _live_members(session, [team.id])[team.id]:
        return False
    links = _links(session, [team.id])
    sod_projects = [  # a deleted project's link confers nothing, so it never triggers SoD
        link.linked_project_id
        for link in _live_project_links(session, links)
        if _is_hold(link.persona) and user_id in _individual_admins(session, link.linked_project_id)
    ]
    if sod_projects and not confirm_sod:
        raise SodConfirmationError([user_id], sod_projects)
    session.add(
        TeamMember(
            tenant_id=team.tenant_id,
            project_id=None,
            team_id=team.id,
            user_id=user_id,
            created_by=actor_id,
            updated_by=actor_id,
        )
    )
    session.flush()
    _sync_links(session, links, confirm_sod=confirm_sod)
    payload: dict[str, Any] = {
        "action": "member_add",
        "user_id": str(user_id),
        "sod_warning": bool(sod_projects),
    }
    if sod_projects:
        payload["sod_project_ids"] = _ids(sod_projects)
    _audit(session, team.id, payload, actor_id)
    return True


def remove_member(
    session: Session, team_id: uuid.UUID, user_id: uuid.UUID, *, actor_id: uuid.UUID | None
) -> bool:
    """Remove the user from the team and take the team's access away (``False`` when the user
    is not a member: nothing to do). Works for deactivated accounts too."""
    team = get_team(session, team_id)
    query = select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user_id)
    row = session.scalars(query).first()
    if row is None:
        return False
    row.soft_delete()
    row.updated_by = actor_id
    session.flush()
    _sync_links(session, _links(session, [team.id]), confirm_sod=False)
    _audit(session, team.id, {"action": "member_remove", "user_id": str(user_id)}, actor_id)
    return True


def _live_link(
    session: Session, team_id: uuid.UUID, project_id: uuid.UUID
) -> TeamProjectLink | None:
    query = select(TeamProjectLink).where(
        TeamProjectLink.team_id == team_id, TeamProjectLink.linked_project_id == project_id
    )
    return session.scalars(query).first()


def set_link(
    session: Session,
    team_id: uuid.UUID,
    project_id: uuid.UUID,
    persona: Persona,
    site_node_id: uuid.UUID | None,
    *,
    confirm_sod: bool,
    actor_id: uuid.UUID | None,
) -> tuple[TeamProjectLink, bool]:
    """Link the team to the project with ``persona``, or change the link's role; returns the
    link and whether anything changed (the same role again is a no-op).

    A deleted or other-tenant project is ``OutOfScopeError`` (404). ``SodConfirmationError``
    (nothing written) when the persona has hold authority and members hold individual PER-09
    on the project, unless ``confirm_sod``.
    """
    team = get_team(session, team_id)
    projects.get_project(session, project_id)
    link = _live_link(session, team.id, project_id)
    if link is not None and (link.persona, link.site_node_id) == (persona.value, site_node_id):
        return link, False
    members = _live_members(session, [team.id])[team.id]
    sod_users = members & _individual_admins(session, project_id) if _is_hold(persona) else set()
    if sod_users and not confirm_sod:
        raise SodConfirmationError(sod_users, [project_id])
    payload: dict[str, Any] = {"project_id": str(project_id)}
    if link is None:
        payload["action"] = "link_create"
        link = TeamProjectLink(
            tenant_id=team.tenant_id,
            project_id=None,
            team_id=team.id,
            linked_project_id=project_id,
            persona=persona.value,
            site_node_id=site_node_id,
            created_by=actor_id,
            updated_by=actor_id,
        )
        session.add(link)
    else:
        payload.update(
            action="link_rerole",
            previous_persona=link.persona,
            previous_site_node_id=str(link.site_node_id) if link.site_node_id else None,
        )
        link.persona, link.site_node_id = persona.value, site_node_id
        link.updated_by = actor_id
    session.flush()
    _sync(session, link, confirm_sod=confirm_sod)
    payload.update(
        link_id=str(link.id),
        persona=persona.value,
        site_node_id=str(site_node_id) if site_node_id else None,
        sod_warning=bool(sod_users),
    )
    if sod_users:
        payload["sod_user_ids"] = _ids(sod_users)
    _audit(session, team.id, payload, actor_id)
    return link, True


def unlink(
    session: Session, team_id: uuid.UUID, project_id: uuid.UUID, *, actor_id: uuid.UUID | None
) -> bool:
    """Remove the team's link to the project and revoke its derived grants (``False`` when the
    team is not linked there). A live link is removable even when its project has been deleted
    since; without one, a deleted or other-tenant project is ``OutOfScopeError``."""
    team = get_team(session, team_id)
    link = _live_link(session, team.id, project_id)
    if link is None:
        projects.get_project(session, project_id)
        return False
    team_grants.revoke_team_link_grants(session, link_id=link.id)
    link.soft_delete()
    link.updated_by = actor_id
    session.flush()
    payload = {
        "action": "link_remove",
        "link_id": str(link.id),
        "project_id": str(project_id),
        "persona": link.persona,
    }
    _audit(session, team.id, payload, actor_id)
    return True


# --- reconciliation (T-0075, ADR 0008 "Reconciliation") ---------------------------------------

RECONCILE_REASON: Final = "reconcile"
DerivedTuple = team_grants.DerivedTuple  # (user_id, project_id, persona, site_node_id)


@dataclass(frozen=True, slots=True)
class LinkDrift:
    """The drift of one link id. ``orphan``: live derived rows whose link is unknown or
    deleted in the teams tables (or whose team is deleted). ``sod_pending``: the missing
    tuples a fix re-adds only with ``confirm_sod``. ``added`` / ``removed``: what the fix
    did (``repaired``)."""

    link_id: uuid.UUID
    orphan: bool
    missing: frozenset[DerivedTuple]
    excess: frozenset[DerivedTuple]
    sod_pending: frozenset[DerivedTuple]
    repaired: bool = False
    added: frozenset[DerivedTuple] = frozenset()
    removed: frozenset[DerivedTuple] = frozenset()

    @property
    def remaining(self) -> frozenset[DerivedTuple]:
        """The drift left after this run (all of it when nothing was repaired)."""
        return (self.missing - self.added) | (self.excess - self.removed)


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """Every link id with drift, by id; empty when the store matches the teams tables."""

    links: tuple[LinkDrift, ...]
    fixed: bool

    @property
    def drift(self) -> bool:
        return bool(self.links)

    @property
    def orphan_link_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(d.link_id for d in self.links if d.orphan)

    @property
    def unresolved(self) -> bool:
        """Whether drift is left: anything when not fixed, else unconfirmed ``sod_pending``."""
        return any(d.remaining for d in self.links)


def _require_system_admin(session: Session) -> None:
    context = session_tenancy(session)
    if context is None or context.project_id is not None:
        raise RuntimeError("reconcile needs a tenant-level bound session")
    if not context.is_system_admin:
        raise team_grants.NotSystemAdminError("reconcile is run by system admins only")


def _expected_grants(
    session: Session,
) -> tuple[dict[uuid.UUID, TeamProjectLink], dict[uuid.UUID, frozenset[DerivedTuple]]]:
    """The live links of live teams, and the tuples each should hold: its team's live
    members whatever their account status (the sync input) on a live project; nothing for a
    link to a deleted project (it confers nothing, like ``_sync_links``)."""
    query = (
        select(TeamProjectLink)
        .join(Team, Team.id == TeamProjectLink.team_id)
        .where(Team.deleted_at.is_(None), TeamProjectLink.deleted_at.is_(None))
    )
    links = {link.id: link for link in session.scalars(query)}
    live = {link.id for link in _live_project_links(session, list(links.values()))}
    members = _live_members(session, {link.team_id for link in links.values()})
    accounts = set(users.users_by_id(session, set().union(*members.values())))
    expected: dict[uuid.UUID, frozenset[DerivedTuple]] = {}
    for link in links.values():
        holders = members[link.team_id] & accounts if link.id in live else set()
        expected[link.id] = frozenset(
            (user_id, link.linked_project_id, link.persona, link.site_node_id)
            for user_id in holders
        )
    return links, expected


def _sod_pending(
    session: Session, link: TeamProjectLink, missing: frozenset[DerivedTuple]
) -> frozenset[DerivedTuple]:
    """Missing tuples that would give a hold-authority persona to a user with individual
    PER-09 on the project: nobody confirmed that combination (T-0014's check could not fire
    while the derived row was missing)."""
    if not missing or not _is_hold(link.persona):
        return frozenset()
    admins = _individual_admins(session, link.linked_project_id)
    return frozenset(t for t in missing if t[0] in admins)


def _repair(
    session: Session,
    drift: LinkDrift,
    link: TeamProjectLink | None,
    expected: frozenset[DerivedTuple],
    *,
    confirm_sod: bool,
) -> LinkDrift:
    """Repair one link through the T-0073 API, audited with ``reason: reconcile``."""
    if link is None or not expected:
        # An orphan, or a link that should hold nothing (e.g. its project was deleted).
        team_grants.revoke_team_link_grants(session, link_id=drift.link_id, reason=RECONCILE_REASON)
        return replace(drift, repaired=True, removed=drift.excess)
    held_back = frozenset() if confirm_sod else drift.sod_pending
    team_grants.sync_team_link_grants(
        session,
        link_id=link.id,
        project_id=link.linked_project_id,
        persona=link.persona,
        site_node_id=link.site_node_id,
        user_ids={t[0] for t in expected - held_back},
        confirm_sod=confirm_sod,
        reason=RECONCILE_REASON,
    )
    return replace(drift, repaired=True, added=drift.missing - held_back, removed=drift.excess)


def reconcile(session: Session, *, fix: bool = False, confirm_sod: bool = False) -> ReconcileReport:
    """Compare the team-derived grants in the core membership store with the teams tables
    (ADR 0008, Reconciliation); a tenant-level ``system_admin`` session only
    (``NotSystemAdminError`` otherwise).

    Expected: for every live link of a live team, ``(user, linked project, persona, site)``
    over the team's live members, deactivated accounts included. Actual: core's
    ``team_link_grants`` (every link id with live derived rows). Per link id the report lists
    the **missing** and **excess** tuples (a wrong persona, site or project is both) and
    whether it is an **orphan** (its link is unknown or deleted in the teams tables).

    ``fix=True`` re-syncs each drifting live link to the expected state and revokes orphans,
    audited by core with ``reason: reconcile``. A missing tuple whose re-adding would combine
    a hold-authority persona with an individual PER-09 is ``sod_pending``: the sync gets the
    expected users minus those (their stale rows of the link are excess and go), unless
    ``confirm_sod`` (then audited ``sod_warning: true``). ``confirm_sod`` alone changes
    nothing. Idempotent: a second ``fix`` run reports no drift.
    """
    _require_system_admin(session)
    links, expected = _expected_grants(session)
    actual = team_grants.team_link_grants(session)
    found: list[LinkDrift] = []
    for link_id in sorted({*links, *actual}, key=str):
        link = links.get(link_id)
        wanted = expected.get(link_id, frozenset())
        held = actual.get(link_id, frozenset())
        missing, excess = wanted - held, held - wanted
        if not missing and not excess:
            continue
        pending = _sod_pending(session, link, missing) if link is not None else frozenset()
        drift = LinkDrift(link_id, link is None, missing, excess, pending)
        if fix:
            drift = _repair(session, drift, link, wanted, confirm_sod=confirm_sod)
        found.append(drift)
    return ReconcileReport(tuple(found), fix)


__all__ = [
    "RECONCILE_REASON",
    "DuplicateTeamNameError",
    "InvalidMemberError",
    "LinkDrift",
    "ProjectTeam",
    "ReconcileReport",
    "SodConfirmationError",
    "TeamDetail",
    "TeamSummary",
    "add_member",
    "create_team",
    "delete_team",
    "get_team",
    "is_member",
    "link_team_names",
    "list_teams",
    "project_team_ids",
    "project_teams",
    "reconcile",
    "remove_member",
    "set_link",
    "team_detail",
    "team_exists",
    "team_member_ids",
    "team_summary",
    "unlink",
    "update_team",
    "user_team_ids",
]
