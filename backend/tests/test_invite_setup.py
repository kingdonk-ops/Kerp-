"""The invite e-mail, the ``setup_token`` and the first password at invite acceptance (T-0059,
diff §4.1 External invitees create their own password, §4.2, §4.6).

The mail queue is a capturing enqueuer (what core hands to the worker after a commit): nothing
is ever sent. Harness, users and projects are those of ``test_invites.py``. Synthetic data only.
"""

import hashlib
import logging
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.auth import service as auth_service
from app.core.auth.models import User
from app.core.auth.password_policy import PasswordPolicyError
from app.core.auth.revocation import RevocationUnavailableError
from app.core.auth.router import REFRESH_COOKIE
from app.core.auth.runtime import app_auth_runtime
from app.core.auth.service import AuthenticationError, AuthService, CredentialsExistError
from app.core.db import utcnow
from app.core.logging import REDACTED, JsonFormatter, scrub_sentry_event, scrub_text
from app.core.mail.queue import QueuedMail, mail_enqueuer, set_mail_enqueuer
from app.core.mail.templates import TEMPLATES, render
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy
from app.modules.admin import invites
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests import test_invites as harness
from tests.auth_support import T1, auth_app, make_settings, seed_user
from tests.db_support import database_url
from tests.test_invites import (
    ADMIN,
    DEPLOYMENT,
    EXT,
    NATIVE_ORIGIN,
    NEW,
    PAM,
    ROOT,
    ROOT_ADMIN,
    Api,
    _Pause,
    _together,
)

# The harness of ``test_invites.py``: the migrated database and the API with its users.
db = harness.db
api = harness.api

SETUP_PREVIEW = f"{ADMIN}/invites/setup-preview"
ACCEPT_WITH_PASSWORD = f"{ADMIN}/invites/accept-with-password"
RESET_REQUEST = "/api/v1/auth/password-reset/request"
RESET_CONFIRM = "/api/v1/auth/password-reset/confirm"
ME = "/api/v1/auth/me"
TEMPLATE = "admin.project_invite"
NEW_PASSWORD = "plum-Radio 41 kettle"  # synthetic
GONE = {"detail": "This invite is no longer valid", "code": "INVITE_GONE"}


class Outbox:
    """The mail enqueuer: the mails core hands to the queue once a request has committed."""

    def __init__(self) -> None:
        self.mails: list[QueuedMail] = []

    def __call__(self, mails: Sequence[QueuedMail]) -> None:
        self.mails.extend(mails)

    def invites(self) -> list[QueuedMail]:
        return [mail for mail in self.mails if mail.template_key == TEMPLATE]

    def link(self, index: int = -1) -> tuple[str, str]:
        """``(invite_token, setup_token)`` of an invite e-mail's link."""
        invite_token, _, setup_token = str(self.invites()[index].context["link"]).partition(".")
        return invite_token, setup_token


@pytest.fixture
def outbox() -> Iterator[Outbox]:
    earlier, box = mail_enqueuer(), Outbox()
    set_mail_enqueuer(box)
    yield box
    set_mail_enqueuer(earlier)


def _preview(api: Api, invite_token: str, setup_token: str) -> Any:
    body = {"invite_token": invite_token, "setup_token": setup_token}
    return api.client.post(SETUP_PREVIEW, json=body)


def _accept(
    api: Api, invite_token: str, setup_token: str, password: str = NEW_PASSWORD, **kwargs: Any
) -> Any:
    body = {"invite_token": invite_token, "setup_token": setup_token, "new_password": password}
    return api.client.post(ACCEPT_WITH_PASSWORD, json=body, **kwargs)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _password_hash(api: Api, email: str) -> str | None:
    account = api.account(email)
    assert account is not None
    return account.password_hash


# --- the invite e-mail and the setup token ----------------------------------------------------


def test_creating_an_invite_queues_the_e_mail_with_both_tokens(api: Api, outbox: Outbox) -> None:
    response = api.invite(NEW, "PER-03")
    assert response.status_code == 201, response.text
    created = response.json()
    [mail] = outbox.invites()  # handed to the queue after the commit; nothing is sent here
    invite_token, setup_token = outbox.link()
    assert mail.to == NEW
    assert invite_token == created["invite_token"]
    assert len(setup_token) >= 43
    assert setup_token != invite_token
    assert {k: v for k, v in mail.context.items() if k != "link"} == {
        "project_code": "P1",
        "project_name": "Synthetic one",
        "inviter_name": "Pam",
        "expires_at": mail.context["expires_at"],
    }
    [row] = api.rows(NEW)
    assert mail.context["expires_at"] == row.expires_at.strftime("%Y-%m-%d %H:%M UTC")
    rendered = render(
        TEMPLATES.get(TEMPLATE), mail.context, locale=None, app_name="KERP", web_origin=DEPLOYMENT
    )
    assert f"{DEPLOYMENT}/invite#{invite_token}.{setup_token}" in rendered.text
    assert setup_token not in rendered.subject
    for part in ("P1", "Synthetic one", "Pam", str(mail.context["expires_at"])):
        assert part in rendered.text
    # The inviter gets the copyable link with the invite token only, and never the setup token.
    assert created["invite_link"] == f"{DEPLOYMENT}/invite#{invite_token}"
    assert setup_token not in response.text
    assert "setup_token" not in response.text
    # Only its hash is stored, and nothing of it is audited or listed.
    assert row.setup_token_hash == _digest(setup_token)
    assert setup_token not in repr({c.name: getattr(row, c.name) for c in row.__table__.columns})
    assert setup_token not in repr(api.audit())
    assert row.setup_token_hash not in repr(api.audit())
    assert api.audit()[-1]["action"] == "create"
    assert api.audit()[-1]["email_queued"] is True
    assert "setup_token" not in repr(api.listed())


def test_a_rolled_back_invite_queues_no_e_mail(api: Api, outbox: Outbox) -> None:
    assert api.invite(NEW, "PER-09", "PER-02").status_code == 409  # needs confirm_sod
    assert outbox.invites() == []


# --- first password at acceptance ---------------------------------------------------------------


def test_a_brand_new_invitee_sets_a_password_joins_and_is_signed_in(
    api: Api, outbox: Outbox
) -> None:
    api.invited(NEW, "PER-03")
    invite_token, setup_token = outbox.link()
    preview = _preview(api, invite_token, setup_token)
    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "can_set_password": True,
        "project_code": "P1",
        "project_name": "Synthetic one",
    }
    assert preview.headers["cache-control"] == "no-store"
    assert api.personas(NEW) == set()
    assert _password_hash(api, NEW) is None

    response = _accept(api, invite_token, setup_token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["joined_project_id"] == str(api.p1)
    assert body["session_expires_at"]
    assert "refresh_token" not in body
    assert REFRESH_COOKIE in response.cookies
    assert response.headers["cache-control"] == "no-store"
    assert "setup_token" not in response.text
    assert NEW_PASSWORD not in response.text
    me = api.client.get(ME, headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == NEW
    assert api.personas(NEW) == {"PER-03"}
    [row] = api.rows(NEW)
    assert (row.status, row.setup_token_hash) == ("accepted", None)
    assert api.actions()[-2:] == ["first_password_set", "accept"]
    assert NEW_PASSWORD not in repr(api.audit())
    # Afterwards: e-mail + password. The link is used up for every endpoint.
    assert api.login(NEW, password=NEW_PASSWORD).status_code == 200
    assert _preview(api, invite_token, setup_token).status_code == 410
    again = _accept(api, invite_token, setup_token, "walnut 73 Lantern-sky")
    assert (again.status_code, again.json()) == (410, GONE)
    assert api.login(NEW, password="walnut 73 Lantern-sky").status_code == 401


def test_a_native_app_gets_the_refresh_token_in_the_body(api: Api, outbox: Outbox) -> None:
    api.invited(NEW)
    response = _accept(api, *outbox.link(), headers={"Origin": NATIVE_ORIGIN})
    assert response.status_code == 200, response.text
    assert response.json()["refresh_token"]
    assert REFRESH_COOKIE not in response.cookies


def test_the_copied_link_cannot_set_a_password(api: Api, outbox: Outbox) -> None:
    copied = api.invited(NEW)["invite_token"]  # what the inviter sees: no setup token
    assert api.client.post(SETUP_PREVIEW, json={"invite_token": copied}).status_code == 422
    body = {"invite_token": copied, "new_password": NEW_PASSWORD}
    assert api.client.post(ACCEPT_WITH_PASSWORD, json=body).status_code == 422
    for guess in (copied, "x", _digest(copied), api.rows(NEW)[0].setup_token_hash or ""):
        assert _preview(api, copied, guess).json() == GONE
        refused = _accept(api, copied, guess)
        assert (refused.status_code, refused.json()) == (410, GONE)
    assert api.login(NEW, copied, NEW_PASSWORD).status_code == 401  # no password: no sign-in
    assert _password_hash(api, NEW) is None
    assert api.personas(NEW) == set()
    assert api.rows(NEW)[0].status == "pending"
    assert api.actions().count("refuse:setup_token") == 8
    # The fallback: the T-0077 reset e-mail sets the password; signing in accepts the invite.
    assert api.client.post(RESET_REQUEST, json={"email": NEW}).status_code == 202
    reset = next(m for m in outbox.mails if m.template_key == "auth.password_reset")
    confirm = {"token": reset.context["token"], "new_password": NEW_PASSWORD}
    assert api.client.post(RESET_CONFIRM, json=confirm).status_code == 200
    assert api.login(NEW, copied, NEW_PASSWORD).json()["joined_project_id"] == str(api.p1)


def test_wrong_expired_revoked_and_unknown_tokens_get_the_neutral_410(
    api: Api, outbox: Outbox
) -> None:
    invite = api.invited(NEW)
    invite_token, setup_token = outbox.link()
    for bad in (("no-such-invite-token", setup_token), (invite_token, "no-such-setup-token")):
        assert (_preview(api, *bad).status_code, _preview(api, *bad).json()) == (410, GONE)
        assert (_accept(api, *bad).status_code, _accept(api, *bad).json()) == (410, GONE)
    api.expire(invite["id"])
    assert _preview(api, invite_token, setup_token).json() == GONE
    assert _accept(api, invite_token, setup_token).json() == GONE
    assert api.rows(NEW)[0].status == "expired"  # stored lazily, as in T-0052

    api.age(NEW)
    revoked = api.invited(EXT.replace("ext", "other"))
    other = outbox.link()
    assert api.revoke(revoked["id"]).status_code == 204
    assert api.rows("other@example.test")[0].setup_token_hash is None  # revoked with the invite
    assert _preview(api, *other).json() == GONE
    assert (_accept(api, *other).status_code, _accept(api, *other).json()) == (410, GONE)
    assert _password_hash(api, NEW) is None
    assert _password_hash(api, "other@example.test") is None
    reasons = [e["reason"] for e in api.audit() if e["action"] == "refuse"]
    assert {"setup_token", "expired", "revoked"} <= set(reasons)
    unmatched = api.audit(chain="project-invites")
    assert {e["stage"] for e in unmatched} == {"setup_preview", "accept_with_password"}
    assert all("user_id" not in e for e in unmatched)  # nobody is identified


def test_a_deactivated_account_or_a_deleted_project_is_gone(api: Api, outbox: Outbox) -> None:
    api.invited(NEW)
    link = outbox.link()
    with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
        auth_service.find_user_by_email(s, NEW).is_active = False  # type: ignore[union-attr]
    assert _preview(api, *link).json() == GONE
    assert (_accept(api, *link).status_code, _accept(api, *link).json()) == (410, GONE)
    assert _password_hash(api, NEW) is None
    assert api.actions()[-1] == "refuse:account_inactive"


def test_an_invite_to_a_deleted_project_is_gone_for_the_e_mailed_link(
    api: Api, outbox: Outbox
) -> None:
    with api.db.unit_of_work(ROOT) as s:
        gone = projects.create_project(s, ProjectCreate(code="GONE", name="Synthetic gone")).id
    api.invited(NEW, by=ROOT_ADMIN, project=gone)
    link = outbox.link()
    with api.db.unit_of_work(ROOT) as s:
        projects.delete_project(s, gone)
    assert _preview(api, *link).json() == GONE
    assert _accept(api, *link).status_code == 410
    assert _password_hash(api, NEW) is None
    assert api.actions(gone) == ["create", "refuse:project_deleted", "refuse:project_deleted"]


def test_an_unreachable_session_store_changes_nothing(
    api: Api, outbox: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.invited(NEW)
    link = outbox.link()

    def down(*_args: object, **_kwargs: object) -> None:
        raise RevocationUnavailableError("synthetic outage")

    monkeypatch.setattr(AuthService, "_issue_pair", down)
    assert _accept(api, *link).status_code == 503
    monkeypatch.undo()
    # The whole unit of work rolled back: no password, no membership, the link still works.
    assert _password_hash(api, NEW) is None
    assert api.personas(NEW) == set()
    assert _accept(api, *link).status_code == 200


def test_a_deployment_without_a_tenant_answers_gone(tmp_path: Path) -> None:
    settings = make_settings(database_url=database_url(tmp_path), default_tenant_id=None)
    app, _runtime = auth_app(settings)
    with TestClient(app) as client:
        body = {"invite_token": "i" * 43, "setup_token": "s" * 43}
        assert client.post(SETUP_PREVIEW, json=body).json() == GONE
        refused = client.post(ACCEPT_WITH_PASSWORD, json={**body, "new_password": NEW_PASSWORD})
        assert (refused.status_code, refused.json()) == (410, GONE)


def test_an_account_with_credentials_must_sign_in(api: Api, outbox: Outbox) -> None:
    api.invited(EXT)  # EXT has a password
    link = outbox.link()
    before = _password_hash(api, EXT)
    assert _preview(api, *link).json() == {"can_set_password": False}  # nothing about the project
    refused = _accept(api, *link)
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "INVITE_SIGN_IN_REQUIRED"
    assert REFRESH_COOKIE not in refused.cookies
    assert "access_token" not in refused.text
    assert _password_hash(api, EXT) == before
    assert api.personas(EXT) == set()
    assert api.rows(EXT)[0].status == "pending"
    assert api.actions()[-1] == "refuse:has_credentials"
    # Signing in as usual accepts the invite (the T-0052 carry-through); the old password stays.
    assert api.login(EXT, link[0]).json()["joined_project_id"] == str(api.p1)
    assert api.login(EXT, password=NEW_PASSWORD).status_code == 401


def test_a_company_account_linked_to_entra_never_gets_a_password(api: Api, outbox: Outbox) -> None:
    seed_user(api.db, T1, NEW, password=None, entra_oid="synthetic-oid", entra_tid="synthetic-tid")
    api.invited(NEW)
    assert _preview(api, *outbox.link()).json() == {"can_set_password": False}
    assert _accept(api, *outbox.link()).json()["code"] == "INVITE_SIGN_IN_REQUIRED"
    assert _password_hash(api, NEW) is None


def test_a_refused_password_leaves_the_invite_pending(api: Api, outbox: Outbox) -> None:
    api.invited(NEW)
    link = outbox.link()
    for weak, code in (("short", "PASSWORD_TOO_SHORT"), ("password123", "PASSWORD_TOO_COMMON")):
        refused = _accept(api, *link, weak)
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == code
        assert refused.json()["min_length"] == 8
        assert REFRESH_COOKIE not in refused.cookies
    [row] = api.rows(NEW)
    assert (row.status, row.setup_token_hash) == ("pending", _digest(link[1]))
    assert _password_hash(api, NEW) is None
    assert api.personas(NEW) == set()
    assert api.actions()[-2:] == ["refuse:password_too_short", "refuse:password_too_common"]
    accepted = _accept(api, *link)  # the same link still works
    assert accepted.status_code == 200, accepted.text
    assert api.personas(NEW) == {"PER-03"}


def test_a_re_invite_revokes_the_old_setup_token(api: Api, outbox: Outbox) -> None:
    api.invited(NEW, "PER-03")
    old = outbox.link()
    api.age(NEW)
    api.invited(NEW, "PER-02")
    new = outbox.link()
    assert old[1] != new[1]
    assert len(outbox.invites()) == 2
    assert api.rows(NEW)[0].setup_token_hash is None
    assert _preview(api, *old).json() == GONE
    assert (_accept(api, *old).status_code, _accept(api, *old).json()) == (410, GONE)
    assert _accept(api, new[0], old[1]).status_code == 410  # the old secret with the new token
    assert _accept(api, *new).status_code == 200
    assert api.personas(NEW) == {"PER-02"}


def test_a_re_send_rotates_the_setup_token_and_mails_the_new_link(api: Api, outbox: Outbox) -> None:
    invite = api.invited(NEW)
    old = outbox.link()
    api.age(NEW)
    response = api.resend(invite["id"])
    assert response.status_code == 200, response.text
    new = outbox.link()
    assert len(outbox.invites()) == 2
    assert outbox.invites()[-1].to == NEW
    assert new[0] == response.json()["invite_token"]
    assert old[0] != new[0]
    assert old[1] != new[1]
    assert new[1] not in response.text
    assert "setup_token" not in response.text
    assert response.json()["invite_link"] == f"{DEPLOYMENT}/invite#{new[0]}"
    [row] = api.rows(NEW)
    assert row.setup_token_hash == _digest(new[1])
    assert outbox.invites()[-1].context["expires_at"] == row.expires_at.strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    resent = api.audit()[-1]
    assert (resent["action"], resent["email_queued"]) == ("resend", True)
    assert new[1] not in repr(api.audit())
    for stale in (old, (new[0], old[1]), (old[0], new[1])):
        assert (_preview(api, *stale).status_code, _preview(api, *stale).json()) == (410, GONE)
        assert (_accept(api, *stale).status_code, _accept(api, *stale).json()) == (410, GONE)
    assert _password_hash(api, NEW) is None
    assert _preview(api, *new).json()["can_set_password"] is True
    assert _accept(api, *new).json()["joined_project_id"] == str(api.p1)
    assert api.login(NEW, password=NEW_PASSWORD).status_code == 200


def test_the_signed_in_accept_also_spends_the_setup_token(api: Api, outbox: Outbox) -> None:
    api.invited(EXT)
    assert api.accept(EXT, outbox.link()[0]).status_code == 200
    assert api.rows(EXT)[0].setup_token_hash is None


def test_an_address_the_mail_service_refuses_still_gets_the_copyable_link(
    api: Api, outbox: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise invites.InvalidAddressError("not a single e-mail address")

    monkeypatch.setattr(invites, "send_mail", refuse)
    assert api.invited(NEW)["invite_token"]
    assert outbox.invites() == []
    assert api.audit()[-1]["email_queued"] is False


# --- single use under concurrency ---------------------------------------------------------------


def test_two_parallel_accepts_with_password_one_200_one_410(
    api: Api, outbox: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.invited(NEW, "PER-03")
    link = outbox.link()
    calls = [
        lambda: _accept(api, *link, NEW_PASSWORD),
        lambda: _accept(api, *link, "walnut 73 Lantern-sky"),
    ]
    responses = _together(monkeypatch, *calls)
    assert sorted(r.status_code for r in responses) == [200, 410], [r.text for r in responses]
    loser = next(r for r in responses if r.status_code == 410)
    assert loser.json() == GONE
    assert REFRESH_COOKIE not in loser.headers.get("set-cookie", "")
    winner = responses.index(next(r for r in responses if r.status_code == 200))
    passwords = (NEW_PASSWORD, "walnut 73 Lantern-sky")
    assert api.login(NEW, password=passwords[winner]).status_code == 200
    assert api.login(NEW, password=passwords[1 - winner]).status_code == 401
    assert api.personas(NEW) == {"PER-03"}
    assert api.rows(NEW)[0].status == "accepted"
    assert api.actions() == ["create", "first_password_set", "accept", "refuse:race_used"]


@pytest.mark.parametrize(
    ("change", "status_code", "code", "reason"),
    [
        ({"password_hash": "scrypt$synthetic"}, 409, "INVITE_SIGN_IN_REQUIRED", "has_credentials"),
        ({"is_active": False}, 410, "INVITE_GONE", "account_inactive"),
    ],
)
def test_an_account_that_changed_while_the_invite_was_read_is_refused_by_core(
    api: Api,
    outbox: Outbox,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, Any],
    status_code: int,
    code: str,
    reason: str,
) -> None:
    """The account gets credentials (a password reset) or is deactivated after the request
    checked it: core auth re-reads the row under its lock and refuses; nothing is written."""
    api.invited(NEW)
    link = outbox.link()

    def changed() -> Any:
        with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
            user = auth_service.find_user_by_email(s, NEW)
            assert user is not None
            for name, value in change.items():
                setattr(user, name, value)
        return None

    first, _ = _Pause(monkeypatch).race(lambda: _accept(api, *link), changed)
    assert (first.status_code, first.json()["code"]) == (status_code, code)
    assert _password_hash(api, NEW) == change.get("password_hash")
    assert api.personas(NEW) == set()
    assert api.rows(NEW)[0].status == "pending"
    assert api.actions()[-1] == f"refuse:{reason}"


# --- core auth: the one way to set a first password and get a session ---------------------------


def test_core_sets_a_first_password_only_on_an_active_account_without_credentials(
    api: Api,
) -> None:
    service = AuthService(app_auth_runtime(api.client.app))  # type: ignore[arg-type]
    fresh = seed_user(api.db, T1, NEW, password=None)
    gone = seed_user(api.db, T1, "gone@example.test", password=None, is_active=False)

    def start(user_id: uuid.UUID, password: str = NEW_PASSWORD) -> Any:
        with api.db.unit_of_work(TenancyContext(tenant_id=T1)) as s:
            return service.start_session_with_first_password(s, user_id, password)

    with pytest.raises(PasswordPolicyError):
        start(fresh, "short")
    assert _password_hash(api, NEW) is None
    with pytest.raises(CredentialsExistError):
        start(api.users[EXT])
    with pytest.raises(AuthenticationError):
        start(gone)
    with pytest.raises(AuthenticationError):
        start(uuid.uuid4())
    pair = start(fresh)
    assert pair.access.claims.user_id == fresh
    assert _password_hash(api, NEW) is not None
    assert api.login(NEW, password=NEW_PASSWORD).status_code == 200
    with pytest.raises(CredentialsExistError):  # single use: never over an existing password
        start(fresh, "walnut 73 Lantern-sky")


# --- the narrow system path always restores the caller's context --------------------------------


def test_the_system_path_restores_the_session_context(
    api: Api, outbox: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = api.invited(EXT)["invite_token"]
    context = TenancyContext(tenant_id=T1, user_id=api.users[EXT])
    with api.db.unit_of_work(context) as s:
        user = s.get(User, api.users[EXT])
        assert user is not None
        now = utcnow()
        assert invites.preview_invite(s, token, user, now=now).project_code == "P1"
        assert session_tenancy(s) == context
        with pytest.raises(invites.InviteGoneError):
            invites.preview_invite(s, "no-such-token-0000", user, now=now)
        assert session_tenancy(s) == context
        with pytest.raises(invites.InviteGoneError):
            invites.accept_invite(s, "no-such-token-0000", user, now=now)
        assert session_tenancy(s) == context
        assert invites.accept_invite(s, token, user, now=now) == api.p1
        assert session_tenancy(s) == context
        assert user in s  # the caller's objects are attached again

    # The flush that ends the path fails: the session must not stay elevated.
    def boom() -> None:
        raise RuntimeError("synthetic flush failure")

    with api.db.unit_of_work(context) as s:
        path = invites._SystemPath(s, api.users[EXT])
        path.__enter__()
        assert session_tenancy(s) != context
        assert session_tenancy(s).is_system_admin
        monkeypatch.setattr(s, "flush", boom)
        with pytest.raises(RuntimeError, match="synthetic flush failure"):
            path.__exit__(None, None, None)
        monkeypatch.undo()
        assert session_tenancy(s) == context

    def unbound_after(call: Any) -> None:
        for s in api.db.session(None):  # the request's session of a caller not signed in
            call(s)
            assert session_tenancy(s) == TenancyContext(tenant_id=T1)

    link = outbox.link()

    def gone(s: Session) -> None:
        with pytest.raises(invites.InviteGoneError):  # EXT's invite is used by now
            invites.setup_preview(s, T1, *link, now=utcnow())

    unbound_after(gone)
    api.age(EXT)
    api.invited(NEW)
    new = outbox.link()
    unbound_after(lambda s: invites.setup_preview(s, T1, *new, now=utcnow()))


# --- never in a log line or an error report -----------------------------------------------------


def test_no_log_record_contains_a_setup_token_or_a_password(
    api: Api, outbox: Outbox, caplog: pytest.LogCaptureFixture
) -> None:
    api.headers(PAM)
    formatter = JsonFormatter()
    with caplog.at_level(logging.DEBUG):
        invite = api.invited(NEW)
        old = outbox.link()
        api.age(NEW)
        assert api.resend(invite["id"]).status_code == 200
        new = outbox.link()
        assert _preview(api, *old).status_code == 410
        assert _accept(api, *old).status_code == 410
        assert _accept(api, *new, "short").status_code == 422
        assert _preview(api, *new).status_code == 200
        assert _accept(api, *new).status_code == 200
    access = [r for r in caplog.records if r.name == "kerp.access"]
    assert {SETUP_PREVIEW, ACCEPT_WITH_PASSWORD} <= {r.__dict__["path"] for r in access}
    assert len(caplog.records) > len(access)  # other loggers were captured too
    for record in caplog.records:
        rendered = formatter.format(record) + record.getMessage() + repr(record.__dict__)
        for secret in (old[1], new[1], NEW_PASSWORD):
            assert secret not in rendered, record.name
    assert new[1] not in repr(outbox.invites())
    assert NEW not in repr(outbox.invites())


def test_setup_tokens_are_scrubbed_from_logs_and_error_reports() -> None:
    secret = "s3cret-Setup_token.value-123"
    body = {"invite_token": "i" * 20, "setup_token": secret, "new_password": secret}
    event = {
        "request": {"data": body, "headers": {"Setup-Token": secret}},
        "extra": {"note": f"setup_token={secret}&next=1", "frames": [{"vars": body}]},
        "breadcrumbs": [{"message": f'body {{"setup_token": "{secret}"}}'}],
    }
    scrubbed = scrub_sentry_event(event)
    assert secret not in repr(scrubbed)
    assert scrubbed["request"]["data"]["setup_token"] == REDACTED
    assert scrubbed["extra"]["note"] == f"setup_token={REDACTED}&next=1"
    record = logging.LogRecord("kerp.test", logging.INFO, __file__, 1, "x", (), None)
    record.__dict__.update(setup_token=secret, body=body)
    assert secret not in JsonFormatter().format(record)
    # An unquoted value runs to the next separator: a quote or a backslash inside it does not
    # end it (the tail must not leak).
    assert scrub_text('new_password=pa"ssw0rd-tail next') == f"new_password={REDACTED} next"
    assert scrub_text("setup_token=ab'cd\\ef, more") == f"setup_token={REDACTED}, more"
    assert scrub_text("password: it's-a-secret; done") == f"password: {REDACTED}; done"
