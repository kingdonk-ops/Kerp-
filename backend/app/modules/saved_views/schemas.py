"""Shapes of a saved register view (T-0020, register-views spec §2, §3).

The configuration is stored exactly as these models dump it, at ``SCHEMA_VERSION``:

* ``columns``: ordered ``{id, width, pinned, visible}``; ``id`` is a catalogue column id.
* ``filter``: the §2 AST, an AND/OR tree of ``{column, op, value}`` conditions. Only the
  structure, the op names and each op's value shape are checked here; whether a column
  exists and supports an op is the filter compiler's job (catalogue whitelist, E-01-01).
* ``sort``: ordered ``{column, direction}``. ``group_by``: columns, aggregates, totals row.
* ``conditional_format``: ``{condition (CEL over the row), style}`` rules.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag,
    model_validator,
)

from app.core.cel import compile_expression

SCHEMA_VERSION: Final = 1
MAX_COLUMNS: Final = 200
MAX_SORT: Final = 10
MAX_GROUP_COLUMNS: Final = 5
MAX_AGGREGATES: Final = 50
MAX_FORMAT_RULES: Final = 50
MAX_FILTER_DEPTH: Final = 6
MAX_FILTER_NODES: Final = 200
MAX_GROUP_CONDITIONS: Final = 50
MAX_LIST_VALUES: Final = 500
MAX_CONDITION_LENGTH: Final = 1000

# Catalogue ids: ``code``, ``custom_fields.budget_hrs``, ``ancestor.site.code``, ...
ColumnId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")]
RegisterKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{1,63}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Visibility = Literal["private", "team", "project", "portal"]
Density = Literal["compact", "standard", "comfortable"]

# §2 ops, grouped by the value they take.
NO_VALUE_OPS: Final = frozenset({"is_empty", "overdue", "me", "my_teams"})
LIST_OPS: Final = frozenset({"in", "not_in"})
SCALAR_OPS: Final = frozenset({"eq", "neq", "contains", "starts_with", "gt", "lt"})
FilterOp = Literal[
    "eq",
    "neq",
    "in",
    "not_in",
    "contains",
    "starts_with",
    "is_empty",
    "between",
    "gt",
    "lt",
    "within_next",
    "overdue",
    "me",
    "my_teams",
    "subtree_of",
]
_DURATION: Final = re.compile(r"^[1-9][0-9]{0,3}[hdwm]$")  # within_next: 7d, 12h, 2w, 3m


def _is_scalar(value: object) -> bool:
    return isinstance(value, str | int | float | bool)


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


class FilterCondition(BaseModel):
    """One condition ``{column, op, value}``; the value shape depends on the op."""

    model_config = ConfigDict(extra="forbid")

    column: ColumnId
    op: FilterOp
    value: Any = None

    @model_validator(mode="after")
    def _value_fits_op(self) -> Self:
        op, value = self.op, self.value
        if op in NO_VALUE_OPS:
            ok = value is None
        elif op in LIST_OPS:
            ok = (
                isinstance(value, list)
                and 0 < len(value) <= MAX_LIST_VALUES
                and all(_is_scalar(v) for v in value)
            )
        elif op == "between":
            ok = isinstance(value, list) and len(value) == 2 and all(_is_scalar(v) for v in value)
        elif op == "within_next":
            ok = isinstance(value, str) and bool(_DURATION.match(value))
        elif op == "subtree_of":
            ok = _is_uuid(value)
        else:
            ok = _is_scalar(value)
        if not ok:
            raise ValueError(f"value {value!r} does not fit op {op!r}")
        return self


def _node_kind(value: object) -> str:
    if isinstance(value, dict):
        return "group" if "conditions" in value else "condition"
    return "group" if isinstance(value, FilterGroup) else "condition"


class FilterGroup(BaseModel):
    """``{op: and|or, conditions: [...]}``; conditions are conditions or nested groups."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["and", "or"]
    conditions: list[FilterNode] = Field(min_length=1, max_length=MAX_GROUP_CONDITIONS)


FilterNode = Annotated[
    Annotated[FilterGroup, Tag("group")] | Annotated[FilterCondition, Tag("condition")],
    Discriminator(_node_kind),
]
FilterGroup.model_rebuild()


def filter_columns(node: FilterGroup | FilterCondition | None) -> list[str]:
    """Column ids the filter references, in tree order (repeats removed)."""
    found: list[str] = []
    stack: list[FilterGroup | FilterCondition] = [node] if node is not None else []
    while stack:
        current = stack.pop(0)
        if isinstance(current, FilterGroup):
            stack[0:0] = current.conditions
        elif current.column not in found:
            found.append(current.column)
    return found


def _check_filter_size(node: FilterGroup | FilterCondition | None) -> None:
    nodes = 0
    stack: list[tuple[FilterGroup | FilterCondition, int]] = [(node, 1)] if node else []
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if depth > MAX_FILTER_DEPTH:
            raise ValueError(f"the filter is nested deeper than {MAX_FILTER_DEPTH} levels")
        if isinstance(current, FilterGroup):
            stack.extend((child, depth + 1) for child in current.conditions)
    if nodes > MAX_FILTER_NODES:
        raise ValueError(f"the filter has more than {MAX_FILTER_NODES} nodes")


class ViewColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ColumnId
    width: int | None = Field(default=None, ge=24, le=2000)
    pinned: Literal["left", "right"] | None = None
    visible: bool = True


class SortKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: ColumnId
    direction: Literal["asc", "desc"] = "asc"


class Aggregate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: ColumnId
    fn: Literal["sum", "avg", "min", "max", "count", "count-distinct"]


class GroupBy(BaseModel):
    """Group rows by ``columns`` (outermost first), with aggregates and a totals row."""

    model_config = ConfigDict(extra="forbid")

    columns: list[ColumnId] = Field(min_length=1, max_length=MAX_GROUP_COLUMNS)
    aggregates: list[Aggregate] = Field(default_factory=list, max_length=MAX_AGGREGATES)
    totals: bool = False


def _cel(expression: str) -> str:
    error = compile_expression(expression)
    if error is not None:
        raise ValueError(f"invalid CEL condition: {error.message}")
    return expression


class FormatStyle(BaseModel):
    """Presentation tokens only (the client maps them to theme colours), never raw CSS."""

    model_config = ConfigDict(extra="forbid")

    tone: Literal["grey", "blue", "green", "amber", "red"]
    bold: bool = False


class FormatRule(BaseModel):
    """``condition`` (CEL over the row, e.g. ``Scoped == "No"``) -> ``style``."""

    model_config = ConfigDict(extra="forbid")

    condition: Annotated[
        str,
        StringConstraints(min_length=1, max_length=MAX_CONDITION_LENGTH),
        AfterValidator(_cel),
    ]
    style: FormatStyle


def _unique(ids: list[str], what: str) -> None:
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise ValueError(f"{what} lists {repeated[0]!r} twice")


class ViewConfig(BaseModel):
    """The register state a view saves and restores."""

    model_config = ConfigDict(extra="forbid")

    columns: list[ViewColumn] = Field(default_factory=list, max_length=MAX_COLUMNS)
    filter: FilterNode | None = None
    sort: list[SortKey] = Field(default_factory=list, max_length=MAX_SORT)
    group_by: GroupBy | None = None
    conditional_format: list[FormatRule] = Field(default_factory=list, max_length=MAX_FORMAT_RULES)
    density: Density = "standard"

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _unique([c.id for c in self.columns], "columns")
        _unique([s.column for s in self.sort], "sort")
        if self.group_by is not None:
            _unique(self.group_by.columns, "group_by")
        _check_filter_size(self.filter)
        return self

    def referenced_columns(self) -> list[str]:
        """Every column id the view uses, in order: columns, filter, sort, group-by."""
        ids = [c.id for c in self.columns] + filter_columns(self.filter)
        ids += [s.column for s in self.sort]
        if self.group_by is not None:
            ids += self.group_by.columns + [a.column for a in self.group_by.aggregates]
        return list(dict.fromkeys(ids))


class SavedViewCreate(ViewConfig):
    """``POST .../views`` (also "save as"). The owner is the caller; the project is the path's.

    ``team_id`` names the team of a ``team`` view and is given for no other visibility.
    ``is_default`` is allowed on ``private`` (the caller's default) and ``project`` (the
    project default, Project Administrator only) views.
    """

    register_key: RegisterKey
    name: Name
    visibility: Visibility = "private"
    team_id: uuid.UUID | None = None
    is_default: bool = False


_CONFIG_FIELDS: Final = frozenset(ViewConfig.model_fields)
_NULLABLE: Final = frozenset({"filter", "group_by", "team_id"})


class SavedViewUpdate(BaseModel):
    """``PATCH .../views/{id}``: edit, rename, share, make default; only given fields change.

    ``filter``, ``group_by`` and ``team_id`` may be ``null`` (clears them). The register
    of a view never changes.
    """

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    visibility: Visibility | None = None
    team_id: uuid.UUID | None = None
    is_default: bool | None = None
    columns: list[ViewColumn] | None = Field(default=None, max_length=MAX_COLUMNS)
    filter: FilterNode | None = None
    sort: list[SortKey] | None = Field(default=None, max_length=MAX_SORT)
    group_by: GroupBy | None = None
    conditional_format: list[FormatRule] | None = Field(default=None, max_length=MAX_FORMAT_RULES)
    density: Density | None = None

    @model_validator(mode="after")
    def _given(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("give at least one field to change")
        for name in sorted(self.model_fields_set - _NULLABLE):
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self

    def config_changes(self) -> dict[str, Any]:
        """The given configuration fields (not name/sharing)."""
        return {name: getattr(self, name) for name in self.model_fields_set & _CONFIG_FIELDS}


class ResolvedColumn(ViewColumn):
    """A saved column as the register shows it; ``missing``: no longer in the catalogue."""

    missing: bool = False


class SavedViewRead(BaseModel):
    """A view as loaded: its configuration plus which referenced columns are missing.

    ``missing_columns`` lists every id (from columns, filter, sort, group-by) the register's
    catalogue no longer has; the register skips them. It is empty when the register has
    no catalogue registered (nothing can be checked).
    """

    id: uuid.UUID
    project_id: uuid.UUID
    register_key: str
    name: str
    owner_user_id: uuid.UUID
    visibility: Visibility
    team_id: uuid.UUID | None
    is_default: bool
    columns: list[ResolvedColumn]
    filter: FilterNode | None
    sort: list[SortKey]
    group_by: GroupBy | None
    conditional_format: list[FormatRule]
    density: Density
    schema_version: int
    missing_columns: list[str]
    revision: int
    created_at: datetime
    updated_at: datetime
