"""Mail API, mounted at ``/api/v1/mail`` (T-0058, diff §4.6).

* ``GET /sends``, ``GET /suppressions``: ``system_admin`` only (else 403). Masked recipients
  and hashes; never an address in clear, a link, a token or a body. ``/sends`` is the
  searchable delivery ledger (T-0226): template, status, period, broadcast and an exact-address
  lookup (the address is hashed here and never kept or logged).
* ``GET /stats``: ``system_admin`` only; counts by status and template for a period (T-0226).
* ``DELETE /suppressions/{id}``: ``system_admin`` only; takes the address off the list
  (soft delete, audit-ledger entry). Unknown / another tenant's / removed: 404.
* ``/templates`` (T-0227): ``system_admin`` only. ``GET /templates`` lists every registered
  template (audience, required flag, placeholders, shipped text and current override per
  locale); ``PUT|DELETE /templates/{key}/{locale}`` saves a new revision / resets to the
  shipped default (audited); ``GET .../revisions``; ``POST .../preview`` renders with sample
  values; ``POST .../test-send`` queues it to the caller's own address (rate-limited).
* ``POST /ses-events``: the public HTTPS endpoint SNS posts SES deliveries, bounces and
  complaints to. No JWT: only verified messages of ``KERP_MAIL_SNS_TOPIC_ARN`` are accepted
  (``sns.py``) and rows are written under ``KERP_DEFAULT_TENANT_ID``, never a tenant from the
  payload. Not in the OpenAPI schema (no client calls it).
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Annotated, Final

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime
from sqlalchemy.exc import IntegrityError

from app.core.auth.access import CallerIsSystemAdmin
from app.core.auth.deps import CurrentPrincipal
from app.core.config import Settings
from app.core.db import Database, DbSession
from app.core.mail import overrides, service, sns
from app.core.mail.addresses import MAX_ADDRESS, InvalidAddressError, address_hash
from app.core.mail.schemas import (
    MailCountsRead,
    MailPreviewRead,
    MailSendRead,
    MailStatsRead,
    MailSuppressionRead,
    MailTemplateCountsRead,
    MailTemplateLocaleRead,
    MailTemplateOverrideRead,
    MailTemplateRead,
    MailTemplateTextRead,
    MailTemplateTextWrite,
    MailTestSendRead,
    SendStatus,
)
from app.core.mail.templates import KEY_PATTERN, TEMPLATES, TemplateText
from app.core.tenancy.context import TenancyContext

PREFIX: Final = "/api/v1/mail"
SNS_PATH: Final = f"{PREFIX}/ses-events"
STATE_ATTR: Final = "mail_sns"
_build_lock = threading.Lock()

router = APIRouter(tags=["mail"])


def _require_system_admin(is_admin: CallerIsSystemAdmin) -> None:
    if not is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


SystemAdminOnly = Depends(_require_system_admin)
Limit = Annotated[int, Query(ge=1, le=service.MAX_LIST)]
Offset = Annotated[int, Query(ge=0)]


Template = Annotated[str | None, Query(alias="template", min_length=1, max_length=100)]
From = Annotated[AwareDatetime | None, Query(alias="from")]
To = Annotated[AwareDatetime | None, Query(alias="to")]


def _period(created_from: datetime | None, created_to: datetime | None) -> None:
    if created_from is not None and created_to is not None and created_from > created_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "from is after to")


@router.get(f"{PREFIX}/sends", response_model=list[MailSendRead], dependencies=[SystemAdminOnly])
def list_sends(
    session: DbSession,
    status_filter: Annotated[SendStatus | None, Query(alias="status")] = None,
    template_key: Template = None,
    created_from: From = None,
    created_to: To = None,
    broadcast_id: uuid.UUID | None = None,
    address: Annotated[str | None, Query(min_length=3, max_length=MAX_ADDRESS)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[MailSendRead]:
    """The delivery ledger, newest first. ``address`` is an exact lookup: hashed here and
    matched on the stored hash (the request log never records query strings)."""
    _period(created_from, created_to)
    recipient_hash = None
    if address is not None:
        try:
            recipient_hash = address_hash(address)
        except InvalidAddressError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "address is not an e-mail address"
            ) from None
    filters = service.SendFilter(
        template_key=template_key,
        status=status_filter,
        created_from=created_from,
        created_to=created_to,
        broadcast_id=broadcast_id,
        recipient_hash=recipient_hash,
    )
    rows = service.list_sends(session, filters, limit=limit, offset=offset)
    return [MailSendRead.model_validate(row) for row in rows]


def _counts(counts: service.SendCounts) -> MailCountsRead:
    return MailCountsRead.model_validate(counts)


@router.get(f"{PREFIX}/stats", response_model=MailStatsRead, dependencies=[SystemAdminOnly])
def send_stats(
    session: DbSession,
    template_key: Template = None,
    created_from: From = None,
    created_to: To = None,
) -> MailStatsRead:
    """Sent, delivered, bounced, complained, failed and suppressed counts and the delivery
    rate of the sends created in ``[from, to)``, in total and per template."""
    _period(created_from, created_to)
    filters = service.SendFilter(
        template_key=template_key, created_from=created_from, created_to=created_to
    )
    stats = service.send_stats(session, filters)
    return MailStatsRead(
        created_from=created_from,
        created_to=created_to,
        template_key=template_key,
        totals=_counts(stats.totals),
        by_template=[
            MailTemplateCountsRead.model_validate(
                {**_counts(counts).model_dump(), "template_key": key}
            )
            for key, counts in stats.by_template.items()
        ],
    )


@router.get(
    f"{PREFIX}/suppressions",
    response_model=list[MailSuppressionRead],
    dependencies=[SystemAdminOnly],
)
def list_suppressions(
    session: DbSession, limit: Limit = 50, offset: Offset = 0
) -> list[MailSuppressionRead]:
    rows = service.list_suppressions(session, limit=limit, offset=offset)
    return [MailSuppressionRead.model_validate(row) for row in rows]


@router.delete(
    f"{PREFIX}/suppressions/{{suppression_id}}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[SystemAdminOnly],
)
def remove_suppression(
    suppression_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
) -> Response:
    service.remove_suppression(session, suppression_id, actor_id=principal.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- automated template overrides (T-0227) ---------------------------------------------------

TemplateKey = Annotated[str, Path(min_length=3, max_length=100, pattern=KEY_PATTERN.pattern)]
Locale = Annotated[str, Path(min_length=2, max_length=35, pattern=overrides.LOCALE_PATTERN)]
TEMPLATE_PATH: Final = f"{PREFIX}/templates/{{template_key}}/{{locale}}"


def _known(template_key: str) -> None:
    if TEMPLATES.find(template_key) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown mail template")


def _invalid(exc: overrides.OverrideInvalidError) -> HTTPException:
    """422 in FastAPI's validation shape: one entry per refused field."""
    detail = [
        {"loc": ["body", field], "msg": reason, "type": "value_error"}
        for field, reason in sorted(exc.problems.items())
    ]
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


def _text(body: MailTemplateTextWrite) -> TemplateText:
    return TemplateText(
        subject=body.subject, intro=body.intro, outro=body.outro, action_label=body.action_label
    )


def _web_origin(settings: Settings) -> str:
    return settings.web_origin or service.DEV_WEB_ORIGIN


@router.get(
    f"{PREFIX}/templates", response_model=list[MailTemplateRead], dependencies=[SystemAdminOnly]
)
def list_templates(request: Request, session: DbSession) -> list[MailTemplateRead]:
    """Every registered template: audience group, required flag, declared placeholders, and
    per locale the shipped text and the tenant's current override."""
    settings: Settings = request.app.state.settings
    return [
        MailTemplateRead(
            key=entry.template.key,
            audience=entry.template.audience,
            required=entry.template.required,
            has_action=entry.template.has_action,
            placeholders=list(entry.template.placeholders()),
            locales=[
                MailTemplateLocaleRead(
                    locale=item.locale,
                    shipped=MailTemplateTextRead.model_validate(item.shipped),
                    override=None
                    if item.override is None
                    else MailTemplateOverrideRead.model_validate(item.override),
                )
                for item in entry.locales
            ],
        )
        for entry in overrides.list_templates(session, settings.mail_default_locale)
    ]


@router.get(
    f"{TEMPLATE_PATH}/revisions",
    response_model=list[MailTemplateOverrideRead],
    dependencies=[SystemAdminOnly],
)
def list_template_revisions(
    template_key: TemplateKey, locale: Locale, session: DbSession
) -> list[MailTemplateOverrideRead]:
    """Every saved revision of the template in this locale, newest first."""
    _known(template_key)
    rows = overrides.list_revisions(session, template_key, locale)
    return [MailTemplateOverrideRead.model_validate(row) for row in rows]


@router.put(TEMPLATE_PATH, response_model=MailTemplateOverrideRead, dependencies=[SystemAdminOnly])
def save_template_override(
    template_key: TemplateKey,
    locale: Locale,
    body: MailTemplateTextWrite,
    principal: CurrentPrincipal,
    session: DbSession,
) -> MailTemplateOverrideRead:
    """Save a new revision and make it current; the next send uses it. Audited."""
    _known(template_key)
    try:
        row = overrides.save_override(
            session, template_key, locale, _text(body), actor_id=principal.user_id
        )
    except overrides.OverrideInvalidError as exc:
        raise _invalid(exc) from None
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The template was changed meanwhile; reload and retry"
        ) from None
    return MailTemplateOverrideRead.model_validate(row)


@router.delete(
    TEMPLATE_PATH, status_code=status.HTTP_204_NO_CONTENT, dependencies=[SystemAdminOnly]
)
def reset_template_override(
    template_key: TemplateKey, locale: Locale, principal: CurrentPrincipal, session: DbSession
) -> Response:
    """Back to the shipped default (revisions kept; audited). Idempotent."""
    _known(template_key)
    overrides.reset_override(session, template_key, locale, actor_id=principal.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    f"{TEMPLATE_PATH}/preview", response_model=MailPreviewRead, dependencies=[SystemAdminOnly]
)
def preview_template(
    template_key: TemplateKey,
    locale: Locale,
    request: Request,
    session: DbSession,
    body: MailTemplateTextWrite | None = None,
) -> MailPreviewRead:
    """Render with synthetic sample values: the draft in the body (checked like a save,
    nothing stored), else what a send would use now."""
    _known(template_key)
    settings: Settings = request.app.state.settings
    draft = None if body is None else _text(body)
    try:
        rendered = overrides.preview(
            session,
            template_key,
            locale,
            settings,
            web_origin=_web_origin(settings),
            draft=draft,
        )
    except overrides.OverrideInvalidError as exc:
        raise _invalid(exc) from None
    return MailPreviewRead(subject=rendered.subject, text=rendered.text, html=rendered.html)


@router.post(
    f"{TEMPLATE_PATH}/test-send",
    response_model=MailTestSendRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[SystemAdminOnly],
)
def send_template_test(
    template_key: TemplateKey, locale: Locale, principal: CurrentPrincipal, session: DbSession
) -> MailTestSendRead:
    """Queue the template with sample values to the caller's own address only (the request
    names no recipient). Rate-limited (429 with ``Retry-After``); audited."""
    _known(template_key)
    try:
        send_id = overrides.send_test(
            session,
            template_key,
            locale,
            user_id=principal.user_id,
            email=principal.user.email,
        )
    except overrides.TestSendRateLimitedError as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many test sends; try again later",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    return MailTestSendRead(send_id=send_id)


# --- SNS ------------------------------------------------------------------------------------


def install_sns_verifier(app: FastAPI, verifier: sns.SnsVerifier) -> None:
    setattr(app.state, STATE_ATTR, verifier)


def app_sns_verifier(app: FastAPI) -> sns.SnsVerifier:
    """The app's verifier (built from the settings on first use; tests install a fake one)."""
    verifier: sns.SnsVerifier | None = getattr(app.state, STATE_ATTR, None)
    if verifier is None:
        with _build_lock:
            verifier = getattr(app.state, STATE_ATTR, None)
            if verifier is None:
                settings: Settings = app.state.settings
                verifier = sns.SnsVerifier(
                    topic_arn=settings.mail_sns_topic_arn, region=settings.aws_region
                )
                install_sns_verifier(app, verifier)
    return verifier


def _refused() -> JSONResponse:
    return JSONResponse({"detail": "Forbidden"}, status_code=status.HTTP_403_FORBIDDEN)


def _apply(db: Database, tenant_id: uuid.UUID, event: dict[str, object]) -> None:
    # The deployment's hidden tenant from settings; the payload names none we would use.
    # ``is_system_admin`` (ADR 0016, never crossing the tenant): a send-log row of a project
    # (an invite) is updated too, and the session still writes tenant-level rows only at
    # tenant level (the suppression list). T-0226, ADR 0020.
    scope = TenancyContext(tenant_id=tenant_id, is_system_admin=True)
    with db.unit_of_work(scope) as session:
        service.record_ses_event(session, event)


@router.post(SNS_PATH, status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
async def ses_events(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    db: Database | None = request.app.state.db
    if db is None or settings.default_tenant_id is None:
        return JSONResponse({"detail": "Unavailable"}, status_code=503)
    verifier = app_sns_verifier(request.app)
    body = await request.body()
    try:
        outcome, event = await run_in_threadpool(sns.handle, verifier, body)
    except sns.SnsRefusedError:
        return _refused()
    if outcome == "notification" and event is not None:
        await run_in_threadpool(_apply, db, settings.default_tenant_id, event)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
