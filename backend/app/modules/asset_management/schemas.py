"""Request and response shapes of the asset hierarchy API (T-0101).

* A node ``code`` is free **text**, stored exactly as given (``101.1``, ``SI-001``): 1-200
  characters, no control characters, no leading or trailing whitespace (refused, never
  trimmed).
* ``spatial_location`` is a GeoJSON (RFC 7946, WGS84) ``Point`` with an elevation:
  ``[lon, lat, elevation_m]``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.modules.projects.schemas import Coordinate

Role = Literal[
    "grouping", "physical", "work_anchor", "inspection_anchor", "progress_unit", "schedule_link"
]
CodeUniqueness = Literal["per_parent", "per_level", "per_project"]
LevelKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
LabelKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{0,119}$")]
Pattern = Annotated[str, StringConstraints(min_length=1, max_length=200)]
NodeCode = Annotated[
    str,
    StringConstraints(pattern=r"^[^\s\x00-\x1f\x7f]([^\x00-\x1f\x7f]{0,198}[^\s\x00-\x1f\x7f])?$"),
]
Quantity = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]


class PointZGeoJSON(BaseModel):
    """A GeoJSON ``Point`` in WGS84 with an elevation in metres."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["Point"]
    coordinates: tuple[
        Annotated[Coordinate, Field(ge=-180, le=180)],
        Annotated[Coordinate, Field(ge=-90, le=90)],
        Coordinate,
    ]

    def as_geojson(self) -> dict[str, Any]:
        return {"type": self.type, "coordinates": list(self.coordinates)}


class LevelDefCreate(BaseModel):
    """``POST .../level-defs``. ``allowed_parent_keys`` empty = a root level (``depth`` 0)."""

    model_config = ConfigDict(extra="forbid")

    key: LevelKey
    depth: int = Field(ge=0, le=64)
    allowed_parent_keys: list[LevelKey] = Field(default_factory=list, max_length=64)
    label_key: LabelKey
    roles: set[Role] = Field(default_factory=set)
    code_pattern: Pattern | None = None
    code_uniqueness: CodeUniqueness = "per_parent"
    field_schema_id: uuid.UUID | None = None
    display_code_template: Pattern | None = None


class LevelDefUpdate(BaseModel):
    """``PATCH .../level-defs/{id}``: only the given fields change. Once nodes use the level,
    changes are additive only (``service.update_level_def``)."""

    model_config = ConfigDict(extra="forbid")

    key: LevelKey | None = None
    depth: int | None = Field(default=None, ge=0, le=64)
    allowed_parent_keys: list[LevelKey] | None = Field(default=None, max_length=64)
    label_key: LabelKey | None = None
    roles: set[Role] | None = None
    code_pattern: Pattern | None = None
    code_uniqueness: CodeUniqueness | None = None
    field_schema_id: uuid.UUID | None = None
    display_code_template: Pattern | None = None

    @model_validator(mode="after")
    def _given(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("give at least one field to change")
        nullable = {"code_pattern", "field_schema_id", "display_code_template"}
        for name in sorted(self.model_fields_set - nullable):
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class LevelDefRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    depth: int
    allowed_parent_keys: list[str]
    label_key: str
    roles: list[Role]
    code_pattern: str | None
    code_uniqueness: CodeUniqueness
    field_schema_id: uuid.UUID | None
    display_code_template: str | None
    revision: int


class NodeCreate(BaseModel):
    """``POST .../nodes``. ``parent_id`` omitted or ``null`` = a root node. The budget fields
    are for ``progress_unit`` levels only."""

    model_config = ConfigDict(extra="forbid")

    level_def_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    code: NodeCode
    name: Annotated[str, StringConstraints(min_length=1, max_length=255)] | None = None
    spatial_location: PointZGeoJSON | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    budget_qty: Quantity | None = None
    uom: Annotated[str, StringConstraints(min_length=1, max_length=32)] | None = None
    budget_hours: Quantity | None = None


class NodeMove(BaseModel):
    """``POST .../nodes/{id}/move``. ``new_parent_id: null`` moves the node to the root."""

    model_config = ConfigDict(extra="forbid")

    new_parent_id: uuid.UUID | None


class NodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    level_def_id: uuid.UUID
    parent_id: uuid.UUID | None
    code: str
    display_code: str
    display_path: str
    name: str | None
    discipline_code: str | None
    wbs_code: str | None
    activity_id: str | None
    external_source: str | None
    external_id: str | None
    synced_at: datetime | None
    planned_start: date | None
    planned_finish: date | None
    spatial_location: PointZGeoJSON | None
    custom_fields: dict[str, Any]
    budget_qty: Decimal | None
    uom: str | None
    budget_hours: Decimal | None
    earned_qty: Decimal | None
    status: Literal["active", "retired"]
    superseded_by: list[uuid.UUID]
    derived_from: list[uuid.UUID]
    revision: int
    created_at: datetime
    updated_at: datetime
