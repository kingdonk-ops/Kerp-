"""Team-derived project grants (T-0073, ADR 0008, diff §4.1 Teams).

Effective project access is the union of a user's individual memberships and the grants
they get from the teams linked to the project. Core never imports the teams module, so the
derived grants are materialised as ``auth_project_membership`` rows with
``source_type = 'team_link'`` and ``source_id`` = the team-project link id.
``access.load_grants`` reads every live row, so ``resolve_scope``, ``project_access``, the
allowed project list and the membership-based RLS count them with no change.

The teams service (T-0013) is the only caller of the writes, and re-syncs on every change:

* ``sync_team_link_grants``: the live derived rows of one link become exactly one row per
  given user, with the link's project, persona and site. Rows that are gone, or carry an old
  project, persona or site, are soft-deleted (before any insert, so the partial unique index
  of live team rows holds); a matching soft-deleted row is restored instead of duplicated;
  unchanged rows are not rewritten. The users are stored exactly as given, never filtered by
  account status: a deactivated account cannot authenticate, so its rows grant nothing, and
  reactivation restores team access with no re-sync.
* ``revoke_team_link_grants``: soft-deletes every derived row of a link.
* ``team_link_grants``: every live derived tuple per link id, orphans included (reconcile,
  T-0075).

All three need a tenant-level ``system_admin`` session (``NotSystemAdminError`` otherwise;
RLS refuses other sessions' writes to derived rows too). Each write that changes rows appends
one audit entry per affected project on chain ``membership:<project_id>`` (link id, added and
removed user ids, actor). Separation of duties is evaluated across sources:
``SodConfirmationRequired`` when a hold-authority persona would reach a user with individual
PER-09 on the project, unless ``confirm_sod=True`` (then audited ``sod_warning: true``).

``check_team_grants_at_startup`` fails closed when the teams module is not loaded but live
derived rows exist.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from typing import TYPE_CHECKING, Final

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth import service as users
from app.core.auth.access import HOLD_AUTHORITY, PERSONAS, Persona, Scope
from app.core.auth.models import (
    MEMBERSHIP_TABLE,
    SOURCE_DIRECT,
    SOURCE_TEAM_LINK,
    ProjectMembership,
)
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import INCLUDE_DELETED

if TYPE_CHECKING:
    from app.core.db import Database

TEAMS_MODULE: Final = "teams"
# The security-definer census function of migration 0025: live team_link rows, all tenants.
COUNT_FUNCTION: Final = "kerp_live_team_link_grant_count"
# Personas a team link may carry (TD-251, product owner 2026-11-13): PER-09 and PER-15 are
# strictly individual assignments.
INDIVIDUAL_ONLY: Final = frozenset({Persona.PROJECT_ADMINISTRATOR, Persona.CLIENT_REPRESENTATIVE})
LINK_PERSONAS: Final = frozenset(Persona) - INDIVIDUAL_ONLY

DerivedTuple = tuple[uuid.UUID, uuid.UUID, str, uuid.UUID | None]


class NotSystemAdminError(PermissionError):
    """Team-derived grants are written and read only in a ``system_admin`` session (403)."""


class SodConfirmationRequiredError(ValueError):
    """The sync would give a hold-authority persona to users holding individual PER-09 on the
    project (ADR 0008); retry with ``confirm_sod=True``. ``user_ids``: the affected users."""

    def __init__(self, project_id: uuid.UUID, user_ids: Iterable[uuid.UUID]) -> None:
        self.project_id = project_id
        self.user_ids = frozenset(user_ids)
        super().__init__(
            "a hold-authority persona together with individual PER-09: confirm with confirm_sod"
        )


# The name ADR 0008 and T-0073 give the API.
SodConfirmationRequired = SodConfirmationRequiredError


class OrphanedTeamGrantsError(RuntimeError):
    """Live team-derived grants exist while the teams module is not loaded (fail closed)."""


def _admin_context(session: Session) -> TenancyContext:
    context = session_tenancy(session)
    if context is None or context.project_id is not None:
        raise RuntimeError("team-derived grants need a tenant-level bound session")
    if not context.is_system_admin:
        raise NotSystemAdminError("team-derived grants are managed by system admins only")
    return context


def _link_persona(persona: Persona | str, site_node_id: uuid.UUID | None) -> Persona:
    """The persona a link may carry, with the membership CHECKs (unknown persona, PER-07 site)."""
    chosen = Persona(persona)  # ValueError for an unknown code
    if chosen in INDIVIDUAL_ONLY:
        raise ValueError(f"{chosen.value} is an individual assignment only, not a team link role")
    site_scoped = PERSONAS[chosen].scope is Scope.SITE
    if site_scoped and site_node_id is None:
        raise ValueError(f"{chosen.value} needs a site_node_id")
    if not site_scoped and site_node_id is not None:
        raise ValueError(f"{chosen.value} is not site-scoped: omit site_node_id")
    return chosen


def _key(row: ProjectMembership) -> tuple[uuid.UUID, str, uuid.UUID | None]:
    return row.member_project_id, row.persona, row.site_node_id


def _link_rows(
    session: Session, tenant_id: uuid.UUID, link_id: uuid.UUID
) -> list[ProjectMembership]:
    query = select(ProjectMembership).where(
        ProjectMembership.tenant_id == tenant_id,
        ProjectMembership.source_type == SOURCE_TEAM_LINK,
        ProjectMembership.source_id == link_id,
    )
    return list(session.scalars(query.execution_options(**{INCLUDE_DELETED: True})))


def _individual_admins(
    session: Session, tenant_id: uuid.UUID, project_id: uuid.UUID
) -> set[uuid.UUID]:
    """Users holding individual (direct) PER-09 on the project."""
    query = select(ProjectMembership.user_id).where(
        ProjectMembership.tenant_id == tenant_id,
        ProjectMembership.member_project_id == project_id,
        ProjectMembership.persona == Persona.PROJECT_ADMINISTRATOR.value,
        ProjectMembership.source_type == SOURCE_DIRECT,
    )
    return set(session.scalars(query))


def _ids(values: Iterable[uuid.UUID]) -> list[str]:
    return sorted(str(v) for v in values)


def _audit(
    session: Session,
    action: str,
    link_id: uuid.UUID,
    added: Mapping[uuid.UUID, set[uuid.UUID]],
    removed: Mapping[uuid.UUID, set[uuid.UUID]],
    actor_id: uuid.UUID | None,
    extra: Mapping[str, object] | None = None,
) -> None:
    """One entry per project whose rows changed, on ``membership:<project_id>``."""
    for project_id in sorted({*added, *removed}, key=str):
        payload = {
            "action": action,
            "link_id": str(link_id),
            "project_id": str(project_id),
            "added": _ids(added.get(project_id, ())),
            "removed": _ids(removed.get(project_id, ())),
            **(extra or {}),
        }
        audit.append(
            session, f"membership:{project_id}", payload, actor_id=actor_id, source="system"
        )


def sync_team_link_grants(
    session: Session,
    *,
    link_id: uuid.UUID,
    project_id: uuid.UUID,
    persona: Persona | str,
    site_node_id: uuid.UUID | None,
    user_ids: Collection[uuid.UUID],
    confirm_sod: bool = False,
    reason: str | None = None,
) -> None:
    """Make the live derived rows of ``link_id`` exactly one per user in ``user_ids`` with
    this project, persona and site (see the module docstring). ``ValueError`` for a persona a
    link may not carry or an unknown user. ``reason`` (e.g. ``"reconcile"``, T-0075) is
    recorded in the audit entry."""
    context = _admin_context(session)
    tenant_id, actor_id = context.tenant_id, context.user_id
    chosen = _link_persona(persona, site_node_id)
    wanted = frozenset(user_ids)
    unknown = wanted - set(users.users_by_id(session, wanted))
    if unknown:
        raise ValueError(f"unknown users: {', '.join(_ids(unknown))}")
    key = (project_id, chosen.value, site_node_id)
    rows = _link_rows(session, tenant_id, link_id)
    live = [r for r in rows if r.deleted_at is None]
    kept = {r.user_id for r in live if _key(r) == key and r.user_id in wanted}
    gone = [r for r in live if _key(r) != key or r.user_id not in wanted]
    new_users = wanted - kept
    hold = chosen in HOLD_AUTHORITY
    sod_users = new_users & _individual_admins(session, tenant_id, project_id) if hold else set()
    if sod_users and not confirm_sod:
        raise SodConfirmationRequired(project_id, sod_users)
    removed: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for row in gone:
        row.soft_delete()
        row.updated_by = actor_id
        removed[row.member_project_id].add(row.user_id)
    session.flush()  # the old live row goes before its re-roled successor arrives
    restorable = {r.user_id: r for r in rows if r.deleted_at is not None and _key(r) == key}
    for user_id in sorted(new_users, key=str):
        revived = restorable.get(user_id)
        if revived is not None:
            revived.deleted_at = None
            revived.updated_by = actor_id
            continue
        session.add(
            ProjectMembership(
                tenant_id=tenant_id,
                project_id=None,
                user_id=user_id,
                member_project_id=project_id,
                persona=chosen.value,
                site_node_id=site_node_id,
                source_type=SOURCE_TEAM_LINK,
                source_id=link_id,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
    session.flush()
    added = {project_id: set(new_users)} if new_users else {}
    if not added and not removed:
        return
    extra: dict[str, object] = {
        "persona": chosen.value,
        "site_node_id": str(site_node_id) if site_node_id else None,
        "sod_warning": bool(sod_users),
    }
    if sod_users:
        extra["sod_user_ids"] = _ids(sod_users)
    if reason is not None:
        extra["reason"] = reason
    _audit(session, "team_link_sync", link_id, added, removed, actor_id, extra)


def revoke_team_link_grants(
    session: Session, *, link_id: uuid.UUID, reason: str | None = None
) -> None:
    """Soft-delete every live derived row of ``link_id`` (a no-op writes nothing). ``reason``
    is recorded in the audit entry."""
    context = _admin_context(session)
    removed: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for row in _link_rows(session, context.tenant_id, link_id):
        if row.deleted_at is None:
            row.soft_delete()
            row.updated_by = context.user_id
            removed[row.member_project_id].add(row.user_id)
    session.flush()
    extra = {"reason": reason} if reason is not None else None
    _audit(session, "team_link_revoke", link_id, {}, removed, context.user_id, extra)


def team_link_grants(session: Session) -> Mapping[uuid.UUID, frozenset[DerivedTuple]]:
    """Every link id with live derived rows in the tenant, whether or not the teams tables
    still know it, with its ``(user_id, project_id, persona, site_node_id)`` tuples."""
    context = _admin_context(session)
    rows = session.execute(
        select(
            ProjectMembership.source_id,
            ProjectMembership.user_id,
            ProjectMembership.member_project_id,
            ProjectMembership.persona,
            ProjectMembership.site_node_id,
        ).where(
            ProjectMembership.tenant_id == context.tenant_id,
            ProjectMembership.source_type == SOURCE_TEAM_LINK,
        )
    ).all()
    found: dict[uuid.UUID, set[DerivedTuple]] = defaultdict(set)
    for link_id, user_id, project_id, persona, site_node_id in rows:
        if link_id is not None:  # the source_id CHECK: every team_link row has one
            found[link_id].add((user_id, project_id, persona, site_node_id))
    return {link_id: frozenset(tuples) for link_id, tuples in found.items()}


# --- startup check ----------------------------------------------------------------------------


def live_team_link_grant_count(database: Database) -> int:
    """Live ``team_link`` rows across all tenants, read before any tenant is bound.

    On PostgreSQL through the census function of migration ``0025`` (a count, never row
    data); on SQLite a plain count. A schema without the source columns holds none.
    """
    with database.engine.connect() as connection:
        schema = inspect(connection)
        if not schema.has_table(MEMBERSHIP_TABLE):
            return 0
        if "source_type" not in {c["name"] for c in schema.get_columns(MEMBERSHIP_TABLE)}:
            return 0
        if connection.dialect.name == "postgresql":
            sql = f"SELECT {COUNT_FUNCTION}()"
        else:
            sql = (
                f"SELECT count(*) FROM {MEMBERSHIP_TABLE}"
                f" WHERE source_type = '{SOURCE_TEAM_LINK}' AND deleted_at IS NULL"
            )
        return int(connection.execute(text(sql)).scalar_one())


def check_team_grants_at_startup(
    database: Database | None, loaded_modules: Collection[str]
) -> None:
    """Refuse to start when the teams module is not loaded and live team-derived grants exist:
    nothing would maintain them (ADR 0008, fail closed). Core reads the loader's module list
    and its own table; it imports no module."""
    if database is None or TEAMS_MODULE in loaded_modules:
        return
    count = live_team_link_grant_count(database)
    if count:
        raise OrphanedTeamGrantsError(
            f"{count} live team-derived project grant(s) exist but the '{TEAMS_MODULE}' module"
            " is not loaded. Re-enable the teams module and unlink every team (which revokes"
            " the grants), or run app.core.auth.team_grants.revoke_team_link_grants for every"
            " link id from team_link_grants() in a system_admin session; see"
            " app/core/auth/README.md, 'Team-derived grants'."
        )


__all__ = [
    "LINK_PERSONAS",
    "TEAMS_MODULE",
    "NotSystemAdminError",
    "OrphanedTeamGrantsError",
    "SodConfirmationRequired",
    "SodConfirmationRequiredError",
    "check_team_grants_at_startup",
    "live_team_link_grant_count",
    "revoke_team_link_grants",
    "sync_team_link_grants",
    "team_link_grants",
]
