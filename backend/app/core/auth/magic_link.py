"""Magic-link sign-in for Field Workers only (T-0057, diff §4.2, TD-136).

Only an **active** account that holds **PER-14 Field Worker** on at least one project may
sign in by magic link: effective grants, direct or team-derived (``access.load_grants``,
T-0073; memberships of a deleted project are revoked with it, so they never count). The
other half of the rule, a valid pending invite naming PER-14 for the account's e-mail,
belongs to the module that owns invites: it registers an **eligibility provider** at
start-up (``register_magic_link_eligibility``, T-0052). Core reads no module table and never
imports ``app.modules`` (CLAUDE.md §6).

Everyone else gets the same neutral ``202`` as an unknown address, and no link is stored or
sent. The refusal of a known, active account is logged and audited with the reason
``magic_link_not_field_worker`` (the user id, never the e-mail or a token).

**No timing oracle.** The request only counts the attempt (the per-mailbox lockout, T-0068)
and schedules one ``MagicLinkJob`` per request, the same job on every path (unknown,
inactive, ineligible, eligible). Everything that differs between the paths (the account
lookup, the eligibility check, the token, its store write, the mail, the refusal audit) runs
in ``AuthService.issue_magic_link`` **after** the response has been sent (a post-response task of
the API process, in its own unit of work), so the ``202`` never waits for it.

Redeem re-checks eligibility (``AuthService.redeem_magic_link``): a link issued while the
user held PER-14 is refused with the neutral invalid-link answer once the last PER-14 grant
is gone or the account is deactivated.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from sqlalchemy.orm import Session

MAGIC_LINK_NOT_FIELD_WORKER: Final = "magic_link_not_field_worker"
# The audit chain of magic-link refusals (one per tenant).
MAGIC_LINK_AUDIT_CHAIN: Final = "auth:magic-link"

_logger = logging.getLogger("kerp.auth.magic_link")


@dataclass(frozen=True, slots=True)
class MagicLinkCandidate:
    """The active account asking for a magic link, as an eligibility provider sees it."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    email: str  # normalised (``models.normalize_email``)

    def __repr__(self) -> str:  # never the address
        return f"MagicLinkCandidate(tenant_id={self.tenant_id}, user_id={self.user_id})"


# A provider answers whether ``candidate`` may sign in by magic link for a reason core does
# not know (an invite naming PER-14, T-0052). It gets the job's (or the redeem's) session,
# bound to the candidate's tenant and user, and must only read through its own module.
MagicLinkEligibilityProvider = Callable[[Session, MagicLinkCandidate], bool]
_providers: list[MagicLinkEligibilityProvider] = []


def register_magic_link_eligibility(provider: MagicLinkEligibilityProvider) -> None:
    """Install an additional eligibility provider (a module, at start-up). Idempotent.
    None by default: then only a PER-14 grant makes an account eligible."""
    if provider not in _providers:
        _providers.append(provider)


def unregister_magic_link_eligibility(provider: MagicLinkEligibilityProvider) -> None:
    """Remove a provider (tests, shutdown). Unknown providers are ignored."""
    if provider in _providers:
        _providers.remove(provider)


def magic_link_eligible(session: Session, candidate: MagicLinkCandidate) -> bool:
    """Whether the (active) account may sign in by magic link: a PER-14 grant on a project,
    or a registered provider says yes. ``session`` must be bound to the candidate's tenant
    and user (the membership policy lets a session read its own rows)."""
    # Imported here: ``access`` imports ``deps``, which imports ``service``, which imports this
    # module.
    from app.core.auth.access import Persona, load_grants

    grants = load_grants(session, candidate.tenant_id, candidate.user_id)
    if any(grant.persona is Persona.FIELD_WORKER for grant in grants):
        return True
    return any(provider(session, candidate) for provider in list(_providers))


@dataclass(frozen=True, slots=True)
class MagicLinkJob:
    """What a magic-link request leaves for after the response: the same on every path."""

    tenant_id: uuid.UUID
    email: str  # normalised

    def __repr__(self) -> str:  # never the address
        return f"MagicLinkJob(tenant_id={self.tenant_id})"


def log_refusal(user_id: uuid.UUID, stage: str) -> None:
    """The plain-log line of a refusal: the user id and the reason, never the e-mail."""
    _logger.info(
        "magic link refused at %s for user %s: %s", stage, user_id, MAGIC_LINK_NOT_FIELD_WORKER
    )
