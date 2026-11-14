"""System default rule sets: versioned seed files shipped with each module (ADR 0004 §2.1).

Each module may ship ``app/modules/<module>/rules/defaults/*.yaml``; one file is one rule set:

.. code-block:: yaml

    key: demo.submit          # rule-set key, unique across all modules
    kind: check
    version: 3                # bump on every change; the system layer reloads on a new version
    source_pack: null         # optional: the client pack the set belongs to (ADR 0003 §4)
    rules:
      - rule_key: calibration_valid
        engine: cel
        expression: "fields.calibrated == true"
        action: block
        severity: error
        locked: true          # mandatory gate: a project cannot change it (§5)
        label: Calibration valid
        params: {message: "Calibration expired"}

Every rule is compiled when the files are loaded (``RuleCompileError`` names the file), so a
broken default never reaches a tenant. Loading reads files only; writing the sets into a
tenant's ``system`` layer is ``service.sync_system_layer``.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.modules.validation.compiler import RuleCompileError, compile_rule
from app.modules.validation.models import RULE_KINDS
from app.modules.validation.schemas import KEY_PATTERN, RuleSpec

__all__ = ["MODULES_ROOT", "DefaultsError", "SeedRuleSet", "load_defaults"]

MODULES_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULTS_GLOB: Final = "*/rules/defaults/*.yaml"


class DefaultsError(ValueError):
    """A seed file is malformed, does not compile, or repeats a rule-set key."""


class SeedRuleSet(BaseModel):
    """One seed file: a system default rule set of one module."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    module: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    key: str = Field(pattern=KEY_PATTERN)
    kind: str
    version: int = Field(ge=1)
    source_pack: str | None = Field(default=None, min_length=1, max_length=120)
    rules: tuple[RuleSpec, ...] = ()

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, kind: str) -> str:
        if kind not in RULE_KINDS:
            raise ValueError(f"kind must be one of {list(RULE_KINDS)}")
        return kind

    @field_validator("rules")
    @classmethod
    def _plain_unique_rules(cls, rules: tuple[RuleSpec, ...]) -> tuple[RuleSpec, ...]:
        keys = [rule.rule_key for rule in rules]
        repeated = sorted({key for key in keys if keys.count(key) > 1})
        if repeated:
            raise ValueError(f"rule_key repeated: {repeated}")
        if any(rule.origin_rule_key is not None or rule.origin_version for rule in rules):
            raise ValueError("system defaults have no origin_rule_key / origin_version")
        return rules


def _parse(path: Path, module: str) -> SeedRuleSet:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DefaultsError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise DefaultsError(f"{path}: a seed file is one mapping (a rule set)")
    try:
        seed = SeedRuleSet.model_validate({**raw, "module": module})
    except ValidationError as exc:
        raise DefaultsError(f"{path}: {exc}") from exc
    for rule in seed.rules:
        try:
            compile_rule(seed.kind, rule.engine, rule.expression, rule.params, rule.action)
        except RuleCompileError as exc:
            raise DefaultsError(f"{path}: rule {rule.rule_key!r}: {exc}") from exc
    return seed


def load_defaults(root: Path = MODULES_ROOT) -> tuple[SeedRuleSet, ...]:
    """Every seed file under ``root/<module>/rules/defaults/``, sorted by key.

    Raises ``DefaultsError`` on the first broken file or a rule-set key used twice.
    """
    seeds = [_parse(path, path.parents[2].name) for path in sorted(root.glob(DEFAULTS_GLOB))]
    return _unique(seeds)


def _unique(seeds: Iterable[SeedRuleSet]) -> tuple[SeedRuleSet, ...]:
    by_key: dict[str, SeedRuleSet] = {}
    for seed in seeds:
        other = by_key.get(seed.key)
        if other is not None:
            raise DefaultsError(
                f"rule set {seed.key!r} is defined by both {other.module} and {seed.module}"
            )
        by_key[seed.key] = seed
    return tuple(by_key[key] for key in sorted(by_key))
