"""The membership store's write side (T-0014): who holds which persona on a project.

``auth_project_membership`` belongs to core auth (``access.load_grants`` reads it at every
request); modules change it only through these functions (CLAUDE.md §6). Authorisation and
audit are the caller's (the admin module's service). Every function needs a tenant-level
session (no bound project) that can see the project's rows under the membership-based policy
(a member of the project, or a ``system_admin`` session, T-0014).

Revoking soft-deletes a row; granting the same persona (and site) again revives it, because
the grant's unique key covers soft-deleted rows too.

These functions write **individual** (``direct``) rows only. Team-derived (``team_link``)
rows belong to ``team_grants`` (T-0073, ADR 0008): ``set_user_grants`` never touches them,
``project_member_grants`` lists them with their source, and ``revoke_project`` refuses while
live ones exist (``TeamGrantsPresentError``), because only the teams module may end them.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.access import Grant, Persona
from app.core.auth.models import SOURCE_DIRECT, SOURCE_TEAM_LINK, ProjectMembership
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import INCLUDE_DELETED


class TeamGrantsPresentError(ValueError):
    """The project still has live team-derived grants: a System admin unlinks its teams first
    (409). Only the teams module ends derived rows (ADR 0008)."""


@dataclass(frozen=True, slots=True)
class MemberGrant:
    """One live membership row with its source: ``direct`` (``source_id`` ``None``) or
    ``team_link`` (``source_id`` = the team link id)."""

    grant: Grant
    source_type: str
    source_id: uuid.UUID | None

    @property
    def is_direct(self) -> bool:
        return self.source_type == SOURCE_DIRECT


def _tenant_id(session: Session) -> uuid.UUID:
    context = session_tenancy(session)
    if context is None or context.project_id is not None:
        raise RuntimeError("the membership store needs a tenant-level bound session")
    return context.tenant_id


def _grant(row: ProjectMembership) -> Grant:
    return Grant(row.member_project_id, Persona(row.persona), row.site_node_id)


def project_member_grants(
    session: Session, project_id: uuid.UUID
) -> dict[uuid.UUID, set[MemberGrant]]:
    """Every live row on ``project_id`` with its source, per user (the member list)."""
    rows = session.scalars(
        select(ProjectMembership).where(
            ProjectMembership.tenant_id == _tenant_id(session),
            ProjectMembership.member_project_id == project_id,
        )
    )
    members: dict[uuid.UUID, set[MemberGrant]] = {}
    for row in rows:
        member = MemberGrant(_grant(row), row.source_type, row.source_id)
        members.setdefault(row.user_id, set()).add(member)
    return members


def member_grants(
    session: Session, project_ids: Collection[uuid.UUID] | None = None
) -> dict[uuid.UUID, set[Grant]]:
    """Every live grant (any source) the session can read, per user: on ``project_ids`` when
    given, else on every project the session sees memberships of (all of the tenant's for a
    ``system_admin`` session; the caller's own projects for anyone else, under the
    membership-based policy). A broadcast's audience is resolved from it (T-0228)."""
    if project_ids is not None and not project_ids:
        return {}
    query = select(ProjectMembership).where(ProjectMembership.tenant_id == _tenant_id(session))
    if project_ids is not None:
        query = query.where(ProjectMembership.member_project_id.in_(list(project_ids)))
    members: dict[uuid.UUID, set[Grant]] = {}
    for row in session.scalars(query):
        members.setdefault(row.user_id, set()).add(_grant(row))
    return members


def project_grants(session: Session, project_id: uuid.UUID) -> dict[uuid.UUID, set[Grant]]:
    """The live **individual** (``direct``) grants on ``project_id``, per user."""
    members: dict[uuid.UUID, set[Grant]] = {}
    for user_id, rows in project_member_grants(session, project_id).items():
        direct = {r.grant for r in rows if r.is_direct}
        if direct:
            members[user_id] = direct
    return members


def set_user_grants(
    session: Session,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    grants: Collection[Grant],
    *,
    actor_id: uuid.UUID | None = None,
) -> tuple[set[Grant], set[Grant]]:
    """Make ``grants`` the user's complete **individual** set on the project; returns
    ``(added, removed)``. Team-derived rows are never touched: a grant the user also holds
    through a team gets its own ``direct`` row, which outlives the team link.

    Every grant must name ``project_id``. An empty set revokes every individual grant.
    """
    tenant_id = _tenant_id(session)
    if any(g.project_id != project_id for g in grants):
        raise ValueError("every grant must be on the given project")
    query = select(ProjectMembership).where(
        ProjectMembership.tenant_id == tenant_id,
        ProjectMembership.member_project_id == project_id,
        ProjectMembership.user_id == user_id,
        ProjectMembership.source_type == SOURCE_DIRECT,
    )
    rows = {
        _grant(r): r for r in session.scalars(query.execution_options(**{INCLUDE_DELETED: True}))
    }
    live = {g for g, r in rows.items() if r.deleted_at is None}
    wanted = set(grants)
    added, removed = wanted - live, live - wanted
    for grant in removed:
        rows[grant].soft_delete()
        rows[grant].updated_by = actor_id
    for grant in added:
        row = rows.get(grant)
        if row is not None:  # revoked before: revive it
            row.deleted_at = None
            row.updated_by = actor_id
            continue
        session.add(
            ProjectMembership(
                tenant_id=tenant_id,
                project_id=None,
                user_id=user_id,
                member_project_id=project_id,
                persona=grant.persona.value,
                site_node_id=grant.site_node_id,
                source_type=SOURCE_DIRECT,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
    session.flush()
    return added, removed


def revoke_project(
    session: Session, project_id: uuid.UUID, *, actor_id: uuid.UUID | None = None
) -> list[tuple[uuid.UUID, Grant]]:
    """Revoke every live grant on ``project_id`` (the project is being deleted: memberships of
    a deleted project confer nothing, diff §4.1, TD-184); returns ``(user, grant)`` pairs.

    ``TeamGrantsPresentError`` while team-derived grants remain on the project: they are
    ended by unlinking the teams (T-0013), never here, so they cannot outlive their link
    unnoticed nor be re-created by the teams reconcile for a deleted project.
    """
    rows = session.scalars(
        select(ProjectMembership).where(
            ProjectMembership.tenant_id == _tenant_id(session),
            ProjectMembership.member_project_id == project_id,
        )
    ).all()
    if any(row.source_type == SOURCE_TEAM_LINK for row in rows):
        raise TeamGrantsPresentError(
            "the project has team-derived members: a system admin unlinks its teams first"
        )
    for row in rows:
        row.soft_delete()
        row.updated_by = actor_id
    session.flush()
    return [(row.user_id, _grant(row)) for row in rows]
