"""Tenant/project/user context (ported from Inspection-ERP ``core/tenancy/context.py``).

The tenant comes only from the verified credential (CLAUDE.md §6). Auth (T-0008) does not
exist yet, so ``get_tenancy_context`` is the seam it fills: until then it returns ``None``
and a request session is unbound. On PostgreSQL an unbound session sees no RLS-protected
rows (fail closed).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TenancyContext:
    """Who the work runs for. ``project_id=None`` means tenant-level rows only."""

    tenant_id: uuid.UUID
    project_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


current_tenant_id: ContextVar[uuid.UUID | None] = ContextVar("current_tenant_id", default=None)
current_project_id: ContextVar[uuid.UUID | None] = ContextVar("current_project_id", default=None)
current_user_id: ContextVar[uuid.UUID | None] = ContextVar("current_user_id", default=None)


def require_tenant_id() -> uuid.UUID:
    tenant_id = current_tenant_id.get()
    if tenant_id is None:
        raise RuntimeError("no tenant in context: the caller is missing the auth dependency")
    return tenant_id


def require_project_id() -> uuid.UUID:
    project_id = current_project_id.get()
    if project_id is None:
        raise RuntimeError("no project in context: the caller is not bound to a project")
    return project_id


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


def get_tenancy_context() -> TenancyContext | None:
    """FastAPI dependency seam for the request's tenancy context.

    T-0008 replaces this with the verified-JWT tenant and the authorised project. Until
    then no request is bound. Tests override it through ``app.dependency_overrides``.
    """
    return None
