"""Tenant/project/user context (ported from Inspection-ERP ``core/tenancy/context.py``).

The tenant comes only from the verified credential (CLAUDE.md §6): ``get_tenancy_context``
reads it from the access token (T-0008) and adds the multi-project read scope bound from
the user's memberships (T-0010, TD-053). Without a valid token it returns ``None`` and the
request session is unbound; on PostgreSQL an unbound session sees no RLS-protected rows
(fail closed).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from app.core.auth.bearer import BearerResult, get_bearer


@dataclass(frozen=True, slots=True)
class TenancyContext:
    """Who the work runs for. ``project_id=None`` means tenant-level rows only.

    * ``project_id``: the single project the session *writes* (``app.current_project_id``).
    * ``allowed_project_ids``: further projects the session may *read*
      (``app.allowed_project_ids``, edge spec §6 amendments, TD-053). Bound only from the
      user's memberships, never from request parameters. There is no implicit
      "every project of the tenant" scope.
    * ``site_node_ids``: Asset-Site scoping (diff §4, PER-07). ``None`` means unrestricted;
      a set limits every ``SiteScopedMixin`` row to those sites (ORM layer).
    * ``member_project_ids``: every project the user holds a membership in, whatever the
      persona (``app.member_project_ids``, T-0014). It widens only the tenant-level rows that
      *describe* a project (the project row, its memberships; ``MembershipScopedMixin``),
      never project-scoped records.
    * ``is_system_admin``: the caller holds the ``system_admin`` grant (``app.is_system_admin``,
      T-0014, diff §4.1). Loaded server-side at authentication, never from the request; a
      script or provisioning job may set it explicitly. It reads every project row and
      membership and writes admin grants, but reads no project-scoped record.
    """

    tenant_id: uuid.UUID
    project_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    allowed_project_ids: frozenset[uuid.UUID] = frozenset()
    site_node_ids: frozenset[uuid.UUID] | None = None
    member_project_ids: frozenset[uuid.UUID] = frozenset()
    is_system_admin: bool = False

    @property
    def readable_project_ids(self) -> frozenset[uuid.UUID]:
        """Every project whose rows the session reads: the bound one plus the allowed list."""
        bound = frozenset({self.project_id}) if self.project_id is not None else frozenset()
        return bound | self.allowed_project_ids

    @property
    def described_project_ids(self) -> frozenset[uuid.UUID]:
        """Projects whose descriptive tenant-level rows the session reads (T-0014, TD-053):
        the readable ones plus every project of the user's memberships."""
        return self.readable_project_ids | self.member_project_ids


current_tenant_id: ContextVar[uuid.UUID | None] = ContextVar("current_tenant_id", default=None)
current_project_id: ContextVar[uuid.UUID | None] = ContextVar("current_project_id", default=None)
current_user_id: ContextVar[uuid.UUID | None] = ContextVar("current_user_id", default=None)


@contextmanager
def use_tenancy(context: TenancyContext) -> Iterator[TenancyContext]:
    """Set the context variables for the duration of a block (workers, scripts)."""
    tenant_token = current_tenant_id.set(context.tenant_id)
    project_token = current_project_id.set(context.project_id)
    user_token = current_user_id.set(context.user_id)
    try:
        yield context
    finally:
        current_user_id.reset(user_token)
        current_project_id.reset(project_token)
        current_tenant_id.reset(tenant_token)


def get_tenancy_context(
    request: Request,
    bearer: Annotated[BearerResult, Depends(get_bearer)],
) -> TenancyContext | None:
    """FastAPI dependency: the request's tenancy, from the verified access token only.

    The tenant and user are the token's ``tid`` and ``sub``. The allowed (read-only)
    projects come from the user's memberships in the membership store (T-0010), and the
    ``system_admin`` flag from the admin grants table (T-0014); nothing in the request can
    widen them. The write project is not bound yet (TD-122). Tests
    override this through ``app.dependency_overrides``.
    """
    claims = bearer.claims
    if claims is None:
        return None
    base = TenancyContext(tenant_id=claims.tenant_id, user_id=claims.user_id)
    database = getattr(request.app.state, "db", None)
    if database is None:
        return base
    # Imported here: app.core.auth.access needs app.core.db, which imports this module.
    from app.core.auth.access import resolve_request_scope

    return resolve_request_scope(database, base, state=request.state)
