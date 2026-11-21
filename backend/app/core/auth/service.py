"""Login, refresh rotation, logout and user helpers (T-0008, diff §4).

* **Login**: an ``AuthProvider`` proves the identity inside the deployment's one tenant
  (``KERP_DEFAULT_TENANT_ID``, diff §4.1); a new login session (``sid``) starts with a
  15-minute access token and a refresh token that expires at sign-in + 12 h.
* **Refresh**: the refresh token is verified, then consumed atomically from the store and
  replaced by a new pair in the same session, expiring when the session ends: rotation
  never extends a session past sign-in + ``KERP_REFRESH_TOKEN_HOURS`` (diff §4.2).
  Presenting an already-used refresh token is treated as theft: the whole session is
  revoked (rotation with reuse detection).
* **Logout**: revokes the access token's ``jti`` and its session, so every token of that
  login (including the refresh token) stops working at once.
* **Field PIN, hybrid** (T-0038, ``field.py``, diff §4.2): the PIN belongs to the enrolled
  device (its hash is on the device row). A sign-in naming an enrolled device of the user
  binds the new session to it at once (the ``did`` claim) and clears the device's PIN
  failures, without touching the PIN. ``refresh`` rotates a session of a device with a PIN
  while its last renewal is less than ``KERP_PIN_IDLE_MINUTES`` ago; after a longer idle it
  raises ``PinGateError(PIN_REQUIRED)`` and only ``unlock`` (refresh token + PIN) renews it.
  Wrong PINs (per device, offline failures included), wrong passwords and magic-link
  requests run through the Redis-backed progressive lockout (``lockout.py``), which raises
  ``LockedOutError`` (HTTP 429) without checking the secret.

Every failure is the same ``AuthenticationError`` (HTTP 401) so callers cannot tell an
unknown account from a wrong password or a revoked session. The one exception is a revoked
device (T-0043, diff §4.3): a call naming a revoked device (``device_id``), or a refresh of a
session killed by a device revoke, raises ``DeviceRevokedError`` (``401`` with
``code: DEVICE_REVOKED``), so the client knows to wipe itself. Sign-in checks the
credentials (password, magic link) **before** the device (T-0080, TD-291): with wrong
credentials an unknown, an active and a revoked device get the same answer.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.orm import Session, object_session

from app.core.audit import service as audit
from app.core.auth.device_signal import DeviceRevokedError
from app.core.auth.devices import device_status, pin_device
from app.core.auth.field import (
    MAGIC_LINK_TTL,
    PIN_EXHAUSTED,
    PIN_INVALID,
    PIN_REQUIRED,
    PIN_TTL_MARGIN,
    MagicLink,
    PinGateError,
    magic_link_digest,
    new_magic_link_token,
)
from app.core.auth.lockout import (
    LOGIN_ACCOUNT_POLICY,
    LOGIN_POLICY,
    MAGIC_LINK_REQUEST_POLICY,
    PIN_POLICY,
    ProgressiveLockout,
)
from app.core.auth.magic_link import (
    MAGIC_LINK_AUDIT_CHAIN,
    MAGIC_LINK_NOT_FIELD_WORKER,
    MagicLinkCandidate,
    MagicLinkJob,
    log_refusal,
    magic_link_eligible,
)
from app.core.auth.models import DEVICE_REVOKED, Device, User, normalize_email
from app.core.auth.password_policy import BreachCheck, check_password
from app.core.auth.passwords import hash_password, verify_password
from app.core.auth.post_auth import NO_INVITE, PostAuthOutcome, run_post_authentication
from app.core.auth.providers import AuthProvider, LocalPasswordProvider, PasswordCredentials
from app.core.auth.runtime import AuthRuntime
from app.core.auth.tokens import InvalidTokenError, IssuedToken, TokenClaims
from app.core.config import Settings, get_settings
from app.core.db import Database, utcnow
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_tenancy, session_tenancy
from app.core.tenancy.scope import get_or_404

_logger = logging.getLogger("kerp.auth")


class CredentialsExistError(Exception):
    """The account already has a way to sign in (``has_credentials``): a first password is
    never set over it (T-0059). Nothing was changed."""


class AuthenticationError(Exception):
    """Credentials or token rejected. Deliberately carries no reason for the client."""


@dataclass(frozen=True, slots=True)
class TokenPair:
    access: IssuedToken
    refresh: IssuedToken
    # What the post-authentication hook did with the sign-in's invite, if it carried one
    # (T-0052): never set by a refresh or a PIN unlock.
    invite: PostAuthOutcome = NO_INVITE


def email_domain_is_internal(email: str, domains: Collection[str]) -> bool:
    """Whether ``email``'s domain is on the internal list: exact and case-insensitive, so a
    subdomain is another domain (``domains`` are lower-cased by the settings)."""
    domain = normalize_email(email).rpartition("@")[2]
    return bool(domain) and domain in domains


def derive_is_internal(user: User, domains: Collection[str]) -> bool:
    """The internal-staff rule (diff §4.1, ADR 0009): linked to a member account of the
    company Entra tenant (only members are ever linked, T-0054) or a listed e-mail domain."""
    return user.entra_oid is not None or email_domain_is_internal(user.email, domains)


def refresh_internal_flag(user: User, domains: Collection[str]) -> None:
    """Recompute ``is_internal`` (account creation, every sign-in). Writes only on change, so a
    sign-in does not bump the row's revision for nothing."""
    internal = derive_is_internal(user, domains)
    if user.is_internal is not internal:
        user.is_internal = internal


def is_internal_user(session: Session, user_id: uuid.UUID) -> bool:
    """Whether ``user_id`` is internal staff: the one way other modules read the flag
    (CLAUDE.md §6). ``False`` for an unknown user or one outside the session's tenant. The
    flag grants no access by itself."""
    flag = session.scalars(select(User.is_internal).where(User.id == user_id)).one_or_none()
    return bool(flag)


def create_user(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    email: str,
    password: str | None,
    display_name: str = "",
    source: str = "api",
    internal_domains: Collection[str] | None = None,
    settings: Settings | None = None,
) -> User:
    """Add a tenant-level user (admin/onboarding tasks build on this). Caller commits.

    ``is_internal`` is derived here from ``internal_domains`` (default: the deployment's
    ``KERP_INTERNAL_EMAIL_DOMAINS``), never taken from the caller. A ``password`` is checked
    against the deployment's password policy (T-0077, ``PasswordPolicyError``) and stored
    NFKC-normalised, like every other password set.
    """
    if internal_domains is None:
        internal_domains = (settings or get_settings()).internal_email_domains
    if password is not None:
        password = check_password(password, email=normalize_email(email), settings=settings)
    user = User(
        tenant_id=tenant_id,
        project_id=None,
        email=normalize_email(email),
        display_name=display_name,
        password_hash=hash_password(password) if password is not None else None,
        is_active=True,
        is_internal=False,
        source=source,
    )
    refresh_internal_flag(user, internal_domains)
    session.add(user)
    session.flush()
    return user


def list_users(session: Session) -> list[User]:
    """The session tenant's live accounts, by e-mail (user administration, T-0014)."""
    return list(session.scalars(select(User).order_by(User.email)))


def get_user(session: Session, user_id: uuid.UUID) -> User:
    """A live account of the session's tenant, else ``OutOfScopeError`` (404)."""
    return get_or_404(session, User, user_id)


def users_by_id(session: Session, user_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, User]:
    """The live accounts among ``user_ids`` (member lists, T-0014)."""
    if not user_ids:
        return {}
    rows = session.scalars(select(User).where(User.id.in_(list(user_ids))))
    return {user.id: user for user in rows}


def active_user_ids(session: Session, user_ids: Collection[uuid.UUID]) -> set[uuid.UUID]:
    """The live, *active* accounts among ``user_ids``, read from the database every time.

    A column query, so the answer never comes from a ``User`` already in the session's
    identity map: after waiting on a row lock, a concurrently committed deactivation is seen
    (the last-active-admin check, T-0211).
    """
    if not user_ids:
        return set()
    query = select(User.id).where(User.id.in_(list(user_ids)), User.is_active.is_(True))
    return set(session.scalars(query))


def user_counts(session: Session) -> dict[str, int]:
    """The session tenant's live accounts by state: ``active``, ``deactivated`` (T-0219).

    Counts only, under the caller's RLS, filtered on the bound tenant explicitly."""
    context = session_tenancy(session)
    if context is None:
        raise RuntimeError("user_counts needs a session bound to a tenant")
    rows = session.execute(
        select(User.is_active, func.count())
        .where(User.tenant_id == context.tenant_id)
        .group_by(User.is_active)
    ).all()
    by_state = {bool(active): int(count) for active, count in rows}
    return {"active": by_state.get(True, 0), "deactivated": by_state.get(False, 0)}


def find_user_by_email(session: Session, email: str) -> User | None:
    """The live account with exactly this e-mail (case-insensitive, trimmed), if any."""
    query = select(User).where(User.email == normalize_email(email))
    return session.scalars(query).one_or_none()


def has_credentials(user: User) -> bool:
    """Whether the account can already sign in on its own: a local password, or a linked
    company Entra ID identity (T-0054). Such an account signs in; it never sets a *first*
    password (T-0059, diff §4.1)."""
    return user.password_hash is not None or user.entra_oid is not None


def set_password(
    user: User,
    password: str,
    *,
    settings: Settings | None = None,
    breach_check: BreachCheck | None = None,
) -> None:
    """Replace the password under the deployment's password policy (T-0077, diff §4.2).

    Raises ``PasswordPolicyError`` (nothing is changed then). The password is stored as the
    scrypt hash of its NFKC form. Every access and refresh token issued before now stops
    working (``token_predates_password_change``, exact to the microsecond: T-0037 / T-0061),
    on every device, and so does every magic link issued before now. Device enrolment and
    the device PIN are not touched.

    For a ``user`` in a session, pending changes are flushed and the user's row stays locked
    until the caller's transaction ends (``lock_user``): commit soon.
    """
    normalised = check_password(
        password, email=user.email, settings=settings, breach_check=breach_check
    )
    apply_checked_password(user, normalised)


# SQLite has no row locks (``FOR UPDATE`` is dropped there): a write that changes nothing
# takes the database's write lock instead, which a concurrent writer waits for.
_SQLITE_WRITE_LOCK = text(f"UPDATE {User.__tablename__} SET id = id WHERE id = :id").bindparams(
    bindparam("id", type_=User.__table__.c.id.type)
)


def lock_user(session: Session, user_id: uuid.UUID, tenant_id: uuid.UUID) -> User | None:
    """The account's row, locked until the caller's transaction ends and read again under
    the lock (pending changes are flushed first; a loaded ``User`` is refreshed in place).

    This is what orders a password change against token issuing (T-0077): the change stamps
    ``password_changed_at`` under the lock, and every path that issues tokens checks the row
    under the same lock and holds it until its tokens exist. So a token is either issued
    before the stamp (``iat`` older: revoked by it) or after the change committed (the
    issuing path saw the new row). ``FOR NO KEY UPDATE``: rows that reference the user are
    not blocked.
    """
    session.flush()
    if session.get_bind().dialect.name == "sqlite":
        session.execute(_SQLITE_WRITE_LOCK, {"id": user_id})
    return session.scalars(
        select(User)
        .where(User.id == user_id, User.tenant_id == tenant_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    ).one_or_none()


def apply_checked_password(user: User, normalised: str, *, require_active: bool = False) -> bool:
    """Store a password that ``check_password`` has just returned for this user. Core auth
    only (the reset checks the policy before it spends its token); everyone else calls
    ``set_password`` / ``set_user_password``.

    The hash is computed first, then the row is locked (``lock_user``) and the change is
    stamped under the lock. ``False``, with nothing changed, when ``require_active`` and the
    locked row is gone or deactivated."""
    password_hash = hash_password(normalised)
    session = object_session(user)
    if session is not None:
        locked = lock_user(session, user.id, user.tenant_id)
        if require_active and (locked is None or not locked.is_active):
            return False
    user.password_hash = password_hash
    user.password_changed_at = utcnow()
    return True


def set_user_password(
    session: Session,
    user_id: uuid.UUID,
    password: str,
    *,
    settings: Settings | None = None,
    breach_check: BreachCheck | None = None,
) -> User:
    """The one way a module sets an account's password (reset, first password at invite
    acceptance: T-0059, change, admin-created accounts): no module hashes a password or
    writes the users table itself (CLAUDE.md §6).

    ``session`` is bound to the account's tenant. ``OutOfScopeError`` (404) for an account
    that is not a live one of that tenant; ``PasswordPolicyError`` when the policy refuses
    the password. See ``set_password`` for what it revokes. Caller commits.
    """
    user = get_user(session, user_id)
    set_password(user, password, settings=settings, breach_check=breach_check)
    session.flush()
    return user


def _aware(moment: datetime) -> datetime:
    # SQLite hands TIMESTAMPTZ back naive; it was stored as UTC.
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _credential_state(user: User) -> tuple[str | None, datetime | None]:
    """What a password change changes, comparable before and after a re-read: the stamp is
    made aware, since SQLite hands back naive what was written aware in the same session."""
    changed = user.password_changed_at
    return user.password_hash, _aware(changed) if changed is not None else None


def token_predates_password_change(user: User, claims: TokenClaims) -> bool:
    """Exact, with no fail-closed window (T-0061): ``iat`` and ``password_changed_at`` both
    carry microseconds, so a token issued before the change is rejected and one issued after
    it (e.g. the login right after ``set_password``) is accepted, even within one second."""
    if user.password_changed_at is None:
        return False
    return claims.issued_at < _aware(user.password_changed_at)


def magic_link_predates_password_change(user: User, issued_at: object) -> bool:
    """Whether a magic link issued at ``issued_at`` (epoch seconds from its stored payload)
    is older than the account's last password change: a password set or reset invalidates
    the user's outstanding magic links (T-0077, diff §4.2). A payload without a usable
    ``iat`` (a link stored before T-0077) counts as issued at the earliest moment a live
    link can have been issued, now minus the link lifetime."""
    if user.password_changed_at is None:
        return False
    if isinstance(issued_at, int | float) and not isinstance(issued_at, bool):
        issued = datetime.fromtimestamp(issued_at, tz=UTC)
    else:
        issued = utcnow() - MAGIC_LINK_TTL
    return issued < _aware(user.password_changed_at)


def load_active_user(session: Session, claims: TokenClaims, *, for_update: bool = False) -> User:
    """The token's user, if it still exists, is active and the token is still current.
    ``for_update``: checked on the locked row (``lock_user``), for a caller that goes on to
    issue tokens."""
    if for_update:
        user = lock_user(session, claims.user_id, claims.tenant_id)
    else:
        user = session.scalars(
            select(User).where(User.id == claims.user_id, User.tenant_id == claims.tenant_id)
        ).one_or_none()
    if user is None or not user.is_active or token_predates_password_change(user, claims):
        raise AuthenticationError("user not active")
    return user


def refuse_revoked_device(session: Session, device_id: uuid.UUID | None) -> None:
    """``DeviceRevokedError`` when ``device_id`` names a revoked device of the session's
    tenant (diff §4.3). Unknown or active devices pass: enrolment happens after sign-in."""
    if device_id is not None and device_status(session, device_id) == DEVICE_REVOKED:
        raise DeviceRevokedError("device revoked")


def _subject(tenant_id: uuid.UUID, email: str) -> str:
    return f"{tenant_id}:{normalize_email(email)}"


def pin_failure_subject(tenant_id: uuid.UUID, device_id: uuid.UUID) -> str:
    """The PIN lockout's subject: one counter per device (diff §4.2)."""
    return f"{tenant_id}:{device_id}"


UNKNOWN_CLIENT = "unknown"


class AuthService:
    def __init__(
        self, runtime: AuthRuntime, provider: AuthProvider[PasswordCredentials] | None = None
    ) -> None:
        self.runtime = runtime
        self.provider: AuthProvider[PasswordCredentials] = provider or LocalPasswordProvider()
        store = runtime.store
        self.login_lockout = ProgressiveLockout(store, LOGIN_POLICY, "login")
        self.login_account_lockout = ProgressiveLockout(
            store, LOGIN_ACCOUNT_POLICY, "login-account"
        )
        self.magic_link_lockout = ProgressiveLockout(store, MAGIC_LINK_REQUEST_POLICY, "magic")
        # Per device. The cap holds until a sign-in on the device clears it: the counter
        # outlives any one session (``lockout.PIN_POLICY``).
        pin_policy = replace(PIN_POLICY, window=self._pin_ttl())
        self.pin_lockout = ProgressiveLockout(store, pin_policy, "pin-device")

    def clear_pin_failures(self, tenant_id: uuid.UUID, device_id: uuid.UUID) -> None:
        """Forget the device's PIN failures: a sign-in on it (a new session bound to it) or a
        PIN change ends the "10th wrong PIN" stop."""
        self.pin_lockout.succeeded(pin_failure_subject(tenant_id, device_id))

    def login(
        self,
        session: Session,
        *,
        credentials: PasswordCredentials,
        client: str | None = None,
        device_id: uuid.UUID | None = None,
        invite_ref: str | None = None,
    ) -> TokenPair:
        """``client`` is the caller's address (``request.client.host``), the lockout key.
        ``device_id`` is the device the client says it is (``X-KERP-Device-Id``).
        ``invite_ref``: the reference of an invite to accept once signed in (``post_auth``)."""
        tenant = self.runtime.settings.default_tenant_id
        if tenant is None:
            raise AuthenticationError("no tenant")
        # Keyed by tenant + e-mail whether or not the account exists: an unknown address
        # locks exactly like a real one, so the lockout reveals nothing. The tight lock is per
        # client, so another client's failures never lock this one out (T-0068); the loose
        # per-account backstop caps guessing from many clients.
        account = _subject(tenant, credentials.email)
        per_client = f"{account}|{client or UNKNOWN_CLIENT}"
        self.login_lockout.begin(per_client)
        self.login_account_lockout.begin(account)
        bind_tenancy(session, TenancyContext(tenant_id=tenant))
        user = self.provider.authenticate(session, tenant, credentials)
        if user is None:
            raise AuthenticationError("invalid credentials")
        self.login_lockout.succeeded(per_client)
        self.login_account_lockout.succeeded(account)
        # Credentials first (T-0080, TD-291): only a caller who proved the account learns
        # that the device it names is revoked (and is told to wipe).
        refuse_revoked_device(session, device_id)
        return self.start_session(
            session, user, device_id=device_id, invite_ref=invite_ref, credential_bound=True
        )

    def refresh(
        self, session: Session, refresh_token: str, *, device_id: uuid.UUID | None = None
    ) -> TokenPair:
        """Rotate. A session of a device with a PIN that was idle (no renewal) for
        ``KERP_PIN_IDLE_MINUTES`` or longer raises ``PinGateError(PIN_REQUIRED)`` instead; the
        token is not spent, so the device can still unlock with it."""
        claims = self._live_refresh_claims(refresh_token)
        self._refuse_revoked_device(session, claims, device_id)
        bound, device = self._session_device(session, claims)
        if device is not None and device.pin_hash is not None and self._idle_too_long(claims):
            raise PinGateError(PIN_REQUIRED)
        return self._rotate(session, claims, device_id=bound)

    def request_magic_link(
        self, *, email: str, invite_ref: str | None = None
    ) -> MagicLinkJob | None:
        """The request half of a magic-link request: count it (the per-mailbox lockout,
        ``LockedOutError`` when locked) and return the job to run after the response.

        Nothing here depends on the address: unknown, inactive, ineligible and eligible
        accounts get the same work and the same job (diff §4.2 *no timing oracle*, T-0057).
        ``None`` only without a deployment tenant (the same for every address).
        ``invite_ref`` (the reference of an invite to accept at redeem, ``post_auth``) rides
        on the job and is kept next to the hashed link record, never in the link (T-0052).
        """
        tenant = self.runtime.settings.default_tenant_id
        if tenant is None:
            return None
        self.magic_link_lockout.begin(_subject(tenant, email))
        return MagicLinkJob(tenant_id=tenant, email=normalize_email(email), invite_ref=invite_ref)

    def issue_magic_link(self, database: Database, job: MagicLinkJob) -> None:
        """The deferred half (after the response, own unit of work): issue and send a link to
        an active Field Worker; refuse (log + audit) any other active account; nothing for an
        unknown or inactive one. Never raises: the client has had its ``202`` already."""
        try:
            with database.unit_of_work(TenancyContext(tenant_id=job.tenant_id)) as session:
                self._issue_magic_link(session, job)
        except Exception:
            _logger.exception("magic-link request not processed")

    def _issue_magic_link(self, session: Session, job: MagicLinkJob) -> None:
        # Columns only: the session holds no objects, so it can be rebound to the user.
        found = session.execute(
            select(User.id, User.is_active).where(
                User.tenant_id == job.tenant_id, User.email == job.email
            )
        ).one_or_none()
        if found is None or not found.is_active:
            return
        user_id: uuid.UUID = found.id
        bind_tenancy(session, TenancyContext(tenant_id=job.tenant_id, user_id=user_id))
        candidate = MagicLinkCandidate(tenant_id=job.tenant_id, user_id=user_id, email=job.email)
        if not magic_link_eligible(session, candidate):
            log_refusal(user_id, "request")
            audit.append(
                session,
                MAGIC_LINK_AUDIT_CHAIN,
                {
                    "action": "magic_link_refused",
                    "reason": MAGIC_LINK_NOT_FIELD_WORKER,
                    "user_id": str(user_id),
                },
                source="api",
            )
            return
        token = new_magic_link_token()
        record: dict[str, object] = {
            "uid": str(user_id),
            "tid": str(job.tenant_id),
            "iat": utcnow().timestamp(),
        }
        if job.invite_ref is not None:
            record["inv"] = job.invite_ref  # the reference only; the link never carries it
        payload = json.dumps(record).encode()
        self.runtime.store.put_magic_link(magic_link_digest(token), payload, MAGIC_LINK_TTL)
        self.runtime.magic_links.send(
            session,
            MagicLink(
                token=token,
                email=job.email,
                user_id=user_id,
                tenant_id=job.tenant_id,
                expires_at=utcnow() + MAGIC_LINK_TTL,
            ),
        )

    def redeem_magic_link(
        self, session: Session, *, token: str, device_id: uuid.UUID | None = None
    ) -> TokenPair:
        """Spend a magic link: start a new session (on ``device_id``, if it is an enrolled
        device of the user: bound at once, its PIN kept, its PIN failures cleared).

        The link is validated first without spending it (T-0080): an unknown, expired or
        spent link is the uniform ``AuthenticationError`` whatever the device. Only then is a
        revoked ``device_id`` refused (``DeviceRevokedError``, the link stays unspent); the
        link is spent once the device check has passed.
        """
        digest = magic_link_digest(token)
        pending = self.runtime.store.peek_magic_link(digest)
        if pending is None:
            raise AuthenticationError("unknown, expired or used magic link")
        if device_id is not None:
            link_tenant = uuid.UUID(json.loads(pending)["tid"])
            bind_tenancy(session, TenancyContext(tenant_id=link_tenant))
            refuse_revoked_device(session, device_id)
        payload = self.runtime.store.take_magic_link(digest)
        if payload is None:  # spent or expired since the peek
            raise AuthenticationError("unknown, expired or used magic link")
        data = json.loads(payload)
        user_id, tenant_id = uuid.UUID(data["uid"]), uuid.UUID(data["tid"])
        bind_tenancy(session, TenancyContext(tenant_id=tenant_id, user_id=user_id))
        user = session.scalars(
            select(User).where(User.id == user_id, User.tenant_id == tenant_id)
        ).one_or_none()
        if user is None or not user.is_active:
            raise AuthenticationError("user not active")
        if magic_link_predates_password_change(user, data.get("iat")):
            # A password set or reset invalidates the links issued before it (T-0077).
            raise AuthenticationError("magic link issued before the password change")
        candidate = MagicLinkCandidate(tenant_id=tenant_id, user_id=user_id, email=user.email)
        if not magic_link_eligible(session, candidate):  # PER-14 lost since the request
            log_refusal(user_id, "redeem")
            raise AuthenticationError("not eligible for a magic link")
        invite_ref = data.get("inv")
        return self.start_session(
            session, user, device_id=device_id, invite_ref=invite_ref, credential_bound=True
        )

    def start_session(
        self,
        session: Session,
        user: User,
        *,
        device_id: uuid.UUID | None = None,
        invite_ref: str | None = None,
        credential_bound: bool = False,
    ) -> TokenPair:
        """A new KERP session for ``user``, whose identity a provider has just proved
        (password, magic link, Entra ID SSO: T-0054): the internal flag is recomputed, the
        session is bound to ``device_id`` when that is an active device the user enrolled,
        and the same 15-minute access + 12-hour refresh pair is issued. The caller has
        already refused a revoked device (credentials first, T-0080).

        The user's row is locked and read again before the tokens are issued (``lock_user``,
        T-0077), so a concurrent password change either waits and revokes this session or
        has committed and is seen here. ``credential_bound`` (password, magic link: proofs a
        password change invalidates): ``AuthenticationError`` when the account was
        deactivated or its password changed since ``user`` was read, i.e. while the
        credential was being checked.

        With ``invite_ref`` the post-authentication hook then accepts that invite in the same
        request (T-0052): its outcome rides on the pair and never fails the sign-in."""
        seen = _credential_state(user)
        locked = lock_user(session, user.id, user.tenant_id)
        if credential_bound and (
            locked is None or not locked.is_active or _credential_state(locked) != seen
        ):
            raise AuthenticationError("account changed during sign-in")
        self._signed_in(user)
        session_id = uuid.uuid4()
        bound = self._bind_sign_in_device(session, user.id, user.tenant_id, session_id, device_id)
        pair = self._issue_pair(user.id, user.tenant_id, session_id, device_id=bound)
        if invite_ref is None:
            return pair
        return replace(pair, invite=run_post_authentication(session, user, invite_ref))

    def start_session_with_first_password(
        self,
        session: Session,
        user_id: uuid.UUID,
        password: str,
        *,
        device_id: uuid.UUID | None = None,
        breach_check: BreachCheck | None = None,
    ) -> TokenPair:
        """Set the **first** password of an account without credentials and sign it in, in
        the caller's unit of work (T-0059, diff §4.1: a brand-new invitee accepting the
        e-mailed invite). The caller has proved that the request comes from the account's
        mailbox; this is the only way a module sets a first password and gets a session.

        ``session`` is bound to the account's tenant. The T-0077 policy is checked first
        (``PasswordPolicyError``: nothing changed). The row is then locked and read again
        (``lock_user``): ``AuthenticationError`` when the account is gone or deactivated,
        ``CredentialsExistError`` when it has credentials by now (of two concurrent calls
        the second finds the first one's password), ``DeviceRevokedError`` for a revoked
        ``device_id``. Only then is the hash stored and the normal session issued
        (``start_session``). Caller commits."""
        user = session.scalars(select(User).where(User.id == user_id)).one_or_none()
        if user is None:
            raise AuthenticationError("user not active")
        normalised = check_password(
            password, email=user.email, settings=self.runtime.settings, breach_check=breach_check
        )
        password_hash = hash_password(normalised)
        locked = lock_user(session, user.id, user.tenant_id)
        if locked is None or not locked.is_active:
            raise AuthenticationError("user not active")
        if has_credentials(locked):
            raise CredentialsExistError("the account already has credentials")
        refuse_revoked_device(session, device_id)
        user.password_hash = password_hash
        user.password_changed_at = utcnow()
        session.flush()
        return self.start_session(session, user, device_id=device_id, credential_bound=True)

    def unlock(
        self,
        session: Session,
        *,
        refresh_token: str,
        pin: str,
        device_id: uuid.UUID | None = None,
        failed_offline: int = 0,
    ) -> TokenPair:
        """Renew a session of a device with a PIN: the device's refresh token *and* the PIN.

        Neither half works alone; a session without a device PIN is ``AuthenticationError``.
        ``failed_offline`` wrong PINs the device saw offline are counted first, with the same
        escalation. Wrong PINs raise ``PinGateError(PIN_INVALID)``; the 10th wrong PIN on the
        device (``PIN_POLICY.max_attempts``) and every attempt after it raise
        ``PinGateError(PIN_EXHAUSTED)`` until a sign-in on the device. Nothing is revoked.
        """
        claims = self._live_refresh_claims(refresh_token)
        self._refuse_revoked_device(session, claims, device_id)
        bound, device = self._session_device(session, claims)
        if bound is None or device is None or device.pin_hash is None:
            raise AuthenticationError("not a session of a device with a PIN")
        pin_hash = device.pin_hash
        subject = pin_failure_subject(claims.tenant_id, bound)
        policy = self.pin_lockout.policy
        attempt = self.pin_lockout.begin(subject, extra=failed_offline)
        if policy.exhausted(attempt):
            raise PinGateError(PIN_EXHAUSTED)
        if not verify_password(pin, pin_hash):
            if policy.max_attempts is not None and attempt >= policy.max_attempts:
                raise PinGateError(PIN_EXHAUSTED)
            raise PinGateError(PIN_INVALID)
        self.pin_lockout.succeeded(subject)
        return self._rotate(session, claims, device_id=bound)

    def _bind_sign_in_device(
        self,
        session: Session,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        device_id: uuid.UUID | None,
    ) -> uuid.UUID | None:
        """Bind a new session to the device it signs in on, if that is an active device the
        user enrolled (else ``None``: enrolment binds it later). A sign-in on the device
        clears its PIN failures and never touches its PIN."""
        if device_id is None or pin_device(session, device_id, user_id) is None:
            return None
        self.runtime.store.bind_device(session_id, device_id, self.runtime.tokens.ttl["refresh"])
        self.clear_pin_failures(tenant_id, device_id)
        return device_id

    def _session_device(
        self, session: Session, claims: TokenClaims
    ) -> tuple[uuid.UUID | None, Device | None]:
        """The session's device: the token's ``did`` claim, else the store's binding (a
        session bound by enrolment, before its first rotation), and its row. A session that
        names a device which is no longer an active device of the user is refused."""
        bound = claims.device_id or self.runtime.store.session_device(claims.session_id)
        if bound is None:
            return None, None
        bind_tenancy(session, TenancyContext(tenant_id=claims.tenant_id, user_id=claims.user_id))
        device = pin_device(session, bound, claims.user_id)
        if device is None:
            raise AuthenticationError("the session's device is not active")
        return bound, device

    def _idle_too_long(self, claims: TokenClaims) -> bool:
        """Idle = time since the session's last renewal (the refresh token's ``iat``)."""
        limit = timedelta(minutes=self.runtime.settings.pin_idle_minutes)
        return self.runtime.tokens.now() - claims.issued_at >= limit

    def _signed_in(self, user: User) -> None:
        """Every successful sign-in (password, magic link, Entra ID SSO) recomputes the
        internal flag, so a domain removed from the list takes effect at the next one. A
        refresh or PIN unlock continues a session and is not a sign-in."""
        refresh_internal_flag(user, self.runtime.settings.internal_email_domains)

    def _live_refresh_claims(self, refresh_token: str) -> TokenClaims:
        try:
            claims = self.runtime.tokens.verify(refresh_token, "refresh")
        except InvalidTokenError as exc:
            raise AuthenticationError("invalid refresh token") from exc
        if claims.expires_at <= self.runtime.tokens.now():
            # The session's end is exact: no clock leeway for a token only we consume.
            raise AuthenticationError("session ended")
        if self.runtime.store.is_revoked(claims.jti, claims.session_id):
            if self.runtime.store.revoked_by_device(claims.session_id):
                raise DeviceRevokedError("device revoked")
            raise AuthenticationError("session revoked")
        return claims

    def _refuse_revoked_device(
        self, session: Session, claims: TokenClaims, device_id: uuid.UUID | None
    ) -> None:
        """A refresh or unlock from a revoked device (header, or the session's own binding)
        kills the session and raises ``DeviceRevokedError`` before the token is spent."""
        bound = self.runtime.store.session_device(claims.session_id)
        candidates = {d for d in (claims.device_id, bound, device_id) if d is not None}
        if not candidates:
            return
        bind_tenancy(session, TenancyContext(tenant_id=claims.tenant_id, user_id=claims.user_id))
        for candidate in candidates:
            try:
                refuse_revoked_device(session, candidate)
            except DeviceRevokedError:
                self.runtime.store.revoke_device_session(
                    claims.session_id, self.runtime.tokens.ttl["refresh"]
                )
                raise

    def _rotate(
        self, session: Session, claims: TokenClaims, *, device_id: uuid.UUID | None = None
    ) -> TokenPair:
        if not self.runtime.store.consume_refresh(claims.jti):
            # Valid signature but already used (or never registered): assume a stolen
            # token is being replayed and kill the whole session.
            self._revoke_session(claims.session_id)
            raise AuthenticationError("refresh token reused")
        bind_tenancy(session, TenancyContext(tenant_id=claims.tenant_id, user_id=claims.user_id))
        try:
            load_active_user(session, claims, for_update=True)
        except AuthenticationError:
            self._revoke_session(claims.session_id)
            raise
        return self._issue_pair(
            claims.user_id,
            claims.tenant_id,
            claims.session_id,
            not_after=claims.expires_at,
            device_id=device_id,
        )

    def _pin_ttl(self) -> timedelta:
        return self.runtime.tokens.ttl["refresh"] + PIN_TTL_MARGIN

    def logout(self, access: TokenClaims) -> None:
        self.runtime.store.revoke_token(access.jti, access.remaining())
        self._revoke_session(access.session_id)

    def _revoke_session(self, session_id: uuid.UUID) -> None:
        self.runtime.store.revoke_session(session_id, self.runtime.tokens.ttl["refresh"])

    def _issue_pair(
        self,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        *,
        not_after: datetime | None = None,
        device_id: uuid.UUID | None = None,
    ) -> TokenPair:
        """A new pair. A new session ends at now + refresh TTL; a rotation passes the
        session's end as ``not_after``, so neither token outlives it. ``device_id`` (the
        session's bound device) becomes both tokens' ``did`` claim."""
        tokens = self.runtime.tokens
        refresh = tokens.issue(
            "refresh",
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            not_after=not_after,
            device_id=device_id,
        )
        access = tokens.issue(
            "access",
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            not_after=refresh.claims.expires_at,
            device_id=device_id,
        )
        self.runtime.store.register_refresh(
            refresh.claims.jti, session_id, refresh.claims.remaining()
        )
        return TokenPair(access=access, refresh=refresh)
