"""Audit explorer service (T-0222, diff §4.7): read-only over the hash-chain ledger.

Every ledger read goes through ``app.core.audit.service`` under the caller's normal RLS; for a
``system_admin`` session that is the tenant-level chains plus the chains of every project of
its tenant (ADR 0016), never another tenant's. Nothing here updates or deletes a ledger row.
The only writes are appended entries on the console chain ``admin-system:<tenant>``: one per
single-chain verification, one when a full verification is requested and one with its outcome
(diff §4.7: every console action is audited; TD-494).
"""

from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.audit.hashing import ChainReport
from app.core.audit.models import AuditEntry
from app.core.auth.access import SYSTEM_ADMIN
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import OutOfScopeError
from app.modules.admin.service import own_admin_grants
from app.modules.jobs import service as jobs

VERIFY_ALL_KIND: Final = "admin.audit_verify_all"
VERIFY_ALL_TIMEOUT_S: Final = 60 * 60
MAX_PAGE: Final = 200
MAX_PROBLEMS: Final = 100  # findings returned for one chain; ``problem_count`` has the total

EntryFilter = audit.EntryFilter


class CursorError(ValueError):
    """The paging cursor is not one this API issued."""


@dataclass(frozen=True, slots=True)
class Page:
    items: list[AuditEntry]
    next_cursor: str | None


def _encode(entry: AuditEntry) -> str:
    raw = f"{entry.created_at.isoformat()}|{entry.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        at, _, entry_id = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
        return datetime.fromisoformat(at), uuid.UUID(entry_id)
    except (ValueError, binascii.Error):
        raise CursorError("invalid cursor") from None


def list_entries(
    session: Session, filters: EntryFilter, *, limit: int, cursor: str | None = None
) -> Page:
    """One page of the ledger entries the caller can read, newest first (keyset paging)."""
    after = _decode(cursor) if cursor else None
    rows = audit.list_entries(session, filters, limit=limit + 1, after=after)
    more = len(rows) > limit
    items = rows[:limit]
    return Page(items, _encode(items[-1]) if more else None)


def _console_chain(session: Session) -> str:
    context = session_tenancy(session)
    if context is None or context.project_id is not None:
        raise RuntimeError("the audit explorer needs a tenant-level bound session")
    return jobs.CONSOLE_CHAIN.format(tenant_id=context.tenant_id)


def _project(project_id: uuid.UUID | None) -> str | None:
    return None if project_id is None else str(project_id)


def verify_chain(
    session: Session, chain_key: str, project_id: uuid.UUID | None, *, actor_id: uuid.UUID | None
) -> ChainReport:
    """Verify one chain of the named scope (audited). A chain without a visible entry does
    not exist for the caller: ``OutOfScopeError`` (404), nothing audited."""
    chain = _console_chain(session)
    report = audit.verify_scoped_chain(session, chain_key, project_id)
    if report.length == 0:
        raise OutOfScopeError("Chain not found")
    audit.append(
        session,
        chain,
        {
            "event": "audit_chain_verified",
            "chain_key": chain_key,
            "project_id": _project(project_id),
            "ok": report.ok,
            "length": report.length,
            "first_broken_seq": report.first_broken_seq,
        },
        actor_id=actor_id,
        source="api",
    )
    return report


def request_verify_all(session: Session, *, actor_id: uuid.UUID) -> uuid.UUID:
    """Queue the ``batch`` job that verifies every chain the caller can read (audited in the
    same transaction; SAQ gets the job once it commits)."""
    chain = _console_chain(session)
    job = jobs.create_job(
        session,
        jobs.JOB_REGISTRY.get(VERIFY_ALL_KIND),
        {"requested_by": str(actor_id)},
        actor_id=actor_id,
    )
    audit.append(
        session,
        chain,
        {"event": "audit_verify_all_requested", "job_id": str(job.id)},
        actor_id=actor_id,
        source="api",
    )
    return job.id


def run_verify_all(
    db: Database, tenancy: TenancyContext, job_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """The job body: the report that is stored as the job's result and audited.

    The worker binds the job's tenant-level scope without any admin flag. ``is_system_admin``
    is bound here only after re-checking, at job start, that the user who asked still holds
    the grant (ADR 0016); the read then runs under normal RLS, so it never leaves the tenant.
    """
    try:
        requester = uuid.UUID(str(payload["requested_by"]))
    except (KeyError, ValueError):
        raise jobs.JobError("The verification names no requesting user") from None
    if tenancy.project_id is not None:
        raise jobs.JobError("The verification is a tenant-level job")
    own = TenancyContext(tenant_id=tenancy.tenant_id, user_id=requester)
    with db.unit_of_work(own) as session:
        if SYSTEM_ADMIN not in own_admin_grants(session, own.tenant_id, requester):
            raise jobs.JobError("The requesting user is not a System admin")
    admin = TenancyContext(tenant_id=own.tenant_id, user_id=requester, is_system_admin=True)
    with db.unit_of_work(admin) as session:
        report = audit.verify_readable_chains(session)
        first = report.first_broken
        result: dict[str, Any] = {
            "ok": report.broken == 0,
            "chains": report.chains,
            "entries": report.entries,
            "broken_chains": report.broken,
            "first_broken": first
            and {
                "project_id": _project(first.project_id),
                "chain_key": first.chain_key,
                "seq": first.seq,
            },
        }
        audit.append(
            session,
            _console_chain(session),
            {"event": "audit_verify_all_finished", "job_id": str(job_id)} | result,
            actor_id=requester,
            source="system",
        )
    return result
