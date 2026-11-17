"""Mail transports behind one protocol (diff §4.6): SES, a fake (tests) and a log (development).

``MailTransport.send`` hands one rendered message to the provider and returns its message id.
It raises ``TransientMailError`` when trying again later may work (throttling, a provider
outage, a network error) and ``PermanentMailError`` when it will not (rejected message,
unverified sender). The worker retries the first with back-off and logs the second as
``failed`` (``service.deliver``).

* ``SesTransport``: AWS SES v2 in the deployment's region (``KERP_AWS_REGION``), the sender
  address and configuration set from deployment configuration, credentials from the AWS
  default chain (instance role / secret store), never from settings or the repo. ``boto3``
  is imported lazily, in the worker only.
* ``FakeTransport``: in memory and inspectable; scripted failures for tests.
* ``LogTransport``: development only. It logs the whole message (links included, so a
  developer can follow them) and refuses to be built in production.
"""

from __future__ import annotations

import logging
import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol

from app.core.config import Settings

if TYPE_CHECKING:
    from app.core.mail.templates import RenderedMail

logger = logging.getLogger("kerp.mail")

# SES / AWS error codes worth another try; every other client error is permanent.
TRANSIENT_CODES: Final = frozenset(
    {
        "Throttling",
        "ThrottlingException",
        "TooManyRequestsException",
        "LimitExceededException",
        "ServiceUnavailable",
        "ServiceUnavailableException",
        "InternalFailure",
        "InternalServiceError",
        "RequestTimeout",
        "RequestTimeoutException",
    }
)


class MailConfigError(RuntimeError):
    """Mail is not configured the way this environment needs (refused at start-up)."""


class MailTransportError(Exception):
    """A send that did not happen. ``code`` is a short provider code, never message content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code[:64]


class TransientMailError(MailTransportError):
    """Try again later."""


class PermanentMailError(MailTransportError):
    """Do not try again."""


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    to: str
    rendered: RenderedMail
    template_key: str


class SesClient(Protocol):
    """The part of the boto3 ``sesv2`` client the transport uses."""

    def send_email(self, **request: object) -> Mapping[str, object]: ...


class MailTransport(Protocol):
    def send(self, message: OutboundMessage) -> str:
        """Send ``message``; return the provider's message id."""
        ...


class FakeTransport:
    """Tests: keeps every message; ``fail_with`` queues errors raised by the next sends."""

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []
        self.failures: deque[MailTransportError] = deque()

    def fail_with(self, *errors: MailTransportError) -> None:
        self.failures.extend(errors)

    def send(self, message: OutboundMessage) -> str:
        if self.failures:
            raise self.failures.popleft()
        self.sent.append(message)
        return f"fake-{uuid.uuid4()}"


class LogTransport:
    """Development: writes the message to the log instead of sending it."""

    def __init__(self, settings: Settings) -> None:
        if settings.environment == "production":
            raise MailConfigError("the log mail transport never runs in production")

    def send(self, message: OutboundMessage) -> str:
        message_id = f"log-{uuid.uuid4()}"
        logger.warning(
            "e-mail (development log transport, never used in production) %s to %s: %s\n%s",
            message.template_key,
            message.to,
            message.rendered.subject,
            message.rendered.text,
        )
        return message_id


class SesTransport:
    """AWS SES v2 ``SendEmail`` (simple content: subject, text, HTML)."""

    def __init__(
        self,
        *,
        region: str,
        from_address: str,
        configuration_set: str | None = None,
        client: SesClient | None = None,
    ) -> None:
        self.region = region
        self.from_address = from_address
        self.configuration_set = configuration_set
        self._client = client

    def _ses(self) -> SesClient:
        if self._client is None:
            import boto3  # heavy: worker only (T-0022)

            client: SesClient = boto3.client("sesv2", region_name=self.region)
            self._client = client
        return self._client

    def send(self, message: OutboundMessage) -> str:
        from botocore.exceptions import BotoCoreError, ClientError

        request: dict[str, Any] = {
            "FromEmailAddress": self.from_address,
            "Destination": {"ToAddresses": [message.to]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": message.rendered.subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": message.rendered.text, "Charset": "UTF-8"},
                        "Html": {"Data": message.rendered.html, "Charset": "UTF-8"},
                    },
                }
            },
            "EmailTags": [{"Name": "template", "Value": message.template_key.replace(".", "_")}],
        }
        if self.configuration_set:
            request["ConfigurationSetName"] = self.configuration_set
        try:
            response = self._ses().send_email(**request)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", "ClientError"))
            status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 400))
            if code in TRANSIENT_CODES or status >= 500 or status == 429:
                raise TransientMailError(f"ses:{code}") from None
            raise PermanentMailError(f"ses:{code}") from None
        except BotoCoreError as exc:  # connection, timeout, credentials not yet available
            raise TransientMailError(f"ses:{type(exc).__name__}") from None
        return str(response["MessageId"])


def check_mail_config(settings: Settings) -> None:
    """Fail fast at start-up (API and worker) on mail configuration this environment cannot
    run with. Production needs SES, a sender, the bounce topic and the web origin (links)."""
    topic = settings.mail_sns_topic_arn
    if topic is not None and topic.split(":")[3] != settings.aws_region:
        raise MailConfigError("KERP_MAIL_SNS_TOPIC_ARN must be in KERP_AWS_REGION")
    if settings.mail_from_address is not None and "@" not in settings.mail_from_address:
        raise MailConfigError("KERP_MAIL_FROM_ADDRESS must be an e-mail address")
    if settings.environment != "production":
        return
    if settings.mail_transport != "ses":
        raise MailConfigError("production sends e-mail through SES: set KERP_MAIL_TRANSPORT=ses")
    missing = [
        name
        for name, value in (
            ("KERP_MAIL_FROM_ADDRESS", settings.mail_from_address),
            ("KERP_MAIL_SNS_TOPIC_ARN", topic),
            ("KERP_WEB_ORIGIN", settings.web_origin),
            ("KERP_DEFAULT_TENANT_ID", settings.default_tenant_id),
        )
        if value is None
    ]
    if missing:
        raise MailConfigError(f"e-mail is not configured: set {', '.join(missing)}")


def transport_from_settings(settings: Settings) -> MailTransport:
    """The worker's transport: SES when configured, else the log transport (never in
    production: ``check_mail_config`` and ``LogTransport`` both refuse)."""
    check_mail_config(settings)
    if settings.mail_transport == "ses":
        if settings.mail_from_address is None:
            raise MailConfigError("KERP_MAIL_TRANSPORT=ses needs KERP_MAIL_FROM_ADDRESS")
        return SesTransport(
            region=settings.aws_region,
            from_address=settings.mail_from_address,
            configuration_set=settings.mail_ses_configuration_set,
        )
    return LogTransport(settings)
