"""Breakdown levels ``oe_asset_level_def`` and the node tree ``oe_asset_node`` (T-0101).

Both are project-scoped under the standard project RLS (migration ``0036``). ADR 0003 as
amended: the levels of a project's breakdown are configuration; rules bind to a level's
**roles**, never its name. There is no BOQ column or foreign key on either table.

``oe_asset_node``: ``parent_id`` is the source of truth; ``tree_path`` (ltree, GiST) is derived
from it, one label per ancestor (the node id, ``tree.child_path``); ``display_path`` is the
breadcrumb of codes, never queried. ``code_scope_key`` is derived from the level's
``code_uniqueness`` and makes a duplicate code a unique-index violation (``service._scope_key``).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, BaseRecordMixin, JSONType
from app.core.geo import GeoJSONGeometry
from app.modules.asset_management.tree import LtreePath

LEVEL_TABLE: Final = "oe_asset_level_def"
NODE_TABLE: Final = "oe_asset_node"
LEVEL_KEY_INDEX: Final = "uq_oe_asset_level_def_project_key"
CODE_INDEX: Final = "uq_oe_asset_node_code_scope"
# Frozen at M1 (T-0131). Level names are data; these are not.
ROLES: Final = (
    "grouping",
    "physical",
    "work_anchor",
    "inspection_anchor",
    "progress_unit",
    "schedule_link",
)
WORK_ANCHOR: Final = "work_anchor"
PROGRESS_UNIT: Final = "progress_unit"
CODE_UNIQUENESS: Final = ("per_parent", "per_level", "per_project")
STATUSES: Final = ("active", "retired")
_QTY: Final = Numeric(18, 4)


def _in(column: str, values: tuple[str, ...]) -> str:
    """SQL ``column IN (...)`` for CHECK constraints. Module constants only, never user input."""
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class AssetLevelDef(Base, BaseRecordMixin):
    """One level of a project's breakdown template (a project may run several side by side)."""

    __tablename__ = LEVEL_TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NOT NULL", name="project_level"),
        CheckConstraint("depth >= 0", name="depth"),
        CheckConstraint(_in("code_uniqueness", CODE_UNIQUENESS), name="code_uniqueness"),
        Index(LEVEL_KEY_INDEX, "project_id", "key", unique=True),
    )

    key: Mapped[str] = mapped_column(String(64), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_parent_keys: Mapped[list[str]] = mapped_column(JSONType(), nullable=False, default=list)
    label_key: Mapped[str] = mapped_column(String(120), nullable=False)
    roles: Mapped[list[str]] = mapped_column(JSONType(), nullable=False, default=list)
    code_pattern: Mapped[str | None] = mapped_column(String(200), nullable=True)
    code_uniqueness: Mapped[str] = mapped_column(String(16), nullable=False, default="per_parent")
    # The level's JSONSchema in the field registry (T-0102 adds the registry and the FK).
    field_schema_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    display_code_template: Mapped[str | None] = mapped_column(String(200), nullable=True)


class AssetNode(Base, BaseRecordMixin):
    """A node of the one physical/work tree."""

    __tablename__ = NODE_TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NOT NULL", name="project_level"),
        CheckConstraint(_in("status", STATUSES), name="status"),
        Index("ix_oe_asset_node_tree_path", "tree_path", postgresql_using="gist"),
        Index(
            CODE_INDEX,
            "project_id",
            "code_scope_key",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    level_def_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey(f"{LEVEL_TABLE}.id"), nullable=False, index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey(f"{NODE_TABLE}.id"), nullable=True, index=True
    )
    tree_path: Mapped[str] = mapped_column(LtreePath(), nullable=False)
    display_path: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(String(200), nullable=False)
    display_code: Mapped[str] = mapped_column(Text, nullable=False)
    code_scope_key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    discipline_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # External keys: systems of record are referenced, never joined (CLAUDE.md §6).
    wbs_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    activity_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    external_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    planned_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_finish: Mapped[date | None] = mapped_column(Date, nullable=True)
    spatial_location: Mapped[dict[str, Any] | None] = mapped_column(
        GeoJSONGeometry("PointZ"), nullable=True
    )
    # ``progress_unit`` levels only.
    budget_qty: Mapped[Decimal | None] = mapped_column(_QTY, nullable=True)
    uom: Mapped[str | None] = mapped_column(String(32), nullable=True)
    budget_hours: Mapped[Decimal | None] = mapped_column(_QTY, nullable=True)
    earned_qty: Mapped[Decimal | None] = mapped_column(_QTY, nullable=True)
    # Lineage (operations: T-0134).
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    superseded_by: Mapped[list[str]] = mapped_column(JSONType(), nullable=False, default=list)
    derived_from: Mapped[list[str]] = mapped_column(JSONType(), nullable=False, default=list)
