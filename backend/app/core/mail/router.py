"""Mail API, mounted at ``/api/v1/mail`` (T-0058, diff §4.6).

* ``GET /sends``, ``GET /suppressions``: ``system_admin`` only (else 403). Masked recipients
  and hashes; never an address in clear, a link, a token or a body. ``/sends`` is the
  searchable delivery ledger (T-0226): template, status, period, broadcast and an exact-address
  lookup (the address is hashed here and never kept or logged).
* ``GET /stats``: ``system_admin`` only; counts by status and template for a period (T-0226).
* ``DELETE /suppressions/{id}``: ``system_admin`` only; takes the address off the list
  (soft delete, audit-ledger entry). Unknown / another tenant's / removed: 404.
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

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime

from app.core.auth.access import CallerIsSystemAdmin
from app.core.auth.deps import CurrentPrincipal
from app.core.config import Settings
from app.core.db import Database, DbSession
from app.core.mail import service, sns
from app.core.mail.addresses import MAX_ADDRESS, InvalidAddressError, address_hash
from app.core.mail.schemas import (
    MailCountsRead,
    MailSendRead,
    MailStatsRead,
    MailSuppressionRead,
    MailTemplateCountsRead,
    SendStatus,
)
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
