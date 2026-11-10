"""Login, refresh rotation, logout and user helpers (T-0008, diff §4).

* **Login**: an ``AuthProvider`` proves the identity inside the chosen tenant; a new login
  session (``sid``) starts with a 15-minute access token and a refresh token.
* **Refresh**: the refresh token is verified, then consumed atomically from the store and
  replaced by a new pair in the same session. Presenting an already-used refresh token is
  treated as theft: the whole session is revoked (rotation with reuse detection).
* **Logout**: revokes the access token's ``jti`` and its session, so every token of that
  login (including the refresh token) stops working at once.

Every failure is the same ``AuthenticationError`` (HTTP 401) so callers cannot tell an
unknown account from a wrong password or a revoked session.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.models import User, normalize_email
from app.core.auth.passwords import hash_password
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


class AuthService:
    def __init__(self, runtime: AuthRuntime, provider: AuthProvider | None = None) -> None:
        self.runtime = runtime
        self.provider: AuthProvider = provider or LocalPasswordProvider()

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
        bind_tenancy(session, TenancyContext(tenant_id=tenant))
        user = self.provider.authenticate(session, tenant, credentials)
        if user is None:
            raise AuthenticationError("invalid credentials")
        return self._issue_pair(user.id, tenant, uuid.uuid4())

    def refresh(self, session: Session, refresh_token: str) -> TokenPair:
        runtime = self.runtime
        try:
            claims = runtime.tokens.verify(refresh_token, "refresh")
        except InvalidTokenError as exc:
            raise AuthenticationError("invalid refresh token") from exc
        if runtime.store.is_revoked(claims.jti, claims.session_id):
            raise AuthenticationError("session revoked")
        if not runtime.store.consume_refresh(claims.jti):
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
