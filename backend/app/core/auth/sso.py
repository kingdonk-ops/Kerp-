"""Server-side state of the Entra ID SSO flow (T-0054, diff §4.5): sign-in ``state`` entries,
native exchange codes, the ``acct_claim_missing`` health marker, and the audit trail.

* **State** (``/start`` → ``/callback``): ``state``, ``nonce`` and the PKCE verifier live in
  the store for at most ``STATE_TTL`` and are taken exactly once (``GETDEL``), together with
  the optional invite reference (``post_auth.invite_reference`` of the ``invite_token`` the
  form-posted ``/start`` received, T-0052: never the token itself), the enrolling
  ``device_id``, the native app's verifier hash
  and the SHA-256 of the random pre-auth cookie that binds the flow to the browser that
  started it (login-CSRF). The key is the SHA-256 of ``state``.
* **Exchange codes** (native handoff): random, stored only as a SHA-256, single use, at most
  ``EXCHANGE_CODE_TTL``, bound to the user, the ``device_id`` and the app verifier hash
  (and carrying the invite reference to the exchange, where the native session starts). A
  spent code leaves a marker for ``SPENT_MARKER_TTL``, so a reuse attempt is recognised
  and audited.
* **Audit.** Link, sign-in and every refusal of a verified Entra identity go to the
  tenant-level chain ``auth:sso:entra`` (reasons only, never token contents). Refusals before
  any identity is proven (unknown state, missing cookie, bad token) go to the security log
  only, so unauthenticated noise cannot grow the ledger (as for wipe confirmations, T-0212).
"""

from __future__ import annotations

import json
import secrets
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Final

from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth.revocation import RevocationStore
from app.core.digest import sha256_hex

STATE_TTL: Final = timedelta(minutes=10)
EXCHANGE_CODE_TTL: Final = timedelta(seconds=60)
SPENT_MARKER_TTL: Final = timedelta(minutes=10)
ACCT_MISSING_WINDOW: Final = timedelta(hours=24)
ACCT_MISSING_KEY: Final = "sso:entra:acct-claim-missing"
AUDIT_CHAIN: Final = "auth:sso:entra"
SECRET_BYTES: Final = 32

# Health-check warning (``/health``): an SSO sign-in was refused for a missing ``acct``.
ACCT_MISSING_WARNING: Final = "sso_acct_claim_missing"


digest = sha256_hex


def new_secret() -> str:
    """256 random bits, URL-safe (``state``, nonce, PKCE verifier, cookie, exchange code)."""
    return secrets.token_urlsafe(SECRET_BYTES)


def _state_key(state: str) -> str:
    return f"sso:entra:state:{digest(state)}"


def _code_key(code: str) -> str:
    return f"sso:entra:exchange:{digest(code)}"


def _spent_key(code: str) -> str:
    return f"sso:entra:exchange-spent:{digest(code)}"


@dataclass(frozen=True, slots=True)
class SsoState:
    nonce: str
    verifier: str
    cookie_hash: str
    redirect_uri: str
    # The reference of the invite to accept after the sign-in (``post_auth``), not the token.
    invite_ref: str | None = None
    device_id: str | None = None
    app_challenge: str | None = None

    @property
    def native(self) -> bool:
        """The Capacitor app's flow: it sent its verifier hash, so it redeems an exchange
        code; tokens never travel in a redirect."""
        return self.app_challenge is not None

    @property
    def device_uuid(self) -> uuid.UUID | None:
        return uuid.UUID(self.device_id) if self.device_id else None


def put_state(store: RevocationStore, state: str, entry: SsoState) -> None:
    store.put_value(_state_key(state), json.dumps(asdict(entry)).encode(), STATE_TTL)


def take_state(store: RevocationStore, state: str) -> SsoState | None:
    """The state's entry, once: unknown, reused and expired states are all ``None``."""
    raw = store.take_value(_state_key(state))
    if raw is None:
        return None
    try:
        return SsoState(**json.loads(raw))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class ExchangeGrant:
    user_id: str
    tenant_id: str
    device_id: str
    app_challenge: str
    invite_ref: str | None = None


def put_exchange_code(store: RevocationStore, grant: ExchangeGrant) -> str:
    code = new_secret()
    store.put_value(_code_key(code), json.dumps(asdict(grant)).encode(), EXCHANGE_CODE_TTL)
    return code


def take_exchange_code(store: RevocationStore, code: str) -> ExchangeGrant | None:
    """The code's grant, exactly once (``GETDEL``); a spent code leaves a marker naming the
    user, so a second redemption can be audited."""
    raw = store.take_value(_code_key(code))
    if raw is None:
        return None
    try:
        grant = ExchangeGrant(**json.loads(raw))
    except (TypeError, ValueError):
        return None
    store.put_value(_spent_key(code), grant.user_id.encode(), SPENT_MARKER_TTL)
    return grant


def spent_code_user(store: RevocationStore, code: str) -> str | None:
    """The user of an exchange code that was already redeemed (reuse), else ``None``."""
    raw = store.peek_value(_spent_key(code))
    return raw.decode() if raw is not None else None


def record_acct_missing(store: RevocationStore) -> None:
    """Remember for 24 h that a sign-in failed for a missing ``acct`` claim (health check)."""
    store.put_value(ACCT_MISSING_KEY, str(int(time.time())).encode(), ACCT_MISSING_WINDOW)


def acct_missing_recently(store: RevocationStore) -> bool:
    return store.peek_value(ACCT_MISSING_KEY) is not None


def audit_event(
    session: Session,
    action: str,
    *,
    flow: str,
    reason: str | None = None,
    user_id: uuid.UUID | None = None,
) -> None:
    """One entry on the tenant-level ``auth:sso:entra`` chain (the session is bound to the
    tenant). Reasons and ids only: never a token, claim value or e-mail."""
    payload: dict[str, object] = {"action": action, "provider": "entra", "flow": flow}
    if reason is not None:
        payload["reason"] = reason
    if user_id is not None:
        payload["user_id"] = str(user_id)
    audit.append(session, AUDIT_CHAIN, payload, source="api", actor_id=user_id)
