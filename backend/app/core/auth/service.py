"""Login, refresh rotation, logout and user helpers (T-0008, diff §4).

* **Login**: an ``AuthProvider`` proves the identity inside the chosen tenant; a new login
  session (``sid``) starts with a 15-minute access token and a refresh token.
* **Refresh**: the refresh token is verified, then consumed atomically from the store and
  replaced by a new pair in the same session. Presenting an already-used refresh token is
  treated as theft: the whole session is revoked (rotation with reuse detection).
* **Logout**: revokes the access token's ``jti`` and its session, so every token of that
  login (including the refresh token) stops working at once.
* **Field workers** (T-0009, ``field.py``): a magic link starts a PIN-gated session; only
  ``unlock`` (refresh token + PIN) rotates it. Wrong PINs, wrong passwords and magic-link
  requests run through the Redis-backed progressive lockout (``lockout.py``), which raises
  ``LockedOutError`` (HTTP 429) without checking the secret.

Every failure is the same ``AuthenticationError`` (HTTP 401) so callers cannot tell an
unknown account from a wrong password or a revoked session.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.field import (
    MAGIC_LINK_TTL,
    PIN_TTL_MARGIN,
    MagicLink,
    magic_link_digest,
    new_magic_link_token,
)
from app.core.auth.lockout import (
    LOGIN_POLICY,
    MAGIC_LINK_REQUEST_POLICY,
    PIN_POLICY,
    ProgressiveLockout,
)
from app.core.auth.models import User, normalize_email
from app.core.auth.passwords import hash_password, verify_password
from app.core.auth.providers import AuthProvider, LocalPasswordProvider, PasswordCredentials
from app.core.auth.runtime import AuthRuntime
from app.core.auth.tokens import InvalidTokenError, IssuedToken, TokenClaims
from app.core.db import utcnow
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_tenancy


class AuthenticationError(Exception):
    """Credentials or token rejected. Deliberately carries no reason for the client."""


@dataclass(frozen=True, slots=True)
class TokenPair:
    access: IssuedToken
    refresh: IssuedToken


def create_user(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    email: str,
    password: str | None,
    display_name: str = "",
    source: str = "api",
) -> User:
    """Add a tenant-level user (admin/onboarding tasks build on this). Caller commits."""
    user = User(
        tenant_id=tenant_id,
        project_id=None,
        email=normalize_email(email),
        display_name=display_name,
        password_hash=hash_password(password) if password is not None else None,
        is_active=True,
        source=source,
    )
    session.add(user)
    session.flush()
    return user


def set_password(user: User, password: str) -> None:
    """Replace the password; every token issued before now stops working."""
    user.password_hash = hash_password(password)
    user.password_changed_at = utcnow()


def _aware(moment: datetime) -> datetime:
    # SQLite hands TIMESTAMPTZ back naive; it was stored as UTC.
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def token_predates_password_change(user: User, claims: TokenClaims) -> bool:
    if user.password_changed_at is None:
        return False
    # ``iat`` has whole-second precision.
    changed = _aware(user.password_changed_at).replace(microsecond=0)
    return claims.issued_at < changed


def load_active_user(session: Session, claims: TokenClaims) -> User:
    """The token's user, if it still exists, is active and the token is still current."""
    user = session.scalars(
        select(User).where(User.id == claims.user_id, User.tenant_id == claims.tenant_id)
    ).one_or_none()
    if user is None or not user.is_active or token_predates_password_change(user, claims):
        raise AuthenticationError("user not active")
    return user


def _subject(tenant_id: uuid.UUID, email: str) -> str:
    return f"{tenant_id}:{normalize_email(email)}"


class AuthService:
    def __init__(self, runtime: AuthRuntime, provider: AuthProvider | None = None) -> None:
        self.runtime = runtime
        self.provider: AuthProvider = provider or LocalPasswordProvider()
        store = runtime.store
        self.login_lockout = ProgressiveLockout(store, LOGIN_POLICY, "login")
        self.magic_link_lockout = ProgressiveLockout(store, MAGIC_LINK_REQUEST_POLICY, "magic")
        self.pin_lockout = ProgressiveLockout(store, PIN_POLICY, "pin")

    def login(
        self,
        session: Session,
        *,
        tenant_id: uuid.UUID | None,
        credentials: PasswordCredentials,
    ) -> TokenPair:
        tenant = tenant_id or self.runtime.settings.default_tenant_id
        if tenant is None:
            raise AuthenticationError("no tenant")
        # Keyed by tenant + e-mail whether or not the account exists: an unknown address
        # locks exactly like a real one, so the lockout reveals nothing.
        subject = _subject(tenant, credentials.email)
        self.login_lockout.begin(subject)
        bind_tenancy(session, TenancyContext(tenant_id=tenant))
        user = self.provider.authenticate(session, tenant, credentials)
        if user is None:
            raise AuthenticationError("invalid credentials")
        self.login_lockout.succeeded(subject)
        return self._issue_pair(user.id, tenant, uuid.uuid4())

    def refresh(self, session: Session, refresh_token: str) -> TokenPair:
        claims = self._live_refresh_claims(refresh_token)
        if self.runtime.store.get_pin(claims.session_id) is not None:
            # A PIN-gated (field-worker) session rotates only through ``unlock``. The token
            # is not consumed: the device can still unlock with it.
            raise AuthenticationError("PIN unlock required")
        return self._rotate(session, claims)

    def request_magic_link(
        self, session: Session, *, tenant_id: uuid.UUID | None, email: str
    ) -> None:
        """Send a one-time sign-in link to an active user. Silent for anyone else."""
        tenant = tenant_id or self.runtime.settings.default_tenant_id
        if tenant is None:
            return
        self.magic_link_lockout.begin(_subject(tenant, email))
        bind_tenancy(session, TenancyContext(tenant_id=tenant))
        user = session.scalars(
            select(User).where(User.tenant_id == tenant, User.email == normalize_email(email))
        ).one_or_none()
        if user is None or not user.is_active:
            return
        token = new_magic_link_token()
        payload = json.dumps({"uid": str(user.id), "tid": str(tenant)}).encode()
        self.runtime.store.put_magic_link(magic_link_digest(token), payload, MAGIC_LINK_TTL)
        self.runtime.magic_links.send(
            MagicLink(
                token=token,
                email=user.email,
                user_id=user.id,
                tenant_id=tenant,
                expires_at=utcnow() + MAGIC_LINK_TTL,
            )
        )

    def redeem_magic_link(self, session: Session, *, token: str, pin: str) -> TokenPair:
        """Spend a magic link: start a new session on this device, gated by ``pin``.

        ``pin`` is validated by the request schema (6 digits, not a weak PIN).
        """
        payload = self.runtime.store.take_magic_link(magic_link_digest(token))
        if payload is None:
            raise AuthenticationError("unknown, expired or used magic link")
        data = json.loads(payload)
        user_id, tenant_id = uuid.UUID(data["uid"]), uuid.UUID(data["tid"])
        bind_tenancy(session, TenancyContext(tenant_id=tenant_id, user_id=user_id))
        user = session.scalars(
            select(User).where(User.id == user_id, User.tenant_id == tenant_id)
        ).one_or_none()
        if user is None or not user.is_active:
            raise AuthenticationError("user not active")
        session_id = uuid.uuid4()
        # The PIN gate first: a session must never have a registered refresh token without
        # it, or plain ``/refresh`` would accept that token.
        self.runtime.store.set_pin(session_id, hash_password(pin), self._pin_ttl())
        return self._issue_pair(user_id, tenant_id, session_id)

    def unlock(self, session: Session, *, refresh_token: str, pin: str) -> TokenPair:
        """Rotate a PIN-gated session with the device's refresh token *and* the PIN.

        Neither half works alone. Wrong PINs escalate the session's lockout; the last
        allowed failure (``PIN_POLICY.max_attempts``) revokes the session.
        """
        claims = self._live_refresh_claims(refresh_token)
        session_id = claims.session_id
        pin_hash = self.runtime.store.get_pin(session_id)
        if pin_hash is None:
            raise AuthenticationError("not a PIN-gated session")
        subject = str(session_id)
        attempt = self.pin_lockout.begin(subject)
        if PIN_POLICY.exhausted(attempt):
            self._revoke_session(session_id)
            raise AuthenticationError("PIN attempts exhausted")
        if not verify_password(pin, pin_hash):
            if attempt == PIN_POLICY.max_attempts:
                self._revoke_session(session_id)
            raise AuthenticationError("wrong PIN")
        self.pin_lockout.succeeded(subject)
        pair = self._rotate(session, claims)
        self.runtime.store.set_pin(session_id, pin_hash, self._pin_ttl())  # re-arm the gate
        return pair

    def _live_refresh_claims(self, refresh_token: str) -> TokenClaims:
        try:
            claims = self.runtime.tokens.verify(refresh_token, "refresh")
        except InvalidTokenError as exc:
            raise AuthenticationError("invalid refresh token") from exc
        if self.runtime.store.is_revoked(claims.jti, claims.session_id):
            raise AuthenticationError("session revoked")
        return claims

    def _rotate(self, session: Session, claims: TokenClaims) -> TokenPair:
        if not self.runtime.store.consume_refresh(claims.jti):
            # Valid signature but already used (or never registered): assume a stolen
            # token is being replayed and kill the whole session.
            self._revoke_session(claims.session_id)
            raise AuthenticationError("refresh token reused")
        bind_tenancy(session, TenancyContext(tenant_id=claims.tenant_id, user_id=claims.user_id))
        try:
            load_active_user(session, claims)
        except AuthenticationError:
            self._revoke_session(claims.session_id)
            raise
        return self._issue_pair(claims.user_id, claims.tenant_id, claims.session_id)

    def _pin_ttl(self) -> timedelta:
        return self.runtime.tokens.ttl["refresh"] + PIN_TTL_MARGIN

    def logout(self, access: TokenClaims) -> None:
        self.runtime.store.revoke_token(access.jti, access.remaining())
        self._revoke_session(access.session_id)

    def _revoke_session(self, session_id: uuid.UUID) -> None:
        self.runtime.store.revoke_session(session_id, self.runtime.tokens.ttl["refresh"])

    def _issue_pair(
        self, user_id: uuid.UUID, tenant_id: uuid.UUID, session_id: uuid.UUID
    ) -> TokenPair:
        tokens = self.runtime.tokens
        ids = {"user_id": user_id, "tenant_id": tenant_id, "session_id": session_id}
        access = tokens.issue("access", **ids)
        refresh = tokens.issue("refresh", **ids)
        self.runtime.store.register_refresh(
            refresh.claims.jti, session_id, refresh.claims.remaining()
        )
        return TokenPair(access=access, refresh=refresh)
