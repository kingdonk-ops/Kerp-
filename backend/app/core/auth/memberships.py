"""The membership store's write side (T-0014): who holds which persona on a project.

``auth_project_membership`` belongs to core auth (``access.load_grants`` reads it at every
request); modules change it only through these functions (CLAUDE.md §6). Authorisation and
audit are the caller's (the admin module's service). Every function needs a tenant-level
session (no bound project) that can see the project's rows under the membership-based policy
(a member of the project, or a ``system_admin`` session, T-0014).

Revoking soft-deletes a row; granting the same persona (and site) again revives it, because
the grant's unique key covers soft-deleted rows too.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.access import Grant, Persona
from app.core.auth.models import ProjectMembership
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import INCLUDE_DELETED


def _tenant_id(session: Session) -> uuid.UUID:
    context = session_tenancy(session)
    if context is None or context.project_id is not None:
        raise RuntimeError("the membership store needs a tenant-level bound session")
    return context.tenant_id


def _grant(row: ProjectMembership) -> Grant:
    return Grant(row.member_project_id, Persona(row.persona), row.site_node_id)


def project_grants(session: Session, project_id: uuid.UUID) -> dict[uuid.UUID, set[Grant]]:
    """The live grants on ``project_id``, per user."""
    rows = session.scalars(
        select(ProjectMembership).where(
            ProjectMembership.tenant_id == _tenant_id(session),
            ProjectMembership.member_project_id == project_id,
        )
    )
    members: dict[uuid.UUID, set[Grant]] = {}
    for row in rows:
        members.setdefault(row.user_id, set()).add(_grant(row))
    return members


def set_user_grants(
    session: Session,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    grants: Collection[Grant],
    *,
    actor_id: uuid.UUID | None = None,
) -> tuple[set[Grant], set[Grant]]:
    """Make ``grants`` the user's complete set on the project; returns ``(added, removed)``.

    Every grant must name ``project_id``. An empty set revokes everything.
    """
    tenant_id = _tenant_id(session)
    if any(g.project_id != project_id for g in grants):
        raise ValueError("every grant must be on the given project")
    query = select(ProjectMembership).where(
        ProjectMembership.tenant_id == tenant_id,
        ProjectMembership.member_project_id == project_id,
        ProjectMembership.user_id == user_id,
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
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
    session.flush()
    return added, removed
