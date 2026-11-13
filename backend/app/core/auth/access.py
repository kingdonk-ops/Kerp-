"""Three-tier RBAC/ABAC (T-0010): capability tiers x 15 personas x project/site scope.

Edge spec §5 (persona matrix, capability tiers, separation of duties), diff §4 (Asset-Site
scoping), edge spec §6 amendments (explicit multi-project read scope, TD-053). Guard shape
ported from Inspection-ERP ``core/auth/deps.py`` (ADR 0001): one shared dependency decides,
endpoints only declare what they need.

* **Tier** (edge spec §5.1): a persona's capabilities are V/C/E from its default tier,
  plus ``A`` (Approver / Gate Authority) where the matrix gives hold or commercial
  authority, and ``S`` (Signatory) for the client representative.
* **Scope**: ``portfolio`` personas (PER-01/10/11/12/13) read every project they are a
  member of in one session (``app.allowed_project_ids``); every other persona reads only
  the bound project. A Site Inspector (PER-07) sees only their site's rows.
* **SoD**: hold release is an explicit per-persona grant. The Project Administrator
  (PER-09) holds no hold authority at all (``SOD_BLOCKED``), whatever its admin tier.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Final

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.deps import CurrentPrincipal
from app.core.auth.models import ProjectMembership
from app.core.db import DbSession
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import OutOfScopeError

if TYPE_CHECKING:
    from app.core.db import Database


class Capability(StrEnum):
    VIEW = "V"
    CREATE = "C"
    EDIT = "E"
    APPROVE = "A"
    SIGN = "S"
    ADMINISTER = "admin"  # users, settings, config packs (PER-09's "Admin" tier)


class Tier(StrEnum):
    """The "Default Tier" column of edge spec §5.2."""

    MANAGER = "manager"
    EDITOR = "editor"
    ADMIN = "admin"
    PORTAL = "portal"


class Scope(StrEnum):
    PORTFOLIO = "portfolio"  # every project the user is a member of, in one session
    ASSIGNED = "assigned"  # assigned projects, one bound at a time
    SINGLE = "single"
    AREA = "area"  # area/discipline filter: not enforced yet (TD-149)
    SITE = "site"  # Asset-Site scoping (site_node_ids; ORM-only, TD-150)
    SUBTREE = "subtree"  # client-portal granted subtree: not enforced yet (TD-149)


class HoldKind(StrEnum):
    QUALITY = "quality"
    SAFETY = "safety"


_V, _C, _E = Capability.VIEW, Capability.CREATE, Capability.EDIT
TIER_CAPABILITIES: Final[Mapping[Tier, frozenset[Capability]]] = {
    Tier.MANAGER: frozenset({_V, _C, _E}),
    Tier.EDITOR: frozenset({_V, _C, _E}),
    Tier.ADMIN: frozenset({_V, _C, _E, Capability.ADMINISTER}),
    Tier.PORTAL: frozenset({_V}),
}


class Persona(StrEnum):
    OPERATIONS_MANAGEMENT = "PER-01"
    PROJECT_MANAGER = "PER-02"
    SITE_SUPERINTENDENT = "PER-03"
    TRADE_SUPERVISOR = "PER-04"
    AREA_PLANNER = "PER-05"
    QAQC_LEAD = "PER-06"
    SITE_INSPECTOR = "PER-07"
    STOREKEEPER = "PER-08"
    PROJECT_ADMINISTRATOR = "PER-09"
    PROCUREMENT_SPECIALIST = "PER-10"
    HSE_MANAGER = "PER-11"
    HR_MASTER = "PER-12"
    QUALITY_DIRECTOR = "PER-13"
    FIELD_WORKER = "PER-14"
    CLIENT_REPRESENTATIVE = "PER-15"


@dataclass(frozen=True, slots=True)
class PersonaSpec:
    tier: Tier
    scope: Scope
    holds: frozenset[HoldKind] = frozenset()  # hold kinds this persona may release
    commercial_approval: bool = False
    signatory: bool = False

    @property
    def capabilities(self) -> frozenset[Capability]:
        extra: set[Capability] = set()
        if self.holds or self.commercial_approval:
            extra.add(Capability.APPROVE)
        if self.signatory:
            extra.add(Capability.SIGN)
        return TIER_CAPABILITIES[self.tier] | extra


_Q, _S = frozenset({HoldKind.QUALITY}), frozenset({HoldKind.SAFETY})
P = Persona
# Edge spec §5.2, row by row. "Read-wide", "Review only", "Audit oversight" and "None" give no
# hold release; "Witness / Co-sign (S)" is a signature, not a release.
PERSONAS: Final[Mapping[Persona, PersonaSpec]] = {
    P.OPERATIONS_MANAGEMENT: PersonaSpec(Tier.MANAGER, Scope.PORTFOLIO),
    P.PROJECT_MANAGER: PersonaSpec(Tier.MANAGER, Scope.ASSIGNED, commercial_approval=True),
    P.SITE_SUPERINTENDENT: PersonaSpec(Tier.MANAGER, Scope.SINGLE),
    P.TRADE_SUPERVISOR: PersonaSpec(Tier.EDITOR, Scope.AREA),
    P.AREA_PLANNER: PersonaSpec(Tier.EDITOR, Scope.SINGLE),
    P.QAQC_LEAD: PersonaSpec(Tier.MANAGER, Scope.SINGLE, holds=_Q),
    P.SITE_INSPECTOR: PersonaSpec(Tier.EDITOR, Scope.SITE, holds=_Q),
    P.STOREKEEPER: PersonaSpec(Tier.EDITOR, Scope.SINGLE),
    P.PROJECT_ADMINISTRATOR: PersonaSpec(Tier.ADMIN, Scope.SINGLE),
    P.PROCUREMENT_SPECIALIST: PersonaSpec(Tier.EDITOR, Scope.PORTFOLIO),
    P.HSE_MANAGER: PersonaSpec(Tier.MANAGER, Scope.PORTFOLIO, holds=_S),
    P.HR_MASTER: PersonaSpec(Tier.EDITOR, Scope.PORTFOLIO),
    P.QUALITY_DIRECTOR: PersonaSpec(Tier.MANAGER, Scope.PORTFOLIO),
    P.FIELD_WORKER: PersonaSpec(Tier.EDITOR, Scope.SINGLE),
    P.CLIENT_REPRESENTATIVE: PersonaSpec(Tier.PORTAL, Scope.SUBTREE, signatory=True),
}

# Separation of duties (edge spec §5.2 PER-09 "None (SoD Block)"): these personas may never
# be granted these hold kinds. ``sod_violations`` checks the matrix against it.
SOD_BLOCKED: Final[Mapping[Persona, frozenset[HoldKind]]] = {
    P.PROJECT_ADMINISTRATOR: frozenset(HoldKind),
}


def sod_violations(matrix: Mapping[Persona, PersonaSpec] = PERSONAS) -> list[str]:
    """Why ``matrix`` breaks a separation-of-duties rule (empty = OK)."""
    return [
        f"{persona} must not release {kind} holds"
        for persona, blocked in SOD_BLOCKED.items()
        for kind in sorted(matrix[persona].holds & blocked)
    ]


@dataclass(frozen=True, slots=True)
class Grant:
    """One membership row: a persona on a project (and, for PER-07, a site)."""

    project_id: uuid.UUID
    persona: Persona
    site_node_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ProjectAccess:
    """What a user may do in one project: the union of their personas there."""

    project_id: uuid.UUID
    grants: frozenset[Grant]

    @property
    def personas(self) -> frozenset[Persona]:
        return frozenset(g.persona for g in self.grants)

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset().union(*(PERSONAS[p].capabilities for p in self.personas))

    def has(self, capability: Capability) -> bool:
        return capability in self.capabilities

    @property
    def site_node_ids(self) -> frozenset[uuid.UUID] | None:
        """The sites the user is limited to, or ``None`` when any persona is unrestricted."""
        if not self.grants or any(PERSONAS[g.persona].scope is not Scope.SITE for g in self.grants):
            return None
        return frozenset(g.site_node_id for g in self.grants if g.site_node_id is not None)

    def can_release_hold(self, kind: HoldKind, *, site_node_id: uuid.UUID | None = None) -> bool:
        """Whether some persona of the user may release a ``kind`` hold (on that site)."""
        for grant in self.grants:
            spec = PERSONAS[grant.persona]
            if kind not in spec.holds or kind in SOD_BLOCKED.get(grant.persona, frozenset()):
                continue
            if spec.scope is Scope.SITE and grant.site_node_id != site_node_id:
                continue
            return True
        return False


def project_access(grants: Iterable[Grant], project_id: uuid.UUID) -> ProjectAccess:
    """The user's access to ``project_id``; ``OutOfScopeError`` (404) for a non-member."""
    here = frozenset(g for g in grants if g.project_id == project_id)
    if not here:
        raise OutOfScopeError("project not found")
    return ProjectAccess(project_id, here)


def resolve_scope(
    grants: Iterable[Grant], base: TenancyContext, project_id: uuid.UUID | None = None
) -> TenancyContext:
    """The session scope for a user: ``base`` plus the bound project and the allowed list.

    The allowed (read-only) projects are those of the user's portfolio-scope personas; the
    bound project, if any, must be one of the user's projects and sets the site filter.
    """
    grants = list(grants)
    allowed = frozenset(
        g.project_id for g in grants if PERSONAS[g.persona].scope is Scope.PORTFOLIO
    )
    sites = project_access(grants, project_id).site_node_ids if project_id is not None else None
    return TenancyContext(
        tenant_id=base.tenant_id,
        project_id=project_id,
        user_id=base.user_id,
        allowed_project_ids=allowed,
        site_node_ids=sites,
    )


def load_grants(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Grant]:
    """The user's active memberships (tenant-level rows, soft-deleted ones excluded)."""
    rows = session.execute(
        select(
            ProjectMembership.member_project_id,
            ProjectMembership.persona,
            ProjectMembership.site_node_id,
        ).where(ProjectMembership.tenant_id == tenant_id, ProjectMembership.user_id == user_id)
    ).all()
    return [Grant(row[0], Persona(row[1]), row[2]) for row in rows]


def resolve_request_scope(database: Database, base: TenancyContext) -> TenancyContext:
    """Bind the request's allowed projects from the membership store (never the request).

    Runs in its own short tenant-level unit of work, before the request session is bound.
    """
    if base.user_id is None:
        return base
    with database.unit_of_work(TenancyContext(tenant_id=base.tenant_id)) as session:
        grants = load_grants(session, base.tenant_id, base.user_id)
    return resolve_scope(grants, base, base.project_id)


# --- FastAPI guards ----------------------------------------------------------------------


def get_project_access(principal: CurrentPrincipal, session: DbSession) -> ProjectAccess:
    """The caller's access to the session's bound project; 404 without one or a membership."""
    context = session_tenancy(session)
    if context is None or context.project_id is None:
        raise OutOfScopeError("project not found")
    grants = load_grants(session, principal.tenant_id, principal.user_id)
    return project_access(grants, context.project_id)


CurrentAccess = Annotated[ProjectAccess, Depends(get_project_access)]


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


def require_capability(capability: Capability) -> Callable[[ProjectAccess], ProjectAccess]:
    """Dependency factory: 403 unless the caller holds ``capability`` in the bound project."""

    def guard(access: CurrentAccess) -> ProjectAccess:
        if not access.has(capability):
            raise _forbidden()
        return access

    return guard


def require_hold_release(kind: HoldKind) -> Callable[[ProjectAccess], ProjectAccess]:
    """Dependency factory: 403 unless some persona may release ``kind`` holds at all.

    Site-limited authority (PER-07) still needs ``access.can_release_hold(kind,
    site_node_id=...)`` with the hold's site once the endpoint has loaded it.
    """

    def guard(access: CurrentAccess) -> ProjectAccess:
        if not any(kind in PERSONAS[p].holds for p in access.personas - frozenset(SOD_BLOCKED)):
            raise _forbidden()
        return access

    return guard
