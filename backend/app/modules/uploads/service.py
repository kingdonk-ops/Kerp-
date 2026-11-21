"""Upload lifecycle (T-0016): presign -> complete -> verify (job) -> download; orphan sweep.

Other modules call this, never the table. Every call works on a session (or, for the job
side, a ``Database`` and a ``TenancyContext``) bound to the file's project; the router
checks the caller's capability first. Storage I/O never holds a transaction open in the
job side: the row is read, the bytes are hashed, then the outcome is written.
"""

from __future__ import annotations

import importlib
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.db import Database, utcnow
from app.core.stats.registry import register_stats_provider
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import OutOfScopeError, get_or_404
from app.modules.jobs.service import CONSOLE_CHAIN, JOB_REGISTRY, create_job, job_status
from app.modules.projects import service as projects
from app.modules.uploads.models import (
    STATUSES,
    UploadedFile,
    quarantine_prefix,
    staging_prefix,
    tenant_prefixes,
)
from app.modules.uploads.scan import (
    Scanner,
    ScannerUnavailableError,
    ScanRefusedError,
    outcome,
    ping_scanner,
)
from app.modules.uploads.schemas import UploadCreate
from app.modules.uploads.storage import (
    HEALTH_PROBE_TIMEOUT_S,
    MAGIC_SNIFF_BYTES,
    OTHER_CLASS,
    STORAGE_CLASSES,
    PresignedRequest,
    Storage,
    get_storage,
    matches_magic,
    sha256_of,
)

if TYPE_CHECKING:
    from app.modules.jobs.models import Job
    from app.modules.jobs.registry import JobKind

__all__ = [
    "HEALTH_PROBE_TIMEOUT_S",
    "MAX_QUARANTINE_LIST",
    "MONITORED_STATUSES",
    "SWEEP_KIND",
    "VERIFY_KIND",
    "VERIFY_LARGE_KIND",
    "CheckRunningError",
    "FileTooLargeError",
    "NotUploaderError",
    "ObjectGoneError",
    "QuarantineCount",
    "QuarantineFile",
    "QueuedJob",
    "UploadStateError",
    "complete_upload",
    "deployment_stats",
    "download_request",
    "get_file",
    "object_store_totals",
    "ping_scanner",
    "ping_storage",
    "presign_upload",
    "quarantine_counts",
    "quarantine_files",
    "queue_project_sweep",
    "recheck_upload",
    "schedule_sweep",
    "sweep_orphans",
    "verify_upload",
]

VERIFY_KIND: Final = "uploads.verify"
VERIFY_LARGE_KIND: Final = "uploads.verify_large"
# Above this size the hash and scan take long enough to belong on the batch queue.
LARGE_FILE_BYTES: Final = 50 * 1024 * 1024
SWEEP_KIND: Final = "uploads.sweep_orphans"
DEFAULT_SWEEP_GRACE: Final = timedelta(hours=1)

# Stored as ``rejection_reason``: codes, so the client translates them (i18n).
Reason = Literal[
    "size_mismatch",
    "type_mismatch",
    "sha256_mismatch",
    "missing_object",
    "infected",
    "verify_failed",
]
# A file still ``verifying`` this long after ``complete`` (or whose verify job failed) is
# rejected by the orphan sweep with ``verify_failed`` (T-0215, TD-209).
VERIFY_STUCK_AFTER: Final = timedelta(hours=6)


class FileTooLargeError(ValueError):
    """The declared size is over ``upload_max_bytes`` (the API answers 413)."""


class NotUploaderError(PermissionError):
    """Only the user who started an upload completes it (the API answers 403)."""


class UploadStateError(RuntimeError):
    """The file is not in a state that allows the call (the API answers 409)."""


class ObjectGoneError(UploadStateError):
    """A stuck file's uploaded bytes are gone from storage: nothing to check (409)."""


class CheckRunningError(UploadStateError):
    """The file's verify job is still ``queued`` or ``running``: it is not stuck (409)."""


def _project_id(session: Session) -> uuid.UUID:
    context = session_tenancy(session)
    if context is None or context.project_id is None:
        raise RuntimeError("the uploads service needs a session bound to a project")
    return context.project_id


def _job_kind(kind: str) -> JobKind:
    importlib.import_module("app.modules.uploads.jobs")  # registers the handlers
    return JOB_REGISTRY.get(kind)


def presign_upload(
    session: Session,
    storage: Storage,
    data: UploadCreate,
    *,
    max_bytes: int,
    expires_in: int,
    actor_id: uuid.UUID | None = None,
) -> tuple[UploadedFile, PresignedRequest]:
    """Add a ``pending`` file and sign a ``PUT`` of exactly its size and type to staging."""
    if data.size_bytes > max_bytes:
        raise FileTooLargeError(f"the file is larger than {max_bytes} bytes")
    project_id = _project_id(session)
    context = session_tenancy(session)
    assert context is not None
    file = UploadedFile(
        id=uuid.uuid4(),
        tenant_id=context.tenant_id,
        project_id=project_id,
        **data.model_dump(),
        status="pending",
        created_by=actor_id,
        updated_by=actor_id,
    )
    upload = storage.presign_put(
        file.staging_key,
        content_type=file.content_type,
        size=file.size_bytes,
        expires_in=expires_in,
    )
    file.expires_at = upload.expires_at
    session.add(file)
    session.flush()
    return file, upload


def get_file(session: Session, file_id: uuid.UUID) -> UploadedFile:
    """The file if it belongs to the session's bound project, else ``OutOfScopeError`` (404).

    A portfolio session also *reads* its other projects; files are only served through
    the project they were uploaded to.
    """
    file = get_or_404(session, UploadedFile, file_id)
    if file.project_id != _project_id(session):
        raise OutOfScopeError("UploadedFile not found")
    return file


def _reject(file: UploadedFile, reason: Reason) -> None:
    file.status, file.rejection_reason = "rejected", reason


def complete_upload(
    session: Session, storage: Storage, file_id: uuid.UUID, *, actor_id: uuid.UUID | None = None
) -> UploadedFile:
    """Check the uploaded object's size and magic bytes, then queue the SHA-256 check.

    A wrong size or type rejects the file (and deletes the object); a missing object is an
    ``UploadStateError`` and the file stays ``pending`` so the client can retry the ``PUT``.
    """
    file = get_file(session, file_id)
    if file.created_by is not None and file.created_by != actor_id:
        raise NotUploaderError("only the uploader can complete an upload")
    if file.status != "pending":
        raise UploadStateError(f"the file is {file.status}")
    info = storage.head(file.staging_key)
    if info is None:
        raise UploadStateError("the file has not been uploaded")
    file.updated_by = actor_id
    if info.size != file.size_bytes:
        _reject(file, "size_mismatch")
    elif not matches_magic(
        file.content_type, storage.read(file.staging_key, length=MAGIC_SNIFF_BYTES)
    ):
        _reject(file, "type_mismatch")
    else:
        file.status = "verifying"
        kind = VERIFY_LARGE_KIND if file.size_bytes > LARGE_FILE_BYTES else VERIFY_KIND
        job: Job = create_job(
            session, _job_kind(kind), {"file_id": str(file.id)}, actor_id=actor_id
        )
        file.job_id = job.id
    session.flush()
    if file.status == "rejected":
        storage.delete(file.staging_key)
    return file


def download_request(
    session: Session, storage: Storage, file_id: uuid.UUID, *, expires_in: int
) -> PresignedRequest:
    """A presigned ``GET`` with the served type and ``Content-Disposition`` (H-10)."""
    file = get_file(session, file_id)
    if file.status != "available" or file.storage_key is None:
        raise UploadStateError(f"the file is {file.status}")
    return storage.presign_get(
        file.storage_key,
        filename=file.filename,
        content_type=file.content_type,
        expires_in=expires_in,
    )


# --- job side ---------------------------------------------------------------------------------


def _check(
    storage: Storage, key: str, expected: tuple[int, str, str], scanner: Scanner
) -> tuple[Reason | None, str | None, str | None]:
    """Check the object at ``key``: size, magic bytes, SHA-256, scan.

    Returns ``(reason, verdict, signature)``. ``ScannerUnavailableError`` propagates (the
    caller keeps the quarantine copy for the retry); an object the scanner refuses to scan
    (over its stream limit) is ``verify_failed``: it is never published unscanned.
    """
    size, content_type, sha256 = expected
    info = storage.head(key)
    if info is None:
        return "missing_object", None, None
    if info.size != size:
        return "size_mismatch", None, None
    if not matches_magic(content_type, storage.read(key, length=MAGIC_SNIFF_BYTES)):
        return "type_mismatch", None, None
    if sha256_of(storage, key) != sha256:
        return "sha256_mismatch", None, None
    try:
        clean, signature = outcome(scanner.scan(storage, key))
    except ScanRefusedError:
        return "verify_failed", None, None
    if clean:
        return None, "clean", None
    return "infected", "infected", signature


def verify_upload(
    db: Database, tenancy: TenancyContext, file_id: uuid.UUID, *, storage: Storage, scanner: Scanner
) -> dict[str, Any]:
    """Quarantine, check and publish the uploaded bytes, or reject them.

    Runs in the worker (``uploads.verify``). The client's presigned ``PUT`` stays valid
    after ``complete``, so staging is first copied to the file's quarantine key (no URL is
    ever issued for it) and deleted; every check then runs on that copy, and that same copy
    is published. An existing content key is never overwritten: it already holds those
    bytes. Idempotent: a file no longer ``verifying`` is left as it is. Staging and
    quarantine objects are deleted in every outcome, an error included, with one exception:
    when the scanner is unavailable (``ScannerUnavailableError``, re-raised) the file stays
    ``verifying`` and its quarantine copy is kept for the job's retry (T-0218); the orphan
    sweep deletes it once the file is no longer ``verifying``.
    """
    with db.unit_of_work(tenancy) as session:
        file = get_file(session, file_id)
        if file.status != "verifying":
            return {"file_id": str(file_id), "status": file.status}
        staging, quarantine, content = file.staging_key, file.quarantine_key, file.content_key
        expected = (file.size_bytes, file.content_type, file.sha256)
    keep_quarantine = False
    try:
        if storage.head(staging) is not None:
            storage.copy(staging, quarantine)
        storage.delete(staging)
        try:
            reason, verdict, signature = _check(storage, quarantine, expected, scanner)
        except ScannerUnavailableError:
            keep_quarantine = True
            raise
        if reason is None and storage.head(content) is None:
            storage.copy(quarantine, content)
    finally:
        if not keep_quarantine:
            storage.delete(quarantine)
        storage.delete(staging)
    with db.unit_of_work(tenancy) as session:
        file = get_file(session, file_id)
        # Re-read under a row lock (PostgreSQL): the orphan sweep may be rejecting this
        # stuck file right now; whoever locks first decides, the other sees the new status.
        session.refresh(file, with_for_update=True)
        if file.status == "verifying":
            file.scan_result = verdict
            file.scan_signature = signature
            if reason is not None:
                _reject(file, reason)
            else:
                file.status, file.storage_key, file.verified_at = "available", content, utcnow()
        status = file.status
    result: dict[str, Any] = {"file_id": str(file_id), "status": status}
    if reason is not None:
        result["reason"] = reason
    return result


def sweep_orphans(
    db: Database,
    tenancy: TenancyContext,
    *,
    storage: Storage,
    now: datetime | None = None,
    grace: timedelta = DEFAULT_SWEEP_GRACE,
) -> dict[str, int]:
    """Expire ``pending`` files past their URL expiry + ``grace``; reject stuck ``verifying``
    files; delete orphaned objects.

    A ``verifying`` file whose verify job ``failed`` (read through the jobs service), or that
    has been ``verifying`` for longer than ``VERIFY_STUCK_AFTER``, is rejected with
    ``verify_failed`` (T-0215, TD-209). A staging or quarantine object is an orphan when no
    ``pending``/``verifying`` file owns it and it is older than ``grace`` (a re-``PUT`` after
    verification, a worker that died mid-verify). Runs per project (``uploads.sweep_orphans``,
    queued hourly in every project by the jobs scheduler), so RLS stays on.
    """
    if tenancy.project_id is None:
        raise RuntimeError("the orphan sweep runs per project")
    moment = now or utcnow()
    cutoff = moment - grace
    project = UploadedFile.project_id == tenancy.project_id
    with db.unit_of_work(tenancy) as session:
        stale = session.scalars(
            select(UploadedFile).where(
                project, UploadedFile.status == "pending", UploadedFile.expires_at < cutoff
            )
        ).all()
        for file in stale:
            file.status = "expired"
        too_old = set(
            session.scalars(
                select(UploadedFile.id).where(
                    project,
                    UploadedFile.status == "verifying",
                    UploadedFile.updated_at < moment - VERIFY_STUCK_AFTER,
                )
            )
        )
        # Locked (PostgreSQL), so a verify job finishing at the same moment cannot publish a
        # file this sweep rejects, or the other way round (verify_upload locks the row too).
        verifying = session.scalars(
            select(UploadedFile)
            .where(project, UploadedFile.status == "verifying")
            .with_for_update()
        ).all()
        stuck = [
            f
            for f in verifying
            if f.id in too_old
            or (f.job_id is not None and job_status(session, f.job_id) == "failed")
        ]
        for file in stuck:
            _reject(file, "verify_failed")
        session.flush()  # the sessions do not autoflush
        live = {
            str(i)
            for i in session.scalars(
                select(UploadedFile.id).where(
                    project, UploadedFile.status.in_(("pending", "verifying"))
                )
            )
        }
    deleted = 0
    for prefix in (staging_prefix, quarantine_prefix):
        for obj in storage.list(prefix(tenancy.tenant_id, tenancy.project_id)):
            if obj.key.rsplit("/", 1)[-1] not in live and obj.last_modified < cutoff:
                storage.delete(obj.key)
                deleted += 1
    return {"expired": len(stale), "deleted": deleted, "rejected": len(stuck)}


def schedule_sweep(session: Session, *, actor_id: uuid.UUID | None = None) -> Job:
    """Queue an orphan sweep of the session's project (batch queue)."""
    _project_id(session)
    return create_job(session, _job_kind(SWEEP_KIND), {}, actor_id=actor_id)


# --- quarantine monitor (T-0221, diff §4.7): System console, every project of the tenant ------

# The statuses the monitor shows: still in the check, refused, or never completed.
MONITORED_STATUSES: Final = ("verifying", "rejected", "expired")
MAX_QUARANTINE_LIST: Final = 200


@dataclass(frozen=True, slots=True)
class QuarantineFile:
    """An upload row as the console shows it: metadata only, never a URL or a storage key."""

    id: uuid.UUID
    project_id: uuid.UUID
    project_code: str | None
    module: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: str
    reason: str | None
    scan_signature: str | None
    uploader_id: uuid.UUID | None
    object_deleted: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class QueuedJob:
    """A job the console queued: its id, project and kind."""

    job_id: uuid.UUID
    project_id: uuid.UUID
    kind: str


@dataclass(frozen=True, slots=True)
class QuarantineCount:
    project_id: uuid.UUID
    project_code: str | None
    status: str
    reason: str | None
    count: int


def _console_tenant(session: Session) -> uuid.UUID:
    """The tenant of the caller's tenant-level request session (checked before any write)."""
    context = session_tenancy(session)
    if context is None or context.project_id is not None:
        raise RuntimeError("the quarantine monitor needs a tenant-level bound session")
    return context.tenant_id


def _monitor_filters(
    statuses: Collection[str] | None, reason: str | None, project_id: uuid.UUID | None
) -> list[Any]:
    wanted = [s for s in (statuses or MONITORED_STATUSES) if s in MONITORED_STATUSES]
    filters: list[Any] = [UploadedFile.status.in_(wanted)]
    if reason is not None:
        filters.append(UploadedFile.rejection_reason == reason)
    if project_id is not None:
        filters.append(UploadedFile.project_id == project_id)
    return filters


def _codes(session: Session, project_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, str]:
    return {p.id: p.code for p in projects.list_projects(session, set(project_ids))}


def quarantine_files(
    session: Session,
    *,
    statuses: Collection[str] | None = None,
    reason: str | None = None,
    project_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[QuarantineFile], int]:
    """``verifying``, ``rejected`` and ``expired`` uploads the caller's RLS shows, most
    recently changed first, and their total.

    Read under the normal project RLS of the caller's tenant-level session: a
    ``system_admin`` sees the rows of every project of its tenant (ADR 0016, T-0274), never
    through a bypass role. A rejected or expired file's bytes are gone (deleted at rejection,
    TD-397; an expired upload's staging object by the same sweep that expires it), so
    ``object_deleted`` is set; there is no purge. A project soft-deleted since keeps its
    rows, without a code. Reason codes are passed through, never enumerated (T-0169).
    """
    _console_tenant(session)
    filters = _monitor_filters(statuses, reason, project_id)
    total = session.scalar(select(func.count()).select_from(UploadedFile).where(*filters)) or 0
    rows = list(
        session.scalars(
            select(UploadedFile)
            .where(*filters)
            .order_by(UploadedFile.updated_at.desc(), UploadedFile.id)
            .limit(max(1, min(limit, MAX_QUARANTINE_LIST)))
            .offset(max(0, offset))
        )
    )
    codes = _codes(session, {f.project_id for f in rows if f.project_id is not None})
    files = [
        QuarantineFile(
            id=f.id,
            project_id=f.project_id,
            project_code=codes.get(f.project_id),
            module=f.module,
            filename=f.filename,
            content_type=f.content_type,
            size_bytes=f.size_bytes,
            sha256=f.sha256,
            status=f.status,
            reason=f.rejection_reason,
            scan_signature=f.scan_signature,
            uploader_id=f.created_by,
            object_deleted=f.status != "verifying",
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in rows
        if f.project_id is not None
    ]
    return files, int(total)


def quarantine_counts(
    session: Session,
    *,
    statuses: Collection[str] | None = None,
    reason: str | None = None,
    project_id: uuid.UUID | None = None,
) -> list[QuarantineCount]:
    """Per project, status and reason code: the number of monitored uploads.

    A ``GROUP BY`` under the same normal RLS as ``quarantine_files``; no ``SECURITY DEFINER``
    function (ADR 0016). Sorted by project code, status and reason.
    """
    _console_tenant(session)
    rows = session.execute(
        select(
            UploadedFile.project_id,
            UploadedFile.status,
            UploadedFile.rejection_reason,
            func.count(),
        )
        .where(*_monitor_filters(statuses, reason, project_id))
        .group_by(UploadedFile.project_id, UploadedFile.status, UploadedFile.rejection_reason)
    ).all()
    codes = _codes(session, {r[0] for r in rows if r[0] is not None})
    counts = [
        QuarantineCount(
            project_id=pid,
            project_code=codes.get(pid),
            status=str(state),
            reason=why,
            count=int(n),
        )
        for pid, state, why, n in rows
        if pid is not None
    ]
    return sorted(
        counts, key=lambda c: (c.project_code or "", str(c.project_id), c.status, c.reason or "")
    )


def _console_audit(
    session: Session, event: str, payload: dict[str, Any], actor_id: uuid.UUID | None
) -> None:
    # After the job write committed in its own unit of work, as the queue monitor does: a
    # failed commit of the request session leaves the action unaudited (TD-455).
    audit.append(
        session,
        CONSOLE_CHAIN.format(tenant_id=_console_tenant(session)),
        {"event": event, **payload},
        actor_id=actor_id,
        source="api",
    )


# A verify job in one of these states is still going to decide the file (or is retrying
# after a scanner outage): a re-check would run a second job on the same quarantine object.
_CHECK_ACTIVE: Final = frozenset({"queued", "running"})


def recheck_upload(
    session: Session,
    db: Database,
    file_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None,
    storage: Storage | None = None,
) -> QueuedJob:
    """Queue the check of a file stuck in ``verifying`` again (audited); the new job.

    ``session`` is the caller's tenant-level request session: the row must be visible to it
    under normal RLS (a ``system_admin`` sees every project of its tenant, ADR 0016), else
    ``OutOfScopeError`` (404). Not ``verifying``: ``UploadStateError``. Stuck means no verify
    job is still working on the file: its current job (read through the jobs service) is not
    ``queued`` or ``running``, else ``CheckRunningError``. Two verify jobs must never run at
    once: both use the same quarantine object and each deletes it before it writes the
    outcome, so the later one could reject a clean file as ``missing_object``. The uploaded
    bytes must still exist, in staging or as the quarantine copy a scanner outage keeps
    (T-0218), else ``ObjectGoneError``. A new verify job is created in the file's own project
    scope (as ``complete_upload`` does; never a bypass role) and becomes the file's job, so the
    sweep judges the new job, and the stuck clock (``VERIFY_STUCK_AFTER``) restarts.
    ``storage`` defaults to the configured object store.
    """
    storage = storage or get_storage()
    tenant_id = _console_tenant(session)
    file = get_or_404(session, UploadedFile, file_id)
    if file.tenant_id != tenant_id or file.project_id is None:  # defence in depth
        raise OutOfScopeError("UploadedFile not found")
    if file.status != "verifying":
        raise UploadStateError(f"the file is {file.status}")
    project_id = file.project_id
    scope = TenancyContext(tenant_id=tenant_id, project_id=project_id, user_id=actor_id)
    with db.unit_of_work(scope) as scoped:
        row = get_file(scoped, file_id)
        scoped.refresh(row, with_for_update=True)
        if row.status != "verifying":
            raise UploadStateError(f"the file is {row.status}")
        # Read in the file's own scope, where its job lives (the worker's scope).
        current = job_status(scoped, row.job_id) if row.job_id is not None else None
        if current in _CHECK_ACTIVE:
            raise CheckRunningError(f"the file's check is {current}")
        if storage.head(row.staging_key) is None and storage.head(row.quarantine_key) is None:
            raise ObjectGoneError("the uploaded object is gone")
        kind = VERIFY_LARGE_KIND if row.size_bytes > LARGE_FILE_BYTES else VERIFY_KIND
        job: Job = create_job(scoped, _job_kind(kind), {"file_id": str(row.id)}, actor_id=actor_id)
        row.job_id, row.updated_by, row.updated_at = job.id, actor_id, utcnow()
        queued = QueuedJob(job.id, project_id, kind)
    _console_audit(
        session,
        "upload_rechecked",
        {"file_id": str(file_id), "project_id": str(project_id), "job_id": str(queued.job_id)},
        actor_id,
    )
    return queued


def queue_project_sweep(
    session: Session, db: Database, project_id: uuid.UUID, *, actor_id: uuid.UUID | None
) -> QueuedJob:
    """Queue the orphan sweep of ``project_id`` now (audited housekeeping); the job.

    The project must be a live project the caller's session shows (a ``system_admin`` sees
    every project of its tenant, ADR 0016), else ``OutOfScopeError`` (404). The job is created
    in the project's own scope and runs there, as the hourly sweep does (T-0215).
    """
    tenant_id = _console_tenant(session)
    project = projects.get_project(session, project_id)
    if project.tenant_id != tenant_id:  # defence in depth: RLS and the ORM scope hide it
        raise OutOfScopeError("Project not found")
    scope = TenancyContext(tenant_id=tenant_id, project_id=project.id, user_id=actor_id)
    with db.unit_of_work(scope) as scoped:
        queued = QueuedJob(schedule_sweep(scoped, actor_id=actor_id).id, project.id, SWEEP_KIND)
    _console_audit(
        session,
        "upload_sweep_queued",
        {"project_id": str(project_id), "job_id": str(queued.job_id)},
        actor_id,
    )
    return queued


# --- System console: deployment stats and health (T-0219, diff §4.7) ---------------------------


@register_stats_provider("uploads")
def deployment_stats(session: Session) -> dict[str, int]:
    """Uploaded files per status and the bytes of the ``available`` ones, of every project the
    caller's RLS shows (a ``system_admin``: every project of the tenant, ADR 0016); counts
    only, filtered on the bound tenant explicitly."""
    context = session_tenancy(session)
    if context is None:
        raise RuntimeError("the uploads stats need a session bound to a tenant")
    rows = session.execute(
        select(
            UploadedFile.status, func.count(), func.coalesce(func.sum(UploadedFile.size_bytes), 0)
        )
        .where(UploadedFile.tenant_id == context.tenant_id)
        .group_by(UploadedFile.status)
    ).all()
    counts = {str(status): (int(n), int(size)) for status, n, size in rows}
    stats = {f"files_{status}": counts.get(status, (0, 0))[0] for status in STATUSES}
    stats["available_bytes"] = counts.get("available", (0, 0))[1]
    return stats


def object_store_totals(
    tenant_id: uuid.UUID, storage: Storage | None = None
) -> dict[str, dict[str, int]]:
    """Object count and bytes per storage class of the tenant's objects (``STANDARD``,
    ``GLACIER_IR``, ``DEEP_ARCHIVE``, ``other``): content, staging and quarantine keys.

    Slow (a paginated listing of the bucket): the admin module runs it as a ``batch`` job."""
    totals = {name: {"objects": 0, "bytes": 0} for name in (*STORAGE_CLASSES, OTHER_CLASS)}
    store = storage or get_storage()
    for prefix in tenant_prefixes(tenant_id):
        for obj in store.list(prefix):
            known = obj.storage_class in STORAGE_CLASSES
            bucket = totals[obj.storage_class if known else OTHER_CLASS]
            bucket["objects"] += 1
            bucket["bytes"] += obj.size
    return totals


def ping_storage(storage: Storage | None = None) -> bool | None:
    """The health probe (T-0219): head bucket; ``None`` for a storage without a probe.

    Answers ``True``/``False`` only, never the endpoint, bucket or credentials."""
    store = storage or get_storage()
    ping = getattr(store, "ping", None)
    if not callable(ping):
        return None
    try:
        return bool(ping())
    except Exception:  # unreachable, denied, misconfigured: down, never the detail
        return False
