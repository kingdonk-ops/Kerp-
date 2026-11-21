"""System console: audit explorer, mounted at ``/api/v1/admin/system/audit`` (T-0222, diff §4.7).

Every endpoint is ``system_admin`` only: any other caller, a PER-09 of every project included,
gets 403. Read-only: no endpoint updates or deletes a ledger row.

* ``GET /system/audit``: ledger entries, newest first, with filters and keyset paging.
* ``POST /system/audit/verify``: verify one chain now (unknown or invisible chain: 404).
* ``POST /system/audit/verify-all``: queue the ``batch`` job that verifies every chain the
  caller can read (202; poll ``GET /api/v1/jobs/{id}`` for the report).

Entries are read under the caller's normal RLS (ADR 0016): the tenant-level chains and the
chains of every project of the tenant. Both verifications are written to the console chain.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import AwareDatetime

from app.core.audit.service import FLAG_PATTERN, MAX_CHAIN_KEY
from app.core.auth.deps import CurrentPrincipal
from app.core.db import DbSession
from app.modules.admin import audit_explorer as explorer
from app.modules.admin.schemas import (
    AuditEntryRead,
    AuditPageRead,
    ChainVerifyIn,
    ChainVerifyRead,
    JobActionRead,
)
from app.modules.admin.system_router import SystemAdminOnly

AUDIT = "/system/audit"

router = APIRouter(tags=["admin"], dependencies=[SystemAdminOnly])


@router.get(AUDIT, response_model=AuditPageRead)
def list_audit_entries(
    session: DbSession,
    actor_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    chain_key_prefix: Annotated[str | None, Query(max_length=MAX_CHAIN_KEY)] = None,
    event: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    flag: Annotated[str | None, Query(pattern=f"^{FLAG_PATTERN.pattern}$")] = None,
    created_from: AwareDatetime | None = None,
    created_to: AwareDatetime | None = None,
    limit: Annotated[int, Query(ge=1, le=explorer.MAX_PAGE)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> AuditPageRead:
    filters = explorer.EntryFilter(
        actor_id=actor_id,
        device_id=device_id,
        chain_key_prefix=chain_key_prefix,
        event=event,
        flag=flag,
        created_from=created_from,
        created_to=created_to,
    )
    try:
        page = explorer.list_entries(session, filters, limit=limit, cursor=cursor)
    except explorer.CursorError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return AuditPageRead(
        items=[AuditEntryRead.model_validate(e) for e in page.items],
        next_cursor=page.next_cursor,
    )


@router.post(f"{AUDIT}/verify", response_model=ChainVerifyRead)
def verify_chain(
    body: ChainVerifyIn, principal: CurrentPrincipal, session: DbSession
) -> ChainVerifyRead:
    # An unknown or invisible chain raises ``OutOfScopeError``: the app answers 404.
    report = explorer.verify_chain(
        session, body.chain_key, body.project_id, actor_id=principal.user_id
    )
    return ChainVerifyRead(
        chain_key=report.chain_key,
        project_id=body.project_id,
        ok=report.ok,
        length=report.length,
        head_hash=report.head_hash,
        first_broken_seq=report.first_broken_seq,
        problem_count=len(report.problems),
        problems=report.problems[: explorer.MAX_PROBLEMS],
    )


@router.post(
    f"{AUDIT}/verify-all", response_model=JobActionRead, status_code=status.HTTP_202_ACCEPTED
)
def verify_all(principal: CurrentPrincipal, session: DbSession) -> JobActionRead:
    job_id = explorer.request_verify_all(session, actor_id=principal.user_id)
    return JobActionRead(id=job_id, kind=explorer.VERIFY_ALL_KIND, status="queued")
