"""Self-service password reset by e-mail (T-0077, diff §4.2 *Self-service password reset*).

**Request** (``POST /auth/password-reset/request``). Always the same neutral ``202``. The
request itself only counts the attempt (per client address, then per tenant + e-mail) and
schedules one ``PasswordResetJob``, identical for every address: unknown, deactivated and
active accounts do the same work before the response (no timing oracle, as for magic links,
T-0057). Everything that differs runs in ``PasswordResetService.issue`` **after the response
has been sent**, in its own unit of work: the account lookup, the token, its store write,
the ``auth.password_reset`` e-mail (a send-log row; the SAQ interactive queue delivers it
after that unit of work commits, T-0058) and the audit entry.

**Token.** 256 random bits (``secrets.token_urlsafe(32)``), e-mailed only inside the link
fragment ``/reset-password#<token>``. The store holds its SHA-256 only, with the link
lifetime (``KERP_PASSWORD_RESET_TTL_MINUTES``) as TTL:

* ``pwreset:token:<sha256>``: ``{uid, tid}`` of the account. Taken atomically (``GETDEL``),
  so of any number of concurrent confirmations exactly one wins.
* ``pwreset:user:<tid>:<uid>``: the digest of the account's **one** live token. A new
  request overwrites it (and deletes the earlier token), a confirmation deletes it; a token
  whose digest is not the one named here is dead, whatever order concurrent requests ran in.

**Confirm** (``POST /auth/password-reset/confirm``). Checks the token without spending it,
checks the new password against the policy (a refused password keeps the link usable), then
spends the token and sets the password. Setting it stamps ``password_changed_at``, which
revokes every access and refresh token issued before (all devices, at their next server
contact; device enrolment and PIN stay, and a device that is offline keeps unlocking locally
within its offline grace) and every outstanding magic link. Nobody is signed in. A
``auth.password_changed`` notice goes to the account's address. An unknown, used, expired or
superseded token, and a token of a deactivated account, get one neutral ``410``.

Requests, confirmations and refusals are audited on the tenant's ``auth:password-reset``
chain with the user id and a reason: never the token, the password or the e-mail address.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth.lockout import (
    MAGIC_LINK_REQUEST_POLICY,
    PASSWORD_RESET_CLIENT_POLICY,
    PASSWORD_RESET_CONFIRM_CLIENT_POLICY,
    PASSWORD_RESET_TOKEN_POLICY,
    ProgressiveLockout,
)
from app.core.auth.models import User, normalize_email
from app.core.auth.password_policy import (
    BreachCheck,
    PasswordPolicyError,
    policy_from_settings,
)
from app.core.auth.runtime import AuthRuntime
from app.core.auth.service import UNKNOWN_CLIENT, apply_checked_password
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_tenancy

RESET_TOKEN_BYTES: Final = 32  # 256 bits
PASSWORD_RESET_AUDIT_CHAIN: Final = "auth:password-reset"
PASSWORD_RESET_TEMPLATE: Final = "auth.password_reset"
PASSWORD_CHANGED_TEMPLATE: Final = "auth.password_changed"

# Audit reasons of a refusal (a refused password is audited with its policy code).
UNKNOWN_ACCOUNT: Final = "unknown_account"
ACCOUNT_INACTIVE: Final = "account_inactive"
INVALID_TOKEN: Final = "invalid_token"
ATTEMPTS_EXHAUSTED: Final = "attempts_exhausted"

_logger = logging.getLogger("kerp.auth.password_reset")


def new_reset_token() -> str:
    return secrets.token_urlsafe(RESET_TOKEN_BYTES)


def reset_digest(token: str) -> str:
    """Only the SHA-256 of a reset token is stored: a store dump resets nobody's password."""
    return hashlib.sha256(token.encode()).hexdigest()


def _token_key(digest: str) -> str:
    return f"pwreset:token:{digest}"


def _user_key(tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return f"pwreset:user:{tenant_id}:{user_id}"


@dataclass(frozen=True, slots=True)
class PasswordResetJob:
    """What a reset request leaves for after the response: the same on every path."""

    tenant_id: uuid.UUID
    email: str  # normalised

    def __repr__(self) -> str:  # never the address
        return f"PasswordResetJob(tenant_id={self.tenant_id})"


@dataclass(frozen=True, slots=True)
class ConfirmResult:
    """``changed``: the password is set. ``invalid``: the neutral dead-link answer.
    ``refused``: the policy refused the password (``error``); the link is still usable."""

    status: Literal["changed", "invalid", "refused"]
    error: PasswordPolicyError | None = None


_INVALID: Final = ConfirmResult("invalid")


@dataclass(frozen=True, slots=True)
class _Pending:
    user_id: uuid.UUID
    tenant_id: uuid.UUID


class PasswordResetService:
    def __init__(self, runtime: AuthRuntime, *, breach_check: BreachCheck | None = None) -> None:
        self.runtime = runtime
        self.breach_check = breach_check
        store = runtime.store
        # The same per-mailbox cap as magic links (tenant + e-mail, TD-224), own counter.
        self.mailbox_lockout = ProgressiveLockout(store, MAGIC_LINK_REQUEST_POLICY, "pwreset")
        self.client_lockout = ProgressiveLockout(
            store, PASSWORD_RESET_CLIENT_POLICY, "pwreset-client"
        )
        self.confirm_client_lockout = ProgressiveLockout(
            store, PASSWORD_RESET_CONFIRM_CLIENT_POLICY, "pwreset-confirm-client"
        )
        token_policy = replace(PASSWORD_RESET_TOKEN_POLICY, window=self.ttl)
        self.token_lockout = ProgressiveLockout(store, token_policy, "pwreset-token")

    @property
    def ttl(self) -> timedelta:
        return timedelta(minutes=self.runtime.settings.password_reset_ttl_minutes)

    # --- request ----------------------------------------------------------------------------

    def request(self, *, email: str, client: str | None = None) -> PasswordResetJob | None:
        """The request half: count it (``LockedOutError`` when the client or the mailbox is
        throttled) and return the job to run after the response.

        Nothing here depends on whether the address has an account. ``None`` only without a
        deployment tenant (the same for every address)."""
        tenant = self.runtime.settings.default_tenant_id
        if tenant is None:
            return None
        address = normalize_email(email)
        self.client_lockout.begin(f"{tenant}|{client or UNKNOWN_CLIENT}")
        self.mailbox_lockout.begin(f"{tenant}:{address}")
        return PasswordResetJob(tenant_id=tenant, email=address)

    def issue(self, database: Database, job: PasswordResetJob) -> None:
        """The deferred half (after the response, own unit of work). Never raises: the
        client has had its ``202`` already."""
        try:
            with database.unit_of_work(TenancyContext(tenant_id=job.tenant_id)) as session:
                self._issue(session, job)
        except Exception:
            _logger.exception("password-reset request not processed")

    def _issue(self, session: Session, job: PasswordResetJob) -> None:
        # Columns only: the session holds no objects, so it can be rebound to the user.
        found = session.execute(
            select(User.id, User.is_active).where(
                User.tenant_id == job.tenant_id, User.email == job.email
            )
        ).one_or_none()
        if found is None:
            self._audit_refusal(session, "request", UNKNOWN_ACCOUNT, None)
            return
        user_id: uuid.UUID = found.id
        bind_tenancy(session, TenancyContext(tenant_id=job.tenant_id, user_id=user_id))
        if not found.is_active:
            self._audit_refusal(session, "request", ACCOUNT_INACTIVE, user_id)
            return
        token = new_reset_token()
        digest = reset_digest(token)
        store, user_key = self.runtime.store, _user_key(job.tenant_id, user_id)
        earlier = store.peek_value(user_key)
        payload = json.dumps({"uid": str(user_id), "tid": str(job.tenant_id)}).encode()
        store.put_value(_token_key(digest), payload, self.ttl)
        # From here the new token is the account's only live one; the earlier one is dead
        # even before its own entry is deleted.
        store.put_value(user_key, digest.encode(), self.ttl)
        if earlier is not None and earlier.decode() != digest:
            store.take_value(_token_key(earlier.decode()))
        self._queue_mail(
            session,
            PASSWORD_RESET_TEMPLATE,
            job.email,
            {"token": token, "expires_minutes": int(self.ttl.total_seconds() // 60)},
            user_id,
        )
        audit.append(
            session,
            PASSWORD_RESET_AUDIT_CHAIN,
            {"action": "password_reset_requested", "user_id": str(user_id)},
            source="api",
            actor_id=user_id,
        )
        _logger.info("password reset link issued for user %s", user_id)

    # --- confirm ----------------------------------------------------------------------------

    def confirm(
        self, session: Session, *, token: str, new_password: str, client: str | None = None
    ) -> ConfirmResult:
        """Spend the reset link and set the password. Does not sign anyone in.

        ``LockedOutError`` when the client, or this link after refused passwords, is
        throttled. Every other outcome is a ``ConfirmResult`` (not an exception), so the
        audit entry of a refusal commits with the request's unit of work."""
        settings = self.runtime.settings
        tenant = settings.default_tenant_id
        if tenant is None:
            return _INVALID
        self.confirm_client_lockout.begin(f"{tenant}|{client or UNKNOWN_CLIENT}")
        bind_tenancy(session, TenancyContext(tenant_id=tenant))
        digest = reset_digest(token)
        pending = self._pending(digest)
        if pending is None or pending.tenant_id != tenant:
            return self._invalid(session, INVALID_TOKEN, None)
        user_id = pending.user_id
        attempt = self.token_lockout.begin(digest)
        policy = self.token_lockout.policy
        if policy.exhausted(attempt):
            self._drop(digest, pending)
            return self._invalid(session, ATTEMPTS_EXHAUSTED, user_id)
        bind_tenancy(session, TenancyContext(tenant_id=tenant, user_id=user_id))
        user = session.scalars(
            select(User).where(User.id == user_id, User.tenant_id == tenant)
        ).one_or_none()
        if user is None or not user.is_active:
            self._drop(digest, pending)
            return self._invalid(session, ACCOUNT_INACTIVE, user_id)
        try:
            normalised = policy_from_settings(settings, breach_check=self.breach_check).check(
                new_password, email=user.email
            )
        except PasswordPolicyError as exc:
            if policy.max_attempts is not None and attempt >= policy.max_attempts:
                self._drop(digest, pending)  # the last attempt of this link
            self._audit_refusal(session, "confirm", exc.code, user_id)
            return ConfirmResult("refused", exc)
        # Single use: of concurrent confirmations exactly one takes the token.
        if self.runtime.store.take_value(_token_key(digest)) is None:
            return self._invalid(session, INVALID_TOKEN, user_id)
        first_password = user.password_hash is None
        # Stamped under the row lock (``service.lock_user``): a sign-in or refresh racing
        # this either issued its tokens before the stamp (revoked by it) or sees the change.
        if not apply_checked_password(user, normalised, require_active=True):
            self.runtime.store.take_value(_user_key(tenant, user_id))
            return self._invalid(session, ACCOUNT_INACTIVE, user_id)  # deactivated meanwhile
        session.flush()
        # A request racing this confirmation may just have written a newer pointer: deleting
        # it kills that newly mailed link too. That fails closed (the user asks again).
        self.runtime.store.take_value(_user_key(tenant, user_id))
        self.token_lockout.succeeded(digest)
        audit.append(
            session,
            PASSWORD_RESET_AUDIT_CHAIN,
            {
                "action": "password_reset_confirmed",
                "user_id": str(user_id),
                "first_password": first_password,
            },
            source="api",
            actor_id=user_id,
        )
        self._queue_mail(session, PASSWORD_CHANGED_TEMPLATE, user.email, {}, user_id)
        _logger.info("password reset confirmed for user %s", user_id)
        return ConfirmResult("changed")

    # --- helpers ----------------------------------------------------------------------------

    def _pending(self, digest: str) -> _Pending | None:
        """The account a live token belongs to; ``None`` for an unknown, expired, used or
        superseded one (a superseded token's leftover entry is deleted)."""
        store = self.runtime.store
        payload = store.peek_value(_token_key(digest))
        if payload is None:
            return None
        try:
            data = json.loads(payload)
            pending = _Pending(uuid.UUID(data["uid"]), uuid.UUID(data["tid"]))
        except (ValueError, KeyError, TypeError):
            return None
        current = store.peek_value(_user_key(pending.tenant_id, pending.user_id))
        if current is None or not secrets.compare_digest(current, digest.encode()):
            store.take_value(_token_key(digest))
            return None
        return pending

    def _drop(self, digest: str, pending: _Pending) -> None:
        store = self.runtime.store
        store.take_value(_token_key(digest))
        store.take_value(_user_key(pending.tenant_id, pending.user_id))

    def _invalid(self, session: Session, reason: str, user_id: uuid.UUID | None) -> ConfirmResult:
        self._audit_refusal(session, "confirm", reason, user_id)
        return _INVALID

    def _audit_refusal(
        self, session: Session, stage: str, reason: str, user_id: uuid.UUID | None
    ) -> None:
        payload: dict[str, object] = {
            "action": "password_reset_refused",
            "stage": stage,
            "reason": reason,
        }
        if user_id is not None:
            payload["user_id"] = str(user_id)
        audit.append(session, PASSWORD_RESET_AUDIT_CHAIN, payload, source="api", actor_id=user_id)
        _logger.info(
            "password reset refused at %s: %s (user %s)", stage, reason, user_id or "unknown"
        )

    def _queue_mail(
        self,
        session: Session,
        template: str,
        to: str,
        context: dict[str, object],
        user_id: uuid.UUID,
    ) -> None:
        """Queue an e-mail in ``session``'s unit of work (sent by the worker after its
        commit, never from here). An address the mail service cannot send to is skipped."""
        # Imported here: the mail service needs the DB layer, which imports core auth while
        # it initialises.
        from app.core.mail.addresses import InvalidAddressError
        from app.core.mail.service import send_mail

        try:
            send_mail(session, template, to, context)
        except InvalidAddressError:
            _logger.warning("%s for user %s not queued: invalid address", template, user_id)
