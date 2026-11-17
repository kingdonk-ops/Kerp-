"""The ``AuthProvider`` seam: who proves a user's identity at login (TD-003).

KERP issues its own tokens whatever proved the identity. Two providers exist: the local
email + scrypt password one below, and Entra ID SSO for staff (``entra.EntraOidcProvider``,
T-0054, TD-043), which verifies an Entra ID token and returns the matching ``User``. Token
issue, rotation and revocation are the same for both (``AuthService.start_session``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.models import User, normalize_email
from app.core.auth.passwords import burn_password_check, verify_password


@dataclass(frozen=True, slots=True)
class PasswordCredentials:
    email: str
    password: str


class AuthProvider[C](Protocol):
    """Resolves login credentials (of type ``C``) to an active user of ``tenant_id``.

    ``session`` is already bound to ``tenant_id`` (tenant level), so RLS and the ORM scope
    confine every lookup to that tenant. Implementations must not reveal *why* a login
    failed to the client: the password provider returns ``None`` for every failure; the
    Entra provider raises ``SsoRefusedError`` whose reason goes only to the logs and audit
    ledger and whose client ``code`` is one of a few translated messages (diff §4.5).
    """

    name: str

    def authenticate(
        self, session: Session, tenant_id: uuid.UUID, credentials: C
    ) -> User | None: ...


class LocalPasswordProvider:
    """Email + scrypt password stored on ``auth_user``."""

    name = "password"

    def authenticate(
        self, session: Session, tenant_id: uuid.UUID, credentials: PasswordCredentials
    ) -> User | None:
        user = session.scalars(
            select(User).where(
                User.tenant_id == tenant_id, User.email == normalize_email(credentials.email)
            )
        ).one_or_none()
        if user is None:
            burn_password_check(credentials.password)
            return None
        if not verify_password(credentials.password, user.password_hash):
            return None
        return user if user.is_active else None
