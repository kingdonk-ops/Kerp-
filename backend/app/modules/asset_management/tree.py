"""Portable ``ltree`` column type and path expressions (T-0101, diff §2.2).

PostgreSQL: the column is ``ltree`` (GiST-indexed) and the expressions compile to the ltree
operators (``<@``, ``@>``, ``subpath``). Anything else (the SQLite fast suite): the same
dot-separated path as ``TEXT`` with prefix comparisons, which are equivalent because every
label is a fixed-width node id (``uuid.hex``: no dots, no ``LIKE`` wildcards).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, Text, TypeDecorator
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.types import TypeEngine, UserDefinedType


class _Ltree(UserDefinedType[str]):
    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "ltree"


# Reflection (Alembic's ``check``) reads the column back as this type.
ischema_names.setdefault("ltree", _Ltree)


class LtreePath(TypeDecorator[str]):
    """A materialised path: ``ltree`` on PostgreSQL, ``TEXT`` elsewhere."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        return dialect.type_descriptor(_Ltree() if dialect.name == "postgresql" else Text())


def child_path(parent_path: str | None, node_id: uuid.UUID) -> str:
    """The path of node ``node_id`` under ``parent_path``. The label is the node's id, so a
    path never depends on a (free text) code."""
    return node_id.hex if parent_path is None else f"{parent_path}.{node_id.hex}"


def is_within(path: str, ancestor: str) -> bool:
    """Whether ``path`` is ``ancestor`` or below it (the Python twin of ``within``)."""
    return path == ancestor or path.startswith(ancestor + ".")


class within(FunctionElement[bool]):  # noqa: N801  (SQL function style)
    """``within(column, path)``: the row is ``path`` or one of its descendants."""

    type = Boolean()
    inherit_cache = True


class contains(FunctionElement[bool]):  # noqa: N801
    """``contains(column, path)``: the row is ``path`` or one of its ancestors."""

    type = Boolean()
    inherit_cache = True


class rebased(FunctionElement[str]):  # noqa: N801
    """``rebased(column, old, new)``: the row's path with the prefix ``old`` replaced by
    ``new``, for rows ``within(column, old)``."""

    type = LtreePath()
    inherit_cache = True


def _args(element: FunctionElement[Any], compiler: SQLCompiler, **kw: object) -> list[str]:
    return [compiler.process(clause, **kw) for clause in element.clauses]


@compiles(within)
def _within(element: within, compiler: SQLCompiler, **kw: object) -> str:
    column, path = _args(element, compiler, **kw)
    return f"({column} = {path} OR {column} LIKE {path} || '.%')"


@compiles(within, "postgresql")
def _pg_within(element: within, compiler: SQLCompiler, **kw: object) -> str:
    column, path = _args(element, compiler, **kw)
    return f"{column} <@ CAST({path} AS ltree)"


@compiles(contains)
def _contains(element: contains, compiler: SQLCompiler, **kw: object) -> str:
    column, path = _args(element, compiler, **kw)
    return f"({path} = {column} OR {path} LIKE {column} || '.%')"


@compiles(contains, "postgresql")
def _pg_contains(element: contains, compiler: SQLCompiler, **kw: object) -> str:
    column, path = _args(element, compiler, **kw)
    return f"{column} @> CAST({path} AS ltree)"


@compiles(rebased)
def _rebased(element: rebased, compiler: SQLCompiler, **kw: object) -> str:
    column, old, new = _args(element, compiler, **kw)
    return f"{new} || substr({column}, length({old}) + 1)"


@compiles(rebased, "postgresql")
def _pg_rebased(element: rebased, compiler: SQLCompiler, **kw: object) -> str:
    column, old, new = _args(element, compiler, **kw)
    old, new = f"CAST({old} AS ltree)", f"CAST({new} AS ltree)"
    # ``subpath`` refuses an offset equal to the path length, hence the moved root's own case.
    return (
        f"CASE WHEN {column} = {old} THEN {new} ELSE {new} || subpath({column}, nlevel({old})) END"
    )
