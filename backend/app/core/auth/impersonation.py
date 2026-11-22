"""System admin impersonation sessions (T-0277, ADR 0015 option (a), diff §4.2).

A ``system_admin`` starts a time-boxed session as a non-admin user of its tenant
(``POST /api/v1/admin/users/{id}/impersonate``, admin module, which checks who may impersonate
whom and calls ``start`` here). The session is one ``impersonation`` token (``tokens.py``):
``sub`` = the target, ``imp`` / ``imp_sid`` = the admin and the admin's login session, its own
``sid`` and ``jti``, no refresh token and no device binding. Its expiry is the registry's
``impersonation_max_minutes`` capped by the admin session's end.

Every request re-checks it (``bearer.py``: the token's ``jti`` / ``sid`` and the admin's
``imp_sid`` against the revocation store; ``access.resolve_request_scope``: the admin still
holds ``system_admin`` and is active, in the same unit of work that loads the target's grants).

An impersonation ends exactly once, whichever path sees it first:

* ``exit``: ``POST /api/v1/auth/impersonation/exit`` (or logout with the impersonation token),
* ``expired``: the first refused use after the expiry, or the batch sweep (``sweep``),
* ``revoked``: the first refused use after the admin's session was revoked (logout, force
  sign-out, device revoke), the admin lost ``system_admin`` or was deactivated; or the sweep.

The end is the one ``UPDATE ... WHERE ended_at IS NULL`` on ``auth_impersonation_session``
that matches; only that caller writes the ``impersonation.ended`` ledger entry and queues the
target's notice e-mail (ADR 0015 §10) through core mail, after the commit, on the interactive
queue. A failed or refused send never affects the session or the ledger.

Ledger: the tenant-level chain ``admin-impersonation:<tenant>`` gets ``impersonation.started``
(admin, target, reason, client IP, user agent, expiry, admin device) and
``impersonation.ended`` (``reason: exit | expired | revoked``). Never the token.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth.models import IMPERSONATION_END_REASONS, ImpersonationSession, User
from app.core.auth.service import load_active_user
from app.core.auth.tokens import IssuedToken, TokenClaims, TokenService
from app.core.db import utcnow
from app.core.mail.templates import MailTemplate, register_template
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy

if TYPE_CHECKING:
    from app.core.auth.revocation import RevocationStore
    from app.core.db import Database

CHAIN: Final = "admin-impersonation:{tenant_id}"
STARTED: Final = "impersonation.started"
ENDED: Final = "impersonation.ended"
EXIT, EXPIRED, REVOKED = IMPERSONATION_END_REASONS
MAX_REASON: Final = 500
MAX_USER_AGENT: Final = 500
# The registry entry (declared by the admin module, T-0230): its key, default and bounds.
MAX_MINUTES_KEY: Final = "impersonation_max_minutes"
MAX_MINUTES_DEFAULT: Final = 30
MAX_MINUTES_BOUNDS: Final = (5, 60)
_TIME_FORMAT: Final = "%Y-%m-%d %H:%M UTC"

# The target's notice (ADR 0015 §10): who, from when to when, and that the actions were
# recorded under the target's name and audited as impersonated. Never the reason, the token or
# a link.
NOTICE_TEMPLATE: Final = register_template(
    MailTemplate("auth.impersonation_ended", params=("admin_name", "started_at", "ended_at"))
)

logger = logging.getLogger("kerp.auth.impersonation")


def chain_key(tenant_id: uuid.UUID) -> str:
    return CHAIN.format(tenant_id=tenant_id)


def _aware(moment: datetime) -> datetime:
    """SQLite hands back naive UTC timestamps; PostgreSQL aware ones."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def admin_session_end(claims: TokenClaims) -> datetime:
    """When the admin's login session ends: the access token's ``sxp``; a token issued before
    ``sxp`` existed only promises its own expiry."""
    return claims.session_expires_at or claims.expires_at


@dataclass(frozen=True, slots=True)
class StartedImpersonation:
    token: IssuedToken
    session_id: uuid.UUID
    started_at: datetime
    expires_at: datetime


def start(
    session: Session,
    tokens: TokenService,
    *,
    admin: TokenClaims,
    admin_device_id: uuid.UUID | None,
    target_id: uuid.UUID,
    reason: str,
    max_minutes: int,
    client_ip: str | None,
    user_agent: str | None,
) -> StartedImpersonation:
    """Record a new impersonation session in ``session`` (the admin's tenant-level unit of
    work) and issue its token. The caller has checked who may impersonate whom."""
    if admin.is_impersonation:
        raise ValueError("no nested impersonation")
    # The admin's row, locked and re-checked until this transaction ends (T-0077): a password
    # change committed since the request was authenticated refuses the start
    # (``AuthenticationError``), and one still to come is stamped after ``started_at``, so the
    # per-request check and the sweep see it.
    load_active_user(session, admin, for_update=True)
    now = tokens.now()
    expires_at = min(now + timedelta(minutes=max_minutes), admin_session_end(admin))
    if expires_at <= now:
        raise ValueError("the admin's session has ended")
    session_id = uuid.uuid4()
    session.add(
        ImpersonationSession(
            id=session_id,
            tenant_id=admin.tenant_id,
            admin_user_id=admin.user_id,
            admin_session_id=admin.session_id,
            target_user_id=target_id,
            started_at=now,
            expires_at=expires_at,
            created_by=admin.user_id,
            updated_by=admin.user_id,
        )
    )
    session.flush()
    payload: dict[str, Any] = {
        "action": STARTED,
        "session_id": str(session_id),
        "admin_user_id": str(admin.user_id),
        "target_user_id": str(target_id),
        "reason": reason,
        "client_ip": client_ip,
        "user_agent": user_agent[:MAX_USER_AGENT] if user_agent else None,
        "started_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "admin_device_id": str(admin_device_id) if admin_device_id is not None else None,
    }
    audit.append(
        session,
        chain_key(admin.tenant_id),
        payload,
        actor_id=admin.user_id,
        source="api",
        device_id=admin_device_id,
    )
    token = tokens.issue_impersonation(
        user_id=target_id,
        tenant_id=admin.tenant_id,
        session_id=session_id,
        impersonator_id=admin.user_id,
        impersonator_session_id=admin.session_id,
        expires_at=expires_at,
    )
    return StartedImpersonation(token, session_id, now, expires_at)


def end(
    session: Session,
    session_id: uuid.UUID,
    reason: str,
    *,
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> bool:
    """End the impersonation ``session_id`` of the session's tenant for ``reason``, once.

    Only the caller whose update matches the still-open row writes the ledger entry and queues
    the target's notice; everyone else gets ``False`` and changes nothing. ``session`` must be
    bound to the tenant at the tenant level (the row and the ledger chain are tenant-level).
    """
    if reason not in IMPERSONATION_END_REASONS:
        raise ValueError(f"reason must be one of {IMPERSONATION_END_REASONS}")
    context = session_tenancy(session)
    if context is None or context.project_id is not None:
        raise RuntimeError("ending an impersonation needs a tenant-level bound session")
    ended_at = now or utcnow()
    table = ImpersonationSession
    result = cast(
        "CursorResult[Any]",
        session.execute(
            update(table)
            .where(
                table.tenant_id == context.tenant_id,
                table.id == session_id,
                table.ended_at.is_(None),
            )
            .values(
                ended_at=ended_at,
                end_reason=reason,
                updated_at=ended_at,
                updated_by=actor_id,
                revision=table.revision + 1,
            )
            .execution_options(synchronize_session=False)
        ),
    )
    if result.rowcount != 1:
        return False
    row = session.scalars(
        select(table).where(table.id == session_id).execution_options(populate_existing=True)
    ).one()
    audit.append(
        session,
        chain_key(context.tenant_id),
        {
            "action": ENDED,
            "session_id": str(session_id),
            "admin_user_id": str(row.admin_user_id),
            "target_user_id": str(row.target_user_id),
            "reason": reason,
            "started_at": _aware(row.started_at).isoformat(),
            "ended_at": ended_at.isoformat(),
        },
        actor_id=actor_id,
        source="api" if actor_id is not None else "system",
    )
    _notify_target(session, row, ended_at)
    return True


def _notify_target(session: Session, row: ImpersonationSession, ended_at: datetime) -> None:
    """Queue the target's notice in its own SAVEPOINT: whatever happens to it, the end and its
    ledger entry stand (ADR 0015 §10)."""
    # Imported here: the mail service needs the DB layer, which imports core auth while it
    # initialises.
    from app.core.mail.service import send_mail

    users = {
        u.id: u
        for u in session.scalars(
            select(User).where(User.id.in_((row.admin_user_id, row.target_user_id)))
        )
    }
    target, admin = users.get(row.target_user_id), users.get(row.admin_user_id)
    if target is None:
        logger.warning("impersonation %s ended: the target account is gone, no notice", row.id)
        return
    admin_name = (admin.display_name or admin.email) if admin is not None else "-"
    context = {
        "admin_name": admin_name,
        "started_at": _aware(row.started_at).astimezone(UTC).strftime(_TIME_FORMAT),
        "ended_at": ended_at.astimezone(UTC).strftime(_TIME_FORMAT),
    }
    try:
        with session.begin_nested():
            send_mail(session, NOTICE_TEMPLATE.key, target.email, context)
    except (ValueError, SQLAlchemyError):
        logger.warning("impersonation %s ended: the target's notice was not queued", row.id)


def end_refused(database: Database, claims: TokenClaims, reason: str) -> None:
    """Record the end of the session of a refused impersonation token (lazily, at its first
    refused use) in a short unit of work of its own. Best effort: the token is refused
    whatever happens here, and the sweep records what this could not."""
    try:
        with database.unit_of_work(TenancyContext(tenant_id=claims.tenant_id)) as session:
            end(session, claims.session_id, reason)
    except SQLAlchemyError:
        logger.exception("recording the end of impersonation %s failed", claims.session_id)


@dataclass(frozen=True, slots=True)
class AdminStanding:
    """What decides whether an admin's impersonations live on: the account is active and
    holds ``system_admin``, and its password has not changed since the impersonation began."""

    active_system_admin: bool
    password_changed_at: datetime | None

    def allows(self, since: datetime) -> bool:
        """``since``: when the impersonation began (its token's ``iat``, or ``started_at``).
        A password change after it ends it, as it ends the admin's own tokens
        (``token_predates_password_change``: same comparison, exact to the microsecond)."""
        if not self.active_system_admin:
            return False
        return self.password_changed_at is None or not since < self.password_changed_at


def admin_standing(session: Session, tenant_id: uuid.UUID, admin_id: uuid.UUID) -> AdminStanding:
    """The admin's ``AdminStanding``. ``session`` must be able to read the admin's grants:
    bound to the admin, or a ``system_admin`` session."""
    # Imported here: ``access`` imports ``deps``, which imports the DB layer.
    from app.core.auth.access import SYSTEM_ADMIN, caller_admin_grants

    row = session.execute(
        select(User.is_active, User.password_changed_at).where(
            User.tenant_id == tenant_id, User.id == admin_id
        )
    ).one_or_none()
    if row is None:
        return AdminStanding(active_system_admin=False, password_changed_at=None)
    active = bool(row.is_active) and SYSTEM_ADMIN in caller_admin_grants(
        session, tenant_id, admin_id
    )
    changed = row.password_changed_at
    return AdminStanding(active, _aware(changed) if changed is not None else None)


def admin_may_impersonate(
    session: Session, tenant_id: uuid.UUID, admin_id: uuid.UUID, *, since: datetime
) -> bool:
    """Whether an impersonation the admin began at ``since`` may go on: the admin is still an
    active ``system_admin`` whose password has not changed since (ADR 0015 §2-3)."""
    return admin_standing(session, tenant_id, admin_id).allows(since)


@dataclass(frozen=True, slots=True)
class _Open:
    id: uuid.UUID
    admin_user_id: uuid.UUID
    admin_session_id: uuid.UUID
    started_at: datetime
    expires_at: datetime


def sweep(
    database: Database,
    store: RevocationStore,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """End every open impersonation of ``tenant_id`` that is over: ``expired`` past its expiry,
    ``revoked`` when its own session or its admin's session is revoked, the admin no longer
    holds ``system_admin`` or is active, or the admin's password changed after it began
    (that ends the admin's tokens without a session revocation). Returns how many ended per
    reason (batch cron)."""
    moment = now or utcnow()
    reader = TenancyContext(tenant_id=tenant_id, is_system_admin=True)
    table = ImpersonationSession
    with database.unit_of_work(reader) as session:
        rows = [
            _Open(
                r.id,
                r.admin_user_id,
                r.admin_session_id,
                _aware(r.started_at),
                _aware(r.expires_at),
            )
            for r in session.execute(
                select(
                    table.id,
                    table.admin_user_id,
                    table.admin_session_id,
                    table.started_at,
                    table.expires_at,
                )
                .where(table.tenant_id == tenant_id, table.ended_at.is_(None))
                .order_by(table.expires_at)
            )
        ]
        admins = {a: admin_standing(session, tenant_id, a) for a in {r.admin_user_id for r in rows}}
    ended = {EXPIRED: 0, REVOKED: 0}
    for row in rows:
        reason = None
        if row.expires_at <= moment:
            reason = EXPIRED
        elif (
            not admins[row.admin_user_id].allows(row.started_at)
            or store.is_session_revoked(row.id)
            or store.is_session_revoked(row.admin_session_id)
        ):
            reason = REVOKED
        if reason is None:
            continue
        if reason == REVOKED:  # the token dies at once, not only at its next refused use
            store.revoke_session(row.id, row.expires_at - moment)
        with database.unit_of_work(TenancyContext(tenant_id=tenant_id)) as session:
            if end(session, row.id, reason, now=moment):
                ended[reason] += 1
    return ended
