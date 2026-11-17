"""SES bounces and complaints over SNS, and the mail admin API (T-0058, diff §4.6 planner rule).

The signing certificate is generated at test time and served by a fake fetcher, so a
"correctly signed" message here is signed exactly the way SNS signs (SignatureVersion 2,
SHA256withRSA over the canonical string). Synthetic data only.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import urllib.request
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.audit.service import chain_entries
from app.core.db import Database
from app.core.mail import sns as sns_module
from app.core.mail.models import MailSend, MailSuppression
from app.core.mail.queue import set_mail_enqueuer
from app.core.mail.router import SNS_PATH, app_sns_verifier, install_sns_verifier
from app.core.mail.service import send_mail, suppress
from app.core.mail.sns import (
    SnsRefusedError,
    SnsVerifier,
    canonical_string,
    check_sns_url,
)
from app.core.tenancy.context import TenancyContext
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, T1, T2, auth_app, make_settings, seed_user
from tests.db_support import database_url

REGION = "ap-southeast-2"
TOPIC = f"arn:aws:sns:{REGION}:123456789012:kerp-mail"
FOREIGN_TOPIC = f"arn:aws:sns:{REGION}:999999999999:attacker-topic"
CERT_URL = f"https://sns.{REGION}.amazonaws.com/SimpleNotificationService-synthetic.pem"
SUBSCRIBE_URL = (
    f"https://sns.{REGION}.amazonaws.com/?Action=ConfirmSubscription&TopicArn={TOPIC}&Token=x"
)
BOUNCED = "bounced.user@example.test"
SCOPE = TenancyContext(tenant_id=T1)
MAIL_API = "/api/v1/mail"


def _key_and_cert() -> tuple[rsa.RSAPrivateKey, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM)


KEY, CERT_PEM = _key_and_cert()


class FakeFetcher:
    """Serves the synthetic certificate; records every URL it is asked for."""

    def __init__(self, pem: bytes = CERT_PEM) -> None:
        self.pem = pem
        self.urls: list[str] = []

    def get(self, url: str, *, max_bytes: int) -> bytes:
        self.urls.append(url)
        return self.pem if url.endswith(".pem") else b"<ConfirmSubscriptionResponse/>"


def _signed(message: dict[str, Any], key: rsa.RSAPrivateKey = KEY) -> dict[str, Any]:
    message = {"SignatureVersion": "2", "SigningCertURL": CERT_URL, **message}
    signature = key.sign(canonical_string(message), padding.PKCS1v15(), hashes.SHA256())
    return {**message, "Signature": base64.b64encode(signature).decode()}


def _notification(event: dict[str, Any], topic: str = TOPIC) -> dict[str, Any]:
    return _signed(
        {
            "Type": "Notification",
            "MessageId": str(uuid.uuid4()),
            "TopicArn": topic,
            "Message": json.dumps(event),
            "Timestamp": "2026-11-17T01:02:03.000Z",
        }
    )


def _bounce(message_id: str = "ses-1", bounce_type: str = "Permanent") -> dict[str, Any]:
    return {
        "notificationType": "Bounce",
        "bounce": {
            "bounceType": bounce_type,
            "bounceSubType": "General",
            "bouncedRecipients": [{"emailAddress": BOUNCED}],
        },
        "mail": {"messageId": message_id, "destination": [BOUNCED]},
    }


def _confirmation(topic: str = TOPIC) -> dict[str, Any]:
    return _signed(
        {
            "Type": "SubscriptionConfirmation",
            "MessageId": str(uuid.uuid4()),
            "Token": "synthetic-token",
            "TopicArn": topic,
            "Message": "You have chosen to subscribe to the topic.",
            "SubscribeURL": SUBSCRIBE_URL,
            "Timestamp": "2026-11-17T01:02:03.000Z",
        }
    )


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


class Sns:
    def __init__(self, client: TestClient, db: Database, fetcher: FakeFetcher) -> None:
        self.client, self.db, self.fetcher = client, db, fetcher

    def post(self, message: dict[str, Any] | bytes) -> int:
        body = message if isinstance(message, bytes) else json.dumps(message).encode()
        headers = {"Content-Type": "text/plain; charset=UTF-8", "x-amz-sns-message-type": "x"}
        return self.client.post(SNS_PATH, content=body, headers=headers).status_code

    def suppressed(self) -> list[MailSuppression]:
        with self.db.unit_of_work(SCOPE) as session:
            rows = list(session.scalars(select(MailSuppression)))
            session.expunge_all()
            return rows


@pytest.fixture
def sns(tmp_path: Path, db: Database) -> Iterator[Sns]:
    settings = make_settings(database_url=database_url(tmp_path), mail_sns_topic_arn=TOPIC)
    app, _ = auth_app(settings)
    fetcher = FakeFetcher()
    install_sns_verifier(app, SnsVerifier(topic_arn=TOPIC, region=REGION, fetcher=fetcher))
    with TestClient(app) as client:
        yield Sns(client, db, fetcher)


def _sent(db: Database, message_id: str) -> uuid.UUID:
    """A send-log row that SES accepted as ``message_id``."""
    set_mail_enqueuer(None)
    with db.unit_of_work(SCOPE) as session:
        send_id = send_mail(
            session, "auth.magic_link", BOUNCED, {"token": "t" * 32, "expires_minutes": 15}
        )
        row = session.get(MailSend, send_id)
        assert row is not None
        row.status, row.provider_message_id = "sent", message_id
    return send_id


def _status(db: Database, send_id: uuid.UUID) -> str:
    with db.unit_of_work(SCOPE) as session:
        row = session.get(MailSend, send_id)
        assert row is not None
        return row.status


# --- accepted -------------------------------------------------------------------------------


def test_a_signed_hard_bounce_of_our_topic_suppresses_the_address(sns: Sns) -> None:
    send_id = _sent(sns.db, "ses-1")
    assert sns.post(_notification(_bounce())) == 204
    [row] = sns.suppressed()
    assert (row.tenant_id, row.reason, row.detail) == (T1, "bounce", "Permanent/General")
    assert row.recipient_masked == "b***@example.test"
    assert _status(sns.db, send_id) == "bounced"
    assert sns.fetcher.urls == [CERT_URL]
    # The certificate is cached: a second message fetches nothing.
    assert sns.post(_notification(_bounce("ses-2"))) == 204
    assert sns.fetcher.urls == [CERT_URL]
    assert len(sns.suppressed()) == 1  # idempotent per address


def test_a_complaint_suppresses_and_marks_the_send(sns: Sns) -> None:
    send_id = _sent(sns.db, "ses-9")
    event = {
        "notificationType": "Complaint",
        "complaint": {
            "complainedRecipients": [{"emailAddress": BOUNCED}],
            "complaintFeedbackType": "abuse",
        },
        "mail": {"messageId": "ses-9"},
    }
    assert sns.post(_notification(event)) == 204
    [row] = sns.suppressed()
    assert (row.reason, row.detail) == ("complaint", "abuse")
    assert _status(sns.db, send_id) == "complained"


def test_soft_bounces_and_other_events_suppress_nothing(sns: Sns) -> None:
    assert sns.post(_notification(_bounce(bounce_type="Transient"))) == 204
    assert sns.post(_notification({"notificationType": "Delivery", "mail": {}})) == 204
    assert sns.post(_notification({"eventType": "Bounce", "bounce": "garbage"})) == 204
    assert sns.suppressed() == []


def test_a_payload_naming_another_tenant_still_writes_under_the_deployment_tenant(
    sns: Sns,
) -> None:
    event = _bounce()
    event["mail"]["tags"] = {"tenant_id": [str(T2)]}
    event["tenant_id"] = str(T2)
    event["mail"]["sendingAccountId"] = "999999999999"
    assert sns.post(_notification(event)) == 204
    [row] = sns.suppressed()
    assert row.tenant_id == T1
    with sns.db.unit_of_work(TenancyContext(tenant_id=T2)) as session:
        assert session.scalars(select(MailSuppression)).all() == []


def test_our_subscription_confirmation_is_confirmed(sns: Sns) -> None:
    assert sns.post(_confirmation()) == 204
    assert sns.fetcher.urls == [CERT_URL, SUBSCRIBE_URL]


def test_unsubscribe_confirmations_are_ignored(sns: Sns) -> None:
    message = _confirmation()
    message = _signed(
        {
            **{k: v for k, v in message.items() if k != "Signature"},
            "Type": "UnsubscribeConfirmation",
        }
    )
    assert sns.post(message) == 204
    assert sns.fetcher.urls == [CERT_URL]


# --- refused --------------------------------------------------------------------------------


def test_a_correctly_signed_message_of_a_foreign_topic_is_refused(
    sns: Sns, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="kerp.mail.sns"):
        assert sns.post(_notification(_bounce(), topic=FOREIGN_TOPIC)) == 403
    assert sns.suppressed() == []
    assert sns.fetcher.urls == []  # refused before any fetch
    assert "sns_foreign_topic" in caplog.text
    assert BOUNCED not in caplog.text
    assert "bounceType" not in caplog.text


def test_a_foreign_subscription_confirmation_is_refused_and_never_fetched(sns: Sns) -> None:
    assert sns.post(_confirmation(topic=FOREIGN_TOPIC)) == 403
    assert sns.fetcher.urls == []


def test_a_bad_signature_is_refused(sns: Sns) -> None:
    other_key, _ = _key_and_cert()
    message = _notification(_bounce())
    forged = _signed({k: v for k, v in message.items() if k != "Signature"}, key=other_key)
    assert sns.post(forged) == 403
    tampered = {**message, "Message": json.dumps(_bounce("ses-x"))}
    assert sns.post(tampered) == 403
    assert sns.post({**message, "Signature": "not base64!"}) == 403
    assert sns.suppressed() == []


@pytest.mark.parametrize(
    "url",
    [
        "https://sns.ap-southeast-2.amazonaws.com.evil.test/cert.pem",
        "https://evil.test/sns.ap-southeast-2.amazonaws.com/cert.pem",
        "http://sns.ap-southeast-2.amazonaws.com/cert.pem",
        "https://sns.eu-central-1.amazonaws.com/cert.pem",
        "https://sns.ap-southeast-2.amazonaws.com:8443/cert.pem",
        "https://user@sns.ap-southeast-2.amazonaws.com/cert.pem",
        "https://xsns.ap-southeast-2.amazonaws.com/cert.pem",
        "https://sns.ap-southeast-2.amazonaws.com/cert.txt",
    ],
)
def test_a_signing_certificate_off_the_regional_sns_host_is_refused(sns: Sns, url: str) -> None:
    message = {
        "Type": "Notification",
        "MessageId": "m-1",
        "TopicArn": TOPIC,
        "Message": json.dumps(_bounce()),
        "Timestamp": "2026-11-17T01:02:03.000Z",
        "SigningCertURL": url,
    }
    assert sns.post(_signed(message)) == 403
    assert sns.fetcher.urls == []
    assert sns.suppressed() == []


def test_signature_version_1_and_malformed_bodies_are_refused(sns: Sns) -> None:
    message = _notification(_bounce())
    assert sns.post({**message, "SignatureVersion": "1"}) == 403
    assert sns.post(b"not json") == 403
    assert sns.post({"Type": "Something"}) == 403
    assert sns.post(b"[1, 2]") == 403
    assert sns.post({k: v for k, v in message.items() if k != "Message"}) == 403
    not_json = _signed(
        {**{k: v for k, v in message.items() if k != "Signature"}, "Message": "plain text"}
    )
    assert sns.post(not_json) == 403
    assert sns.suppressed() == []


def test_an_unreachable_or_expired_certificate_refuses(tmp_path: Path) -> None:
    class Down(FakeFetcher):
        def get(self, url: str, *, max_bytes: int) -> bytes:
            raise OSError("unreachable")

    body = json.dumps(_notification(_bounce())).encode()
    with pytest.raises(SnsRefusedError, match="unavailable"):
        SnsVerifier(topic_arn=TOPIC, region=REGION, fetcher=Down()).verify(body)
    with pytest.raises(SnsRefusedError, match="unavailable"):
        SnsVerifier(topic_arn=TOPIC, region=REGION, fetcher=FakeFetcher(b"junk")).verify(body)


def test_without_a_configured_topic_everything_is_refused() -> None:
    body = json.dumps(_notification(_bounce())).encode()
    fetcher = FakeFetcher()
    with pytest.raises(SnsRefusedError, match="foreign_topic"):
        SnsVerifier(topic_arn=None, region=REGION, fetcher=fetcher).verify(body)
    assert fetcher.urls == []


def test_url_check_is_exact() -> None:
    assert check_sns_url(CERT_URL, REGION) == CERT_URL
    for bad in (None, 42, "x" * 3000, "https://sns.ap-southeast-2.amazonaws.com:bad/x.pem"):
        with pytest.raises(SnsRefusedError):
            check_sns_url(bad, REGION)


def test_the_endpoint_needs_a_database_and_tenant(tmp_path: Path) -> None:
    app, _ = auth_app(make_settings(mail_sns_topic_arn=TOPIC, database_url=None))  # no DB
    verifier = app_sns_verifier(app)
    assert (verifier.topic_arn, verifier.region) == (TOPIC, REGION)
    assert app_sns_verifier(app) is verifier
    with TestClient(app) as client:
        assert client.post(SNS_PATH, content=b"{}").status_code == 503


# --- admin API ------------------------------------------------------------------------------


class Admin:
    def __init__(self, client: TestClient, db: Database) -> None:
        from app.core.auth.access import SYSTEM_ADMIN
        from app.modules.admin import service as admin

        self.client, self.db = client, db
        alice, self.bob = seed_user(db, T1, ALICE), seed_user(db, T1, BOB)
        with db.unit_of_work(TenancyContext(tenant_id=T1, is_system_admin=True)) as session:
            admin.grant(session, alice, SYSTEM_ADMIN, actor_id=None)
        self.alice = alice

    def headers(self, email: str) -> dict[str, str]:
        response = self.client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def admin(tmp_path: Path, db: Database) -> Iterator[Admin]:
    app, _ = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        yield Admin(client, db)


def test_only_system_admins_read_the_send_log_and_suppressions(admin: Admin) -> None:
    send_id = _sent(admin.db, "ses-1")
    with admin.db.unit_of_work(SCOPE) as session:
        suppress(session, BOUNCED, "bounce", detail="Permanent/General")
    for path in ("/sends", "/suppressions"):
        assert admin.client.get(MAIL_API + path).status_code == 401
        assert admin.client.get(MAIL_API + path, headers=admin.headers(BOB)).status_code == 403
    alice = admin.headers(ALICE)
    sends = admin.client.get(f"{MAIL_API}/sends", headers=alice).json()
    assert [s["id"] for s in sends] == [str(send_id)]
    assert sends[0]["recipient_masked"] == "b***@example.test"
    assert BOUNCED not in json.dumps(sends)
    assert "t" * 32 not in json.dumps(sends)
    filtered = admin.client.get(f"{MAIL_API}/sends?status=failed", headers=alice).json()
    assert filtered == []
    assert admin.client.get(f"{MAIL_API}/sends?status=nope", headers=alice).status_code == 422
    [entry] = admin.client.get(f"{MAIL_API}/suppressions", headers=alice).json()
    assert (entry["reason"], entry["recipient_masked"]) == ("bounce", "b***@example.test")


def test_a_system_admin_removes_a_suppression_audited(admin: Admin) -> None:
    with admin.db.unit_of_work(SCOPE) as session:
        row_id = suppress(session, BOUNCED, "complaint").id
    url = f"{MAIL_API}/suppressions/{row_id}"
    assert admin.client.delete(url, headers=admin.headers(BOB)).status_code == 403
    alice = admin.headers(ALICE)
    assert admin.client.delete(url, headers=alice).status_code == 204
    assert admin.client.get(f"{MAIL_API}/suppressions", headers=alice).json() == []
    assert admin.client.delete(url, headers=alice).status_code == 404  # already removed
    unknown = f"{MAIL_API}/suppressions/{uuid.uuid4()}"
    assert admin.client.delete(unknown, headers=alice).status_code == 404
    with admin.db.unit_of_work(SCOPE) as session:
        [entry] = chain_entries(session, f"mail-suppression:{row_id}")
        assert entry.payload["action"] == "remove"
        assert entry.created_by == admin.alice
        assert BOUNCED not in json.dumps(entry.payload)
        # Re-suppressing after a removal creates a new live row.
        assert suppress(session, BOUNCED, "bounce").id != row_id


def test_another_tenants_suppression_is_not_found(admin: Admin) -> None:
    with admin.db.unit_of_work(TenancyContext(tenant_id=T2)) as session:
        foreign = suppress(session, BOUNCED, "bounce").id
    url = f"{MAIL_API}/suppressions/{foreign}"
    assert admin.client.delete(url, headers=admin.headers(ALICE)).status_code == 404


# --- verifier edges -------------------------------------------------------------------------


def _verifier(fetcher: FakeFetcher, clock: list[float] | None = None) -> SnsVerifier:
    ticks = clock if clock is not None else [0.0]
    return SnsVerifier(topic_arn=TOPIC, region=REGION, fetcher=fetcher, clock=lambda: ticks[0])


def test_certificates_are_cached_per_url_and_refetched_after_a_day() -> None:
    fetcher, clock = FakeFetcher(), [0.0]
    verifier = _verifier(fetcher, clock)
    body = json.dumps(_notification(_bounce())).encode()
    verifier.verify(body)
    verifier.verify(body)
    assert fetcher.urls == [CERT_URL]
    clock[0] = 24 * 3600 + 1
    verifier.verify(body)
    assert fetcher.urls == [CERT_URL, CERT_URL]


def test_the_certificate_cache_is_bounded() -> None:
    verifier = _verifier(FakeFetcher())
    for n in range(sns_module.CERT_CACHE_SIZE + 2):
        url = f"https://sns.{REGION}.amazonaws.com/cert-{n}.pem"
        message = {**_notification(_bounce()), "SigningCertURL": url}
        unsigned = {k: v for k, v in message.items() if k != "Signature"}
        verifier.verify(json.dumps(_signed(unsigned)).encode())
    assert len(verifier._keys) == sns_module.CERT_CACHE_SIZE


def test_an_expired_or_non_rsa_certificate_is_refused() -> None:
    body = json.dumps(_notification(_bounce())).encode()
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .serial_number(x509.random_serial_number())
    )
    expired = (
        builder.public_key(KEY.public_key())
        .not_valid_before(now - timedelta(days=10))
        .not_valid_after(now - timedelta(days=1))
        .sign(KEY, hashes.SHA256())
    )
    with pytest.raises(SnsRefusedError, match="expired"):
        _verifier(FakeFetcher(expired.public_bytes(serialization.Encoding.PEM))).verify(body)
    ec_key = ec.generate_private_key(ec.SECP256R1())
    ec_cert = (
        builder.public_key(ec_key.public_key())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(ec_key, hashes.SHA256())
    )
    with pytest.raises(SnsRefusedError, match="certificate_invalid"):
        _verifier(FakeFetcher(ec_cert.public_bytes(serialization.Encoding.PEM))).verify(body)


class NoConfirm(FakeFetcher):
    def get(self, url: str, *, max_bytes: int) -> bytes:
        if not url.endswith(".pem"):
            raise OSError("unreachable")
        return super().get(url, max_bytes=max_bytes)


def test_a_failed_subscription_confirmation_is_refused() -> None:
    with pytest.raises(SnsRefusedError, match="confirmation_failed"):
        sns_module.handle(_verifier(NoConfirm()), json.dumps(_confirmation()).encode())


def test_oversized_bodies_are_refused_unread() -> None:
    with pytest.raises(SnsRefusedError, match="too_large"):
        _verifier(FakeFetcher()).verify(b" " * (sns_module.MAX_BODY + 1))


def test_the_https_fetcher_caps_the_response(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, float]] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> io.BytesIO:
        seen.append((request.full_url, timeout))
        return io.BytesIO(b"x" * 10)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    fetcher = sns_module.HttpsSnsFetcher()
    assert fetcher.get(CERT_URL, max_bytes=10) == b"x" * 10
    with pytest.raises(SnsRefusedError, match="too_large"):
        fetcher.get(CERT_URL, max_bytes=9)
    assert seen[0] == (CERT_URL, sns_module.FETCH_TIMEOUT_S)
