"""Project record lifecycle (T-0012). Other modules call this, never the table.

A project is a tenant-level row, so every write needs a session bound to the tenant with no
project (the request session until TD-122; ``Database.unit_of_work(TenancyContext(tid))``
elsewhere). A project-bound session cannot create or change it (``OutOfScopeError``).
Each change is appended to the audit chain ``project:<id>``. Authorisation is the router's.

The creator named by the router (``creator_id``) is granted PER-09 Project Administrator on the
new project in the same transaction (diff §4.1, T-0039); deleting a project revokes every
membership on it, so memberships of a deleted project confer nothing (TD-184). Memberships are
written through the membership store (``app.core.auth.memberships``).

Modules that set a new project up (the validation module copies the tenant's default rule
sets into it, ADR 0004 §2.3) register a **created listener**; ``create_project`` calls it in
the same session and transaction, so a project never exists without its setup.

Fields the creator leaves out (status, site timezone, currency, locale) take the deployment's
defaults for new projects from the settings module (T-0044, diff §4.4); the service always
passes all four explicitly, so the column defaults are only fallback literals.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Collection
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth import memberships
from app.core.auth.access import (
    Capability,
    Grant,
    Persona,
    ProjectAccess,
    project_access,
    resolve_scope,
)
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_tenancy, session_tenancy
from app.core.tenancy.scope import get_or_404

if TYPE_CHECKING:
    from app.core.auth.deps import Principal
from app.modules.projects.models import Project
from app.modules.projects.schemas import DEFAULTED_FIELDS, ProjectCreate, ProjectUpdate
from app.modules.settings import service as deployment_settings

__all__ = [
    "DuplicateProjectCodeError",
    "add_created_listener",
    "bind_project",
    "create_project",
    "delete_project",
    "get_project",
    "list_projects",
    "live_project_ids",
    "site_zone",
    "update_project",
]

CHAIN_PREFIX: Final = "project:"

# ``listener(session, project)``: called by ``create_project`` once the row is flushed.
CreatedListener = Callable[[Session, Project], None]
_CREATED_LISTENERS: list[CreatedListener] = []


def add_created_listener(listener: CreatedListener) -> None:
    """Call ``listener`` for every project ``create_project`` creates (idempotent)."""
    if listener not in _CREATED_LISTENERS:
        _CREATED_LISTENERS.append(listener)


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
    session: Session,
    data: ProjectCreate,
    *,
    actor_id: uuid.UUID | None = None,
    creator_id: uuid.UUID | None = None,
) -> Project:
    """Create the project; ``creator_id`` (the Project Admin creating it through the API) is
    granted PER-09 on it in the same transaction. Provisioning scripts pass none. Omitted
    status, site timezone, currency and locale take the deployment defaults (T-0044)."""
    tenant_id = _tenant_id(session)
    defaults = deployment_settings.project_defaults(session)
    values = data.model_dump(exclude={"spatial_bounds"})
    for name in DEFAULTED_FIELDS:
        if values[name] is None:
            values[name] = getattr(defaults, name)
    project = Project(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        project_id=None,
        **values,
        spatial_bounds=data.spatial_bounds.as_geojson() if data.spatial_bounds else None,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(project)
    _flush(session, project.code)
    extra: dict[str, list[str]] = {}
    if creator_id is not None:
        creator = Grant(project.id, Persona.PROJECT_ADMINISTRATOR)
        memberships.set_user_grants(session, project.id, creator_id, [creator], actor_id=actor_id)
        extra["creator_grants"] = [creator.persona.value]
    _audit(session, project, "create", actor_id, **extra)
    for listener in list(_CREATED_LISTENERS):
        listener(session, project)
    return project


def bind_project(
    session: Session,
    principal: Principal,
    grants: Collection[Grant],
    project_id: uuid.UUID,
    *,
    require: Capability | None = None,
) -> ProjectAccess:
    """Bind a request session to ``project_id`` for a project-scoped endpoint (T-0217).

    The one sequence every project-scoped router uses: a non-member gets ``OutOfScopeError``
    (404); a member without ``require`` gets a 403 ``HTTPException``; then the session
    is rebound to the project scope resolved from the caller's memberships (never the
    request), and a deleted project is 404. Returns the caller's access to the project.

    The tenant-level objects loaded so far (the principal's user) are detached first: a
    session holding objects cannot be rebound (``bind_tenancy``).
    """
    access = project_access(grants, project_id)
    if require is not None and not access.has(require):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    base = session_tenancy(session) or TenancyContext(
        tenant_id=principal.tenant_id, user_id=principal.user_id
    )
    session.expunge_all()
    bind_tenancy(session, resolve_scope(grants, base, project_id))
    get_project(session, project_id)
    return access


def get_project(session: Session, project_id: uuid.UUID) -> Project:
    """The live project if the session can see it, else ``OutOfScopeError`` (404)."""
    return get_or_404(session, Project, project_id)


def list_projects(session: Session, project_ids: Collection[uuid.UUID]) -> list[Project]:
    """The live projects among ``project_ids`` that the session can see, by code."""
    if not project_ids:
        return []
    query = select(Project).where(Project.id.in_(list(project_ids))).order_by(Project.code)
    return list(session.scalars(query))


def live_project_ids(session: Session) -> list[uuid.UUID]:
    """Every live project of the session's tenant that the session can see, by id.

    The jobs scheduler (T-0214) calls it on a tenant-level session bound with
    ``is_system_admin=True``, which reads every project row (never a project-scoped record), so
    it lists the deployment's projects under RLS without a bypass.
    """
    query = select(Project.id).where(Project.tenant_id == _tenant_id(session)).order_by(Project.id)
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
    """Soft-delete the project and revoke every membership on it; it then 404s everywhere
    (its code stays taken)."""
    project = get_project(session, project_id)
    revoked = memberships.revoke_project(session, project_id, actor_id=actor_id)
    project.soft_delete()
    project.updated_by = actor_id
    session.flush()
    gone = sorted(f"{user}:{grant.persona.value}" for user, grant in revoked)
    _audit(session, project, "delete", actor_id, revoked_memberships=gone)


def site_zone(project: Project) -> ZoneInfo:
    """The project's site timezone, for converting UTC timestamps for display (spec §1.2)."""
    return ZoneInfo(project.site_timezone)
