"""Default-upgrade offers, the tenant drift report and "Try it" (T-0036).

ADR 0004 §4, §6 and Consequences. Built on the rule store in ``service.py``; other modules
call these functions, never the tables.

* **Upgrade offers (§4).** A copy (project → tenant default, tenant → system default) is
  never changed by its default. ``pending_updates`` diffs the set against the **current**
  default rule by rule; ``accept_update`` applies one rule (versioned, audited). Nothing is
  applied automatically.
* **Drift report (Consequences).** ``drift_report`` lists, for every project the session can
  read, the project rules that differ from the tenant defaults.
* **Try it (§6).** ``try_rule`` evaluates a stored rule or a draft against a sample context and
  returns the result and the finding ``run_checks`` would log, without writing anything.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Mapping
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cel import CelError, evaluate
from app.core.tenancy.scope import get_or_404
from app.modules.validation.compiler import compile_regex, compile_rule
from app.modules.validation.models import Rule, RuleSet
from app.modules.validation.schemas import (
    CheckFinding,
    DriftEntryRead,
    RegexMatch,
    RuleRead,
    RuleSetUpdatesRead,
    RuleSpec,
    RuleUpdateRead,
    TryError,
    TryResult,
)
from app.modules.validation.service import (
    _EDIT_FIELDS,
    RuleNotFoundError,
    RuleStoreError,
    _context,
    _guard_reset_keeps_locks,
    _guard_source,
    _message,
    _parent_set,
    _record_change,
    _reset_to,
    _tenant_context,
    list_rules,
)

__all__ = [
    "MAX_SAMPLE_CONTEXT_BYTES",
    "MAX_SAMPLE_DEPTH",
    "MAX_SAMPLE_TEXT",
    "SampleContextError",
    "accept_update",
    "drift_report",
    "pending_updates",
    "try_rule",
]

ACCEPTABLE: Final = frozenset({"new", "changed", "removed"})
MAX_SAMPLE_TEXT: Final = 20_000  # characters of sample text a regex rule is tried on
MAX_SAMPLE_CONTEXT_BYTES: Final = 64 * 1024  # compact JSON of a CEL sample (as job payloads)
MAX_SAMPLE_DEPTH: Final = 32  # nested objects/lists in a CEL sample, the context itself = 1
MAX_MATCHES: Final = 100  # matches returned by "Try it" on an autolink rule


class SampleContextError(ValueError):
    """The sample context cannot be evaluated (malformed, reserved key): the API answers 422."""


@dataclasses.dataclass(frozen=True)
class _Candidate:
    """The rule being tried: a stored rule's values, or a draft's (``version`` None)."""

    rule_key: str
    version: int | None
    engine: str
    expression: str
    params: Mapping[str, Any]
    action: str
    severity: str

    @classmethod
    def of(cls, rule: Rule | RuleSpec, rule_key: str, version: int | None) -> _Candidate:
        names = ("engine", "expression", "params", "action", "severity")
        return cls(rule_key, version, **{name: getattr(rule, name) for name in names})


# --- upgrade offers (ADR 0004 §4) ---------------------------------------------------------------


def _differing(rule: Rule, default: Rule) -> list[str]:
    """The editable fields in which ``rule`` differs from ``default``, in field order."""
    return [name for name in _EDIT_FIELDS if getattr(rule, name) != getattr(default, name)]


def _diff(
    rule_set: RuleSet, mine: Mapping[str, Rule], defaults: Mapping[str, Rule]
) -> list[RuleUpdateRead]:
    updates: list[RuleUpdateRead] = []

    def offer(
        key: str, change: str, rule: Rule | None, default: Rule | None, fields: list[str]
    ) -> None:
        updates.append(
            RuleUpdateRead.model_validate(
                {
                    "rule_key": key,
                    "change": change,
                    "acceptable": change in ACCEPTABLE,
                    "fields": fields,
                    "current": None if rule is None else RuleRead.model_validate(rule),
                    "default": None if default is None else RuleRead.model_validate(default),
                }
            )
        )

    for key, default in defaults.items():
        rule = mine.get(key)
        if rule is None:
            offer(key, "new", None, default, [])
        elif rule.origin_rule_key is None:
            offer(key, "key_in_use", rule, default, [])
        elif (rule.origin_version or 0) >= default.version:
            continue  # the default has not changed since it was copied
        elif rule.deleted_at is not None:
            offer(key, f"removed_by_{rule_set.layer}", rule, default, [])
        elif fields := _differing(rule, default):
            offer(key, "changed", rule, default, fields)
    for key, rule in mine.items():
        if key not in defaults and rule.origin_rule_key is not None and rule.deleted_at is None:
            offer(key, "removed", rule, None, [])
    return sorted(updates, key=lambda u: u.rule_key)


def _state(session: Session, rule_set: RuleSet) -> tuple[RuleSet, dict[str, Rule], dict[str, Rule]]:
    parent = _parent_set(session, rule_set)
    mine = {rule.rule_key: rule for rule in list_rules(session, rule_set)}
    defaults = {r.rule_key: r for r in list_rules(session, parent) if r.deleted_at is None}
    return parent, mine, defaults


def pending_updates(session: Session, rule_set_id: uuid.UUID) -> RuleSetUpdatesRead:
    """The per-rule diff of a project (or tenant) set against its current default.

    ``new``: a default the set lacks. ``changed``: the default has a newer version than the
    one the rule was copied from and its values differ (``fields`` names them).
    ``removed``: the default no longer has a rule the set copied. ``removed_by_<layer>``: the
    set deleted its copy and the default has changed since the copy (not necessarily since
    the delete) — shown until the set is reset, never re-added (ADR 0004 §3).
    ``key_in_use``: the set added its own rule under a default's key. Only the first three can
    be accepted. Reads only; nothing is applied.
    """
    _context(session)
    rule_set = get_or_404(session, RuleSet, rule_set_id)
    parent, mine, defaults = _state(session, rule_set)
    updates = _diff(rule_set, mine, defaults)
    return RuleSetUpdatesRead(
        rule_set_id=rule_set.id,
        key=rule_set.key,
        kind=rule_set.kind,
        layer=rule_set.layer,
        version=rule_set.version,
        default_rule_set_id=parent.id,
        default_layer=parent.layer,
        default_version=parent.version,
        default_seed_version=parent.seed_version,
        available=sum(u.acceptable for u in updates),
        updates=updates,
    )


def accept_update(
    session: Session,
    rule_set_id: uuid.UUID,
    rule_key: str,
    *,
    actor_id: uuid.UUID | None = None,
    source: str = "api",
) -> Rule:
    """Apply the offered update of one rule, and only that rule (ADR 0004 §4).

    ``new``/``changed``: the rule becomes a copy of the current default (``origin_version``
    = the default's version). ``removed``: the rule is soft-deleted (its key stays taken).
    The rule's and the set's versions go up and the change is ledgered as
    ``rule_update_accepted``. A rule the layer deleted or added itself is refused
    (``RuleStoreError``), as is anything not on offer (``RuleNotFoundError``). On the tenant
    layer, accepting may not delete or unlock a locked rule (two-person unlock, §5).
    """
    context = _context(session)
    _guard_source(source)
    actor = actor_id or context.user_id
    rule_set = get_or_404(session, RuleSet, rule_set_id)
    parent, mine, defaults = _state(session, rule_set)
    update = next((u for u in _diff(rule_set, mine, defaults) if u.rule_key == rule_key), None)
    if update is None:
        raise RuleNotFoundError(f"there is no update to rule {rule_key!r} on offer")
    if update.change == "key_in_use":
        raise RuleStoreError(f"rule {rule_key!r} was added on this layer; delete it first")
    if not update.acceptable:
        raise RuleStoreError(f"rule {rule_key!r} was deleted on this layer; reset it instead")
    rule = mine.get(rule_key)
    extra: dict[str, str | int | None] = {"change": update.change, "from_layer": parent.layer}
    if update.change == "removed":
        assert rule is not None  # a removed update always names the set's rule
        _guard_reset_keeps_locks(rule_set, {rule_key: rule}, [])
        rule.soft_delete()
        rule.version += 1
        rule.updated_by = actor
        rule.source = source
        _record_change(session, rule_set, rule, "rule_update_accepted", actor, source, **extra)
        return rule
    default = defaults[rule_key]
    _guard_reset_keeps_locks(rule_set, {rule_key: rule} if rule else {}, [default])
    extra["default_version"] = default.version
    return _reset_to(
        session, rule_set, rule, default, actor, source, "rule_update_accepted", **extra
    )


# --- drift report (ADR 0004 Consequences) ------------------------------------------------------


def drift_report(session: Session) -> list[DriftEntryRead]:
    """Project rules that differ from the tenant defaults, for every project the tenant-level
    session can read (RLS: the caller's portfolio projects, ``allowed_project_ids``; every project
    of the tenant for a ``system_admin`` session, ADR 0016).

    ``added``: a live rule the project added (incl. every rule of a set the tenant has no
    default for). ``modified``: a copied rule whose values differ from the current default
    (``fields``). ``deleted``: a default the project deleted. ``missing``: a default the
    project's copy lacks (an update not accepted). ``default_removed``: a copied rule whose
    default the tenant removed. Sorted by project, set key and rule key.
    """
    context = _tenant_context(session)
    sets = session.scalars(
        select(RuleSet).where(RuleSet.tenant_id == context.tenant_id, RuleSet.layer == "project")
    ).all()
    tenant_sets = {
        s.key: s
        for s in session.scalars(
            select(RuleSet).where(
                RuleSet.tenant_id == context.tenant_id,
                RuleSet.project_id.is_(None),
                RuleSet.layer == "tenant",
            )
        )
    }
    entries: list[DriftEntryRead] = []
    for rule_set in sets:
        default_set = tenant_sets.get(rule_set.key)
        defaults = (
            {}
            if default_set is None
            else {r.rule_key: r for r in list_rules(session, default_set) if r.deleted_at is None}
        )
        mine = {rule.rule_key: rule for rule in list_rules(session, rule_set)}
        for key, rule in mine.items():
            default = None if rule.origin_rule_key is None else defaults.get(rule.origin_rule_key)
            drift, fields = _drift(rule, default)
            if drift is not None:
                entries.append(_entry(rule_set, key, drift, fields, rule, default))
        for key, default in defaults.items():
            if key not in mine:
                entries.append(_entry(rule_set, key, "missing", [], None, default))
    return sorted(entries, key=lambda e: (str(e.project_id), e.rule_set_key, e.rule_key))


def _drift(rule: Rule, default: Rule | None) -> tuple[str | None, list[str]]:
    live = rule.deleted_at is None
    if rule.origin_rule_key is None:
        return ("added" if live else None), []
    if default is None:
        return ("default_removed" if live else None), []
    if not live:
        return "deleted", []
    fields = _differing(rule, default)
    return ("modified" if fields else None), fields


def _entry(
    rule_set: RuleSet,
    rule_key: str,
    drift: str,
    fields: list[str],
    rule: Rule | None,
    default: Rule | None,
) -> DriftEntryRead:
    return DriftEntryRead.model_validate(
        {
            "project_id": rule_set.project_id,
            "rule_set_id": rule_set.id,
            "rule_set_key": rule_set.key,
            "kind": rule_set.kind,
            "rule_key": rule_key,
            "drift": drift,
            "fields": fields,
            "project_version": None if rule is None else rule.version,
            "default_version": None if default is None else default.version,
        }
    )


# --- "Try it" (ADR 0004 §6) -------------------------------------------------------------------


def try_rule(
    session: Session,
    rule_set_id: uuid.UUID,
    rule_key: str,
    context: Mapping[str, Any],
    *,
    draft: RuleSpec | None = None,
) -> TryResult:
    """Evaluate one rule of the set — the stored live rule, enabled or not, or ``draft``
    (compiled first: ``RuleCompileError``) — against a sample ``context``. Writes nothing: no
    finding, no version, no ledger entry. A malformed sample is ``SampleContextError``.
    """
    _context(session)
    rule_set = get_or_404(session, RuleSet, rule_set_id)
    if draft is not None:
        compile_rule(rule_set.kind, draft.engine, draft.expression, draft.params, draft.action)
        candidate = _Candidate.of(draft, rule_key, None)
    else:
        found = session.scalars(
            select(Rule).where(Rule.rule_set_id == rule_set.id, Rule.rule_key == rule_key)
        ).first()
        if found is None:
            raise RuleNotFoundError(f"rule set {rule_set.key!r} has no rule {rule_key!r}")
        candidate = _Candidate.of(found, rule_key, found.version)
    base: dict[str, Any] = {
        "rule_key": rule_key,
        "rule_version": candidate.version,
        "kind": rule_set.kind,
        "engine": candidate.engine,
    }
    if candidate.engine == "regex":
        return _try_regex(rule_set.kind, candidate.expression, context, base)
    return _try_cel(rule_set.kind, candidate, context, base)


def _try_cel(
    kind: str, rule: _Candidate, context: Mapping[str, Any], base: dict[str, Any]
) -> TryResult:
    if "params" in context:
        raise SampleContextError("'params' is reserved for the rule's own params")
    _bounded_sample(context)
    try:
        result = evaluate(rule.expression, {**context, "params": dict(rule.params)})
    except (TypeError, ValueError) as exc:
        raise SampleContextError(f"the sample context is not valid KERP CEL JSON: {exc}") from exc
    value: Any = None
    error: TryError | None = None
    if isinstance(result, CelError):
        outcome = "error"
        error = TryError(code=str(result.code), message=result.message)
    else:
        outcome = "pass" if result.value is True else "fail"
        value = result.value
    finding = None
    if kind == "check" and outcome != "pass":
        finding = CheckFinding(
            rule_key=rule.rule_key,
            rule_version=rule.version or 0,
            outcome="error" if outcome == "error" else "fail",
            action=rule.action,
            severity=rule.severity,
            message=_message(rule.rule_key, rule.params, result),
        )
    return TryResult.model_validate(
        {**base, "outcome": outcome, "value": value, "error": error, "finding": finding}
    )


def _bounded_sample(context: Mapping[str, Any]) -> None:
    """Refuse a CEL sample nested deeper than ``MAX_SAMPLE_DEPTH`` or larger than
    ``MAX_SAMPLE_CONTEXT_BYTES`` as compact JSON. Depth is checked first and iteratively, so
    the size check's ``json.dumps`` never meets deep nesting."""
    stack: list[tuple[Any, int]] = [(context, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_SAMPLE_DEPTH:
            raise SampleContextError(f"sample context nested deeper than {MAX_SAMPLE_DEPTH} levels")
        children = node.values() if isinstance(node, Mapping) else node
        stack.extend((c, depth + 1) for c in children if isinstance(c, Mapping | list))
    try:
        size = len(json.dumps(context, separators=(",", ":"), ensure_ascii=False).encode())
    except (TypeError, ValueError) as exc:
        raise SampleContextError(f"the sample context is not valid KERP CEL JSON: {exc}") from exc
    if size > MAX_SAMPLE_CONTEXT_BYTES:
        limit = MAX_SAMPLE_CONTEXT_BYTES // 1024
        raise SampleContextError(
            f"sample context is {size} bytes as compact JSON; the limit is {limit} KiB"
        )


def _try_regex(
    kind: str, pattern: str, context: Mapping[str, Any], base: dict[str, Any]
) -> TryResult:
    import re2  # type: ignore[import-untyped]  # lazy: native library (CLAUDE.md §6)

    text = context.get("text")
    if set(context) != {"text"} or not isinstance(text, str):
        raise SampleContextError('a regex rule is tried on {"text": "<sample text>"}')
    if len(text) > MAX_SAMPLE_TEXT:
        raise SampleContextError(f"sample text longer than {MAX_SAMPLE_TEXT} characters")
    compile_regex(pattern)  # length cap and RE2 compile, as on save
    options = re2.Options()
    options.log_errors = False
    compiled = re2.compile(pattern, options)
    if kind == "p6_grammar":
        full = compiled.fullmatch(text)
        found = [] if full is None else [full]
    else:
        found = []
        for match in compiled.finditer(text):
            found.append(match)
            if len(found) == MAX_MATCHES:
                break
    matches = [
        RegexMatch(text=m.group(0), start=m.start(), end=m.end(), groups=m.groupdict())
        for m in found
    ]
    return TryResult.model_validate(
        {**base, "outcome": "pass" if matches else "fail", "matches": matches}
    )
