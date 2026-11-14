"""The rule store and ``run_checks`` (T-0018, ADR 0004 §1, §3, §6, Amendment E).

Other modules call these functions, never the tables. The tenant and project always come
from the session's tenancy binding (T-0007); a project session reads its own sets plus the
tenant-level (``system``/``tenant`` layer) sets, and writes only its own.

Every change to a rule (add, edit, enable/disable, delete, reset, unlock) is compiled first
where it has an expression, raises the rule's ``version`` and the set's ``version``, and is
appended to the set's audit chain (T-0006).

Layers (T-0034, ADR 0004 §2): the ``system`` layer is a per-tenant copy of the seed files
(``sync_system_layer``); ``seed_tenant`` copies it into the ``tenant`` layer; a project gets a
**copy** of the tenant layer when it is created (``seed_project``) or when a module is first
enabled on it (``copy_module_defaults``). Copies are never live overlays. A ``locked`` rule
cannot be changed, disabled or deleted on the project layer, nor on the tenant layer until a
two-person unlock (``request_unlock`` + ``approve_unlock``) has cleared it (§5, Amendment B).
Permissions (who may call what, §7) are the router's.

Upgrade offers and "Try it" are T-0036; the per-record override of a blocked check
(Amendment A) T-0035.
"""

from __future__ import annotations

import dataclasses
import json
import re
import uuid
from collections.abc import Callable, Collection, Iterable, Mapping
from functools import cache
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import ColumnElement, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.audit import service as audit
from app.core.cel import CelError, CelOk, JsonValue, evaluate
from app.core.db import SIDE_WRITE_DB_KEY, Database, utcnow
from app.core.module_loader import service as module_settings
from app.core.module_loader.loader import discover_modules
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import SESSION_INFO_KEY, bind_tenancy, session_tenancy
from app.core.tenancy.scope import OutOfScopeError, get_or_404
from app.modules.projects import service as projects
from app.modules.validation.compiler import RuleCompileError, compile_rule
from app.modules.validation.defaults import SeedRuleSet, load_defaults
from app.modules.validation.models import (
    LAYERS,
    RULE_KINDS,
    Finding,
    Rule,
    RuleSet,
    UnlockRequest,
)
from app.modules.validation.schemas import KEY_PATTERN, CheckFinding, CheckResult, RuleSpec

if TYPE_CHECKING:
    from app.modules.projects.models import Project

__all__ = [
    "RuleCompileError",
    "RuleLockedError",
    "RuleNotFoundError",
    "RuleSetNotFoundError",
    "RuleStoreError",
    "UnlockPermissionError",
    "approve_unlock",
    "audit_chain",
    "copy_module_defaults",
    "create_rule_set",
    "delete_rule",
    "get_rule_set",
    "list_rule_sets",
    "list_rules",
    "list_unlock_requests",
    "reject_unlock",
    "request_unlock",
    "reset_rule",
    "reset_rule_set",
    "resolve_rule_set",
    "run_checks",
    "save_rule",
    "seed_project",
    "seed_tenant",
    "set_rule_enabled",
    "sync_system_defaults",
    "sync_system_layer",
]

# Most specific first: a project's own set wins over the tenant's, which wins over system.
_LAYER_PRIORITY: Final = {"project": 0, "tenant": 1, "system": 2}
_RULE_FIELDS: Final = tuple(name for name in RuleSpec.model_fields if name != "rule_key")
_ORIGIN_FIELDS: Final = ("origin_rule_key", "origin_version")
# What an edit may change; the origin of a rule is fixed when it is copied or added.
_EDIT_FIELDS: Final = tuple(name for name in _RULE_FIELDS if name not in _ORIGIN_FIELDS)
# What a copy or a reset takes from the source rule (besides its key and version).
_COPY_FIELDS: Final = (*_EDIT_FIELDS, "unlock_adr_reference")
# The layer each layer is copied from (and reset to).
_PARENT_LAYER: Final = {"project": "tenant", "tenant": "system"}


class RuleStoreError(ValueError):
    """The change is not allowed (duplicate set, wrong layer, deleted rule, AI source)."""


class RuleSetNotFoundError(LookupError):
    """No rule set of that key and kind is visible to the session."""


class RuleNotFoundError(LookupError):
    """The set has no live rule with that key."""


class RuleLockedError(RuleStoreError):
    """The rule is ``locked`` (ADR 0004 §5): the API answers 409."""


class UnlockPermissionError(PermissionError):
    """The requester tried to approve their own unlock (Amendment B): the API answers 403."""


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
    error). Saving identical values writes nothing. A new rule is an *added* rule
    (``origin_rule_key`` NULL; only copies name an origin); an edit keeps the rule's origin.
    ``RuleLockedError`` for a locked rule on the project or tenant layer, and for locking a
    rule on the project layer (only defaults are locked, ADR 0004 §5).
    """
    context = _context(session)
    _guard_source(source)
    rule_set = get_or_404(session, RuleSet, rule_set_id)
    compile_rule(rule_set.kind, spec.engine, spec.expression, spec.params, spec.action)
    actor = actor_id or context.user_id
    values = {name: getattr(spec, name) for name in _EDIT_FIELDS}
    rule = _find_rule(session, rule_set, spec.rule_key, include_deleted=True)
    if rule is not None and rule.deleted_at is not None:
        raise RuleStoreError(f"rule {spec.rule_key!r} was deleted; its key cannot be reused")
    if rule is not None:
        _guard_unlocked(rule_set, rule)
    if spec.locked and rule_set.layer == "project":
        raise RuleLockedError("only default rules are locked; a project rule cannot be")
    if rule is None:
        if spec.origin_rule_key is not None or spec.origin_version is not None:
            raise RuleStoreError("origin_rule_key is set only when a default is copied")
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
    """Soft-delete a rule; it keeps its ``rule_key`` (ADR 0004 §3). Not a locked one."""
    context = _context(session)
    _guard_source(source)
    rule_set = get_or_404(session, RuleSet, rule_set_id)
    rule = _live_rule(session, rule_set, rule_key)
    _guard_unlocked(rule_set, rule)
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

    The findings are committed in their **own** unit of work on a dedicated pool (same
    tenancy), so they survive when the caller then refuses the transition and rolls back: a
    blocked transition always leaves its findings (the record the T-0035 override builds on).
    That pool is never the caller's request pool, so logging a finding never waits on a
    connection a request already holds. The rule set must be committed before it is run (the
    findings reference it).
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


# --- reading (the Rules settings screen, ADR 0004 §7) ---------------------------------------


def list_rule_sets(session: Session, layer: str) -> list[RuleSet]:
    """The sets of ``layer`` in the session's own scope (its project, or the tenant), by key."""
    context = _context(session)
    query = select(RuleSet).where(
        RuleSet.tenant_id == context.tenant_id,
        _own_scope(context),
        RuleSet.layer == layer,
    )
    return list(session.scalars(query.order_by(RuleSet.key)))


def get_rule_set(session: Session, rule_set_id: uuid.UUID, layer: str) -> RuleSet:
    """A set of ``layer`` in the session's own scope; any other set is ``OutOfScopeError``.

    A project session can *read* tenant-level sets; this narrows it to its own project's.
    """
    context = _context(session)
    rule_set = get_or_404(session, RuleSet, rule_set_id)
    if rule_set.layer != layer or rule_set.project_id != context.project_id:
        raise OutOfScopeError("RuleSet not found")
    return rule_set


def list_rules(session: Session, rule_set: RuleSet) -> list[Rule]:
    """Every rule of the set, deleted ones included (they keep their key), in run order."""
    query = (
        select(Rule)
        .where(Rule.rule_set_id == rule_set.id)
        .order_by(Rule.sort_order, Rule.rule_key)
        .execution_options(include_deleted=True)
    )
    return list(session.scalars(query))


# --- project / tenant operations (ADR 0004 §3, §5) -----------------------------------------


def set_rule_enabled(
    session: Session,
    rule_set_id: uuid.UUID,
    rule_key: str,
    enabled: bool,
    *,
    actor_id: uuid.UUID | None = None,
    source: str = "api",
) -> Rule:
    """Disable or enable a rule (versioned, audited). Not a locked one."""
    context = _context(session)
    _guard_source(source)
    rule_set = get_or_404(session, RuleSet, rule_set_id)
    rule = _live_rule(session, rule_set, rule_key)
    _guard_unlocked(rule_set, rule)
    if rule.enabled == enabled:
        return rule
    actor = actor_id or context.user_id
    rule.enabled = enabled
    rule.version += 1
    rule.updated_by = actor
    rule.source = source
    _record_change(
        session, rule_set, rule, "rule_enabled" if enabled else "rule_disabled", actor, source
    )
    return rule


def reset_rule(
    session: Session,
    rule_set_id: uuid.UUID,
    rule_key: str,
    *,
    actor_id: uuid.UUID | None = None,
    source: str = "api",
) -> Rule:
    """Reset one rule to the **current** default it was copied from (ADR 0004 §3).

    The default is the rule of the same key in the parent layer's set of the same key
    (project -> tenant, tenant -> system). A deleted rule comes back; a default the set does
    not have yet is added. A rule the layer added itself has no default (``RuleStoreError``).
    Resetting may touch a locked rule: it can only make it equal to the default. On the
    tenant layer it may not *remove* a lock the default lacks: that would be a one-person
    unlock (``RuleLockedError``; TD-268).
    """
    context = _context(session)
    _guard_source(source)
    rule_set = get_or_404(session, RuleSet, rule_set_id)
    parent = _parent_set(session, rule_set)
    rule = _find_rule(session, rule_set, rule_key, include_deleted=True)
    if rule is not None and rule.origin_rule_key is None:
        raise RuleStoreError(f"rule {rule_key!r} was added on this layer; it has no default")
    origin_key = rule_key if rule is None else rule.origin_rule_key
    default = _find_rule(session, parent, str(origin_key), include_deleted=False)
    if default is None:
        raise RuleNotFoundError(f"the default set {parent.key!r} has no rule {origin_key!r}")
    _guard_reset_keeps_locks(rule_set, {default.rule_key: rule} if rule else {}, [default])
    return _reset_to(session, rule_set, rule, default, actor_id or context.user_id, source)


def reset_rule_set(
    session: Session,
    rule_set_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
    source: str = "api",
) -> RuleSet:
    """Reset the whole set to the current default: every default rule as it is now, and every
    other rule of the set soft-deleted (its key stays taken). Each change is audited.

    On the tenant layer a reset that would delete a live **locked** rule the tenant added
    itself, or unlock one whose default is not locked, would skip the two-person unlock, so
    the whole reset is refused with ``RuleLockedError`` naming the rules (fail closed, like
    ``delete_rule``; TD-268). A project cannot add locked rules, and its locked copies only
    lose their default's lock after a tenant two-person unlock, so the project layer needs
    no such check."""
    context = _context(session)
    _guard_source(source)
    actor = actor_id or context.user_id
    rule_set = get_or_404(session, RuleSet, rule_set_id)
    parent = _parent_set(session, rule_set)
    mine = {rule.rule_key: rule for rule in list_rules(session, rule_set)}
    defaults = [rule for rule in list_rules(session, parent) if rule.deleted_at is None]
    _guard_reset_keeps_locks(rule_set, mine, defaults)
    for default in defaults:
        _reset_to(session, rule_set, mine.pop(default.rule_key, None), default, actor, source)
    for rule in mine.values():
        if rule.deleted_at is None:
            rule.soft_delete()
            rule.version += 1
            rule.updated_by = actor
            rule.source = source
            _record_change(session, rule_set, rule, "rule_deleted", actor, source, reset=True)
    rule_set.seed_version = parent.seed_version
    session.flush()
    return rule_set


# --- layers: system seed -> tenant -> project copies (ADR 0004 §2) --------------------------


def sync_system_layer(session: Session, seeds: Iterable[SeedRuleSet] | None = None) -> list[str]:
    """Write the seed files into the session tenant's ``system`` layer; return changed keys.

    A set is created on first load and rewritten only when its seed ``version`` is newer
    than the stored one, so reloading the same version writes nothing (ADR 0004 §2.1); an
    older seed version is ignored. Copies already made from the system layer (tenant,
    projects) do not change: new defaults are *offered* to them (T-0036).
    """
    context = _tenant_context(session)
    changed = []
    for seed in load_defaults() if seeds is None else seeds:
        if _sync_seed(session, context, seed):
            changed.append(seed.key)
    return changed


def seed_tenant(
    session: Session,
    seeds: Iterable[SeedRuleSet] | None = None,
    *,
    actor_id: uuid.UUID | None = None,
) -> list[str]:
    """At tenant creation: load the system layer, then copy every system set the tenant layer
    does not have yet into it (ADR 0004 §2.2). Returns the keys copied. Idempotent."""
    context = _tenant_context(session)
    sync_system_layer(session, seeds)
    have = {rule_set.key for rule_set in list_rule_sets(session, "tenant")}
    actor = actor_id or context.user_id
    return [
        _copy_set(session, system_set, "tenant", actor).key
        for system_set in list_rule_sets(session, "system")
        if system_set.key not in have
    ]


def sync_system_defaults(
    database: Database,
    tenant_ids: Iterable[uuid.UUID],
    seeds: Iterable[SeedRuleSet] | None = None,
) -> dict[uuid.UUID, list[str]]:
    """Bring every listed tenant's ``system`` layer to the shipped seed versions (on deploy,
    when a seed version changed). One unit of work per tenant, so RLS applies unchanged."""
    loaded = load_defaults() if seeds is None else tuple(seeds)
    changed: dict[uuid.UUID, list[str]] = {}
    for tenant_id in tenant_ids:
        with database.unit_of_work(TenancyContext(tenant_id=tenant_id)) as session:
            changed[tenant_id] = sync_system_layer(session, loaded)
    return changed


def seed_project(
    session: Session,
    project_id: uuid.UUID,
    *,
    source_pack: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> list[str]:
    """At project creation: copy the tenant layer into the new project's ``project`` layer.

    Runs in the tenant-level session that created the project, in the same transaction: the
    session is re-bound to the project for the copy and back afterwards (objects it held are
    re-attached). Copies the sets of the modules enabled by default plus sets owned by no
    module; with ``source_pack``, that client pack's sets too (other packs' sets never).
    Returns the keys copied.
    """
    context = _tenant_context(session)
    enabled = _default_enabled_modules()
    actor = actor_id or context.user_id
    with _ProjectBinding(session, context, project_id):
        return _copy_to_project(
            session, lambda s: s.module is None or s.module in enabled, source_pack, actor
        )


def copy_module_defaults(
    session: Session, modules: Collection[str], *, actor_id: uuid.UUID | None = None
) -> list[str]:
    """When ``modules`` are first enabled on the session's project: copy their tenant sets
    the project does not have yet (ADR 0004 §2.3). Existing copies are never touched."""
    context = _context(session)
    if context.project_id is None:
        raise RuntimeError("copying defaults into a project needs a project-bound session")
    names = frozenset(modules)
    return _copy_to_project(session, lambda s: s.module in names, None, actor_id or context.user_id)


def _on_modules_enabled(session: Session, modules: frozenset[str]) -> None:
    copy_module_defaults(session, modules)


def _on_project_created(session: Session, project: Project) -> None:
    seed_project(session, project.id, actor_id=project.created_by)


module_settings.add_enable_listener(_on_modules_enabled)
projects.add_created_listener(_on_project_created)


# --- two-person unlock (ADR 0004 §5, Amendment B, TD-116) ----------------------------------


def request_unlock(
    session: Session,
    rule_set_id: uuid.UUID,
    rule_key: str,
    *,
    adr_reference: str,
    reason: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> UnlockRequest:
    """Step 1: ask to unlock a locked **tenant-layer** rule, citing an ADR (audited)."""
    context = _tenant_context(session)
    actor = actor_id or context.user_id
    if actor is None:
        raise RuleStoreError("an unlock request needs the requesting user")
    adr_reference = adr_reference.strip()
    if not adr_reference:
        raise RuleStoreError("an unlock needs an ADR reference")
    rule_set = get_or_404(session, RuleSet, rule_set_id)
    if rule_set.layer != "tenant":
        raise RuleStoreError("locked rules are unlocked on the tenant layer only")
    rule = _live_rule(session, rule_set, rule_key)
    if not rule.locked:
        raise RuleStoreError(f"rule {rule_key!r} is not locked")
    pending = session.scalars(
        select(UnlockRequest.id).where(
            UnlockRequest.rule_id == rule.id, UnlockRequest.status == "pending"
        )
    ).first()
    if pending is not None:
        raise RuleStoreError(f"rule {rule_key!r} already has a pending unlock request")
    request = UnlockRequest(
        tenant_id=context.tenant_id,
        project_id=None,
        rule_set_id=rule_set.id,
        rule_id=rule.id,
        rule_key=rule.rule_key,
        rule_version=rule.version,
        adr_reference=adr_reference,
        reason=reason,
        status="pending",
        requested_by=actor,
        created_by=actor,
        updated_by=actor,
        source="api",
    )
    session.add(request)
    session.flush()
    _audit_unlock(session, rule_set, request, "unlock_requested", actor, reason=reason)
    return request


def approve_unlock(
    session: Session, request_id: uuid.UUID, *, actor_id: uuid.UUID | None = None
) -> Rule:
    """Step 2: a **different** person approves; the rule is unlocked with the ADR reference
    recorded on it (versioned, audited). The requester can never complete their own unlock
    (``UnlockPermissionError``). A rule changed since the request is not unlocked."""
    context = _tenant_context(session)
    actor = actor_id or context.user_id
    request = _pending_unlock(session, request_id)
    if actor is None or actor == request.requested_by:
        raise UnlockPermissionError("a second, different person must approve an unlock")
    rule_set = get_or_404(session, RuleSet, request.rule_set_id)
    rule = get_or_404(session, Rule, request.rule_id)
    if rule.version != request.rule_version or not rule.locked:
        raise RuleStoreError("the rule changed since the unlock was requested; request again")
    _decide(request, "approved", actor)
    rule.locked = False
    rule.unlock_adr_reference = request.adr_reference
    rule.version += 1
    rule.updated_by = actor
    rule.source = "api"
    _audit_unlock(session, rule_set, request, "unlock_approved", actor)
    _record_change(
        session,
        rule_set,
        rule,
        "rule_unlocked",
        actor,
        "api",
        unlock_request=str(request.id),
        requested_by=str(request.requested_by),
        approved_by=str(actor),
        adr_reference=request.adr_reference,
    )
    return rule


def reject_unlock(
    session: Session, request_id: uuid.UUID, *, actor_id: uuid.UUID | None = None
) -> UnlockRequest:
    """Close a pending request without unlocking: ``rejected`` by an approver, ``cancelled``
    when the requester withdraws it (audited)."""
    context = _tenant_context(session)
    actor = actor_id or context.user_id
    if actor is None:
        raise UnlockPermissionError("deciding an unlock needs a user")
    request = _pending_unlock(session, request_id)
    rule_set = get_or_404(session, RuleSet, request.rule_set_id)
    status = "cancelled" if actor == request.requested_by else "rejected"
    _decide(request, status, actor)
    _audit_unlock(session, rule_set, request, f"unlock_{status}", actor)
    return request


def list_unlock_requests(session: Session, status: str | None = None) -> list[UnlockRequest]:
    """The tenant's unlock requests, oldest first, optionally of one ``status``."""
    context = _tenant_context(session)
    query = select(UnlockRequest).where(UnlockRequest.tenant_id == context.tenant_id)
    if status is not None:
        query = query.where(UnlockRequest.status == status)
    return list(session.scalars(query.order_by(UnlockRequest.created_at, UnlockRequest.id)))


# --- layer helpers --------------------------------------------------------------------------


def _tenant_context(session: Session) -> TenancyContext:
    context = _context(session)
    if context.project_id is not None:
        raise RuleStoreError("this needs a tenant-level session (no project bound)")
    return context


def _own_scope(context: TenancyContext) -> ColumnElement[bool]:
    if context.project_id is None:
        return RuleSet.project_id.is_(None)
    return RuleSet.project_id == context.project_id


def _guard_unlocked(rule_set: RuleSet, rule: Rule) -> None:
    if not rule.locked or rule_set.layer == "system":
        return
    if rule_set.layer == "project":
        raise RuleLockedError(
            f"rule {rule.rule_key!r} is locked; only the tenant layer can unlock it"
        )
    raise RuleLockedError(f"rule {rule.rule_key!r} is locked; unlock it first (two-person unlock)")


def _guard_reset_keeps_locks(
    rule_set: RuleSet, mine: Mapping[str, Rule], defaults: Collection[Rule]
) -> None:
    """A tenant-layer reset must not delete or unlock a live locked rule (§5, Amendment B)."""
    if rule_set.layer != "tenant":
        return
    locked_defaults = {default.rule_key: default.locked for default in defaults}
    lost = sorted(
        key
        for key, rule in mine.items()
        if rule.deleted_at is None and rule.locked and not locked_defaults.get(key, False)
    )
    if lost:
        raise RuleLockedError(
            f"reset would delete or unlock locked rule(s) {', '.join(map(repr, lost))}; "
            "unlock them first (two-person unlock)"
        )


def _live_rule(session: Session, rule_set: RuleSet, rule_key: str) -> Rule:
    rule = _find_rule(session, rule_set, rule_key, include_deleted=False)
    if rule is None:
        raise RuleNotFoundError(f"rule set {rule_set.key!r} has no rule {rule_key!r}")
    return rule


def _parent_set(session: Session, rule_set: RuleSet) -> RuleSet:
    layer = _PARENT_LAYER.get(rule_set.layer)
    if layer is None:
        raise RuleStoreError("the system layer is the default; it has nothing to reset to")
    parent = session.scalars(
        select(RuleSet).where(
            RuleSet.tenant_id == rule_set.tenant_id,
            RuleSet.project_id.is_(None),
            RuleSet.layer == layer,
            RuleSet.key == rule_set.key,
        )
    ).first()
    if parent is None:
        raise RuleStoreError(f"there is no {layer} default set {rule_set.key!r} to reset to")
    return parent


def _copied_values(source: Rule) -> dict[str, Any]:
    values = {name: getattr(source, name) for name in _COPY_FIELDS}
    values["params"] = json.loads(json.dumps(source.params))  # never share the source's dict
    values["origin_rule_key"] = source.rule_key
    values["origin_version"] = source.version
    return values


def _reset_to(
    session: Session,
    rule_set: RuleSet,
    rule: Rule | None,
    default: Rule,
    actor: uuid.UUID | None,
    source: str,
) -> Rule:
    values = _copied_values(default)
    if rule is None:
        rule = Rule(
            tenant_id=rule_set.tenant_id,
            project_id=rule_set.project_id,
            rule_set_id=rule_set.id,
            rule_key=default.rule_key,
            version=1,
            created_by=actor,
            **values,
        )
        session.add(rule)
    elif rule.deleted_at is None and all(getattr(rule, n) == v for n, v in values.items()):
        return rule
    else:
        for name, value in values.items():
            setattr(rule, name, value)
        rule.deleted_at = None
        rule.version += 1
    rule.updated_by = actor
    rule.source = source
    _record_change(session, rule_set, rule, "rule_reset", actor, source)
    return rule


def _copy_set(session: Session, source: RuleSet, layer: str, actor: uuid.UUID | None) -> RuleSet:
    """Copy ``source`` and its live rules into ``layer`` of the session's scope (one audit
    entry). Every rule names its origin (``origin_rule_key`` + ``origin_version``)."""
    context = _context(session)
    copy = RuleSet(
        tenant_id=context.tenant_id,
        project_id=context.project_id,
        key=source.key,
        kind=source.kind,
        layer=layer,
        version=1,
        source_pack=source.source_pack,
        module=source.module,
        seed_version=source.seed_version,
        created_by=actor,
        updated_by=actor,
        source="system",
    )
    session.add(copy)
    session.flush()
    rules = [rule for rule in list_rules(session, source) if rule.deleted_at is None]
    session.add_all(
        Rule(
            tenant_id=copy.tenant_id,
            project_id=copy.project_id,
            rule_set_id=copy.id,
            rule_key=rule.rule_key,
            version=1,
            created_by=actor,
            updated_by=actor,
            source="system",
            **_copied_values(rule),
        )
        for rule in rules
    )
    session.flush()
    audit.append(
        session,
        audit_chain(copy),
        {
            "event": "rule_set_copied",
            "rule_set": copy.key,
            "kind": copy.kind,
            "layer": layer,
            "from_layer": source.layer,
            "from_rule_set_version": source.version,
            "seed_version": source.seed_version,
            "rules": [{"rule_key": r.rule_key, "origin_version": r.version} for r in rules],
        },
        source="system",
        actor_id=actor,
    )
    return copy


def _copy_to_project(
    session: Session,
    wanted: Callable[[RuleSet], bool],
    source_pack: str | None,
    actor: uuid.UUID | None,
) -> list[str]:
    context = _context(session)
    have = {rule_set.key for rule_set in list_rule_sets(session, "project")}
    tenant_sets = session.scalars(
        select(RuleSet)
        .where(
            RuleSet.tenant_id == context.tenant_id,
            RuleSet.project_id.is_(None),
            RuleSet.layer == "tenant",
        )
        .order_by(RuleSet.key)
    ).all()
    return [
        _copy_set(session, tenant_set, "project", actor).key
        for tenant_set in tenant_sets
        if tenant_set.key not in have
        and wanted(tenant_set)
        and tenant_set.source_pack in (None, source_pack)
    ]


class _ProjectBinding:
    """Re-bind a tenant-level session to one project for a copy, then back (same transaction).

    ``bind_tenancy`` refuses to switch a session holding objects, so they are flushed,
    detached, and re-attached once the session is back in the tenant scope.
    """

    def __init__(self, session: Session, context: TenancyContext, project_id: uuid.UUID) -> None:
        self.session, self.context = session, context
        self.project = dataclasses.replace(context, project_id=project_id)
        self.held: list[object] = []

    def __enter__(self) -> None:
        self.session.flush()
        self.held = list(self.session.identity_map.values())
        self.session.expunge_all()
        bind_tenancy(self.session, self.project)

    def __exit__(self, exc_type: object, *_exc: object) -> None:
        if exc_type is None:
            self.session.flush()
        self.session.expunge_all()
        if exc_type is None:
            bind_tenancy(self.session, self.context)
        else:
            try:
                bind_tenancy(self.session, self.context)
            except SQLAlchemyError:
                # The transaction is already failed (it will be rolled back): restore the
                # scope on the session without touching the connection.
                self.session.info[SESSION_INFO_KEY] = self.context
        self.session.add_all(self.held)


@cache  # module manifests are static for the life of the process, so discover them once
def _default_enabled_modules() -> frozenset[str]:
    states = module_settings.default_states(discover_modules())
    return frozenset(name for name, enabled in states.items() if enabled)


def _sync_seed(session: Session, context: TenancyContext, seed: SeedRuleSet) -> bool:
    rule_set = session.scalars(
        select(RuleSet).where(
            RuleSet.tenant_id == context.tenant_id,
            RuleSet.project_id.is_(None),
            RuleSet.layer == "system",
            RuleSet.key == seed.key,
        )
    ).first()
    if rule_set is None:
        rule_set = RuleSet(
            tenant_id=context.tenant_id,
            project_id=None,
            key=seed.key,
            kind=seed.kind,
            layer="system",
            version=1,
            source_pack=seed.source_pack,
            module=seed.module,
            seed_version=seed.version,
            source="system",
        )
        session.add(rule_set)
        session.flush()
        changes = [_seed_rule(session, rule_set, spec, None) for spec in seed.rules]
        _audit_seed(session, rule_set, "system_defaults_loaded", None, changes)
        return True
    if rule_set.kind != seed.kind:
        raise RuleStoreError(f"seed {seed.key!r} changes the kind; a set's kind is fixed")
    if rule_set.seed_version is not None and rule_set.seed_version >= seed.version:
        return False
    previous = rule_set.seed_version
    current = {rule.rule_key: rule for rule in list_rules(session, rule_set)}
    changes = [
        _seed_rule(session, rule_set, spec, current.pop(spec.rule_key, None)) for spec in seed.rules
    ]
    for rule in current.values():
        if rule.deleted_at is None:
            rule.soft_delete()
            rule.version += 1
            rule.source = "system"
            changes.append({"rule_key": rule.rule_key, "change": "removed"})
    rule_set.version += 1
    rule_set.seed_version = seed.version
    rule_set.module = seed.module
    rule_set.source_pack = seed.source_pack
    session.flush()
    _audit_seed(session, rule_set, "system_defaults_upgraded", previous, changes)
    return True


def _seed_rule(
    session: Session, rule_set: RuleSet, spec: RuleSpec, rule: Rule | None
) -> dict[str, Any]:
    values = {name: getattr(spec, name) for name in _EDIT_FIELDS}
    if rule is None:
        session.add(
            Rule(
                tenant_id=rule_set.tenant_id,
                project_id=None,
                rule_set_id=rule_set.id,
                rule_key=spec.rule_key,
                version=1,
                source="system",
                **values,
            )
        )
        change = "added"
    elif rule.deleted_at is None and all(getattr(rule, n) == v for n, v in values.items()):
        change = "unchanged"
    else:
        for name, value in values.items():
            setattr(rule, name, value)
        rule.deleted_at = None
        rule.version += 1
        rule.source = "system"
        change = "changed"
    return {"rule_key": spec.rule_key, "change": change}


def _audit_seed(
    session: Session,
    rule_set: RuleSet,
    event: str,
    previous: int | None,
    changes: list[dict[str, Any]],
) -> None:
    session.flush()
    audit.append(
        session,
        audit_chain(rule_set),
        {
            "event": event,
            "rule_set": rule_set.key,
            "rule_set_version": rule_set.version,
            "module": rule_set.module,
            "seed_version": rule_set.seed_version,
            "previous_seed_version": previous,
            "changes": changes,
        },
        source="system",
    )


def _pending_unlock(session: Session, request_id: uuid.UUID) -> UnlockRequest:
    request = get_or_404(session, UnlockRequest, request_id)
    if request.status != "pending":
        raise RuleStoreError(f"the unlock request is already {request.status}")
    return request


def _decide(request: UnlockRequest, status: str, actor: uuid.UUID) -> None:
    request.status = status
    request.decided_by = actor
    request.decided_at = utcnow()
    request.updated_by = actor


def _audit_unlock(
    session: Session,
    rule_set: RuleSet,
    request: UnlockRequest,
    event: str,
    actor: uuid.UUID,
    **extra: str | int | bool | None,
) -> None:
    session.flush()
    audit.append(
        session,
        audit_chain(rule_set),
        {
            "event": event,
            "rule_set": rule_set.key,
            "rule_key": request.rule_key,
            "rule_version": request.rule_version,
            "unlock_request": str(request.id),
            "adr_reference": request.adr_reference,
            "requested_by": str(request.requested_by),
            **extra,
        },
        source="api",
        actor_id=actor,
    )


# --- helpers ------------------------------------------------------------------------------


def _persist_findings(session: Session, tenancy: TenancyContext, rows: list[Finding]) -> None:
    """Commit ``rows`` independently of the caller's transaction (see ``run_checks``).

    The write runs on the ``Database``'s dedicated side-write pool, never on the request pool
    the caller's connection belongs to: that pool can be fully held by requests sitting inside
    ``run_checks``, so checking out a second connection from it would risk a client-side
    deadlock (every holder waiting on another) that times out and 500s. The separate pool
    cannot form that cycle. On SQLite (local tests only) a caller that has already written in
    its open transaction holds the database write lock, so call ``run_checks`` before writing.
    """
    if not rows:
        return
    with _side_write_sessionmaker(session)() as log:
        bind_tenancy(log, tenancy)
        log.add_all(rows)
        log.commit()


def _side_write_sessionmaker(session: Session) -> sessionmaker[Session]:
    """The dedicated findings-log session factory for ``session``'s ``Database``.

    ``Database.session`` stashes its ``Database`` on ``session.info``. A session opened some
    other way (a bare ``Session`` in a script or test) has none; such a session is never the
    shared request pool, so a plain factory on its own engine is safe (no circular-wait risk).
    """
    database = session.info.get(SIDE_WRITE_DB_KEY)
    if database is not None:
        maker: sessionmaker[Session] = database.side_write_sessionmaker
        return maker
    return sessionmaker(bind=session.get_bind().engine, autoflush=False, expire_on_commit=False)


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
    **extra: str | int | bool | None,
) -> None:
    rule_set.version += 1
    rule_set.updated_by = actor
    session.flush()
    audit.append(
        session,
        audit_chain(rule_set),
        {
            "event": event,
            "rule_set": rule_set.key,
            "rule_set_version": rule_set.version,
            "rule_key": rule.rule_key,
            "rule_version": rule.version,
            "rule": _snapshot(rule),
            **extra,
        },
        source=source,
        actor_id=actor,
    )


def _snapshot(rule: Rule) -> dict[str, Any]:
    names = (*_RULE_FIELDS, "unlock_adr_reference")
    snapshot = {name: getattr(rule, name) for name in names if name != "params"}
    # params may hold floats, which the ledger refuses (README there): store canonical text.
    snapshot["params_json"] = json.dumps(rule.params, sort_keys=True, separators=(",", ":"))
    snapshot["deleted"] = rule.deleted_at is not None
    return snapshot


def _message(rule: Rule, result: CelOk | CelError) -> str:
    if isinstance(result, CelError):
        return f"{rule.rule_key}: rule could not be evaluated ({result.code})"
    message = rule.params.get("message")
    return message if isinstance(message, str) and message else f"{rule.rule_key}: check failed"
