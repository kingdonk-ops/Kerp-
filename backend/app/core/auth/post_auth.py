"""The post-authentication hook: what a sign-in does with an ``invite_token`` (T-0052,
diff §4.1 Invites).

Login, magic-link redeem and the Entra ID SSO callback live in core auth, invites in the admin
module. A sign-in that carries an invite token must accept the invite in the same request,
and core never imports ``app.modules`` or reads a module table (CLAUDE.md §6). So the module
that owns invites registers a hook at start-up, like the magic-link eligibility provider
(``magic_link.py``, T-0057):

* ``reference(invite_token)`` turns the opaque token into the opaque **reference** core keeps
  where a sign-in spans two requests (the magic-link record, the SSO ``state`` entry, the
  native exchange code). Core computes it as soon as the token arrives and never stores,
  logs or forwards the token itself.
* ``after_authentication(session, user, reference)`` runs right after the identity is proven
  and the session's tokens are issued, in the request's unit of work, and returns the
  outcome: ``joined_project_id`` or ``invite_error``.

**Empty by default.** Without a registered hook a sign-in works as before and ``invite_token``
is ignored. **An invite never blocks a sign-in**: a refused token is an ``invite_error`` in the
token response, and a hook that raises is logged (never the token or the reference), its
work is rolled back (the sign-in's own writes are committed before the hook runs) and the
sign-in still succeeds with ``INVITE_UNAVAILABLE``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Final, Protocol

from sqlalchemy.orm import Session

from app.core.auth.models import User

# The hook raised: nothing was joined; the client may retry from the invite link.
INVITE_UNAVAILABLE: Final = "INVITE_UNAVAILABLE"

_logger = logging.getLogger("kerp.auth.post_auth")


@dataclass(frozen=True, slots=True)
class PostAuthOutcome:
    """What the hook did with the invite: the project joined, or why not (a ``code`` the
    client translates). Both ``None`` when the sign-in carried no invite."""

    joined_project_id: uuid.UUID | None = None
    invite_error: str | None = None


NO_INVITE: Final = PostAuthOutcome()


class PostAuthenticationHook(Protocol):
    def reference(self, invite_token: str) -> str:
        """The opaque reference of ``invite_token`` (never the token itself)."""
        ...

    def after_authentication(self, session: Session, user: User, reference: str) -> PostAuthOutcome:
        """Accept the invite ``reference`` names for the freshly authenticated ``user``."""
        ...


_hooks: list[PostAuthenticationHook] = []


def register_post_authentication_hook(hook: PostAuthenticationHook) -> None:
    """Install the hook (the module owning invites, at start-up). It replaces any earlier
    one: there is one invite owner."""
    _hooks[:] = [hook]


def unregister_post_authentication_hook(hook: PostAuthenticationHook) -> None:
    """Remove the hook (tests, shutdown). An unknown hook is ignored."""
    if hook in _hooks:
        _hooks.remove(hook)


def invite_reference(invite_token: str | None) -> str | None:
    """The reference core carries for ``invite_token``; ``None`` without a token or a hook."""
    if not invite_token or not _hooks:
        return None
    return _hooks[0].reference(invite_token)


def run_post_authentication(session: Session, user: User, reference: str | None) -> PostAuthOutcome:
    """Run the hook for a sign-in that carried an invite. Never raises."""
    if reference is None or not _hooks:
        return NO_INVITE
    user_id = user.id
    # The sign-in's own writes (the internal flag, an SSO link and its audit entry) are made
    # durable first, so a failing hook can only undo its own work.
    session.commit()
    try:
        return _hooks[0].after_authentication(session, user, reference)
    except Exception:
        _logger.exception("post-authentication hook failed for user %s", user_id)
        session.rollback()
        return PostAuthOutcome(invite_error=INVITE_UNAVAILABLE)
