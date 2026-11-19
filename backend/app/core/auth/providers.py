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
from app.core.auth.password_policy import normalize_password
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


def password_candidates(password: str) -> tuple[str, ...]:
    """What a typed password is checked as: its NFKC form (how every password is stored
    since T-0077, diff §4.2) and, when that differs, exactly as typed (a password stored
    before the policy normalised)."""
    return tuple(dict.fromkeys((normalize_password(password), password)))


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
        candidates = password_candidates(credentials.password)
        if user is None:
            for candidate in candidates:
                burn_password_check(candidate)
            return None
        # Every candidate is checked (no short-circuit), so an unknown account, a wrong
        # password and a right one cost the same number of scrypt rounds.
        matches = [verify_password(candidate, user.password_hash) for candidate in candidates]
        if not any(matches):
            return None
        return user if user.is_active else None
