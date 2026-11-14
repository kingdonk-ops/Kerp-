"""Users, project memberships and admin grants (T-0014, diff §4.1). Authorisation is the
router's; this service writes and audits.

Every function needs a tenant-level session (no bound project). Users are read and written
only through ``app.core.auth.service`` (``create_user`` derives ``is_internal``) and
memberships only through ``app.core.auth.memberships``; this module owns ``oe_admin_grant``.

Audit chains (tenant-level scope): ``admin-user:<user>`` (create, update),
``admin-user-lookup:<caller>`` (every e-mail lookup), ``project-members:<project>`` (assign,
remove; ``sod_warning`` flagged), ``admin-grant:<user>`` (grant, revoke).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth import memberships
from app.core.auth import service as users
from app.core.auth.access import (
    ADMIN_GRANTS,
    Grant,
    register_admin_grant_lookup,
    sod_warning,
)
from app.core.auth.models import User, normalize_email
from app.core.tenancy.rls import session_tenancy
from app.modules.admin.models import AdminGrant
from app.modules.admin.schemas import UserCreate, UserUpdate
from app.modules.projects import service as projects

# The security-definer function of migration 0015: the caller's own live grant kinds.
CALLER_GRANTS_FUNCTION: Final = "kerp_caller_admin_grants"


class DuplicateUserError(ValueError):
    """An account with this e-mail exists already (409)."""


class SodConfirmationError(ValueError):
    """PER-09 + hold-authority persona without ``confirm_sod`` (409, TD-151)."""


class NotAMemberError(LookupError):
    """The user holds no membership on the project (404)."""


@dataclass(frozen=True, slots=True)
class Member:
    user: User
    grants: frozenset[Grant]


def _tenant_id(session: Session) -> uuid.UUID:
    context = session_tenancy(session)
    if context is None or context.project_id is not None:
        raise RuntimeError("the admin service needs a tenant-level bound session")
    return context.tenant_id


def _append(session: Session, chain: str, payload: dict[str, Any], actor: uuid.UUID | None) -> None:
    audit.append(session, chain, payload, actor_id=actor, source="api")


def _grant_payload(grants: Iterable[Grant]) -> list[dict[str, str | None]]:
    return [
        {
            "persona": g.persona.value,
            "site_node_id": str(g.site_node_id) if g.site_node_id else None,
        }
        for g in sorted(grants, key=lambda g: (g.persona.value, str(g.site_node_id or "")))
    ]


# --- admin grants ---------------------------------------------------------------------------


def own_admin_grants(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> frozenset[str]:
    """The user's own live grants: what ``resolve_request_scope`` binds as
    ``app.is_system_admin``. On PostgreSQL through the security-definer function, which
    returns only the session user's rows; the admin-grant policy limits it the same way."""
    context = session_tenancy(session)
    bind = session.get_bind()
    if (
        bind.dialect.name == "postgresql"
        and context is not None
        and (context.tenant_id, context.user_id) == (tenant_id, user_id)
    ):
        kinds = session.execute(text(f"SELECT kind FROM {CALLER_GRANTS_FUNCTION}()")).scalars()
    else:
        kinds = session.scalars(
            select(AdminGrant.kind).where(
                AdminGrant.tenant_id == tenant_id, AdminGrant.user_id == user_id
            )
        )
    return frozenset(kinds)


register_admin_grant_lookup(own_admin_grants)


def grant(session: Session, user_id: uuid.UUID, kind: str, *, actor_id: uuid.UUID | None) -> bool:
    """Grant ``kind`` (``system_admin`` sessions only: the RLS policy and the ORM guard refuse
    anyone else). ``False`` when the user already holds it. T-0039 adds the API."""
    if kind not in ADMIN_GRANTS:
        raise ValueError(f"unknown admin grant {kind!r}")
    tenant_id = _tenant_id(session)
    users.get_user(session, user_id)
    if kind in own_admin_grants(session, tenant_id, user_id):
        return False
    session.add(
        AdminGrant(
            tenant_id=tenant_id,
            project_id=None,
            user_id=user_id,
            kind=kind,
            created_by=actor_id,
            updated_by=actor_id,
        )
    )
    session.flush()
    _append(session, f"admin-grant:{user_id}", {"action": "grant", "grant": kind}, actor_id)
    return True


def revoke(session: Session, user_id: uuid.UUID, kind: str, *, actor_id: uuid.UUID | None) -> bool:
    """Revoke ``kind`` (soft delete). ``False`` when the user does not hold it."""
    row = session.scalars(
        select(AdminGrant).where(
            AdminGrant.tenant_id == _tenant_id(session),
            AdminGrant.user_id == user_id,
            AdminGrant.kind == kind,
        )
    ).one_or_none()
    if row is None:
        return False
    row.soft_delete()
    row.updated_by = actor_id
    session.flush()
    _append(session, f"admin-grant:{user_id}", {"action": "revoke", "grant": kind}, actor_id)
    return True


# --- users ----------------------------------------------------------------------------------


def create_user(
    session: Session,
    data: UserCreate,
    *,
    actor_id: uuid.UUID,
    internal_domains: Collection[str] | None = None,
) -> User:
    """A new account without credentials; ``is_internal`` is derived by core auth from
    ``internal_domains`` (default: ``KERP_INTERNAL_EMAIL_DOMAINS``)."""
    if users.find_user_by_email(session, data.email) is not None:
        raise DuplicateUserError("an account with this e-mail exists already")
    user = users.create_user(
        session,
        tenant_id=_tenant_id(session),
        email=data.email,
        password=None,
        display_name=data.display_name,
        internal_domains=internal_domains,
    )
    user.created_by = user.updated_by = actor_id
    _append(session, f"admin-user:{user.id}", {"action": "create"}, actor_id)
    return user


def update_user(
    session: Session, user_id: uuid.UUID, data: UserUpdate, *, actor_id: uuid.UUID
) -> User:
    """Rename, deactivate or reactivate. A deactivated user's tokens stop working at once."""
    user = users.get_user(session, user_id)
    changed = sorted(n for n in data.model_fields_set if getattr(user, n) != getattr(data, n))
    for name in changed:
        setattr(user, name, getattr(data, name))
    if changed:
        user.updated_by = actor_id
        session.flush()
        payload: dict[str, Any] = {"action": "update", "fields": changed}
        if "is_active" in changed:
            payload["is_active"] = user.is_active
        _append(session, f"admin-user:{user.id}", payload, actor_id)
    return user


def lookup_user(session: Session, email: str, *, actor_id: uuid.UUID) -> User | None:
    """An *active* account with exactly this e-mail, for assigning it to a project. Every
    lookup is audited (hit or miss); the e-mail is kept only as a SHA-256 digest."""
    user = users.find_user_by_email(session, email)
    if user is not None and not user.is_active:
        user = None
    digest = hashlib.sha256(normalize_email(email).encode()).hexdigest()
    payload = {
        "action": "lookup",
        "email_sha256": digest,
        "user_id": str(user.id) if user else None,
    }
    _append(session, f"admin-user-lookup:{actor_id}", payload, actor_id)
    return user


# --- memberships ----------------------------------------------------------------------------


def project_members(session: Session, project_id: uuid.UUID) -> list[Member]:
    """The project's members with their grants, by e-mail (a deleted project: 404)."""
    projects.get_project(session, project_id)
    grants = memberships.project_grants(session, project_id)
    accounts = users.users_by_id(session, grants)
    found = [Member(accounts[u], frozenset(g)) for u, g in grants.items() if u in accounts]
    return sorted(found, key=lambda m: m.user.email)


def assign_member(
    session: Session,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    personas: Iterable[Grant],
    *,
    confirm_sod: bool,
    actor_id: uuid.UUID,
) -> tuple[Member, bool]:
    """Set the user's complete persona set on the project; returns it and ``sod_warning``.

    PER-09 with a hold-authority persona needs ``confirm_sod`` (else
    ``SodConfirmationError``) and is audited with ``sod_warning: true``. Assigning PER-09
    adds no hold or signing capability: capabilities come only from each persona (TD-151).
    """
    projects.get_project(session, project_id)
    user = users.get_user(session, user_id)
    wanted = frozenset(personas)
    warn = sod_warning(g.persona for g in wanted)
    if warn and not confirm_sod:
        raise SodConfirmationError(
            "PER-09 together with a hold-authority persona: confirm with confirm_sod"
        )
    added, removed = memberships.set_user_grants(
        session, project_id, user_id, wanted, actor_id=actor_id
    )
    if added or removed:
        payload = {
            "action": "assign",
            "user_id": str(user_id),
            "added": _grant_payload(added),
            "removed": _grant_payload(removed),
            "grants": _grant_payload(wanted),
            "sod_warning": warn,
        }
        _append(session, f"project-members:{project_id}", payload, actor_id)
    return Member(user, wanted), warn


def remove_member(
    session: Session, project_id: uuid.UUID, user_id: uuid.UUID, *, actor_id: uuid.UUID
) -> None:
    """Revoke every persona of the user on the project (``NotAMemberError`` if none)."""
    projects.get_project(session, project_id)
    _added, removed = memberships.set_user_grants(
        session, project_id, user_id, (), actor_id=actor_id
    )
    if not removed:
        raise NotAMemberError("not a member of this project")
    payload = {"action": "remove", "user_id": str(user_id), "removed": _grant_payload(removed)}
    _append(session, f"project-members:{project_id}", payload, actor_id)


__all__ = [
    "DuplicateUserError",
    "Member",
    "NotAMemberError",
    "SodConfirmationError",
    "assign_member",
    "create_user",
    "grant",
    "lookup_user",
    "own_admin_grants",
    "project_members",
    "remove_member",
    "revoke",
    "update_user",
]
