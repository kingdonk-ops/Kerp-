"""Input and result shapes of the rule store and ``run_checks`` (T-0018).

``RuleSpec`` is what a caller saves; ``CheckResult``/``CheckFinding`` are what ``run_checks``
returns. Both are frozen at M1 (T-0131). The Rules settings API shapes (T-0034) follow them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

KEY_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,119}$"


class RuleSpec(BaseModel):
    """One rule as saved by its author. Validated and compiled before it is stored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_key: str = Field(pattern=KEY_PATTERN)
    engine: Literal["cel", "regex"]
    expression: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    action: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    severity: Literal["info", "warning", "error"] = "error"
    enabled: bool = True
    locked: bool = False
    sort_order: int = 0
    origin_rule_key: str | None = Field(default=None, pattern=KEY_PATTERN)
    origin_version: int | None = Field(default=None, ge=1)
    label: str | None = Field(default=None, min_length=1, max_length=200)


class CheckFinding(BaseModel):
    """A rule of the set that did not pass. ``message`` is the user-readable reason."""

    model_config = ConfigDict(frozen=True)

    rule_key: str
    rule_version: int
    outcome: Literal["fail", "error"]
    action: str
    severity: str
    message: str


class CheckResult(BaseModel):
    """Outcome of ``run_checks``. ``blocked`` is True when any ``block`` rule did not pass."""

    model_config = ConfigDict(frozen=True)

    run_id: uuid.UUID
    rule_set: str
    rule_set_version: int
    evaluated: int
    findings: tuple[CheckFinding, ...]

    @property
    def blocked(self) -> bool:
        return any(f.action == "block" for f in self.findings)

    @property
    def passed(self) -> bool:
        return not self.blocked

    @property
    def reason(self) -> str | None:
        """The blocking findings' messages, for the user who tried the transition."""
        messages = [f.message for f in self.findings if f.action == "block"]
        return "; ".join(messages) if messages else None


# --- Rules settings API (T-0034, ADR 0004 §3, §5, §7) --------------------------------------


class RuleInput(BaseModel):
    """Body of ``PUT .../rules/{rule_key}``: add a rule, or edit the rule with that key.

    The origin of a rule is set by copies only; ``locked`` is honoured on the tenant layer
    (locking a default) and refused on the project layer.
    """

    model_config = ConfigDict(extra="forbid")

    engine: Literal["cel", "regex"]
    expression: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    action: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    severity: Literal["info", "warning", "error"] = "error"
    enabled: bool = True
    locked: bool = False
    sort_order: int = 0
    label: str | None = Field(default=None, min_length=1, max_length=200)

    def to_spec(self, rule_key: str) -> RuleSpec:
        return RuleSpec.model_validate({"rule_key": rule_key, **self.model_dump()})


class RuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_key: str
    label: str | None
    engine: str
    expression: str
    params: dict[str, Any]
    action: str
    severity: str
    enabled: bool
    locked: bool
    sort_order: int
    origin_rule_key: str | None
    origin_version: int | None
    unlock_adr_reference: str | None
    version: int
    deleted_at: datetime | None


class RuleSetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    kind: str
    layer: str
    version: int
    module: str | None
    seed_version: int | None
    source_pack: str | None
    project_id: uuid.UUID | None
    rules: list[RuleRead]


class UnlockRequestCreate(BaseModel):
    """Body of an unlock request: the ADR that justifies unlocking (ADR 0004 §5)."""

    model_config = ConfigDict(extra="forbid")

    adr_reference: str = Field(min_length=1, max_length=200, pattern=r"\S")
    reason: str | None = Field(default=None, max_length=2000)


class UnlockRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_set_id: uuid.UUID
    rule_key: str
    rule_version: int
    adr_reference: str
    reason: str | None
    status: str
    requested_by: uuid.UUID
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    created_at: datetime


# --- upgrade offers, drift report, "Try it" (T-0036, ADR 0004 §4, §6, Consequences) ---------

# ``removed_by_<layer>``: the layer deleted the rule and the default has changed since; shown,
# never re-added (ADR 0004 §3). ``key_in_use``: the layer added its own rule under the key of
# a new default. Neither can be accepted.
UpdateChange = Literal[
    "new", "changed", "removed", "removed_by_project", "removed_by_tenant", "key_in_use"
]
DriftKind = Literal["added", "modified", "deleted", "missing", "default_removed"]


class RuleUpdateRead(BaseModel):
    """One rule of an upgrade offer: the set's rule (``current``) next to the new default."""

    rule_key: str
    change: UpdateChange
    acceptable: bool
    fields: list[str]  # the fields that differ (``changed`` only)
    current: RuleRead | None
    default: RuleRead | None


class RuleSetUpdatesRead(BaseModel):
    """``GET .../rule-sets/{set}/updates``: the per-rule diff against the parent default."""

    rule_set_id: uuid.UUID
    key: str
    kind: str
    layer: str
    version: int
    default_rule_set_id: uuid.UUID
    default_layer: str
    default_version: int
    default_seed_version: int | None
    available: int  # how many updates can be accepted
    updates: list[RuleUpdateRead]


class DriftEntryRead(BaseModel):
    """A project rule that differs from the tenant default (or a default the project lacks)."""

    project_id: uuid.UUID
    rule_set_id: uuid.UUID
    rule_set_key: str
    kind: str
    rule_key: str
    drift: DriftKind
    fields: list[str]  # the fields that differ (``modified`` only)
    project_version: int | None
    default_version: int | None


class TryRuleInput(BaseModel):
    """Body of ``POST .../rules/{rule_key}/try``: a sample context and, optionally, a draft
    rule to try instead of the stored one (nothing is saved either way)."""

    model_config = ConfigDict(extra="forbid")

    context: dict[str, Any] = Field(default_factory=dict)
    rule: RuleInput | None = None


class TryError(BaseModel):
    code: str
    message: str


class RegexMatch(BaseModel):
    text: str
    start: int
    end: int
    groups: dict[str, str | None]


class TryResult(BaseModel):
    """What a rule does with a sample context (ADR 0004 §6). Never persisted.

    CEL rules: ``pass`` when the expression returns ``true``, ``fail`` on any other value,
    ``error`` when it cannot be evaluated; ``value`` is the result (KERP CEL JSON). A ``check``
    rule that does not pass also returns the ``finding`` ``run_checks`` would log. Regex rules
    (sample ``{"text": ...}``): ``pass`` when the pattern matches (``autolink``: every match;
    ``p6_grammar``: a full match), with the matches and their named groups.
    """

    rule_key: str
    rule_version: int | None  # None for a draft
    kind: str
    engine: str
    outcome: Literal["pass", "fail", "error"]
    value: Any = None
    error: TryError | None = None
    finding: CheckFinding | None = None
    matches: list[RegexMatch] = Field(default_factory=list)
