"""Request and response shapes of the projects API, with the settings validation (T-0012).

* ``site_timezone``: an IANA zone name from the ``tzdata`` package (e.g. ``Australia/Perth``).
* ``currency_code``: an active ISO 4217 code. ``locale``: a BCP 47 tag ``ll[-Ssss][-RR|-nnn]``.
* ``spatial_bounds``: a GeoJSON (RFC 7946, WGS84) ``Polygon``, 2D positions, closed rings.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from functools import cache
from importlib.resources import files
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.modules.projects.models import DEFAULT_CURRENCY, DEFAULT_LOCALE, DEFAULT_TIMEZONE

# Active ISO 4217 codes (precious metals, SDR and test codes excluded).
_ISO_4217_CODES: Final = """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD BTN
    BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUC CUP CVE CZK DJF DKK DOP DZD EGP
    ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK
    JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK
    MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN
    PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB
    TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAF XCD
    XCG XOF XPF YER ZAR ZMW ZWG ZWL
"""
ISO_4217: Final = frozenset(_ISO_4217_CODES.split())
_LOCALE: Final = re.compile(r"^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$")
MAX_RINGS: Final = 100
MAX_RING_POSITIONS: Final = 10_000


# In the tzdata zone list but not a real location; browsers' Intl rejects it.
_NOT_SITE_ZONES: Final = frozenset({"Factory"})


@cache
def _zones() -> frozenset[str]:
    """IANA zone names from the ``tzdata`` package, not from the host's ``TZPATH``.

    ``zoneinfo.available_timezones()`` lists every file under the host's zoneinfo directory,
    including ``localtime`` and ``posixrules``, whose meaning depends on the host.
    """
    names = (files("tzdata") / "zones").read_text(encoding="utf-8").split()
    return frozenset(names) - _NOT_SITE_ZONES


def _iana(value: str) -> str:
    if value not in _zones():
        raise ValueError(f"{value!r} is not an IANA timezone (e.g. 'Australia/Perth')")
    return value


def _currency(value: str) -> str:
    if value not in ISO_4217:
        raise ValueError(f"{value!r} is not an active ISO 4217 currency code (e.g. 'AUD')")
    return value


def _locale(value: str) -> str:
    if not _LOCALE.match(value):
        raise ValueError(f"{value!r} is not a BCP 47 locale tag (e.g. 'en-AU')")
    return value


TimeZone = Annotated[str, AfterValidator(_iana)]
Currency = Annotated[str, AfterValidator(_currency)]
Locale = Annotated[str, AfterValidator(_locale)]
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


class ProjectCreate(BaseModel):
    """``POST /projects``. The tenant comes from the token, never from the body."""

    model_config = ConfigDict(extra="forbid")

    code: Code
    name: Name
    status: Status = "ACTIVE"
    site_timezone: TimeZone = DEFAULT_TIMEZONE
    spatial_bounds: PolygonGeoJSON | None = None
    currency_code: Currency = DEFAULT_CURRENCY
    locale: Locale = DEFAULT_LOCALE


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
    revision: int
    created_at: datetime
    updated_at: datetime
