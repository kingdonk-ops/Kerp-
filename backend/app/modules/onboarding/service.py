"""First-run provisioning of a deployment (T-0023; diff §4.1, §4.4).

A deployment serves exactly one hidden tenant. ``first_run`` sets it up:

1. **The tenant.** Its id is the request's, else ``KERP_DEFAULT_TENANT_ID``, else a new one
   (only while the directory is empty). The directory is counted first
   (``provision.tenant_directory_count``, the census function of migration ``0029``): if any
   row other than this tenant's exists, the run is refused (``AnotherTenantError``) and
   nothing is written. The row itself is written by core provisioning
   (``provision.ensure_tenant``, T-0079).
2. **Deployment settings.** ``settings.ensure_record`` stores the shipped defaults (diff §4.4)
   as the tenant's record unless it has one.
3. **The first System admin.** An account for the given e-mail, without credentials (it signs
   in with Entra ID SSO, which links it by e-mail, or sets a password through a one-time link;
   never a password here), created through ``admin.create_user`` (``is_internal`` derived from
   ``KERP_INTERNAL_EMAIL_DOMAINS``), and the ``system_admin`` grant. Refused when another
   active account already holds ``system_admin`` (``SystemAdminExistsError``: further admins
   are appointed in the app) or the e-mail's account is deactivated.
4. **No project**, except with ``demo`` (refused in production): the admin also gets
   ``create_projects`` and one synthetic demo project with the admin as PER-09.

Re-running with the same input changes nothing. Steps 2-4 run in one tenant-level unit of
work bound as a System admin (``TenancyContext(tenant, is_system_admin=True)``, the binding
provisioning scripts use, ``app/core/tenancy/README.md``). On PostgreSQL the whole run holds a
session advisory lock, so two first-runs at once cannot both find an empty directory. What
changed is appended to the tenant-level audit chain ``onboarding:<tenant>``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth import service as users
from app.core.auth.access import CREATE_PROJECTS, SYSTEM_ADMIN
from app.core.auth.models import User
from app.core.config import Settings
from app.core.db import Database
from app.core.tenancy import provision
from app.core.tenancy.context import TenancyContext
from app.modules.admin import service as admin
from app.modules.admin.schemas import UserCreate
from app.modules.onboarding.schemas import (
    DEMO_PROJECT_CODE,
    DEMO_PROJECT_NAME,
    FirstRunRequest,
    FirstRunResult,
)
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.settings import service as deployment_settings

__all__ = [
    "CHAIN_PREFIX",
    "LOCK_KEY",
    "AnotherTenantError",
    "DemoNotAllowedError",
    "FirstRunError",
    "InactiveAccountError",
    "SystemAdminExistsError",
    "TenantMismatchError",
    "TenantNotConfiguredError",
    "first_run",
]

CHAIN_PREFIX: Final = "onboarding:"
# ``pg_advisory_lock`` key of the first-run command (any constant; "KERP" + 0x0023).
LOCK_KEY: Final = 0x4B4552500023


class FirstRunError(RuntimeError):
    """The run was refused; nothing was written."""


class AnotherTenantError(FirstRunError):
    """The deployment already has a different tenant (one tenant per deployment)."""


class TenantNotConfiguredError(FirstRunError):
    """A tenant exists but none was named: set ``KERP_DEFAULT_TENANT_ID`` to it."""


class TenantMismatchError(FirstRunError):
    """The requested tenant id differs from ``KERP_DEFAULT_TENANT_ID``."""


class SystemAdminExistsError(FirstRunError):
    """Another active account already holds ``system_admin``."""


class InactiveAccountError(FirstRunError):
    """The e-mail belongs to a deactivated account."""


class DemoNotAllowedError(FirstRunError):
    """``demo`` in a production deployment."""


@contextmanager
def _exclusive(database: Database) -> Iterator[None]:
    """Hold the first-run advisory lock (PostgreSQL) for the duration of the run."""
    if database.engine.dialect.name != "postgresql":
        yield
        return
    with database.engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": LOCK_KEY})
        connection.commit()  # the lock is session-level; no transaction stays open
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
            connection.commit()


def _tenant_id(request: FirstRunRequest, settings: Settings, directory_rows: int) -> uuid.UUID:
    configured = settings.default_tenant_id
    if request.tenant_id is not None and configured is not None and request.tenant_id != configured:
        raise TenantMismatchError("the tenant id given differs from KERP_DEFAULT_TENANT_ID")
    chosen = request.tenant_id or configured
    if chosen is not None:
        return chosen
    if directory_rows:
        raise TenantNotConfiguredError(
            "this deployment already has a tenant: set KERP_DEFAULT_TENANT_ID to its id"
        )
    return uuid.uuid4()


def first_run(
    database: Database, request: FirstRunRequest, *, settings: Settings
) -> FirstRunResult:
    """Provision the deployment's tenant, settings and first System admin (idempotent)."""
    if request.demo and settings.environment == "production":
        raise DemoNotAllowedError("--demo is for development deployments only")
    with _exclusive(database):
        rows = provision.tenant_directory_count(database)
        tenant_id = _tenant_id(request, settings, rows)
        own = 1 if rows and provision.tenant_exists(database, tenant_id) else 0
        if rows - own:
            raise AnotherTenantError("this deployment already has a tenant (one per deployment)")
        tenant_created = provision.ensure_tenant(database, tenant_id, request.tenant_display_name)
        context = TenancyContext(tenant_id=tenant_id, is_system_admin=True)
        with database.unit_of_work(context) as session:
            result = _provision_tenant(session, request, settings, tenant_id, tenant_created)
    return result


def _provision_tenant(
    session: Session,
    request: FirstRunRequest,
    settings: Settings,
    tenant_id: uuid.UUID,
    tenant_created: bool,
) -> FirstRunResult:
    settings_created = deployment_settings.ensure_record(session, actor_id=None)
    user, user_created = _admin_account(session, request, settings)
    granted = admin.grant(session, user.id, SYSTEM_ADMIN, actor_id=None)
    demo_id: uuid.UUID | None = None
    demo_created = False
    if request.demo:
        admin.grant(session, user.id, CREATE_PROJECTS, actor_id=None)
        demo_id, demo_created = _demo_project(session, user.id)
    result = FirstRunResult(
        tenant_id=tenant_id,
        tenant_created=tenant_created,
        settings_created=settings_created,
        admin_user_id=user.id,
        admin_created=user_created,
        admin_granted=granted,
        admin_is_internal=user.is_internal,
        demo_project_id=demo_id,
        demo_project_created=demo_created,
    )
    if result.changed:
        payload = {
            "action": "first_run",
            "tenant_created": tenant_created,
            "settings_created": settings_created,
            "admin_user_id": str(user.id),
            "admin_created": user_created,
            "admin_granted": granted,
            "demo_project_id": str(demo_id) if demo_id else None,
        }
        audit.append(session, f"{CHAIN_PREFIX}{tenant_id}", payload, source="system")
    return result


def _admin_account(
    session: Session, request: FirstRunRequest, settings: Settings
) -> tuple[User, bool]:
    """The account for ``admin_email`` (created without credentials if missing)."""
    existing = users.find_user_by_email(session, request.admin_email)
    if existing is not None and not existing.is_active:
        raise InactiveAccountError("the account with this e-mail is deactivated")
    others = {
        account.id
        for account, kind in admin.list_grants(session)
        if kind == SYSTEM_ADMIN and account.is_active
    } - ({existing.id} if existing is not None else set())
    if others:
        raise SystemAdminExistsError(
            "this deployment already has a System admin: appoint further ones in the app"
        )
    if existing is not None:
        return existing, False
    data = UserCreate(email=request.admin_email, display_name=request.admin_display_name)
    created = admin.create_user(
        session, data, actor_id=None, internal_domains=settings.internal_email_domains
    )
    return created, True


def _demo_project(session: Session, admin_id: uuid.UUID) -> tuple[uuid.UUID, bool]:
    """The synthetic demo project (created with the admin as PER-09 if missing)."""
    for project in projects.list_projects(session, projects.live_project_ids(session)):
        if project.code == DEMO_PROJECT_CODE:
            return project.id, False
    data = ProjectCreate(code=DEMO_PROJECT_CODE, name=DEMO_PROJECT_NAME)
    project = projects.create_project(session, data, actor_id=None, creator_id=admin_id)
    return project.id, True
