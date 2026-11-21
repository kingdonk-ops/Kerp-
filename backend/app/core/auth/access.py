"""Three-tier RBAC/ABAC (T-0010): capability tiers x 16 personas x project/site scope.

Edge spec §5 (persona matrix, capability tiers, separation of duties), diff §4 (Asset-Site
scoping), edge spec §6 amendments (explicit multi-project read scope, TD-053). Guard shape
ported from Inspection-ERP ``core/auth/deps.py`` (ADR 0001): one shared dependency decides,
endpoints only declare what they need.

* **Tier** (edge spec §5.1): a persona's capabilities are V/C/E from its default tier,
  plus ``A`` (Approver / Gate Authority) where the matrix gives hold or commercial
  authority, and ``S`` (Signatory) for the client representative. The Project Viewer
  (PER-16, Viewer tier, T-0076) has ``V`` only.
* **Scope**: ``portfolio`` personas (PER-01/10/11/12/13) read every project they are a
  member of in one session (``app.allowed_project_ids``); every other persona reads only
  the bound project. A Site Inspector (PER-07) sees only their site's rows.
* **SoD**: hold release is an explicit per-persona grant. The Project Administrator
  (PER-09) holds no hold authority at all (``SOD_BLOCKED``), whatever its admin tier.
* **System admin** (ADR 0016, T-0275): a ``system_admin`` has the Admin tier (``V``, ``C``,
  ``E``, ``admin``: PER-09 equivalent) on every live project of its tenant without a
  membership, never ``A`` or ``S``: those come only from its own personas on the project.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Final

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.auth.deps import CurrentPrincipal
from app.core.auth.models import ProjectMembership
from app.core.db import SIDE_WRITE_DB_KEY, DbSession
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_tenancy, session_tenancy
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
    VIEWER = "viewer"  # PER-16 Project Viewer (edge spec §5.2 amendment, TD-251)


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
    Tier.VIEWER: frozenset({_V}),
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
    PROJECT_VIEWER = "PER-16"


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
    # Read-only across the project's modules; no hold, approval or signing authority, so no
    # separation-of-duties rule applies to it.
    P.PROJECT_VIEWER: PersonaSpec(Tier.VIEWER, Scope.SINGLE),
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
    """What a user may do in one project: the union of their personas there, plus, for a
    ``system_admin`` (ADR 0016, T-0275), the Admin tier (``V``/``C``/``E``/``admin``, PER-09
    equivalent) with or without a membership. ``A`` and ``S`` and hold release come only from
    the user's own personas (``grants``), never from ``system_admin``."""

    project_id: uuid.UUID
    grants: frozenset[Grant]
    system_admin: bool = False

    @property
    def personas(self) -> frozenset[Persona]:
        """The user's own personas on the project (memberships only)."""
        return frozenset(g.persona for g in self.grants)

    @property
    def acting_personas(self) -> frozenset[Persona]:
        """``personas`` plus PER-09 for a ``system_admin``: for persona-gated *configuration*
        rules ("PER-09 may edit ..."). PER-09 has no ``A``/``S`` and no hold authority, so
        this never widens approval; approvers are still checked against ``personas``."""
        admin = {Persona.PROJECT_ADMINISTRATOR} if self.system_admin else set()
        return self.personas | admin

    @property
    def capabilities(self) -> frozenset[Capability]:
        admin = TIER_CAPABILITIES[Tier.ADMIN] if self.system_admin else frozenset()
        return admin.union(*(PERSONAS[p].capabilities for p in self.personas))

    def has(self, capability: Capability) -> bool:
        return capability in self.capabilities

    @property
    def site_node_ids(self) -> frozenset[uuid.UUID] | None:
        """The sites the user is limited to, or ``None`` when any persona is unrestricted (a
        ``system_admin`` is never site-filtered, ``scope.site_filter``)."""
        if (
            self.system_admin
            or not self.grants
            or any(PERSONAS[g.persona].scope is not Scope.SITE for g in self.grants)
        ):
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


def project_access(
    grants: Iterable[Grant], project_id: uuid.UUID, *, system_admin: bool = False
) -> ProjectAccess:
    """The user's access to ``project_id``; ``OutOfScopeError`` (404) for a non-member who is
    not a ``system_admin``.

    ``system_admin`` must be the flag the session was bound with (``resolve_request_scope``,
    never the request). This is pure: it does not check that the project exists, is live or
    belongs to the tenant; the caller loads it under the session's scope (``get_project``),
    which answers 404 for an unknown, deleted or other-tenant project.
    """
    here = frozenset(g for g in grants if g.project_id == project_id)
    if not here and not system_admin:
        raise OutOfScopeError("project not found")
    return ProjectAccess(project_id, here, system_admin)


def resolve_scope(
    grants: Iterable[Grant], base: TenancyContext, project_id: uuid.UUID | None = None
) -> TenancyContext:
    """The session scope for a user: ``base`` plus the bound project and the allowed list.

    The allowed (read-only) projects are those of the user's portfolio-scope personas; the
    bound project, if any, must be one of the user's projects (any project of the tenant for a
    ``system_admin``, ADR 0016) and sets the site filter. Every
    project of the grants becomes a member project (it widens only the rows describing a
    project, T-0014); the projects where the user holds PER-09 become admin projects (they widen
    only the access requests of those projects, T-0050); ``base.is_system_admin`` is kept.
    """
    grants = list(grants)
    allowed = frozenset(
        g.project_id for g in grants if PERSONAS[g.persona].scope is Scope.PORTFOLIO
    )
    sites = (
        project_access(grants, project_id, system_admin=base.is_system_admin).site_node_ids
        if project_id is not None
        else None
    )
    return TenancyContext(
        tenant_id=base.tenant_id,
        project_id=project_id,
        user_id=base.user_id,
        allowed_project_ids=allowed,
        site_node_ids=sites,
        member_project_ids=frozenset(g.project_id for g in grants),
        is_system_admin=base.is_system_admin,
        admin_project_ids=frozenset(
            g.project_id for g in grants if g.persona is Persona.PROJECT_ADMINISTRATOR
        ),
    )


def load_grants(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Grant]:
    """The user's active memberships (tenant-level rows, soft-deleted ones excluded).

    Every source counts (ADR 0008, T-0073): individual (``direct``) and team-derived
    (``team_link``) rows alike, nothing filters on the source. The same grant from two
    sources collapses into one ``Grant``.
    """
    rows = session.execute(
        select(
            ProjectMembership.member_project_id,
            ProjectMembership.persona,
            ProjectMembership.site_node_id,
        ).where(ProjectMembership.tenant_id == tenant_id, ProjectMembership.user_id == user_id)
    ).all()
    return list(dict.fromkeys(Grant(row[0], Persona(row[1]), row[2]) for row in rows))


def persona_holders(
    session: Session, tenant_id: uuid.UUID, project_id: uuid.UUID, persona: Persona
) -> list[uuid.UUID]:
    """The users holding ``persona`` on ``project_id`` (active memberships), by id.

    Works on any session that can read the project's memberships, e.g. one bound to the
    project (the membership read policy, T-0014). Used to route approvals (T-0035).
    """
    rows = session.scalars(
        select(ProjectMembership.user_id)
        .where(
            ProjectMembership.tenant_id == tenant_id,
            ProjectMembership.member_project_id == project_id,
            ProjectMembership.persona == persona.value,
        )
        .distinct()
    ).all()
    return sorted(rows, key=str)


# --- tenant-level admin grants (T-0014, diff §4.1) ---------------------------------------

SYSTEM_ADMIN: Final = "system_admin"
CREATE_PROJECTS: Final = "create_projects"
ADMIN_GRANTS: Final = (SYSTEM_ADMIN, CREATE_PROJECTS)

AdminGrantLookup = Callable[[Session, uuid.UUID, uuid.UUID], frozenset[str]]
_admin_grant_lookups: list[AdminGrantLookup] = []


def register_admin_grant_lookup(lookup: AdminGrantLookup) -> None:
    """Install the admin module's lookup of a user's own grants (core never imports a
    module). Idempotent; without one nobody is a system admin (fail closed)."""
    if lookup not in _admin_grant_lookups:
        _admin_grant_lookups.append(lookup)


def caller_admin_grants(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> frozenset[str]:
    """The user's own tenant-level admin grants (``system_admin``, ``create_projects``).

    Neither grant is a persona or a membership. ``system_admin`` gives the Admin tier on every
    project of the tenant (``project_access(..., system_admin=True)``, ADR 0016); no ``A`` or
    ``S`` comes from either (TD-151): those come only from the personas in ``PERSONAS``.
    """
    found: frozenset[str] = frozenset()
    for lookup in _admin_grant_lookups:
        found |= lookup(session, tenant_id, user_id)
    return found & frozenset(ADMIN_GRANTS)


# Personas with hold or commercial authority (``A``): PER-02, PER-06, PER-07, PER-11. Derived
# from the matrix, so a persona without ``A`` (e.g. PER-16) never warns (TD-256).
HOLD_AUTHORITY: Final = frozenset(
    p for p, spec in PERSONAS.items() if Capability.APPROVE in spec.capabilities
)


def sod_warning(personas: Iterable[Persona], *, system_admin: bool = False) -> bool:
    """Whether a user's *full* persona set on one project combines PER-09, or the user's
    ``system_admin`` grant (ADR 0016, diff §4.1), with a hold-authority persona (TD-151, edge
    spec §5 Admins): allowed, but the assignment must be confirmed and is audited with
    ``sod_warning: true``. Callers pass every grant of the user on the project, whatever its
    source (individual or team-derived, T-0073), and whether the user holds ``system_admin``
    (``system_admin_holders``)."""
    held = frozenset(personas)
    admin = system_admin or Persona.PROJECT_ADMINISTRATOR in held
    return admin and bool(held & HOLD_AUTHORITY)


def system_admin_holders(
    session: Session, tenant_id: uuid.UUID, user_ids: Iterable[uuid.UUID]
) -> frozenset[uuid.UUID]:
    """The users among ``user_ids`` who hold ``system_admin`` (for the SoD warning, ADR 0016).

    A ``system_admin`` session reads every admin grant of its tenant, any session its own; for
    anyone else (a PER-09 assigning a persona) each user's own grants are read in a short
    read-only session bound to that user, as ``resolve_request_scope`` does (the admin-grant
    policy shows a session its own rows), on the side pool so it never waits on the request
    pool (T-0210). Only the yes/no leaves this function.
    """
    context = session_tenancy(session)
    found: set[uuid.UUID] = set()
    for user_id in set(user_ids):
        if context is not None and (context.is_system_admin or context.user_id == user_id):
            grants = caller_admin_grants(session, tenant_id, user_id)
        else:
            with _side_sessionmaker(session)() as side:
                bind_tenancy(side, TenancyContext(tenant_id=tenant_id, user_id=user_id))
                grants = caller_admin_grants(side, tenant_id, user_id)
        if SYSTEM_ADMIN in grants:
            found.add(user_id)
    return frozenset(found)


def _side_sessionmaker(session: Session) -> sessionmaker[Session]:
    """The side pool of ``session``'s ``Database`` (``Database.session`` stashes it under
    ``SIDE_WRITE_DB_KEY``; request sessions always carry it, so a request never waits on its
    own pool here, T-0210), else a plain factory on its own engine (a bare session, scripts and
    tests, is never the shared request pool)."""
    database = session.info.get(SIDE_WRITE_DB_KEY)
    if database is not None:
        maker: sessionmaker[Session] = database.side_write_sessionmaker
        return maker
    return sessionmaker(bind=session.get_bind().engine, autoflush=False, expire_on_commit=False)


# ``request.state`` attribute holding ``(tenant_id, user_id, grants)`` once the request's
# scope has been resolved, so guards and routers do not query the memberships again (T-0071).
GRANTS_STATE: Final = "kerp_grants"
# The same for the caller's tenant-level admin grants (T-0039).
ADMIN_GRANTS_STATE: Final = "kerp_admin_grants"


def resolve_request_scope(
    database: Database, base: TenancyContext, *, state: object | None = None
) -> TenancyContext:
    """Bind the request's allowed projects from the membership store (never the request).

    Runs in its own short tenant-level unit of work, before the request session is bound.
    The same unit of work loads the caller's own admin grants (``caller_admin_grants``) and
    binds ``system_admin`` as ``is_system_admin`` (T-0014, edge spec §6 amendments): never
    from token claims, request parameters or headers. With ``state`` (the request's
    ``request.state``) the loaded grants are kept there for ``get_caller_grants``.
    """
    if base.user_id is None:
        return base
    # Bound to the caller (``app.current_user_id``): the membership and admin-grant policies
    # let a session read its own rows, so neither lookup depends on what it decides (TD-152).
    own = TenancyContext(tenant_id=base.tenant_id, user_id=base.user_id)
    with database.unit_of_work(own) as session:
        grants = load_grants(session, base.tenant_id, base.user_id)
        admin_grants = caller_admin_grants(session, base.tenant_id, base.user_id)
    if state is not None:
        setattr(state, GRANTS_STATE, (base.tenant_id, base.user_id, tuple(grants)))
        setattr(state, ADMIN_GRANTS_STATE, (base.tenant_id, base.user_id, admin_grants))
    is_admin = SYSTEM_ADMIN in admin_grants
    return resolve_scope(grants, replace(base, is_system_admin=is_admin), base.project_id)


def user_has_projects_beyond(
    database: Database,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    project_ids: frozenset[uuid.UUID],
) -> bool:
    """Whether ``user_id`` holds a live membership in a project outside ``project_ids``.

    Answers only yes/no, so the other projects never leave this function (T-0043: whether a
    PER-09's device revoke wipes work of projects the PER-09 does not administer). Read in its
    own short unit of work bound to that user (the membership policy lets a session read its
    own rows), like ``resolve_request_scope``; read-only.

    The caller is a request that already holds a connection of the request pool, so this read
    runs on the ``Database``'s separate side-write pool: waiting on the request pool for a
    second connection could deadlock it under load (T-0210).
    """
    with database.side_write_sessionmaker() as session:
        bind_tenancy(session, TenancyContext(tenant_id=tenant_id, user_id=user_id))
        grants = load_grants(session, tenant_id, user_id)
        return any(g.project_id not in project_ids for g in grants)


# --- FastAPI guards ----------------------------------------------------------------------


def get_caller_grants(
    request: Request, principal: CurrentPrincipal, session: DbSession
) -> list[Grant]:
    """The caller's memberships: the ones this request's scope was resolved from, else loaded.

    They are only reused when they belong to the authenticated tenant and user; a request
    whose tenancy dependency was overridden (tests) has none and loads them here.
    """
    cached = getattr(request.state, GRANTS_STATE, None)
    if cached is not None and cached[:2] == (principal.tenant_id, principal.user_id):
        return list(cached[2])
    return load_grants(session, principal.tenant_id, principal.user_id)


CallerGrants = Annotated[list[Grant], Depends(get_caller_grants)]


def get_caller_is_system_admin(principal: CurrentPrincipal, session: DbSession) -> bool:
    """Whether the request session was bound with ``is_system_admin`` for this caller: the
    flag ``resolve_request_scope`` loaded from the grants table (never the request)."""
    context = session_tenancy(session)
    return context is not None and context.is_system_admin and context.user_id == principal.user_id


CallerIsSystemAdmin = Annotated[bool, Depends(get_caller_is_system_admin)]


def get_caller_admin_grants(
    request: Request, principal: CurrentPrincipal, session: DbSession
) -> frozenset[str]:
    """The caller's own tenant-level admin grants (``create_projects`` gates project creation,
    T-0039): the ones loaded when the request's scope was resolved, else looked up. Never
    from the request. They confer no ``A`` or ``S`` (``caller_admin_grants``)."""
    cached = getattr(request.state, ADMIN_GRANTS_STATE, None)
    if cached is not None and cached[:2] == (principal.tenant_id, principal.user_id):
        return frozenset(cached[2])
    return caller_admin_grants(session, principal.tenant_id, principal.user_id)


CallerAdminGrants = Annotated[frozenset[str], Depends(get_caller_admin_grants)]
