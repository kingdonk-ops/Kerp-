"""Append to and verify hash chains in the audit ledger (T-0006).

Modules never touch ``audit_ledger`` directly; they call ``append`` on their own session
(the caller's unit of work, so the entry commits or rolls back with the change it records).
The tenant and project come from the session's tenancy binding, never from arguments.

A chain is ``(tenant, scope, chain_key)``, where the scope is the session's project or the
tenant level. Every function works on the chain of the session's own scope: a project
session never reads, extends or verifies a tenant-level or another project's chain, even
one with the same ``chain_key``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import Select, select, text
from sqlalchemy.orm import Session

from app.core.audit.hashing import (
    GENESIS_HASH,
    ChainReport,
    canonical_json,
    compute_content_hash,
    verify_entries,
)
from app.core.audit.models import AuditEntry, scope_of
from app.core.db import RECORD_SOURCES, utcnow
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy

MAX_CHAIN_KEY = 200


def _context(session: Session) -> TenancyContext:
    context = session_tenancy(session)
    if context is None:
        raise RuntimeError("the audit ledger needs a session bound to a tenant (bind_tenancy)")
    return context


def _lock_chain(session: Session, context: TenancyContext, chain_key: str) -> None:
    """Serialise appends to one chain until the transaction ends (PostgreSQL only).

    Without it two writers would read the same tail; the unique ``(tenant_id, scope_id,
    chain_key, seq)`` constraint would then reject one of them. With it the second waits and
    links to the first. SQLite serialises writers itself.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    scope = scope_of(context.project_id)
    digest = hashlib.sha256(f"audit_ledger|{context.tenant_id}|{scope}|{chain_key}".encode())
    key = int.from_bytes(digest.digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _query(context: TenancyContext, chain_key: str) -> Select[AuditEntry]:
    return (
        select(AuditEntry)
        .where(
            AuditEntry.tenant_id == context.tenant_id,
            AuditEntry.scope_id == scope_of(context.project_id),
            AuditEntry.chain_key == chain_key,
        )
        .execution_options(include_deleted=True)
    )


def append(
    session: Session,
    chain_key: str,
    payload: dict[str, Any],
    *,
    source: str = "system",
    actor_id: uuid.UUID | None = None,
) -> AuditEntry:
    """Add the next entry to ``chain_key`` in the session's scope and flush it.

    ``chain_key`` should embed the entity it tracks (``"inspection:<uuid>"``).
    """
    context = _context(session)
    if not isinstance(payload, dict):
        raise TypeError("payload must be a JSON object")
    if source not in RECORD_SOURCES:
        raise ValueError(f"source must be one of {sorted(RECORD_SOURCES)}")
    if not chain_key or len(chain_key) > MAX_CHAIN_KEY:
        raise ValueError(f"chain_key must be 1-{MAX_CHAIN_KEY} characters")
    stored = json.loads(canonical_json(payload))  # validated, detached copy

    _lock_chain(session, context, chain_key)
    tail = session.scalars(
        _query(context, chain_key).order_by(AuditEntry.seq.desc()).limit(1)
    ).first()

    entry = AuditEntry(
        tenant_id=context.tenant_id,
        project_id=context.project_id,
        scope_id=scope_of(context.project_id),
        chain_key=chain_key,
        seq=1 if tail is None else tail.seq + 1,
        prev_hash=GENESIS_HASH if tail is None else tail.content_hash,
        created_at=utcnow(),
        created_by=actor_id or context.user_id,
        source=source,
        payload=stored,
    )
    entry.content_hash = compute_content_hash(entry)
    session.add(entry)
    session.flush()
    return entry


def chain_entries(session: Session, chain_key: str) -> list[AuditEntry]:
    """Every entry of ``chain_key`` in the session's scope, in ``seq`` order."""
    context = _context(session)
    return list(session.scalars(_query(context, chain_key).order_by(AuditEntry.seq)))


def verify_chain(session: Session, chain_key: str) -> ChainReport:
    """Re-hash and re-link the chain. ``report.ok`` is ``False`` if anything was altered."""
    entries = chain_entries(session, chain_key)
    return ChainReport(
        chain_key=chain_key,
        length=len(entries),
        head_hash=entries[-1].content_hash if entries else GENESIS_HASH,
        problems=verify_entries(entries),
    )
