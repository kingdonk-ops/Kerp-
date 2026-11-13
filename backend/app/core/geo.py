"""Portable spatial column type: PostGIS ``geometry`` on PostgreSQL, GeoJSON text elsewhere.

Master spec §1.2/§4.1: spatial data is PostGIS in EPSG:4326 (WGS84). The ORM value is a
GeoJSON geometry ``dict`` on every dialect, so services and schemas never see WKB:

* PostgreSQL: the column is ``geometry(<kind>, 4326)``. Writes go through
  ``ST_SetSRID(ST_GeomFromGeoJSON(...), 4326)`` and reads through ``ST_AsGeoJSON(...)``, so
  PostGIS enforces the geometry kind and SRID and spatial SQL works on the column.
* Anything else (the SQLite fast suite): the same GeoJSON stored as ``TEXT``; no spatial SQL.

Validate the GeoJSON in the API schema before it reaches the column (see the projects module).
"""

from __future__ import annotations

import json
from typing import Any, Final

from sqlalchemy import Text, TypeDecorator
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.elements import BindParameter, ColumnElement
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.types import TypeEngine, UserDefinedType

WGS84: Final = 4326


class _PostgisGeometry(UserDefinedType[Any]):
    cache_ok = True

    def __init__(self, kind: str = "Geometry", srid: int | str = WGS84) -> None:
        self.kind = kind
        self.srid = int(srid)

    def get_col_spec(self, **kw: object) -> str:
        return f"geometry({self.kind},{self.srid})"


# Reflection (Alembic's ``check``) reads ``geometry(Polygon,4326)`` back as this type, so a
# changed kind or SRID shows up as a diff instead of an "unrecognised type" warning.
ischema_names.setdefault("geometry", _PostgisGeometry)


class _GeoJSONToGeometry(FunctionElement[Any]):
    """The bound GeoJSON text as a WGS84 geometry (PostgreSQL); the text itself elsewhere."""

    inherit_cache = True


class _GeometryToGeoJSON(FunctionElement[Any]):
    """The column as GeoJSON text (PostgreSQL); the column itself elsewhere."""

    inherit_cache = True


@compiles(_GeoJSONToGeometry)
@compiles(_GeometryToGeoJSON)
def _passthrough(element: FunctionElement[Any], compiler: SQLCompiler, **kw: object) -> str:
    return compiler.process(element.clauses, **kw)


@compiles(_GeoJSONToGeometry, "postgresql")
def _pg_from_geojson(element: FunctionElement[Any], compiler: SQLCompiler, **kw: object) -> str:
    value = compiler.process(element.clauses, **kw)
    return f"ST_SetSRID(ST_GeomFromGeoJSON(CAST({value} AS TEXT)), {WGS84})"


@compiles(_GeometryToGeoJSON, "postgresql")
def _pg_to_geojson(element: FunctionElement[Any], compiler: SQLCompiler, **kw: object) -> str:
    return f"ST_AsGeoJSON({compiler.process(element.clauses, **kw)})"


class GeoJSONGeometry(TypeDecorator[dict[str, Any]]):
    """A WGS84 geometry of one ``kind`` (``"Polygon"``, ``"Point"``, ...) as a GeoJSON dict."""

    impl = Text
    cache_ok = True

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PostgisGeometry(self.kind))
        return dialect.type_descriptor(Text())

    def bind_expression(self, bindvalue: BindParameter[dict[str, Any]]) -> ColumnElement[Any]:
        return _GeoJSONToGeometry(bindvalue)

    def column_expression(self, colexpr: ColumnElement[Any]) -> ColumnElement[Any]:
        expression = _GeometryToGeoJSON(colexpr)
        expression.type = self  # so the result still goes through ``process_result_value``
        return expression

    def process_bind_param(self, value: dict[str, Any] | None, dialect: Dialect) -> str | None:
        return None if value is None else json.dumps(value, separators=(",", ":"))

    def process_result_value(self, value: str | None, dialect: Dialect) -> dict[str, Any] | None:
        if value is None:
            return None
        loaded: dict[str, Any] = json.loads(value)
        return loaded
