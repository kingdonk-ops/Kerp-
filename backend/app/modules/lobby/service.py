"""Lobby discovery (T-0040, diff §4.1, edge spec §6 amendments). Authorisation is the router's.

* ``lobby_projects``: the lobby-visible projects, for internal users only. Read through the
  narrow lobby read of ``projects.service`` (id, code, name), which itself returns nothing for
  a caller who is not internal (defence in depth); the ``projects`` policy is not widened.
* ``find_by_code``: the one project with exactly that code, lobby-visible or not, for every
  signed-in user. A code finds a project; it never grants access (TD-202). Throttled per user
  and deployment-wide (``throttle_code_lookup``; per-client-IP limits: TD-138).
* **Lookup ticket**: a code lookup returns a signed JWT bound to the caller (user, tenant) and
  the project, valid ``LOOKUP_TICKET_TTL`` (15 minutes, planner default). The access-request
  API (T-0050) checks it with ``verify_lookup_ticket`` on a user's first request for a project
  when the user is external or the project is not lobby-visible, so a leaked project id alone
  is not enough. It is signed with the deployment's token keys but has its own ``type`` and
  ``typ``, so it is never accepted as an access or refresh token, nor they as a ticket.

``member`` on each result: the caller already has effective access to the project (the grants
the request scope was resolved from: direct memberships, and team-derived ones once T-0073
lands, ADR 0008), so the UI opens it instead of offering a request.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import jwt
from sqlalchemy.orm import Session

from app.core.auth.access import Grant
from app.core.auth.lockout import LockoutPolicy, ProgressiveLockout
from app.core.auth.revocation import RevocationStore
from app.core.auth.tokens import LEEWAY, TokenService
from app.modules.lobby.schemas import LobbyProject
from app.modules.projects import service as projects

LOOKUP_TICKET_TTL: Final = timedelta(minutes=15)  # planner default (diff §4.1)
TICKET_TYPE: Final = "lobby_lookup"
TICKET_TYP_HEADER: Final = "kerp-lookup+jwt"
_TICKET_REQUIRED: Final = ["exp", "iat", "nbf", "iss", "aud", "sub", "tid", "pid", "jti"]

# Every code lookup counts, hit or miss (nothing to get right). Per user: 30 back to back,
# then 1 min doubling up to 15 min, per hour: enough to find a few projects, too slow to sweep
# codes. Deployment-wide: a backstop against a sweep spread over many accounts (planner
# defaults, T-0040). Per client IP: TD-138.
USER_LOOKUP_POLICY: Final = LockoutPolicy(
    free_attempts=30,
    base_delay=timedelta(minutes=1),
    max_delay=timedelta(minutes=15),
    window=timedelta(hours=1),
)
DEPLOYMENT_LOOKUP_POLICY: Final = LockoutPolicy(
    free_attempts=1000,
    base_delay=timedelta(minutes=1),
    max_delay=timedelta(minutes=15),
    window=timedelta(hours=1),
)
USER_SCOPE: Final = "lobby-code-lookup"
DEPLOYMENT_SCOPE: Final = "lobby-code-lookup-deployment"


class InvalidLookupTicketError(Exception):
    """The ticket is malformed, forged, expired, or for another user, tenant or project."""


@dataclass(frozen=True, slots=True)
class LookupTicket:
    token: str
    expires_at: datetime


def _members(grants: Iterable[Grant]) -> set[uuid.UUID]:
    return {g.project_id for g in grants}


def _entry(project: projects.ProjectSummary, members: set[uuid.UUID]) -> LobbyProject:
    return LobbyProject(
        id=project.id, code=project.code, name=project.name, member=project.id in members
    )


def lobby_projects(session: Session, grants: Iterable[Grant]) -> list[LobbyProject]:
    """The lobby-visible projects, by name then code; empty for a caller who is not internal
    (the narrow read checks it itself). ``session``: the request session, bound to the caller."""
    members = _members(grants)
    return [_entry(p, members) for p in projects.lobby_projects(session)]


def find_by_code(session: Session, code: str, grants: Iterable[Grant]) -> LobbyProject | None:
    """The project with exactly ``code``, lobby-visible or not; ``None`` if there is none.
    Reads only; never creates a membership or anything else."""
    found = projects.project_by_code(session, code)
    return None if found is None else _entry(found, _members(grants))


def throttle_code_lookup(
    store: RevocationStore,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    user_policy: LockoutPolicy | None = None,
    deployment_policy: LockoutPolicy | None = None,
) -> None:
    """Count one code lookup for the user and for the deployment; ``LockedOutError`` (429)
    when either is locked. The user's limit is checked first, so a locked-out user's attempts
    do not use up everyone's deployment-wide budget."""
    user = user_policy or USER_LOOKUP_POLICY
    deployment = deployment_policy or DEPLOYMENT_LOOKUP_POLICY
    ProgressiveLockout(store, user, USER_SCOPE).begin(f"{tenant_id}:{user_id}")
    ProgressiveLockout(store, deployment, DEPLOYMENT_SCOPE).begin(str(tenant_id))


def issue_lookup_ticket(
    tokens: TokenService, *, user_id: uuid.UUID, tenant_id: uuid.UUID, project_id: uuid.UUID
) -> LookupTicket:
    """A lookup ticket for ``user_id`` and ``project_id``, valid ``LOOKUP_TICKET_TTL``."""
    now = tokens.now()
    expires_at = now + LOOKUP_TICKET_TTL
    payload: dict[str, Any] = {
        "iss": tokens.issuer,
        "aud": tokens.audience,
        "sub": str(user_id),
        "tid": str(tenant_id),
        "pid": str(project_id),
        "jti": uuid.uuid4().hex,
        "type": TICKET_TYPE,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    signing = tokens.keys.signing
    token = jwt.encode(
        payload,
        signing.private_key,
        algorithm=signing.algorithm,
        headers={"kid": signing.kid, "typ": TICKET_TYP_HEADER},
    )
    return LookupTicket(token, datetime.fromtimestamp(payload["exp"], UTC))


def _moment(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidLookupTicketError("nbf and exp must be numbers")
    return datetime.fromtimestamp(value, UTC)


def verify_lookup_ticket(
    tokens: TokenService,
    ticket: str,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
) -> None:
    """Accept a ticket issued to ``user_id`` (in ``tenant_id``) for ``project_id`` and not yet
    expired; else ``InvalidLookupTicketError`` (T-0050 answers 404). Only the algorithm of the
    key named by ``kid`` is accepted (no ``alg: none``, no algorithm confusion)."""
    try:
        kid = jwt.get_unverified_header(ticket).get("kid")
        key = tokens.keys.get(kid) if isinstance(kid, str) else None
        if key is None:
            raise InvalidLookupTicketError("unknown signing key")
        payload = jwt.decode(
            ticket,
            key.public_key,
            algorithms=[key.algorithm],
            audience=tokens.audience,
            issuer=tokens.issuer,
            leeway=LEEWAY,
            # Lifetime is checked below against the token service's clock, not the host's.
            options={
                "require": _TICKET_REQUIRED,
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
            },
        )
    except InvalidLookupTicketError:
        raise
    except (jwt.PyJWTError, AttributeError, KeyError, TypeError, ValueError) as exc:
        raise InvalidLookupTicketError(str(exc) or type(exc).__name__) from exc
    now = tokens.now()
    if now + LEEWAY < _moment(payload["nbf"]) or now >= _moment(payload["exp"]) + LEEWAY:
        raise InvalidLookupTicketError("ticket not valid now")
    expected = {
        "type": TICKET_TYPE,
        "sub": str(user_id),
        "tid": str(tenant_id),
        "pid": str(project_id),
    }
    if any(payload.get(name) != value for name, value in expected.items()):
        raise InvalidLookupTicketError("ticket is for another user, tenant or project")
