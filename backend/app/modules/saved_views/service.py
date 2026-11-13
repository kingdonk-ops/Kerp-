"""Saved register views (T-0020, register-views spec §3). Other modules call this, never the table.

Every call works on a session bound to the view's project; the router has checked the
caller's membership. Who sees and edits which view is decided here from a ``Viewer``:

* ``private``: the owner only. ``team``: the owner and members of ``team_id``.
  ``project``: every project member. ``portal``: the owner and Project Administrators;
  client portal layouts read portal views once they exist (TD-243).
* Edit / delete: the owner; a shared (non-private) view also a Project Administrator.
* Publishing a shared view needs ``can_share`` (the ``C`` capability); making a view the
  project default, or un-making it, needs ``is_admin`` (PER-09).
* At most one default per owner and register among private views, and one project default
  per register: setting a default clears the previous one.

A register declares its column catalogue with ``register_catalogue``; a loaded view flags
every referenced column the catalogue no longer has as missing instead of failing.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import OutOfScopeError, get_or_404
from app.modules.saved_views.models import DEFAULT_VISIBILITIES, SavedView
from app.modules.saved_views.schemas import (
    SCHEMA_VERSION,
    ResolvedColumn,
    SavedViewCreate,
    SavedViewRead,
    SavedViewUpdate,
    ViewConfig,
)

__all__ = [
    "InvalidViewError",
    "ViewPermissionError",
    "Viewer",
    "create_view",
    "default_view",
    "delete_view",
    "get_view",
    "known_columns",
    "list_views",
    "load_config",
    "present",
    "register_catalogue",
    "update_view",
]

ColumnCatalogue = Callable[[Session], Collection[str]]
_CATALOGUES: Final[dict[str, ColumnCatalogue]] = {}
SHARED: Final = frozenset({"team", "project", "portal"})


class ViewPermissionError(PermissionError):
    """The caller sees the view but may not make this change (the API answers 403)."""


class InvalidViewError(ValueError):
    """The resulting view breaks a rule of the view shape (the API answers 422)."""


@dataclass(frozen=True, slots=True)
class Viewer:
    """Who is asking, in the bound project.

    * ``team_ids``: the caller's teams in this project (from the teams module, T-0013).
    * ``can_share``: may publish ``team`` / ``project`` / ``portal`` views (``C``).
    * ``is_admin``: Project Administrator (PER-09): edits any shared view, sets the
      project default, sees portal views.
    """

    user_id: uuid.UUID
    team_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    can_share: bool = False
    is_admin: bool = False


# --- column catalogue -------------------------------------------------------------------------


def register_catalogue(register_key: str, catalogue: ColumnCatalogue | None) -> None:
    """Declare (or, with ``None``, withdraw) the column ids a register offers right now.

    The column catalogue (E-01-01) builds this from core columns, field defs, ancestors,
    rollups and column providers; it is called on every load, so a removed field def
    shows up as missing without a deploy.
    """
    if catalogue is None:
        _CATALOGUES.pop(register_key, None)
    else:
        _CATALOGUES[register_key] = catalogue


def known_columns(session: Session, register_key: str) -> frozenset[str] | None:
    """The register's current column ids, or ``None`` when it has no catalogue yet."""
    catalogue = _CATALOGUES.get(register_key)
    return None if catalogue is None else frozenset(catalogue(session))


# --- reads ------------------------------------------------------------------------------------


def _project_id(session: Session) -> uuid.UUID:
    context = session_tenancy(session)
    if context is None or context.project_id is None:
        raise RuntimeError("the saved_views service needs a session bound to a project")
    return context.project_id


def can_see(viewer: Viewer, view: SavedView) -> bool:
    if view.owner_user_id == viewer.user_id:
        return True
    if view.visibility == "project":
        return True
    if view.visibility == "team":
        return view.team_id in viewer.team_ids
    if view.visibility == "portal":
        return viewer.is_admin
    return False


def _can_edit(viewer: Viewer, view: SavedView) -> bool:
    return view.owner_user_id == viewer.user_id or (view.visibility in SHARED and viewer.is_admin)


def get_view(session: Session, viewer: Viewer, view_id: uuid.UUID) -> SavedView:
    """The view if it is in the bound project and ``viewer`` may see it, else 404."""
    view = get_or_404(session, SavedView, view_id)
    if view.project_id != _project_id(session) or not can_see(viewer, view):
        raise OutOfScopeError("SavedView not found")
    return view


def _register_views(session: Session, register_key: str) -> list[SavedView]:
    query = (
        select(SavedView)
        .where(SavedView.project_id == _project_id(session))
        .where(SavedView.register_key == register_key)
        .order_by(SavedView.name, SavedView.created_at)
    )
    return list(session.scalars(query))


def list_views(session: Session, viewer: Viewer, register_key: str) -> list[SavedView]:
    """The register's views ``viewer`` may see in the bound project, by name."""
    return [v for v in _register_views(session, register_key) if can_see(viewer, v)]


def default_view(session: Session, viewer: Viewer, register_key: str) -> SavedView | None:
    """The view a register opens with: the caller's default, else the project default."""
    views = _register_views(session, register_key)
    mine = [v for v in views if v.visibility == "private" and v.owner_user_id == viewer.user_id]
    for view in mine:
        if view.is_default:
            return view
    return next((v for v in views if v.is_project_default), None)


def load_config(view: SavedView) -> ViewConfig:
    """The stored configuration as a ``ViewConfig`` (stored at ``SCHEMA_VERSION`` 1).

    A later schema version adds an upgrade step here, keyed on ``view.schema_version``.
    """
    return ViewConfig.model_validate(
        {
            "columns": view.columns,
            "filter": view.filter,
            "sort": view.sort,
            "group_by": view.group_by,
            "conditional_format": view.conditional_format,
            "density": view.density,
        }
    )


def present(session: Session, view: SavedView) -> SavedViewRead:
    """The view as the register loads it, with missing columns flagged (spec §3)."""
    config = load_config(view)
    known = known_columns(session, view.register_key)
    missing = [] if known is None else [c for c in config.referenced_columns() if c not in known]
    columns = [ResolvedColumn(**c.model_dump(), missing=c.id in missing) for c in config.columns]
    assert view.project_id is not None
    return SavedViewRead(
        id=view.id,
        project_id=view.project_id,
        register_key=view.register_key,
        name=view.name,
        owner_user_id=view.owner_user_id,
        visibility=view.visibility,  # type: ignore[arg-type]  # CHECK constraint
        team_id=view.team_id,
        is_default=view.is_default,
        columns=columns,
        filter=config.filter,
        sort=config.sort,
        group_by=config.group_by,
        conditional_format=config.conditional_format,
        density=config.density,
        schema_version=view.schema_version,
        missing_columns=missing,
        revision=view.revision,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


# --- writes -----------------------------------------------------------------------------------


def _store_config(view: SavedView, config: ViewConfig) -> None:
    data = config.model_dump(mode="json")
    view.columns = data["columns"]
    view.filter = data["filter"]
    view.sort = data["sort"]
    view.group_by = data["group_by"]
    view.conditional_format = data["conditional_format"]
    view.density = data["density"]
    view.schema_version = SCHEMA_VERSION


def _check_sharing(
    viewer: Viewer, visibility: str, team_id: uuid.UUID | None, is_default: bool
) -> None:
    if (visibility == "team") != (team_id is not None):
        raise InvalidViewError("a team view needs team_id, and only a team view has one")
    if is_default and visibility not in DEFAULT_VISIBILITIES:
        raise InvalidViewError("only a private or a project view can be a default")
    if visibility == "team" and team_id not in viewer.team_ids and not viewer.is_admin:
        raise ViewPermissionError("you can only share a view with a team you belong to")
    if visibility in SHARED and not viewer.can_share:
        raise ViewPermissionError("you may not share views in this project")


def _clear_other_defaults(session: Session, view: SavedView, actor_id: uuid.UUID) -> None:
    for other in _register_views(session, view.register_key):
        if other.id == view.id or not other.is_default:
            continue
        same_owner_private = (
            view.visibility == "private"
            and other.visibility == "private"
            and other.owner_user_id == view.owner_user_id
        )
        if same_owner_private or (view.is_project_default and other.is_project_default):
            other.is_default = False
            other.updated_by = actor_id


def create_view(session: Session, viewer: Viewer, data: SavedViewCreate) -> SavedView:
    """Save a new view owned by ``viewer`` in the bound project."""
    _check_sharing(viewer, data.visibility, data.team_id, data.is_default)
    if data.is_default and data.visibility == "project" and not viewer.is_admin:
        raise ViewPermissionError("only a Project Administrator sets the project default")
    context = session_tenancy(session)
    assert context is not None
    view = SavedView(
        id=uuid.uuid4(),
        tenant_id=context.tenant_id,
        project_id=_project_id(session),
        register_key=data.register_key,
        name=data.name,
        owner_user_id=viewer.user_id,
        visibility=data.visibility,
        team_id=data.team_id,
        is_default=data.is_default,
        created_by=viewer.user_id,
        updated_by=viewer.user_id,
    )
    _store_config(view, data)
    if view.is_default:
        _clear_other_defaults(session, view, viewer.user_id)
    session.add(view)
    session.flush()
    return view


def update_view(
    session: Session, viewer: Viewer, view_id: uuid.UUID, data: SavedViewUpdate
) -> SavedView:
    """Apply the given fields; the merged view is validated as a whole."""
    view = get_view(session, viewer, view_id)
    if not _can_edit(viewer, view):
        raise ViewPermissionError("only the owner or a Project Administrator edits this view")
    given = data.model_fields_set
    visibility = data.visibility if "visibility" in given else view.visibility
    team_id = data.team_id if "team_id" in given else view.team_id
    is_default = data.is_default if "is_default" in given else view.is_default
    assert visibility is not None
    assert is_default is not None
    if visibility != view.visibility or team_id != view.team_id:
        _check_sharing(viewer, visibility, team_id, is_default)
    elif is_default and visibility not in DEFAULT_VISIBILITIES:
        raise InvalidViewError("only a private or a project view can be a default")
    was_project_default = view.is_project_default
    if (is_default and visibility == "project") != was_project_default and not viewer.is_admin:
        raise ViewPermissionError("only a Project Administrator changes the project default")
    changes = data.config_changes()
    if changes:
        merged: dict[str, Any] = load_config(view).model_dump()
        merged.update(data.model_dump(include=set(changes)))
        try:
            config = ViewConfig.model_validate(merged)
        except ValidationError as exc:
            raise InvalidViewError(str(exc)) from exc
        _store_config(view, config)
    if "name" in given:
        assert data.name is not None
        view.name = data.name
    view.visibility, view.team_id, view.is_default = visibility, team_id, is_default
    view.updated_by = viewer.user_id
    if view.is_default:
        _clear_other_defaults(session, view, viewer.user_id)
    session.flush()
    return view


def delete_view(session: Session, viewer: Viewer, view_id: uuid.UUID) -> None:
    """Soft-delete the view; deleting the project default needs a Project Administrator."""
    view = get_view(session, viewer, view_id)
    if not _can_edit(viewer, view):
        raise ViewPermissionError("only the owner or a Project Administrator deletes this view")
    if view.is_project_default and not viewer.is_admin:
        raise ViewPermissionError("only a Project Administrator removes the project default")
    view.soft_delete()
    view.updated_by = viewer.user_id
    session.flush()
