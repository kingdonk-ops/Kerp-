"""Per-record override of a blocked check (T-0035, ADR 0004 Amendment A).

When a ``check`` rule blocks a transition on one record, a member may **request an
override** of that blocking finding with a typed reason. The request is routed to an
approver persona (A.4), the approver is notified and the requester learns the decision
(A.3), and request and decision go to the record's activity history: the audit chain
``<record_type>:<record_id>`` in the project's ledger scope (A.5). An approved override is
bound to the rule as evaluated and to the hash of the values it checked; ``run_checks``
lets the record pass that rule only while the hash (and rule version) still match, and a
later evaluation with a different hash voids the override, so the block returns (A.6).

This is separate from unlocking a rule (ADR 0004 §5, Amendment B: ``request_unlock``).

* The record is the ``run_checks`` ``subject``: ``"<record_type>:<record_id>"`` (a UUID id).
  Findings of a run without such a subject cannot be overridden.
* The values checked are ``params.override_binding`` of the rule (dotted context paths), or
  the whole ``record`` and ``fields`` when the rule names none (``compiler.py``).
* Routing: PER-06 QA/QC Lead; a requester holding PER-06 goes to PER-02 Project Manager; a
  requester holding PER-02 (and not PER-06) goes to PER-06. The requester is never an
  approver: not routed to, and refused when deciding (``OverridePermissionError``, 403).

Other modules call ``run_checks`` only; the request and decision functions are the
validation API's (``router.py``). Tenant and project come from the session's binding.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth.access import Persona, load_grants, persona_holders
from app.core.db import utcnow
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import OutOfScopeError, get_or_404
from app.modules.notifications import service as notifications
from app.modules.notifications.schemas import NotificationCreate
from app.modules.validation.compiler import DEFAULT_OVERRIDE_BINDING, OVERRIDE_BINDING_PARAM
from app.modules.validation.models import (
    OVERRIDE_ACTIVE,
    OVERRIDE_ACTIVE_INDEX,
    Finding,
    Override,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

__all__ = [
    "MAX_REASON_LENGTH",
    "OverrideError",
    "OverrideInputError",
    "OverridePermissionError",
    "Void",
    "active_overrides",
    "apply_voids",
    "approve_override",
    "checked_values",
    "checked_values_hash",
    "get_override",
    "list_overrides",
    "parse_subject",
    "record_subject",
    "reject_override",
    "request_override",
    "route_approver",
    "void_cause",
]

logger = logging.getLogger("kerp.validation")

MAX_REASON_LENGTH: Final = 2000
SUBJECT_TYPE: Final = "validation.override"
_SUBJECT: Final = re.compile(r"^(?P<type>[a-z][a-z0-9_.]{0,59}):(?P<id>[0-9a-fA-F-]{36})$")
_SQLITE_ACTIVE_UNIQUE: Final = "UNIQUE constraint failed: oe_validation_override.tenant_id"


class OverrideError(ValueError):
    """The request or decision is not possible in the record's state (API 409)."""


class OverrideInputError(ValueError):
    """The input is invalid: blank reason, ineligible approver (API 422)."""


class OverridePermissionError(PermissionError):
    """The caller may not request or decide this override, e.g. their own (API 403)."""


@dataclass(frozen=True, slots=True)
class Void:
    """An active override a ``run_checks`` evaluation found stale."""

    override_id: uuid.UUID
    cause: str  # "checked_values_changed" | "rule_changed"
    evaluated_hash: str
    evaluated_rule_version: int


# --- binding (Amendment A.6) -----------------------------------------------------------------


def record_subject(record_type: str, record_id: uuid.UUID) -> str:
    """The ``run_checks`` subject (and audit chain key) of a record."""
    return f"{record_type}:{record_id}"


def parse_subject(subject: str | None) -> tuple[str, uuid.UUID] | None:
    """``(record_type, record_id)`` of a ``"<type>:<uuid>"`` subject, else ``None``."""
    match = _SUBJECT.fullmatch(subject or "")
    if match is None:
        return None
    try:
        return match["type"], uuid.UUID(match["id"])
    except ValueError:
        return None


def _lookup(context: Mapping[str, Any], path: str) -> object:
    value: object = context
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def checked_values(params: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    """The values the rule checked: its ``override_binding`` paths, else record + fields."""
    binding = params.get(OVERRIDE_BINDING_PARAM)
    paths = binding if isinstance(binding, list) and binding else DEFAULT_OVERRIDE_BINDING
    return {str(path): _lookup(context, str(path)) for path in paths}


def checked_values_hash(params: Mapping[str, Any], context: Mapping[str, Any]) -> str:
    """SHA-256 (hex) of the canonical JSON of ``checked_values``."""
    canonical = json.dumps(
        checked_values(params, context),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def void_cause(override: Override, rule_version: int, values_hash: str) -> str | None:
    """Why an active override no longer applies to this evaluation, or ``None``."""
    if override.rule_version != rule_version:
        return "rule_changed"
    if override.checked_values_hash != values_hash:
        return "checked_values_changed"
    return None


def active_overrides(
    session: Session, subject: str | None, rule_set_key: str
) -> dict[str, Override]:
    """The record's pending and approved overrides of ``rule_set_key`` rules, by rule key.

    Empty for a subject that names no record or a session bound to no project.
    """
    context = session_tenancy(session)
    target = parse_subject(subject)
    if context is None or context.project_id is None or target is None:
        return {}
    record_type, record_id = target
    rows = session.scalars(
        select(Override).where(
            Override.tenant_id == context.tenant_id,
            Override.project_id == context.project_id,
            Override.record_type == record_type,
            Override.record_id == record_id,
            Override.rule_set_key == rule_set_key,
            Override.decision.in_(OVERRIDE_ACTIVE),
        )
    ).all()
    return {row.rule_key: row for row in rows}


def apply_voids(log: Session, voids: Iterable[Void]) -> None:
    """Void the overrides (in ``run_checks``' own unit of work) and record each on the
    record's audit chain. An override already decided otherwise meanwhile is left alone."""
    for void in voids:
        override = log.get(Override, void.override_id)
        if override is None:
            continue
        previous = override.decision
        now = utcnow()
        result = log.execute(
            update(Override)
            .where(Override.id == void.override_id, Override.decision.in_(OVERRIDE_ACTIVE))
            .values(
                decision="void",
                decided_at=now,
                decided_by=None,
                decision_comment=void.cause,
                updated_at=now,
                updated_by=None,
                revision=Override.revision + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if not cast("CursorResult[Any]", result).rowcount:
            continue
        audit.append(
            log,
            override.audit_chain_key,
            {
                **_audit_fields(override),
                "event": "validation_override_voided",
                "previous_decision": previous,
                "cause": void.cause,
                "evaluated_hash": void.evaluated_hash,
                "evaluated_rule_version": void.evaluated_rule_version,
                "decided_at": now.isoformat(),
            },
            source="system",
        )


# --- routing (Amendment A.4) -----------------------------------------------------------------


def route_approver(personas: Collection[Persona]) -> Persona:
    """The approver persona for a requester holding ``personas``."""
    if Persona.QAQC_LEAD in personas:
        return Persona.PROJECT_MANAGER
    return Persona.QAQC_LEAD


def _project_context(session: Session) -> tuple[TenancyContext, uuid.UUID]:
    context = session_tenancy(session)
    if context is None or context.project_id is None:
        raise RuntimeError("overrides need a session bound to a project")
    return context, context.project_id


def _personas(session: Session, context: TenancyContext, user_id: uuid.UUID) -> set[Persona]:
    grants = load_grants(session, context.tenant_id, user_id)
    return {g.persona for g in grants if g.project_id == context.project_id}


# --- request and decision (Amendment A.1-A.5) ------------------------------------------------


def request_override(
    session: Session,
    finding_id: uuid.UUID,
    reason: str,
    *,
    actor_id: uuid.UUID | None = None,
    approver_user_id: uuid.UUID | None = None,
    source: str = "api",
) -> Override:
    """Request an override of one blocking finding (audited, approver notified).

    Accepted only while the finding is a blocking one (``action == "block"``) of the
    record's latest run of the rule set that logged findings, and the record has no pending
    or approved override of that rule (``OverrideError``). ``reason`` must be non-blank
    (``OverrideInputError``). The approver is routed (``route_approver``); naming
    ``approver_user_id`` routes to that user, who must hold the persona and not be the
    requester (the requester: ``OverridePermissionError``; anybody else:
    ``OverrideInputError``). Without one, every holder of the persona but the requester is
    notified and may decide.
    """
    context, project_id = _project_context(session)
    actor = actor_id or context.user_id
    if actor is None:
        raise OverridePermissionError("an override request needs the requesting user")
    if source == "ai":  # CLAUDE.md §6: no human-confirmation flow for overrides yet
        raise OverrideError("AI-initiated override requests need human confirmation")
    reason = reason.strip()
    if not reason:
        raise OverrideInputError("an override needs a reason")
    if len(reason) > MAX_REASON_LENGTH or "\x00" in reason:
        raise OverrideInputError(f"the reason must be at most {MAX_REASON_LENGTH} characters")
    finding = get_or_404(session, Finding, finding_id)
    if finding.project_id != project_id:
        raise OutOfScopeError("Finding not found")
    target = parse_subject(finding.subject)
    if target is None:
        raise OverrideError("the finding names no record (run_checks subject)")
    if finding.action != "block":
        raise OverrideError("only a blocking finding can be overridden")
    if finding.outcome != "fail":
        raise OverrideError("a rule that could not be evaluated cannot be overridden; fix it")
    if finding.checked_values_hash is None:
        raise OverrideError("the finding predates override binding; run the check again")
    if not _is_current(session, finding):
        raise OverrideError("the record was checked again since; request on the latest finding")
    record_type, record_id = target
    requester = _personas(session, context, actor)
    if not requester:
        raise OverridePermissionError("only a member of the project can request an override")
    persona = route_approver(requester)
    candidates = [
        u for u in persona_holders(session, context.tenant_id, project_id, persona) if u != actor
    ]
    if approver_user_id is not None:
        if approver_user_id == actor:
            raise OverridePermissionError("nobody approves their own override request")
        if approver_user_id not in candidates:
            raise OverrideInputError(f"the approver must hold {persona.value} on the project")
        candidates = [approver_user_id]
    if not candidates:
        raise OverrideError(f"no other member holds {persona.value} to approve the override")
    already = OverrideError("the record already has a pending or approved override of this rule")
    if _active(session, project_id, record_type, record_id, finding) is not None:
        raise already
    now = utcnow()
    override = Override(
        tenant_id=context.tenant_id,
        project_id=project_id,
        record_type=record_type,
        record_id=record_id,
        rule_set_key=finding.rule_set_key,
        rule_key=finding.rule_key,
        rule_version=finding.rule_version,
        finding_id=finding.id,
        requested_by=actor,
        requested_at=now,
        reason=reason,
        approver_persona=persona.value,
        approver_user_id=approver_user_id if approver_user_id is not None else _only(candidates),
        decision="pending",
        checked_values_hash=finding.checked_values_hash,
        audit_chain_key=record_subject(record_type, record_id),
        created_by=actor,
        updated_by=actor,
        source=source,
    )
    session.add(override)
    try:
        # A concurrent request landing after the check above hits the active index: 409. No
        # SAVEPOINT (see ``request_unlock``): the caller's unit of work rolls back instead.
        session.flush()
    except IntegrityError as exc:
        detail = str(exc.orig)
        if OVERRIDE_ACTIVE_INDEX not in detail and _SQLITE_ACTIVE_UNIQUE not in detail:
            raise
        raise already from exc
    audit.append(
        session,
        override.audit_chain_key,
        {
            **_audit_fields(override),
            "event": "validation_override_requested",
            "finding_id": str(finding.id),
            "reason": reason,
            "requested_at": now.isoformat(),
            "checked_values_hash": override.checked_values_hash,
        },
        source=source,
        actor_id=actor,
    )
    for recipient in candidates:
        _notify(session, override, recipient, "validation.override_requested", actor, "high")
    return override


def approve_override(
    session: Session,
    override_id: uuid.UUID,
    *,
    comment: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> Override:
    """The routed approver approves (audited, requester notified)."""
    return _decide(session, override_id, "approved", comment, actor_id)


def reject_override(
    session: Session,
    override_id: uuid.UUID,
    *,
    comment: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> Override:
    """The routed approver rejects (audited, requester notified)."""
    return _decide(session, override_id, "rejected", comment, actor_id)


def get_override(session: Session, override_id: uuid.UUID) -> Override:
    """An override of the bound project, else ``OutOfScopeError`` (404)."""
    _, project_id = _project_context(session)
    override = get_or_404(session, Override, override_id)
    if override.project_id != project_id:
        raise OutOfScopeError("Override not found")
    return override


def list_overrides(
    session: Session,
    *,
    decision: str | None = None,
    record_type: str | None = None,
    record_id: uuid.UUID | None = None,
) -> list[Override]:
    """The bound project's overrides, newest first, optionally filtered."""
    _, project_id = _project_context(session)
    query = select(Override).where(Override.project_id == project_id)
    if decision is not None:
        query = query.where(Override.decision == decision)
    if record_type is not None:
        query = query.where(Override.record_type == record_type)
    if record_id is not None:
        query = query.where(Override.record_id == record_id)
    order = (Override.requested_at.desc(), Override.id.desc())
    return list(session.scalars(query.order_by(*order)))


# --- helpers ----------------------------------------------------------------------------------


def _only(candidates: list[uuid.UUID]) -> uuid.UUID | None:
    return candidates[0] if len(candidates) == 1 else None


def _is_current(session: Session, finding: Finding) -> bool:
    """Whether ``finding`` is from the latest run of its set on its record that logged any."""
    latest = session.scalars(
        select(Finding.run_id)
        .where(
            Finding.project_id == finding.project_id,
            Finding.subject == finding.subject,
            Finding.rule_set_key == finding.rule_set_key,
        )
        .order_by(Finding.created_at.desc(), Finding.id.desc())
        .limit(1)
    ).first()
    return latest == finding.run_id


def _active(
    session: Session,
    project_id: uuid.UUID,
    record_type: str,
    record_id: uuid.UUID,
    finding: Finding,
) -> uuid.UUID | None:
    return session.scalars(
        select(Override.id).where(
            Override.project_id == project_id,
            Override.record_type == record_type,
            Override.record_id == record_id,
            Override.rule_set_key == finding.rule_set_key,
            Override.rule_key == finding.rule_key,
            Override.decision.in_(OVERRIDE_ACTIVE),
        )
    ).first()


def _decide(
    session: Session,
    override_id: uuid.UUID,
    decision: str,
    comment: str | None,
    actor_id: uuid.UUID | None,
) -> Override:
    context, _ = _project_context(session)
    actor = actor_id or context.user_id
    override = get_override(session, override_id)
    if actor is None or actor == override.requested_by:
        raise OverridePermissionError("nobody approves or rejects their own override request")
    if override.approver_user_id is not None and actor != override.approver_user_id:
        raise OverridePermissionError("the override is routed to another approver")
    if Persona(override.approver_persona) not in _personas(session, context, actor):
        raise OverridePermissionError(f"deciding needs {override.approver_persona}")
    if override.decision != "pending":
        raise OverrideError(f"the override is already {override.decision}")
    comment = comment.strip() if comment is not None else None
    if comment is not None and "\x00" in comment:
        raise OverrideInputError("the comment contains a NUL character")
    now = utcnow()
    # Guarded in the database: a concurrent decision or a ``run_checks`` void committed since
    # the row was loaded leaves no pending row to update, so nothing is overwritten.
    result = session.execute(
        update(Override)
        .where(Override.id == override.id, Override.decision == "pending")
        .values(
            decision=decision,
            decided_by=actor,
            decided_at=now,
            decision_comment=comment or None,
            updated_at=now,
            updated_by=actor,
            revision=Override.revision + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if not cast("CursorResult[Any]", result).rowcount:
        raise OverrideError("the override was decided or voided meanwhile")
    session.refresh(override)
    audit.append(
        session,
        override.audit_chain_key,
        {
            **_audit_fields(override),
            "event": f"validation_override_{decision}",
            "decided_by": str(actor),
            "decided_at": now.isoformat(),
            "decision_comment": override.decision_comment,
        },
        source="api",
        actor_id=actor,
    )
    _notify(session, override, override.requested_by, f"validation.override_{decision}", actor)
    return override


def _audit_fields(override: Override) -> dict[str, Any]:
    return {
        "override_id": str(override.id),
        "record_type": override.record_type,
        "record_id": str(override.record_id),
        "rule_set": override.rule_set_key,
        "rule_key": override.rule_key,
        "rule_version": override.rule_version,
        "requested_by": str(override.requested_by),
        "approver_persona": override.approver_persona,
        "approver_user_id": None
        if override.approver_user_id is None
        else str(override.approver_user_id),
    }


def _notify(
    session: Session,
    override: Override,
    recipient: uuid.UUID,
    kind: str,
    actor: uuid.UUID,
    priority: str = "normal",
) -> None:
    data = NotificationCreate.model_validate(
        {
            "recipient_id": recipient,
            "kind": kind,
            "params": {
                "record_type": override.record_type,
                "rule_set": override.rule_set_key,
                "rule_key": override.rule_key,
            },
            "label": "validation",
            "priority": priority,
            "subject_type": SUBJECT_TYPE,
            "subject_id": override.id,
        }
    )
    try:
        notifications.notify(session, data, actor_id=actor)
    except notifications.RecipientNotMemberError:
        # The requester left the project meanwhile: the decision stands, nobody to tell.
        logger.info("override %s: recipient is no longer a project member", override.id)
