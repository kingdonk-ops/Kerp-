"""The rule store and ``run_checks`` (T-0018, ADR 0004 §1, §3, §6, Amendment E).

Other modules call these functions, never the tables. The tenant and project always come
from the session's tenancy binding (T-0007); a project session reads its own sets plus the
tenant-level (``system``/``tenant`` layer) sets, and writes only its own.

Every change to a rule (add, edit, delete) is compiled first, raises the rule's ``version``
and the set's ``version``, and is appended to the set's audit chain (T-0006).

Layers, project copies, locked rules and unlock are T-0034; upgrade offers and "Try it"
T-0036; the per-record override of a blocked check (Amendment A) T-0035.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.cel import CelError, CelOk, JsonValue, evaluate
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_tenancy, session_tenancy
from app.core.tenancy.scope import get_or_404
from app.modules.validation.compiler import RuleCompileError, compile_rule
from app.modules.validation.models import LAYERS, RULE_KINDS, Finding, Rule, RuleSet
from app.modules.validation.schemas import KEY_PATTERN, CheckFinding, CheckResult, RuleSpec

__all__ = [
    "RuleCompileError",
    "RuleNotFoundError",
    "RuleSetNotFoundError",
    "RuleStoreError",
    "audit_chain",
    "create_rule_set",
    "delete_rule",
    "resolve_rule_set",
    "run_checks",
    "save_rule",
]

# Most specific first: a project's own set wins over the tenant's, which wins over system.
_LAYER_PRIORITY: Final = {"project": 0, "tenant": 1, "system": 2}
_RULE_FIELDS: Final = tuple(name for name in RuleSpec.model_fields if name != "rule_key")


class RuleStoreError(ValueError):
    """The change is not allowed (duplicate set, wrong layer, deleted rule, AI source)."""


class RuleSetNotFoundError(LookupError):
    """No rule set of that key and kind is visible to the session."""


class RuleNotFoundError(LookupError):
    """The set has no live rule with that key."""


def audit_chain(rule_set: RuleSet) -> str:
    return f"validation.rule_set:{rule_set.id}"


def _context(session: Session) -> TenancyContext:
    context = session_tenancy(session)
    if context is None:
        raise RuntimeError("the rule store needs a session bound to a tenant (bind_tenancy)")
    return context


def create_rule_set(
    session: Session,
    *,
    key: str,
    kind: str,
    layer: str,
    source_pack: str | None = None,
    actor_id: uuid.UUID | None = None,
    source: str = "api",
) -> RuleSet:
    """Create an empty set in the session's scope: ``project`` layer on a project session,
    ``system``/``tenant`` on a tenant-level session."""
    context = _context(session)
    if kind not in RULE_KINDS:
        raise RuleStoreError(f"kind must be one of {list(RULE_KINDS)}")
    if layer not in LAYERS:
        raise RuleStoreError(f"layer must be one of {list(LAYERS)}")
    if not re.fullmatch(KEY_PATTERN, key):
        raise RuleStoreError(f"key must match {KEY_PATTERN}")
    if (layer == "project") != (context.project_id is not None):
        raise RuleStoreError(
            "project-layer sets need a project session; system/tenant sets a tenant session"
        )
    duplicate = session.scalars(
        select(RuleSet.id)
        .where(
            RuleSet.tenant_id == context.tenant_id,
            RuleSet.project_id.is_(None)
            if context.project_id is None
            else RuleSet.project_id == context.project_id,
            RuleSet.layer == layer,
            RuleSet.key == key,
        )
        .execution_options(include_deleted=True)
    ).first()
    if duplicate is not None:
        raise RuleStoreError(f"rule set {key!r} already exists on the {layer} layer")
    _guard_source(source)
    actor = actor_id or context.user_id
    rule_set = RuleSet(
        tenant_id=context.tenant_id,
        project_id=context.project_id,
        key=key,
        kind=kind,
        layer=layer,
        version=1,
        source_pack=source_pack,
        created_by=actor,
        updated_by=actor,
        source=source,
    )
    session.add(rule_set)
    session.flush()
    audit.append(
        session,
        audit_chain(rule_set),
        {
            "event": "rule_set_created",
            "rule_set": key,
            "kind": kind,
            "layer": layer,
            "source_pack": source_pack,
        },
        source=source,
        actor_id=actor,
    )
    return rule_set


def save_rule(
    session: Session,
    rule_set_id: uuid.UUID,
    spec: RuleSpec,
    *,
    actor_id: uuid.UUID | None = None,
    source: str = "api",
) -> Rule:
    """Add ``spec`` to the set, or change the rule with its ``rule_key``.

    The rule is compiled against its kind first (``RuleCompileError`` with the compile
    error). Saving identical values writes nothing.
    """
    context = _context(session)
    _guard_source(source)
    rule_set = get_or_404(session, RuleSet, rule_set_id)
    compile_rule(rule_set.kind, spec.engine, spec.expression, spec.params, spec.action)
    actor = actor_id or context.user_id
    values = {name: getattr(spec, name) for name in _RULE_FIELDS}
    rule = _find_rule(session, rule_set, spec.rule_key, include_deleted=True)
    if rule is None:
        rule = Rule(
            tenant_id=rule_set.tenant_id,
            project_id=rule_set.project_id,
            rule_set_id=rule_set.id,
            rule_key=spec.rule_key,
            version=1,
            created_by=actor,
            **values,
        )
        session.add(rule)
    elif rule.deleted_at is not None:
        raise RuleStoreError(f"rule {spec.rule_key!r} was deleted; its key cannot be reused")
    elif all(getattr(rule, name) == value for name, value in values.items()):
        return rule
    else:
        for name, value in values.items():
            setattr(rule, name, value)
        rule.version += 1
    rule.updated_by = actor
    rule.source = source
    _record_change(session, rule_set, rule, "rule_saved", actor, source)
    return rule


def delete_rule(
    session: Session,
    rule_set_id: uuid.UUID,
    rule_key: str,
    *,
    actor_id: uuid.UUID | None = None,
    source: str = "api",
) -> Rule:
    """Soft-delete a rule; it keeps its ``rule_key`` (ADR 0004 §3)."""
    context = _context(session)
    _guard_source(source)
    rule_set = get_or_404(session, RuleSet, rule_set_id)
    rule = _find_rule(session, rule_set, rule_key, include_deleted=False)
    if rule is None:
        raise RuleNotFoundError(f"rule set {rule_set.key!r} has no rule {rule_key!r}")
    actor = actor_id or context.user_id
    rule.soft_delete()
    rule.version += 1
    rule.updated_by = actor
    rule.source = source
    _record_change(session, rule_set, rule, "rule_deleted", actor, source)
    return rule


def resolve_rule_set(session: Session, key: str, kind: str) -> RuleSet:
    """The most specific visible set named ``key``: project, else tenant, else system."""
    context = _context(session)
    candidates = session.scalars(
        select(RuleSet).where(
            RuleSet.tenant_id == context.tenant_id, RuleSet.key == key, RuleSet.kind == kind
        )
    ).all()
    if not candidates:
        raise RuleSetNotFoundError(f"no {kind} rule set {key!r}")
    # Ties (only possible without NULLS NOT DISTINCT, i.e. SQLite) break by id, deterministically.
    return min(candidates, key=lambda s: (_LAYER_PRIORITY[s.layer], str(s.id)))


def run_checks(
    context: Mapping[str, JsonValue],
    rule_set: str,
    *,
    session: Session,
    subject: str | None = None,
) -> CheckResult:
    """Evaluate the enabled ``check`` rules of ``rule_set`` against ``context``.

    Rules run in ``sort_order`` (then ``rule_key``) with ``params`` bound to the rule's own
    params. A rule passes only on ``CelOk(True)`` (fail closed). Every rule that does not
    pass is written to the findings log with its ``rule_key`` and version; the result is
    ``blocked`` when any of them has ``action == "block"``. ``subject`` names the record
    being checked (e.g. ``"inspection:<uuid>"``). An unknown set raises
    ``RuleSetNotFoundError``, so a misconfigured gate never passes silently.

    The findings are committed in their **own** unit of work (same engine, same tenancy),
    so they survive when the caller then refuses the transition and rolls back: a blocked
    transition always leaves its findings (the record the T-0035 override builds on). The
    rule set must be committed before it is run (the findings reference it).
    """
    tenancy = _context(session)
    if "params" in context:
        raise ValueError("'params' is reserved for the rule's own params")
    found = resolve_rule_set(session, rule_set, "check")
    rules = session.scalars(
        select(Rule)
        .where(Rule.rule_set_id == found.id, Rule.enabled.is_(True))
        .order_by(Rule.sort_order, Rule.rule_key)
    ).all()
    run_id = uuid.uuid4()
    findings: list[CheckFinding] = []
    rows: list[Finding] = []
    for rule in rules:
        result = evaluate(rule.expression, {**context, "params": rule.params})
        if isinstance(result, CelOk) and result.value is True:
            continue
        finding = CheckFinding(
            rule_key=rule.rule_key,
            rule_version=rule.version,
            outcome="error" if isinstance(result, CelError) else "fail",
            action=rule.action,
            severity=rule.severity,
            message=_message(rule, result),
        )
        findings.append(finding)
        rows.append(
            Finding(
                tenant_id=tenancy.tenant_id,
                project_id=tenancy.project_id,
                run_id=run_id,
                subject=subject,
                rule_set_id=found.id,
                rule_set_key=found.key,
                rule_set_version=found.version,
                rule_id=rule.id,
                created_by=tenancy.user_id,
                source="system",
                **finding.model_dump(),
            )
        )
    _persist_findings(session, tenancy, rows)
    return CheckResult(
        run_id=run_id,
        rule_set=found.key,
        rule_set_version=found.version,
        evaluated=len(rules),
        findings=tuple(findings),
    )


# --- helpers ------------------------------------------------------------------------------


def _persist_findings(session: Session, tenancy: TenancyContext, rows: list[Finding]) -> None:
    """Commit ``rows`` independently of the caller's transaction (see ``run_checks``).

    On SQLite (local tests only) a caller that has already written in its open transaction
    holds the database write lock, so call ``run_checks`` before writing.
    """
    if not rows:
        return
    with Session(bind=session.get_bind().engine) as log:
        bind_tenancy(log, tenancy)
        log.add_all(rows)
        log.commit()


def _guard_source(source: str) -> None:
    # CLAUDE.md §6: AI-initiated changes need human confirmation before they apply; there is
    # no confirmation flow for rules yet, so they are refused.
    if source == "ai":
        raise RuleStoreError("AI-proposed rule changes need human confirmation")


def _find_rule(
    session: Session, rule_set: RuleSet, rule_key: str, *, include_deleted: bool
) -> Rule | None:
    return session.scalars(
        select(Rule)
        .where(Rule.rule_set_id == rule_set.id, Rule.rule_key == rule_key)
        .execution_options(include_deleted=include_deleted)
    ).first()


def _record_change(
    session: Session,
    rule_set: RuleSet,
    rule: Rule,
    event: str,
    actor: uuid.UUID | None,
    source: str,
) -> None:
    rule_set.version += 1
    rule_set.updated_by = actor
    session.flush()
    snapshot = {name: getattr(rule, name) for name in _RULE_FIELDS if name != "params"}
    # params may hold floats, which the ledger refuses (README there): store canonical text.
    snapshot["params_json"] = json.dumps(rule.params, sort_keys=True, separators=(",", ":"))
    audit.append(
        session,
        audit_chain(rule_set),
        {
            "event": event,
            "rule_set": rule_set.key,
            "rule_set_version": rule_set.version,
            "rule_key": rule.rule_key,
            "rule_version": rule.version,
            "rule": snapshot,
        },
        source=source,
        actor_id=actor,
    )


def _message(rule: Rule, result: CelOk | CelError) -> str:
    if isinstance(result, CelError):
        return f"{rule.rule_key}: rule could not be evaluated ({result.code})"
    message = rule.params.get("message")
    return message if isinstance(message, str) and message else f"{rule.rule_key}: check failed"
