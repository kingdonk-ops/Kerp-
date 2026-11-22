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
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from itertools import groupby
from typing import Any, Final

from sqlalchemy import ColumnElement, Select, and_, func, or_, select, text, type_coerce
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.core.audit.hashing import (
    GENESIS_HASH,
    HASH_VERSION,
    IMPERSONATION_HASH_VERSION,
    ChainReport,
    canonical_json,
    compute_content_hash,
    find_problems,
)
from app.core.audit.models import TENANT_SCOPE, AuditEntry, scope_of
from app.core.db import RECORD_SOURCES, utcnow
from app.core.tenancy.context import TenancyContext, current_device_id, current_impersonator_id
from app.core.tenancy.rls import session_tenancy

MAX_CHAIN_KEY = 200
# A payload key the explorer may filter on as a boolean flag (``sod_warning``; TD-402).
FLAG_PATTERN: Final = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
VERIFY_BATCH: Final = 1000  # rows fetched per round trip by ``verify_readable_chains``


def _context(session: Session) -> TenancyContext:
    context = session_tenancy(session)
    if context is None:
        raise RuntimeError("the audit ledger needs a session bound to a tenant (bind_tenancy)")
    return context


# SQLite has no advisory or row locks. A write that matches no row still makes the driver
# open the transaction and takes the database's write lock (RESERVED), held until commit.
_SQLITE_WRITE_LOCK = text(f"UPDATE {AuditEntry.__tablename__} SET seq = seq WHERE 0")


def _lock_chain(session: Session, context: TenancyContext, chain_key: str) -> None:
    """Serialise appends to one chain until the transaction ends.

    Without it two writers would read the same tail; the unique ``(tenant_id, scope_id,
    chain_key, seq)`` constraint would then reject one of them. With it the second waits and
    links to the first.

    PostgreSQL: a transaction-scoped advisory lock per chain.

    SQLite: the database write lock, taken *before* the tail is read. SQLite does serialise
    writers, but only from their first write: the driver opens the transaction lazily (a
    plain deferred ``BEGIN`` before the first DML), so the tail ``SELECT`` runs under no lock,
    two writers can read the same tail, queue on the INSERT, and the second violates the
    unique ``seq`` (a 500 in the concurrent password-reset confirm, T-0077). A no-op write is
    the minimal fix that also works mid-transaction: ``BEGIN IMMEDIATE`` cannot be issued once
    the caller's unit of work has begun, and a process-level lock would neither cover other
    processes nor be released at commit. It locks the whole database, which on SQLite (dev
    and tests only) any write does anyway.
    """
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        session.execute(_SQLITE_WRITE_LOCK)
        return
    if dialect != "postgresql":
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
    device_id: uuid.UUID | None = None,
) -> AuditEntry:
    """Add the next entry to ``chain_key`` in the session's scope and flush it.

    ``chain_key`` should embed the entity it tracks (``"inspection:<uuid>"``).
    ``device_id`` is the enrolled field device the change came through (T-0043); unset, it
    is the request's device (``current_device_id``, set at authentication), else ``NULL``.
    A job replaying a device's queued work passes the device explicitly.

    While a ``system_admin`` impersonates (T-0279, ADR 0015 §6), the entry also names the
    admin in ``impersonated_by_user_id`` and is hashed as v3. The admin comes only from the
    request context (``current_impersonator_id``, set from the verified principal); there is
    deliberately no argument for it. ``created_by`` stays the acting (target) user. Outside
    impersonation entries are v2 without it.
    """
    context = _context(session)
    if not isinstance(payload, dict):
        raise TypeError("payload must be a JSON object")
    if source not in RECORD_SOURCES:
        raise ValueError(f"source must be one of {sorted(RECORD_SOURCES)}")
    if not chain_key or len(chain_key) > MAX_CHAIN_KEY:
        raise ValueError(f"chain_key must be 1-{MAX_CHAIN_KEY} characters")
    stored = json.loads(canonical_json(payload))  # validated, detached copy

    impersonator = current_impersonator_id.get()

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
        device_id=device_id or current_device_id.get(),
        impersonated_by_user_id=impersonator,
        hash_version=HASH_VERSION if impersonator is None else IMPERSONATION_HASH_VERSION,
    )
    entry.content_hash = compute_content_hash(entry)
    session.add(entry)
    session.flush()
    return entry


def chain_entries(session: Session, chain_key: str) -> list[AuditEntry]:
    """Every entry of ``chain_key`` in the session's scope, in ``seq`` order."""
    context = _context(session)
    return list(session.scalars(_query(context, chain_key).order_by(AuditEntry.seq)))


def _report(chain_key: str, entries: Sequence[AuditEntry]) -> ChainReport:
    problems = find_problems(entries)
    return ChainReport(
        chain_key=chain_key,
        length=len(entries),
        head_hash=entries[-1].content_hash if entries else GENESIS_HASH,
        problems=[f"seq {p.seq}: {p.message}" for p in problems],
        first_broken_seq=problems[0].seq if problems else None,
    )


def verify_chain(session: Session, chain_key: str) -> ChainReport:
    """Re-hash and re-link the chain. ``report.ok`` is ``False`` if anything was altered."""
    return _report(chain_key, chain_entries(session, chain_key))


# --- read helpers for the audit explorer (T-0222, diff §4.7) ---------------------------------
# Unlike the functions above they read across scopes: every chain the session can see under
# its normal RLS / ORM scope (for a ``system_admin`` session the tenant-level chains and the
# chains of every project of its tenant, ADR 0016). They never write, and they name the
# tenant and, where one chain is meant, its scope explicitly (visibility is not scope).


@dataclass(frozen=True, slots=True)
class EntryFilter:
    """Filters of ``list_entries``; unset fields do not filter. ``event`` is the payload's
    top-level ``event`` key, ``flag`` a top-level payload key that is the boolean ``true``
    (TD-402). ``created_from`` is inclusive, ``created_to`` exclusive."""

    actor_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    impersonated_by_user_id: uuid.UUID | None = None
    chain_key_prefix: str | None = None
    event: str | None = None
    flag: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None


def _payload_is(session: Session, key: str, value: str | bool) -> ColumnElement[bool]:
    """The payload's top-level ``key`` is exactly ``value`` (same JSON type)."""
    if not FLAG_PATTERN.fullmatch(key):  # on SQLite the key goes into a JSON path
        raise ValueError(f"payload key {key!r} must match {FLAG_PATTERN.pattern}")
    if session.get_bind().dialect.name == "postgresql":
        return type_coerce(AuditEntry.payload, JSONB).contains({key: value})
    path = f"$.{key}"
    kind = func.json_type(AuditEntry.payload, path)
    if isinstance(value, bool):
        return kind == ("true" if value else "false")
    return and_(kind == "text", func.json_extract(AuditEntry.payload, path) == value)


def list_entries(
    session: Session,
    filters: EntryFilter,
    *,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None = None,
) -> list[AuditEntry]:
    """Up to ``limit`` visible entries of the session's tenant, newest first
    (``created_at``, then ``id``, descending). ``after`` is the ``(created_at, id)`` of the
    last entry of the previous page (keyset paging: stable while entries are appended)."""
    query = select(AuditEntry).where(AuditEntry.tenant_id == _context(session).tenant_id)
    if filters.actor_id is not None:
        query = query.where(AuditEntry.created_by == filters.actor_id)
    if filters.device_id is not None:
        query = query.where(AuditEntry.device_id == filters.device_id)
    if filters.impersonated_by_user_id is not None:
        query = query.where(AuditEntry.impersonated_by_user_id == filters.impersonated_by_user_id)
    if filters.chain_key_prefix:
        prefix = filters.chain_key_prefix
        query = query.where(AuditEntry.chain_key.startswith(prefix, autoescape=True))
    if filters.event is not None:
        query = query.where(_payload_is(session, "event", filters.event))
    if filters.flag is not None:
        query = query.where(_payload_is(session, filters.flag, True))
    if filters.created_from is not None:
        query = query.where(AuditEntry.created_at >= filters.created_from.astimezone(UTC))
    if filters.created_to is not None:
        query = query.where(AuditEntry.created_at < filters.created_to.astimezone(UTC))
    if after is not None:
        at = after[0].astimezone(UTC)
        query = query.where(
            or_(
                AuditEntry.created_at < at,
                and_(AuditEntry.created_at == at, AuditEntry.id < after[1]),
            )
        )
    query = query.order_by(AuditEntry.created_at.desc(), AuditEntry.id.desc()).limit(limit)
    return list(session.scalars(query.execution_options(include_deleted=True)))


def verify_scoped_chain(
    session: Session, chain_key: str, project_id: uuid.UUID | None
) -> ChainReport:
    """Verify the chain ``chain_key`` of the named scope (``project_id``, or ``None`` for the
    tenant level) of the session's tenant, whatever scope the session is bound to. A chain
    the session cannot see has ``length`` 0."""
    scope = replace(_context(session), project_id=project_id)
    entries = session.scalars(_query(scope, chain_key).order_by(AuditEntry.seq))
    return _report(chain_key, list(entries))


@dataclass(frozen=True, slots=True)
class BrokenChain:
    """A chain that failed verification, with the ``seq`` of its first finding."""

    project_id: uuid.UUID | None
    chain_key: str
    seq: int


@dataclass(frozen=True, slots=True)
class LedgerReport:
    """Result of ``verify_readable_chains``: counts, and the first broken chain in
    (scope, chain key) order, the tenant level first."""

    chains: int
    entries: int
    broken: int
    first_broken: BrokenChain | None


def verify_readable_chains(session: Session) -> LedgerReport:
    """Verify every chain of the session's tenant that the session can see.

    One statement (one snapshot) streams the entries in chain order, so memory is bounded by
    the longest chain. Heavy on a large ledger: call it from a batch job, not a request.
    """
    query = (
        select(AuditEntry)
        .where(AuditEntry.tenant_id == _context(session).tenant_id)
        .order_by(AuditEntry.scope_id, AuditEntry.chain_key, AuditEntry.seq)
        .execution_options(include_deleted=True, yield_per=VERIFY_BATCH)
    )
    chains = entries = broken = 0
    first: BrokenChain | None = None
    for (scope_id, chain_key), group in groupby(
        session.scalars(query), key=lambda e: (e.scope_id, e.chain_key)
    ):
        chain = list(group)
        chains, entries = chains + 1, entries + len(chain)
        problems = find_problems(chain)
        if problems:
            broken += 1
            if first is None:
                project_id = None if scope_id == TENANT_SCOPE else scope_id
                first = BrokenChain(project_id, chain_key, problems[0].seq)
    return LedgerReport(chains, entries, broken, first)
