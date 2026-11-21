"""Rules settings API, mounted at ``/api/v1/validation`` (T-0034, ADR 0004 §3, §5, §7).

One API for the tenant default layer and the project set (the same Rules screen, §7):

* ``GET    /rule-sets``                                     tenant default sets
* ``PUT    /rule-sets/{set}/rules/{key}``                   add or edit a rule
* ``POST   /rule-sets/{set}/rules/{key}/disable``, ``/enable``  disable / enable
* ``DELETE /rule-sets/{set}/rules/{key}``                   soft delete (key kept)
* ``POST   /rule-sets/{set}/rules/{key}/reset``             reset one rule to the default
* ``POST   /rule-sets/{set}/reset``                         reset the whole set
* ``POST   /rule-sets/{set}/rules/{key}/unlock-requests``   ask to unlock a locked rule
* ``GET    /unlock-requests``, ``POST /unlock-requests/{id}/approve|reject``
* ``/projects/{project}/rule-sets...``                      the same rule operations on the
  project's own copy (no unlock: only the tenant layer unlocks)
* ``GET    .../rule-sets/{set}/updates``                    upgrade offer: per-rule diff
* ``POST   .../rule-sets/{set}/updates/{key}/accept``       accept one rule of it (T-0036)
* ``POST   .../rule-sets/{set}/rules/{key}/try``            "Try it" on a sample context
* ``GET    /drift-report``                                  project rules that differ from
  the tenant defaults
* ``POST   /projects/{project}/overrides``                   request a per-record override of
  a blocking finding (T-0035, Amendment A); ``GET`` lists, ``GET .../{id}`` reads,
  ``POST .../{id}/approve|reject`` decides

**Who may (§7).** Tenant layer: ``check`` sets PER-13 Quality Director, other kinds PER-01
Operations Management. Project set: ``check`` sets PER-06 QA/QC Lead, other kinds PER-09
Project Administrator, so PER-09 never changes, disables or deletes a ``check`` rule (SoD).
Unlock: PER-13 requests; a second, different PER-13 or PER-01 user approves (TD-116 interim).
An upgrade offer is read by whoever reads the set and accepted by whoever edits it; "Try it"
is for whoever edits the set; the drift report is for the tenant readers (PER-13, PER-01)
and covers the projects their portfolio scope reads (RLS). Everyone else is 403. The request
binds no project yet (TD-122) and there is no tenant-level role store yet (T-0014), so a
tenant persona is one the caller holds on any project of the tenant (the same interim as
project creation, TD-184). A non-member of the project in the
path, or a set outside the path's scope, is 404.

**Overrides (Amendment A).** Every member with ``C`` (create) may request; every member reads.
The service routes the approver from the requester's memberships (PER-06, or PER-02 when the
requester holds PER-06) and refuses the requester as approver (403); a caller without the
routed persona is 403. Blank reason or an ineligible named approver 422; a finding that is
not blocking, not the latest, or already has an active override 409.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Final, Literal

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.auth.access import CallerGrants, Capability, Grant, Persona
from app.core.auth.deps import CurrentPrincipal, Principal
from app.core.db import DbSession
from app.modules.projects import service as projects
from app.modules.validation import overrides, service, upgrades
from app.modules.validation.models import RuleSet
from app.modules.validation.schemas import (
    KEY_PATTERN,
    DriftEntryRead,
    OverrideCreate,
    OverrideDecision,
    OverrideDecisionInput,
    OverrideRead,
    RuleInput,
    RuleRead,
    RuleSetRead,
    RuleSetUpdatesRead,
    TryResult,
    TryRuleInput,
    UnlockRequestCreate,
    UnlockRequestRead,
)

router = APIRouter(tags=["validation"])

P = Persona
# ADR 0004 §7: who edits which kind on which layer.
TENANT_EDITORS: Final = {"check": frozenset({P.QUALITY_DIRECTOR})}
TENANT_OTHER: Final = frozenset({P.OPERATIONS_MANAGEMENT})
PROJECT_EDITORS: Final = {"check": frozenset({P.QAQC_LEAD})}
PROJECT_OTHER: Final = frozenset({P.PROJECT_ADMINISTRATOR})
TENANT_READERS: Final = frozenset({P.QUALITY_DIRECTOR, P.OPERATIONS_MANAGEMENT})
UNLOCK_REQUESTERS: Final = frozenset({P.QUALITY_DIRECTOR})
UNLOCK_APPROVERS: Final = frozenset({P.QUALITY_DIRECTOR, P.OPERATIONS_MANAGEMENT})  # TD-116

TENANT_SET = "/rule-sets/{rule_set_id}"
PROJECT_SET = "/projects/{project_id}/rule-sets/{rule_set_id}"
RuleKey = Annotated[str, Path(pattern=KEY_PATTERN)]
Toggle = Literal["enable", "disable"]


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


@contextmanager
def _errors() -> Iterator[None]:
    """The service's refusals as HTTP answers."""
    try:
        yield
    except (service.RuleCompileError, upgrades.SampleContextError, ValidationError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except service.RuleStoreError as exc:  # incl. RuleLockedError
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except service.RuleNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except service.UnlockPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@contextmanager
def _override_errors() -> Iterator[None]:
    """The override service's refusals as HTTP answers (T-0035)."""
    try:
        yield
    except overrides.OverrideInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except overrides.OverrideError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except overrides.OverridePermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


def _tenant_personas(grants: list[Grant]) -> frozenset[Persona]:
    """Personas the caller holds anywhere in the tenant (interim tenant role, TD-184)."""
    return frozenset(g.persona for g in grants)


def _require(held: frozenset[Persona], allowed: frozenset[Persona]) -> None:
    if not held & allowed:
        raise _forbidden()


def _read(session: Session, rule_set: RuleSet) -> RuleSetRead:
    rules = [RuleRead.model_validate(rule) for rule in service.list_rules(session, rule_set)]
    return RuleSetRead.model_validate({**_set_fields(rule_set), "rules": rules})


def _set_fields(rule_set: RuleSet) -> dict[str, object]:
    names = RuleSetRead.model_fields.keys() - {"rules"}
    return {name: getattr(rule_set, name) for name in names}


# --- scopes ------------------------------------------------------------------------------


def _tenant_set(
    session: Session, grants: list[Grant], rule_set_id: uuid.UUID
) -> tuple[RuleSet, frozenset[Persona]]:
    """A tenant-layer set the caller may edit (403 without the §7 persona for its kind)."""
    held = _tenant_personas(grants)
    _require(held, TENANT_READERS)
    rule_set = service.get_rule_set(session, rule_set_id, "tenant")
    _require(held, TENANT_EDITORS.get(rule_set.kind, TENANT_OTHER))
    return rule_set, held


def _tenant_set_read(session: Session, grants: list[Grant], rule_set_id: uuid.UUID) -> RuleSet:
    """A tenant-layer set the caller may read (PER-13 or PER-01)."""
    _require(_tenant_personas(grants), TENANT_READERS)
    return service.get_rule_set(session, rule_set_id, "tenant")


def _bind_project(
    principal: Principal, grants: list[Grant], session: Session, project_id: uuid.UUID
) -> frozenset[Persona]:
    """Bind the request session to ``project_id`` (non-member: 404); the caller's personas,
    with PER-09 for a ``system_admin`` (PER-09 equivalent, ADR 0016; it gives no approval)."""
    return projects.bind_project(session, principal, grants, project_id).acting_personas


def _project_set(
    principal: Principal,
    grants: list[Grant],
    session: Session,
    project_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    *,
    edit: bool = True,
) -> RuleSet:
    """A set of the project's own copy that the caller may edit (§7, SoD for PER-09), or,
    with ``edit=False``, read (every member)."""
    held = _bind_project(principal, grants, session, project_id)
    rule_set = service.get_rule_set(session, rule_set_id, "project")
    if edit:
        _require(held, PROJECT_EDITORS.get(rule_set.kind, PROJECT_OTHER))
    return rule_set


# --- shared rule operations -----------------------------------------------------------------


def _put(
    session: Session, rule_set: RuleSet, rule_key: str, body: RuleInput, actor: uuid.UUID
) -> RuleSetRead:
    with _errors():
        service.save_rule(session, rule_set.id, body.to_spec(rule_key), actor_id=actor)
    return _read(session, rule_set)


def _toggle(
    session: Session, rule_set: RuleSet, rule_key: str, action: Toggle, actor: uuid.UUID
) -> RuleSetRead:
    with _errors():
        enabled = action == "enable"
        service.set_rule_enabled(session, rule_set.id, rule_key, enabled, actor_id=actor)
    return _read(session, rule_set)


def _delete(session: Session, rule_set: RuleSet, rule_key: str, actor: uuid.UUID) -> RuleSetRead:
    with _errors():
        service.delete_rule(session, rule_set.id, rule_key, actor_id=actor)
    return _read(session, rule_set)


def _reset(
    session: Session, rule_set: RuleSet, rule_key: str | None, actor: uuid.UUID
) -> RuleSetRead:
    with _errors():
        if rule_key is None:
            service.reset_rule_set(session, rule_set.id, actor_id=actor)
        else:
            service.reset_rule(session, rule_set.id, rule_key, actor_id=actor)
    return _read(session, rule_set)


def _accept(session: Session, rule_set: RuleSet, rule_key: str, actor: uuid.UUID) -> RuleSetRead:
    with _errors():
        upgrades.accept_update(session, rule_set.id, rule_key, actor_id=actor)
    return _read(session, rule_set)


def _try(session: Session, rule_set: RuleSet, rule_key: str, body: TryRuleInput) -> TryResult:
    with _errors():
        draft = None if body.rule is None else body.rule.to_spec(rule_key)
        return upgrades.try_rule(session, rule_set.id, rule_key, body.context, draft=draft)


# --- tenant default layer ---------------------------------------------------------------------


@router.get("/rule-sets", response_model=list[RuleSetRead])
def list_tenant_rule_sets(grants: CallerGrants, session: DbSession) -> list[RuleSetRead]:
    _require(_tenant_personas(grants), TENANT_READERS)
    return [_read(session, s) for s in service.list_rule_sets(session, "tenant")]


@router.put(f"{TENANT_SET}/rules/{{rule_key}}", response_model=RuleSetRead)
def put_tenant_rule(
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    body: RuleInput,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> RuleSetRead:
    rule_set, _ = _tenant_set(session, grants, rule_set_id)
    return _put(session, rule_set, rule_key, body, principal.user_id)


@router.post(f"{TENANT_SET}/rules/{{rule_key}}/enable", response_model=RuleSetRead)
def enable_tenant_rule(
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> RuleSetRead:
    rule_set, _ = _tenant_set(session, grants, rule_set_id)
    return _toggle(session, rule_set, rule_key, "enable", principal.user_id)


@router.post(f"{TENANT_SET}/rules/{{rule_key}}/disable", response_model=RuleSetRead)
def disable_tenant_rule(
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> RuleSetRead:
    rule_set, _ = _tenant_set(session, grants, rule_set_id)
    return _toggle(session, rule_set, rule_key, "disable", principal.user_id)


@router.delete(f"{TENANT_SET}/rules/{{rule_key}}", response_model=RuleSetRead)
def delete_tenant_rule(
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> RuleSetRead:
    rule_set, _ = _tenant_set(session, grants, rule_set_id)
    return _delete(session, rule_set, rule_key, principal.user_id)


@router.post(f"{TENANT_SET}/rules/{{rule_key}}/reset", response_model=RuleSetRead)
def reset_tenant_rule(
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> RuleSetRead:
    rule_set, _ = _tenant_set(session, grants, rule_set_id)
    return _reset(session, rule_set, rule_key, principal.user_id)


@router.post(f"{TENANT_SET}/reset", response_model=RuleSetRead)
def reset_tenant_rule_set(
    rule_set_id: uuid.UUID, principal: CurrentPrincipal, grants: CallerGrants, session: DbSession
) -> RuleSetRead:
    rule_set, _ = _tenant_set(session, grants, rule_set_id)
    return _reset(session, rule_set, None, principal.user_id)


@router.get(f"{TENANT_SET}/updates", response_model=RuleSetUpdatesRead)
def tenant_rule_set_updates(
    rule_set_id: uuid.UUID, grants: CallerGrants, session: DbSession
) -> RuleSetUpdatesRead:
    rule_set = _tenant_set_read(session, grants, rule_set_id)
    with _errors():
        return upgrades.pending_updates(session, rule_set.id)


@router.post(f"{TENANT_SET}/updates/{{rule_key}}/accept", response_model=RuleSetRead)
def accept_tenant_update(
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> RuleSetRead:
    rule_set, _ = _tenant_set(session, grants, rule_set_id)
    return _accept(session, rule_set, rule_key, principal.user_id)


@router.post(f"{TENANT_SET}/rules/{{rule_key}}/try", response_model=TryResult)
def try_tenant_rule(
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    body: TryRuleInput,
    grants: CallerGrants,
    session: DbSession,
) -> TryResult:
    rule_set, _ = _tenant_set(session, grants, rule_set_id)
    return _try(session, rule_set, rule_key, body)


@router.get("/drift-report", response_model=list[DriftEntryRead])
def drift_report(grants: CallerGrants, session: DbSession) -> list[DriftEntryRead]:
    _require(_tenant_personas(grants), TENANT_READERS)
    return upgrades.drift_report(session)


# --- two-person unlock (ADR 0004 §5, Amendment B, TD-116) -------------------------------------


@router.post(
    f"{TENANT_SET}/rules/{{rule_key}}/unlock-requests",
    response_model=UnlockRequestRead,
    status_code=status.HTTP_201_CREATED,
)
def request_unlock(
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    body: UnlockRequestCreate,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> UnlockRequestRead:
    _require(_tenant_personas(grants), UNLOCK_REQUESTERS)
    rule_set = service.get_rule_set(session, rule_set_id, "tenant")
    with _errors():
        request = service.request_unlock(
            session,
            rule_set.id,
            rule_key,
            adr_reference=body.adr_reference,
            reason=body.reason,
            actor_id=principal.user_id,
        )
    return UnlockRequestRead.model_validate(request)


@router.get("/unlock-requests", response_model=list[UnlockRequestRead])
def list_unlock_requests(
    grants: CallerGrants,
    session: DbSession,
    status_filter: Annotated[
        Literal["pending", "approved", "rejected", "cancelled"] | None, Query(alias="status")
    ] = None,
) -> list[UnlockRequestRead]:
    _require(_tenant_personas(grants), UNLOCK_APPROVERS)
    requests = service.list_unlock_requests(session, status_filter)
    return [UnlockRequestRead.model_validate(r) for r in requests]


@router.post("/unlock-requests/{request_id}/approve", response_model=RuleSetRead)
def approve_unlock(
    request_id: uuid.UUID, principal: CurrentPrincipal, grants: CallerGrants, session: DbSession
) -> RuleSetRead:
    _require(_tenant_personas(grants), UNLOCK_APPROVERS)
    with _errors():
        rule = service.approve_unlock(session, request_id, actor_id=principal.user_id)
    return _read(session, service.get_rule_set(session, rule.rule_set_id, "tenant"))


@router.post("/unlock-requests/{request_id}/reject", response_model=UnlockRequestRead)
def reject_unlock(
    request_id: uuid.UUID, principal: CurrentPrincipal, grants: CallerGrants, session: DbSession
) -> UnlockRequestRead:
    _require(_tenant_personas(grants), UNLOCK_APPROVERS)
    with _errors():
        request = service.reject_unlock(session, request_id, actor_id=principal.user_id)
    return UnlockRequestRead.model_validate(request)


# --- project set ------------------------------------------------------------------------------


@router.get("/projects/{project_id}/rule-sets", response_model=list[RuleSetRead])
def list_project_rule_sets(
    project_id: uuid.UUID, principal: CurrentPrincipal, grants: CallerGrants, session: DbSession
) -> list[RuleSetRead]:
    _bind_project(principal, grants, session, project_id)  # every member reads its gates
    return [_read(session, s) for s in service.list_rule_sets(session, "project")]


@router.put(f"{PROJECT_SET}/rules/{{rule_key}}", response_model=RuleSetRead)
def put_project_rule(
    project_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    body: RuleInput,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> RuleSetRead:
    rule_set = _project_set(principal, grants, session, project_id, rule_set_id)
    return _put(session, rule_set, rule_key, body, principal.user_id)


@router.post(f"{PROJECT_SET}/rules/{{rule_key}}/enable", response_model=RuleSetRead)
def enable_project_rule(
    project_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> RuleSetRead:
    rule_set = _project_set(principal, grants, session, project_id, rule_set_id)
    return _toggle(session, rule_set, rule_key, "enable", principal.user_id)


@router.post(f"{PROJECT_SET}/rules/{{rule_key}}/disable", response_model=RuleSetRead)
def disable_project_rule(
    project_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> RuleSetRead:
    rule_set = _project_set(principal, grants, session, project_id, rule_set_id)
    return _toggle(session, rule_set, rule_key, "disable", principal.user_id)


@router.delete(f"{PROJECT_SET}/rules/{{rule_key}}", response_model=RuleSetRead)
def delete_project_rule(
    project_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> RuleSetRead:
    rule_set = _project_set(principal, grants, session, project_id, rule_set_id)
    return _delete(session, rule_set, rule_key, principal.user_id)


@router.post(f"{PROJECT_SET}/rules/{{rule_key}}/reset", response_model=RuleSetRead)
def reset_project_rule(
    project_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> RuleSetRead:
    rule_set = _project_set(principal, grants, session, project_id, rule_set_id)
    return _reset(session, rule_set, rule_key, principal.user_id)


@router.post(f"{PROJECT_SET}/reset", response_model=RuleSetRead)
def reset_project_rule_set(
    project_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> RuleSetRead:
    rule_set = _project_set(principal, grants, session, project_id, rule_set_id)
    return _reset(session, rule_set, None, principal.user_id)


@router.get(f"{PROJECT_SET}/updates", response_model=RuleSetUpdatesRead)
def project_rule_set_updates(
    project_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> RuleSetUpdatesRead:
    rule_set = _project_set(principal, grants, session, project_id, rule_set_id, edit=False)
    with _errors():
        return upgrades.pending_updates(session, rule_set.id)


@router.post(f"{PROJECT_SET}/updates/{{rule_key}}/accept", response_model=RuleSetRead)
def accept_project_update(
    project_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> RuleSetRead:
    rule_set = _project_set(principal, grants, session, project_id, rule_set_id)
    return _accept(session, rule_set, rule_key, principal.user_id)


@router.post(f"{PROJECT_SET}/rules/{{rule_key}}/try", response_model=TryResult)
def try_project_rule(
    project_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    rule_key: RuleKey,
    body: TryRuleInput,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> TryResult:
    rule_set = _project_set(principal, grants, session, project_id, rule_set_id)
    return _try(session, rule_set, rule_key, body)


# --- per-record override of a blocked check (T-0035, ADR 0004 Amendment A) ------------------

OVERRIDES = "/projects/{project_id}/overrides"


@router.post(OVERRIDES, response_model=OverrideRead, status_code=status.HTTP_201_CREATED)
def request_override(
    project_id: uuid.UUID,
    body: OverrideCreate,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> OverrideRead:
    projects.bind_project(session, principal, grants, project_id, require=Capability.CREATE)
    with _override_errors():
        override = overrides.request_override(
            session,
            body.finding_id,
            body.reason,
            actor_id=principal.user_id,
            approver_user_id=body.approver_user_id,
        )
    return OverrideRead.model_validate(override)


@router.get(OVERRIDES, response_model=list[OverrideRead])
def list_overrides(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
    decision: OverrideDecision | None = None,
    record_type: Annotated[str | None, Query(max_length=60)] = None,
    record_id: uuid.UUID | None = None,
) -> list[OverrideRead]:
    _bind_project(principal, grants, session, project_id)  # every member reads
    rows = overrides.list_overrides(
        session, decision=decision, record_type=record_type, record_id=record_id
    )
    return [OverrideRead.model_validate(row) for row in rows]


@router.get(f"{OVERRIDES}/{{override_id}}", response_model=OverrideRead)
def get_override(
    project_id: uuid.UUID,
    override_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> OverrideRead:
    _bind_project(principal, grants, session, project_id)
    return OverrideRead.model_validate(overrides.get_override(session, override_id))


@router.post(f"{OVERRIDES}/{{override_id}}/approve", response_model=OverrideRead)
def approve_override(
    project_id: uuid.UUID,
    override_id: uuid.UUID,
    body: OverrideDecisionInput,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> OverrideRead:
    _bind_project(principal, grants, session, project_id)  # the service checks the approver
    with _override_errors():
        override = overrides.approve_override(
            session, override_id, comment=body.comment, actor_id=principal.user_id
        )
    return OverrideRead.model_validate(override)


@router.post(f"{OVERRIDES}/{{override_id}}/reject", response_model=OverrideRead)
def reject_override(
    project_id: uuid.UUID,
    override_id: uuid.UUID,
    body: OverrideDecisionInput,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> OverrideRead:
    _bind_project(principal, grants, session, project_id)
    with _override_errors():
        override = overrides.reject_override(
            session, override_id, comment=body.comment, actor_id=principal.user_id
        )
    return OverrideRead.model_validate(override)
