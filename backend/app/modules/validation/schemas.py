"""Input and result shapes of the rule store and ``run_checks`` (T-0018).

``RuleSpec`` is what a caller saves; ``CheckResult``/``CheckFinding`` are what ``run_checks``
returns. Both are frozen at M1 (T-0131).
"""

from __future__ import annotations

import uuid
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
