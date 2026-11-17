"""Mail API, mounted at ``/api/v1/mail`` (T-0058, diff §4.6).

* ``GET /sends``, ``GET /suppressions``: ``system_admin`` only (else 403). Masked recipients
  and hashes; never an address in clear, a link, a token or a body.
* ``DELETE /suppressions/{id}``: ``system_admin`` only; takes the address off the list
  (soft delete, audit-ledger entry). Unknown / another tenant's / removed: 404.
* ``POST /ses-events``: the public HTTPS endpoint SNS posts SES bounces and complaints to.
  No JWT: only verified messages of ``KERP_MAIL_SNS_TOPIC_ARN`` are accepted (``sns.py``) and
  rows are written under ``KERP_DEFAULT_TENANT_ID``, never a tenant from the payload. Not in
  the OpenAPI schema (no client calls it).
"""

from __future__ import annotations

import threading
import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.core.auth.access import CallerIsSystemAdmin
from app.core.auth.deps import CurrentPrincipal
from app.core.config import Settings
from app.core.db import Database, DbSession
from app.core.mail import service, sns
from app.core.mail.schemas import MailSendRead, MailSuppressionRead, SendStatus
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


@router.get(f"{PREFIX}/sends", response_model=list[MailSendRead], dependencies=[SystemAdminOnly])
def list_sends(
    session: DbSession,
    status_filter: Annotated[SendStatus | None, Query(alias="status")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[MailSendRead]:
    rows = service.list_sends(session, status=status_filter, limit=limit, offset=offset)
    return [MailSendRead.model_validate(row) for row in rows]


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
    with db.unit_of_work(TenancyContext(tenant_id=tenant_id)) as session:
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
