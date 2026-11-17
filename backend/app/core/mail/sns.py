"""The SES bounce/complaint endpoint's SNS checks (T-0058, diff §4.6 planner rule, PR #79).

A valid SNS signature alone does not prove a message is ours: anyone can sign messages of
their own topic. So, in this order, before anything is fetched or written:

1. The body is an SNS JSON message of a known ``Type``.
2. ``TopicArn`` equals ``KERP_MAIL_SNS_TOPIC_ARN`` exactly (else refused, nothing fetched).
3. ``SignatureVersion`` is ``2`` (SHA256withRSA) and ``SigningCertURL`` is
   ``https://sns.<KERP_AWS_REGION>.amazonaws.com/<...>.pem`` (exact host, no port, no user
   info, no query); the certificate is fetched only from there and cached per URL.
4. The signature verifies over the SNS canonical string.

A verified ``SubscriptionConfirmation`` (only possible for the configured topic) is confirmed
by fetching its ``SubscribeURL``, which must be on the same host. A verified ``Notification``
is applied under the deployment's tenant (``KERP_DEFAULT_TENANT_ID``) by the caller; no tenant
or account value of the payload is ever used. Refusals are logged with a reason code only,
never the body.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal, Protocol
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

logger = logging.getLogger("kerp.mail.sns")

MAX_BODY: Final = 256 * 1024  # SNS messages are at most 256 KiB
MAX_CERT: Final = 64 * 1024
FETCH_TIMEOUT_S: Final = 5.0
CERT_TTL_S: Final = 24 * 3600
CERT_CACHE_SIZE: Final = 16
MESSAGE_TYPES: Final = frozenset(
    {"Notification", "SubscriptionConfirmation", "UnsubscribeConfirmation"}
)
_NOTIFICATION_FIELDS: Final = ("Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type")
_SUBSCRIPTION_FIELDS: Final = (
    "Message",
    "MessageId",
    "SubscribeURL",
    "Timestamp",
    "Token",
    "TopicArn",
    "Type",
)

Outcome = Literal["notification", "confirmed", "ignored"]


class SnsRefusedError(Exception):
    """The message is not a verified message of our topic. ``reason`` is a short code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SnsFetcher(Protocol):
    """HTTPS GETs to SNS: the signing certificate and the subscription confirmation."""

    def get(self, url: str, *, max_bytes: int) -> bytes: ...


class HttpsSnsFetcher:
    """Production fetcher (stdlib, verified TLS, short timeout, size cap)."""

    def get(self, url: str, *, max_bytes: int) -> bytes:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
            data: bytes = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise SnsRefusedError("sns_response_too_large")
        return data


@dataclass(frozen=True, slots=True)
class VerifiedMessage:
    type: str
    message_id: str
    message: str
    subscribe_url: str | None


def sns_host(region: str) -> str:
    return f"sns.{region}.amazonaws.com"


def check_sns_url(url: object, region: str) -> str:
    """``url`` if it is ``https://sns.<region>.amazonaws.com/...`` exactly, else refused."""
    if not isinstance(url, str) or len(url) > 2048:
        raise SnsRefusedError("sns_url_invalid")
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        raise SnsRefusedError("sns_url_invalid") from None
    if (
        parts.scheme != "https"
        or parts.netloc.lower() != sns_host(region)
        or parts.hostname != sns_host(region)
        or port is not None
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or not parts.path.startswith("/")
    ):
        raise SnsRefusedError("sns_url_foreign_host")
    return url


def canonical_string(message: Mapping[str, Any]) -> bytes:
    fields = _NOTIFICATION_FIELDS if message["Type"] == "Notification" else _SUBSCRIPTION_FIELDS
    lines: list[str] = []
    for name in fields:
        value = message.get(name)
        if value is None:
            if name == "Subject":
                continue
            raise SnsRefusedError("sns_field_missing")
        if not isinstance(value, str):
            raise SnsRefusedError("sns_field_invalid")
        lines += [name, value]
    return ("\n".join(lines) + "\n").encode()


class SnsVerifier:
    """Checks SNS messages for one topic in one region; caches signing keys per URL."""

    def __init__(
        self,
        *,
        topic_arn: str | None,
        region: str,
        fetcher: SnsFetcher | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.topic_arn = topic_arn
        self.region = region
        self.fetcher: SnsFetcher = fetcher or HttpsSnsFetcher()
        self._clock = clock
        self._keys: dict[str, tuple[float, rsa.RSAPublicKey]] = {}
        self._lock = threading.Lock()

    def parse(self, body: bytes) -> dict[str, Any]:
        if len(body) > MAX_BODY:
            raise SnsRefusedError("sns_body_too_large")
        try:
            message = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise SnsRefusedError("sns_not_json") from None
        if not isinstance(message, dict) or message.get("Type") not in MESSAGE_TYPES:
            raise SnsRefusedError("sns_unknown_type")
        return message

    def verify(self, body: bytes) -> VerifiedMessage:
        """Steps 1-4 of the module docstring; ``SnsRefusedError`` at the first that fails."""
        message = self.parse(body)
        if self.topic_arn is None or message.get("TopicArn") != self.topic_arn:
            raise SnsRefusedError("sns_foreign_topic")
        if message.get("SignatureVersion") != "2":
            raise SnsRefusedError("sns_signature_version")
        cert_url = check_sns_url(message.get("SigningCertURL"), self.region)
        if not urlsplit(cert_url).path.endswith(".pem"):
            raise SnsRefusedError("sns_url_foreign_host")
        try:
            signature = base64.b64decode(str(message.get("Signature", "")), validate=True)
        except (binascii.Error, ValueError):
            raise SnsRefusedError("sns_signature_invalid") from None
        data = canonical_string(message)
        try:
            self._key(cert_url).verify(signature, data, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            raise SnsRefusedError("sns_signature_invalid") from None
        subscribe_url = message.get("SubscribeURL")
        return VerifiedMessage(
            type=str(message["Type"]),
            message_id=str(message.get("MessageId", "")),
            message=str(message.get("Message", "")),
            subscribe_url=subscribe_url if isinstance(subscribe_url, str) else None,
        )

    def confirm_subscription(self, verified: VerifiedMessage) -> None:
        """Confirm a verified subscription of our topic (``SubscribeURL`` host-checked)."""
        url = check_sns_url(verified.subscribe_url, self.region)
        try:
            self.fetcher.get(url, max_bytes=MAX_CERT)
        except SnsRefusedError:
            raise
        except Exception:
            raise SnsRefusedError("sns_confirmation_failed") from None

    def _key(self, url: str) -> rsa.RSAPublicKey:
        now = self._clock()
        with self._lock:
            cached = self._keys.get(url)
            if cached is not None and now - cached[0] < CERT_TTL_S:
                return cached[1]
        try:
            pem = self.fetcher.get(url, max_bytes=MAX_CERT)
            cert = x509.load_pem_x509_certificate(pem)
        except SnsRefusedError:
            raise
        except Exception:
            raise SnsRefusedError("sns_certificate_unavailable") from None
        if cert.not_valid_after_utc < datetime.now(UTC):
            raise SnsRefusedError("sns_certificate_expired")
        key = cert.public_key()
        if not isinstance(key, rsa.RSAPublicKey):
            raise SnsRefusedError("sns_certificate_invalid")
        with self._lock:
            if len(self._keys) >= CERT_CACHE_SIZE:
                self._keys.pop(next(iter(self._keys)))
            self._keys[url] = (now, key)
        return key


def handle(verifier: SnsVerifier, body: bytes) -> tuple[Outcome, dict[str, Any] | None]:
    """Verify ``body``; confirm our subscription, or return the SES event to apply.

    Raises ``SnsRefusedError`` (logged here with its reason, never the body)."""
    try:
        verified = verifier.verify(body)
        if verified.type == "SubscriptionConfirmation":
            verifier.confirm_subscription(verified)
            logger.warning("SNS subscription to the mail topic confirmed")
            return "confirmed", None
        if verified.type != "Notification":
            return "ignored", None
        try:
            event = json.loads(verified.message)
        except ValueError:
            raise SnsRefusedError("sns_message_not_json") from None
        if not isinstance(event, dict):
            raise SnsRefusedError("sns_message_not_json")
        return "notification", event
    except SnsRefusedError as exc:
        logger.warning("SNS message refused: %s", exc.reason)
        raise
