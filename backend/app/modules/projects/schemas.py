"""Request and response shapes of the projects API, with the settings validation (T-0012).

* ``site_timezone``: an IANA zone name from the ``tzdata`` package (e.g. ``Australia/Perth``).
* ``currency_code``: an active ISO 4217 code. ``locale``: a BCP 47 tag ``ll[-Ssss][-RR|-nnn]``.
* ``spatial_bounds``: a GeoJSON (RFC 7946, WGS84) ``Polygon``, 2D positions, closed rings,
  at most ``MAX_TOTAL_POSITIONS`` positions across all rings.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.core.locale import Currency, Locale, TimeZone

MAX_RINGS: Final = 100
MAX_RING_POSITIONS: Final = 10_000
# All rings together (T-0060): a site boundary fits easily, and a body can no longer ask for
# MAX_RINGS x MAX_RING_POSITIONS (1M positions, ~22 MB) of validation work.
MAX_TOTAL_POSITIONS: Final = 10_000


Code = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Status = Literal["ACTIVE", "ON_HOLD", "CLOSED"]
# Strict: a JSON number (integers allowed), never a boolean or a numeric string (RFC 7946).
Coordinate = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class PolygonGeoJSON(BaseModel):
    """A GeoJSON ``Polygon`` in WGS84: ``[lon, lat]`` positions, exterior ring first."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["Polygon"]
    coordinates: list[
        Annotated[list[tuple[Coordinate, Coordinate]], Field(max_length=MAX_RING_POSITIONS)]
    ] = Field(min_length=1, max_length=MAX_RINGS)

    @field_validator("coordinates", mode="before")
    @classmethod
    def _total_positions(cls, rings: object) -> object:
        """Count the positions before validating any of them, so the cap bounds the cost."""
        if isinstance(rings, list) and len(rings) <= MAX_RINGS:
            total = sum(len(ring) for ring in rings if isinstance(ring, list))
            if total > MAX_TOTAL_POSITIONS:
                raise ValueError(
                    f"a polygon may have at most {MAX_TOTAL_POSITIONS} positions in total"
                    f" ({total} given)"
                )
        return rings

    @field_validator("coordinates")
    @classmethod
    def _rings(cls, rings: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
        for ring in rings:
            if len(ring) < 4:
                raise ValueError("a polygon ring needs at least 4 positions")
            if ring[0] != ring[-1]:
                raise ValueError("a polygon ring must be closed (first position == last)")
            for lon, lat in ring:
                if not -180 <= lon <= 180:
                    raise ValueError(f"longitude {lon} is outside -180..180")
                if not -90 <= lat <= 90:
                    raise ValueError(f"latitude {lat} is outside -90..90")
        return rings

    def as_geojson(self) -> dict[str, Any]:
        return {"type": self.type, "coordinates": [[list(p) for p in r] for r in self.coordinates]}


# Fields of ``ProjectCreate`` that, when omitted, take the deployment's default (T-0044).
DEFAULTED_FIELDS: Final = ("status", "site_timezone", "currency_code", "locale")


class ProjectCreate(BaseModel):
    """``POST /projects``. The tenant comes from the token, never from the body.

    ``status``, ``site_timezone``, ``currency_code`` and ``locale`` may be omitted: the service
    then takes the deployment's defaults for new projects (settings module, T-0044). They
    cannot be ``null``.
    """

    model_config = ConfigDict(extra="forbid")

    code: Code
    name: Name
    status: Status | None = None
    site_timezone: TimeZone | None = None
    spatial_bounds: PolygonGeoJSON | None = None
    currency_code: Currency | None = None
    locale: Locale | None = None

    @model_validator(mode="after")
    def _not_null(self) -> Self:
        for name in DEFAULTED_FIELDS:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null (omit it to use the deployment default)")
        return self


class ProjectUpdate(BaseModel):
    """``PATCH /projects/{id}``: only the given fields change; ``spatial_bounds: null`` clears."""

    model_config = ConfigDict(extra="forbid")

    code: Code | None = None
    name: Name | None = None
    status: Status | None = None
    site_timezone: TimeZone | None = None
    spatial_bounds: PolygonGeoJSON | None = None
    currency_code: Currency | None = None
    locale: Locale | None = None

    @model_validator(mode="after")
    def _given(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("give at least one field to change")
        for name in sorted(self.model_fields_set - {"spatial_bounds"}):
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class LobbyVisibility(BaseModel):
    """``PUT /projects/{id}/lobby``: list the project in the lobby for internal users or not
    (T-0040, diff §4.1). PER-09 of the project or a System admin only."""

    model_config = ConfigDict(extra="forbid")

    visible_in_lobby: bool


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    status: Status
    site_timezone: str
    spatial_bounds: PolygonGeoJSON | None
    currency_code: str
    locale: str
    visible_in_lobby: bool
    revision: int
    created_at: datetime
    updated_at: datetime
