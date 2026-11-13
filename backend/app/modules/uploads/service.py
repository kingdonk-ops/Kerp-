"""Upload lifecycle (T-0016): presign -> complete -> verify (job) -> download; orphan sweep.

Other modules call this, never the table. Every call works on a session (or, for the job
side, a ``Database`` and a ``TenancyContext``) bound to the file's project; the router
checks the caller's capability first. Storage I/O never holds a transaction open in the
job side: the row is read, the bytes are hashed, then the outcome is written.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import Database, utcnow
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import OutOfScopeError, get_or_404
from app.modules.jobs.service import JOB_REGISTRY, create_job
from app.modules.uploads.models import UploadedFile, staging_prefix
from app.modules.uploads.scan import Scanner
from app.modules.uploads.schemas import UploadCreate
from app.modules.uploads.storage import (
    MAGIC_SNIFF_BYTES,
    PresignedRequest,
    Storage,
    matches_magic,
    sha256_of,
)

if TYPE_CHECKING:
    from app.modules.jobs.models import Job
    from app.modules.jobs.registry import JobKind

__all__ = [
    "SWEEP_KIND",
    "VERIFY_KIND",
    "FileTooLargeError",
    "UploadStateError",
    "complete_upload",
    "download_request",
    "get_file",
    "presign_upload",
    "schedule_sweep",
    "sweep_orphans",
    "verify_upload",
]

VERIFY_KIND: Final = "uploads.verify"
SWEEP_KIND: Final = "uploads.sweep_orphans"
DEFAULT_SWEEP_GRACE: Final = timedelta(hours=1)

# Stored as ``rejection_reason``: codes, so the client translates them (i18n).
Reason = Literal["size_mismatch", "type_mismatch", "sha256_mismatch", "missing_object", "infected"]


class FileTooLargeError(ValueError):
    """The declared size is over ``upload_max_bytes`` (the API answers 413)."""


class UploadStateError(RuntimeError):
    """The file is not in a state that allows the call (the API answers 409)."""


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
        job: Job = create_job(
            session, _job_kind(VERIFY_KIND), {"file_id": str(file.id)}, actor_id=actor_id
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


def verify_upload(
    db: Database, tenancy: TenancyContext, file_id: uuid.UUID, *, storage: Storage, scanner: Scanner
) -> dict[str, Any]:
    """Recompute the SHA-256, scan, then publish to the content key or reject.

    Runs in the worker (``uploads.verify``). Idempotent: a file no longer ``verifying`` is
    left as it is. The staging object is deleted in every outcome.
    """
    with db.unit_of_work(tenancy) as session:
        file = get_file(session, file_id)
        if file.status != "verifying":
            return {"file_id": str(file_id), "status": file.status}
        staging, content, expected = file.staging_key, file.content_key, file.sha256
    reason: Reason | None = None
    verdict: str | None = None
    if storage.head(staging) is None:
        reason = "missing_object"
    elif sha256_of(storage, staging) != expected:
        reason = "sha256_mismatch"
    else:
        verdict = "clean" if scanner.scan(storage, staging) == "clean" else "infected"
        if verdict == "clean":
            storage.copy(staging, content)
        else:
            reason = "infected"
    with db.unit_of_work(tenancy) as session:
        file = get_file(session, file_id)
        if file.status == "verifying":
            file.scan_result = verdict
            if reason is not None:
                _reject(file, reason)
            else:
                file.status, file.storage_key, file.verified_at = "available", content, utcnow()
        status = file.status
    storage.delete(staging)
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
    """Expire ``pending`` files past their URL expiry + ``grace``; delete orphaned staging objects.

    A staging object is an orphan when no ``pending``/``verifying`` file owns it and it is
    older than ``grace``. Runs per project (``uploads.sweep_orphans``), so RLS stays on.
    """
    if tenancy.project_id is None:
        raise RuntimeError("the orphan sweep runs per project")
    cutoff = (now or utcnow()) - grace
    project = UploadedFile.project_id == tenancy.project_id
    with db.unit_of_work(tenancy) as session:
        stale = session.scalars(
            select(UploadedFile).where(
                project, UploadedFile.status == "pending", UploadedFile.expires_at < cutoff
            )
        ).all()
        for file in stale:
            file.status = "expired"
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
    for obj in storage.list(staging_prefix(tenancy.tenant_id, tenancy.project_id)):
        if obj.key.rsplit("/", 1)[-1] not in live and obj.last_modified < cutoff:
            storage.delete(obj.key)
            deleted += 1
    return {"expired": len(stale), "deleted": deleted}


def schedule_sweep(session: Session, *, actor_id: uuid.UUID | None = None) -> Job:
    """Queue an orphan sweep of the session's project (batch queue)."""
    _project_id(session)
    return create_job(session, _job_kind(SWEEP_KIND), {}, actor_id=actor_id)
