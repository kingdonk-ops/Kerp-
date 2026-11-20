"""The saved register view ``oe_saved_view`` (T-0020, register-views spec §3).

One row per saved view, always project-scoped, under the standard project RLS (migration
``0011``). The view configuration (``columns``, ``filter``, ``sort``, ``group_by``,
``conditional_format``, ``density``) is stored as JSON in the shape of
``app.modules.saved_views.schemas`` at ``schema_version``; column ids are catalogue ids
(spec §1), never SQL.

A ``team`` view names its teams in ``oe_saved_view_team`` (T-0074, migration ``0032``): one
live row per (view, team), same project RLS. ``team_id`` on the view is **deprecated**: the
single team of T-0020, copied into the link table by ``0032`` and never read or written since
(always ``NULL`` on rows written after it). Dropping the column is destructive and needs an
ADR (TD-381).
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, BaseRecordMixin, JSONType

TABLE: Final = "oe_saved_view"
TEAM_TABLE: Final = "oe_saved_view_team"
TEAM_INDEX: Final = "uq_oe_saved_view_team_live"
VISIBILITIES: Final = ("private", "team", "project", "portal")
DENSITIES: Final = ("compact", "standard", "comfortable")
# ``is_default`` means "the owner's default" on a private view and "the project default"
# (set by PER-09) on a project view; no other visibility can be a default.
DEFAULT_VISIBILITIES: Final = ("private", "project")


def _in(column: str, values: tuple[str, ...]) -> str:
    """SQL ``column IN (...)`` for CHECK constraints. Module constants only, never user input."""
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class SavedView(Base, BaseRecordMixin):
    """A saved view of one register (``register_key``) in one project."""

    __tablename__ = TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NOT NULL", name="project_level"),
        CheckConstraint(_in("visibility", VISIBILITIES), name="visibility"),
        CheckConstraint(_in("density", DENSITIES), name="density"),
        # Deprecated ``team_id`` (T-0074): only a legacy team view may still carry one.
        CheckConstraint("team_id IS NULL OR visibility = 'team'", name="team_matches_visibility"),
        CheckConstraint(
            "NOT is_default OR " + _in("visibility", DEFAULT_VISIBILITIES), name="default_kind"
        ),
        CheckConstraint("schema_version >= 1", name="schema_version"),
        Index("ix_oe_saved_view_project_register", "project_id", "register_key"),
    )

    register_key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    # Deprecated (T-0074): superseded by ``SavedViewTeam``; unused, NULL on new rows.
    team_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSONType(), nullable=False, default=list)
    filter: Mapped[dict[str, Any] | None] = mapped_column(JSONType(), nullable=True)
    sort: Mapped[list[dict[str, Any]]] = mapped_column(JSONType(), nullable=False, default=list)
    group_by: Mapped[dict[str, Any] | None] = mapped_column(JSONType(), nullable=True)
    conditional_format: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType(), nullable=False, default=list
    )
    density: Mapped[str] = mapped_column(String(16), nullable=False, default="standard")
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    @property
    def is_project_default(self) -> bool:
        return self.is_default and self.visibility == "project"


class SavedViewTeam(Base, BaseRecordMixin):
    """A team a ``team`` view is shared with (T-0074). ``team_id`` has no foreign key: the team
    belongs to the teams module (CLAUDE.md §6); whether it is still linked to the project is
    asked of ``teams.service`` on every read."""

    __tablename__ = TEAM_TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NOT NULL", name="project_level"),
        Index(
            TEAM_INDEX,
            "saved_view_id",
            "team_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    saved_view_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey(f"{TABLE}.id"), nullable=False, index=True
    )
    team_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
