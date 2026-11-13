"""Project record lifecycle (T-0012). Other modules call this, never the table.

A project is a tenant-level row, so every write needs a session bound to the tenant with no
project (the request session until TD-122; ``Database.unit_of_work(TenancyContext(tid))``
elsewhere). A project-bound session cannot create or change it (``OutOfScopeError``).
Each change is appended to the audit chain ``project:<id>``. Authorisation is the router's.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import get_or_404
from app.modules.projects.models import Project
from app.modules.projects.schemas import ProjectCreate, ProjectUpdate

__all__ = [
    "DuplicateProjectCodeError",
    "create_project",
    "delete_project",
    "get_project",
    "list_projects",
    "site_zone",
    "update_project",
]

CHAIN_PREFIX: Final = "project:"


class DuplicateProjectCodeError(ValueError):
    """The tenant already has a project with this code (the API answers 409)."""


def _tenant_id(session: Session) -> uuid.UUID:
    context = session_tenancy(session)
    if context is None:
        raise RuntimeError("the projects service needs a session bound to a tenant")
    return context.tenant_id


def _flush(session: Session, code: str) -> None:
    """Flush; the unique ``(tenant_id, code)`` violation becomes ``DuplicateProjectCodeError``.

    The transaction is unusable afterwards; the caller's unit of work rolls it back.
    """
    try:
        session.flush()
    except IntegrityError as exc:
        if "code" not in str(exc.orig):
            raise
        raise DuplicateProjectCodeError(f"a project with code {code!r} already exists") from exc


def _audit(
    session: Session, project: Project, action: str, actor_id: uuid.UUID | None, **extra: list[str]
) -> None:
    payload = {"action": action, "code": project.code, **extra}
    audit.append(session, f"{CHAIN_PREFIX}{project.id}", payload, actor_id=actor_id, source="api")


def create_project(
    session: Session, data: ProjectCreate, *, actor_id: uuid.UUID | None = None
) -> Project:
    project = Project(
        id=uuid.uuid4(),
        tenant_id=_tenant_id(session),
        project_id=None,
        **data.model_dump(exclude={"spatial_bounds"}),
        spatial_bounds=data.spatial_bounds.as_geojson() if data.spatial_bounds else None,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(project)
    _flush(session, project.code)
    _audit(session, project, "create", actor_id)
    return project


def get_project(session: Session, project_id: uuid.UUID) -> Project:
    """The live project if the session can see it, else ``OutOfScopeError`` (404)."""
    return get_or_404(session, Project, project_id)


def list_projects(session: Session, project_ids: Collection[uuid.UUID]) -> list[Project]:
    """The live projects among ``project_ids`` that the session can see, by code."""
    if not project_ids:
        return []
    query = select(Project).where(Project.id.in_(list(project_ids))).order_by(Project.code)
    return list(session.scalars(query))


def update_project(
    session: Session,
    project_id: uuid.UUID,
    data: ProjectUpdate,
    *,
    actor_id: uuid.UUID | None = None,
) -> Project:
    """Apply the fields given in ``data``; a call that changes nothing writes nothing."""
    project = get_project(session, project_id)
    changed: list[str] = []
    for name in sorted(data.model_fields_set):
        if name == "spatial_bounds":
            value: Any = data.spatial_bounds.as_geojson() if data.spatial_bounds else None
        else:
            value = getattr(data, name)
        if getattr(project, name) != value:
            setattr(project, name, value)
            changed.append(name)
    if changed:
        project.updated_by = actor_id
        _flush(session, project.code)
        _audit(session, project, "update", actor_id, fields=changed)
    return project


def delete_project(
    session: Session, project_id: uuid.UUID, *, actor_id: uuid.UUID | None = None
) -> None:
    """Soft-delete the project; it then 404s everywhere (its code stays taken)."""
    project = get_project(session, project_id)
    project.soft_delete()
    project.updated_by = actor_id
    session.flush()
    _audit(session, project, "delete", actor_id)


def site_zone(project: Project) -> ZoneInfo:
    """The project's site timezone, for converting UTC timestamps for display (spec §1.2)."""
    return ZoneInfo(project.site_timezone)
