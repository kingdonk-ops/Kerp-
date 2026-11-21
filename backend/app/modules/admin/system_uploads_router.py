"""System console: upload quarantine monitor, mounted with the admin API at
``/api/v1/admin/system/uploads`` (T-0221; diff §4.7).

Every endpoint is ``system_admin`` only: any other caller, a PER-09 of every project included,
gets 403 (an admin surface, not a project record). The uploads module does all of it through
its ``service.py`` (no table access here).

* ``GET  /system/uploads?status=&reason=&project=&limit=&offset=``: ``verifying``,
  ``rejected`` and ``expired`` uploads of every project of the tenant, paged.
* ``GET  /system/uploads/counts?status=&reason=&project=``: per project, status and reason
  code, the number of those uploads.
* ``POST /system/uploads/{file_id}/recheck``: queue the check of a file stuck in
  ``verifying`` again (409 ``NOT_VERIFYING``; 409 ``CHECK_RUNNING`` while its verify job is
  ``queued`` or ``running``; 409 ``OBJECT_GONE`` when its bytes are gone).
* ``POST /system/uploads/sweep?project=``: queue the project's orphan sweep now.

Rows are read under the caller's normal project RLS, which shows a ``system_admin`` every
project of its tenant (ADR 0016); no bypass role, no ``SECURITY DEFINER`` function. Re-check
and sweep are SAQ jobs created in the file's or project's own scope and are written to the
tenant-level console chain ``admin-system:<tenant>``. There is no purge: rejected bytes are
deleted at rejection (TD-397).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.auth.access import CallerIsSystemAdmin
from app.core.auth.deps import CurrentPrincipal
from app.core.db import Database, DbSession
from app.modules.uploads import service as uploads
from app.modules.uploads.schemas import (
    MonitoredStatus,
    QuarantineCountRead,
    QuarantineFileRead,
    QuarantineJobRead,
    QuarantinePage,
)

UPLOADS: Final = "/system/uploads"
MAX_REASON_FILTER: Final = 200

router = APIRouter(tags=["admin"])


def _require_system_admin(is_admin: CallerIsSystemAdmin) -> None:
    if not is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


SystemAdminOnly = Depends(_require_system_admin)

StatusFilter = Annotated[MonitoredStatus | None, Query(alias="status")]
ReasonFilter = Annotated[str | None, Query(min_length=1, max_length=MAX_REASON_FILTER)]
ProjectFilter = Annotated[uuid.UUID | None, Query()]


def _database(request: Request) -> Database:
    db: Database | None = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "the database is not configured")
    return db


def _statuses(value: MonitoredStatus | None) -> tuple[str, ...] | None:
    return (value,) if value is not None else None


def _queued(job: uploads.QueuedJob) -> QuarantineJobRead:
    return QuarantineJobRead(
        job_id=job.job_id, project_id=job.project_id, kind=job.kind, status="queued"
    )


@router.get(UPLOADS, response_model=QuarantinePage, dependencies=[SystemAdminOnly])
def quarantine_files(
    session: DbSession,
    state: StatusFilter = None,
    reason: ReasonFilter = None,
    project: ProjectFilter = None,
    limit: Annotated[int, Query(ge=1, le=uploads.MAX_QUARANTINE_LIST)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QuarantinePage:
    files, total = uploads.quarantine_files(
        session,
        statuses=_statuses(state),
        reason=reason,
        project_id=project,
        limit=limit,
        offset=offset,
    )
    return QuarantinePage(
        items=[QuarantineFileRead.model_validate(f) for f in files],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    f"{UPLOADS}/counts", response_model=list[QuarantineCountRead], dependencies=[SystemAdminOnly]
)
def quarantine_counts(
    session: DbSession,
    state: StatusFilter = None,
    reason: ReasonFilter = None,
    project: ProjectFilter = None,
) -> list[QuarantineCountRead]:
    counts = uploads.quarantine_counts(
        session, statuses=_statuses(state), reason=reason, project_id=project
    )
    return [QuarantineCountRead.model_validate(c) for c in counts]


@router.post(
    f"{UPLOADS}/sweep",
    response_model=QuarantineJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[SystemAdminOnly],
)
def sweep_project(
    project: Annotated[uuid.UUID, Query()],
    principal: CurrentPrincipal,
    request: Request,
    session: DbSession,
) -> QuarantineJobRead:
    job = uploads.queue_project_sweep(
        session, _database(request), project, actor_id=principal.user_id
    )
    return _queued(job)


@router.post(
    f"{UPLOADS}/{{file_id}}/recheck",
    response_model=QuarantineJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[SystemAdminOnly],
)
def recheck_file(
    file_id: uuid.UUID, principal: CurrentPrincipal, request: Request, session: DbSession
) -> QuarantineJobRead:
    try:
        job = uploads.recheck_upload(
            session, _database(request), file_id, actor_id=principal.user_id
        )
    except uploads.CheckRunningError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "CHECK_RUNNING", "message": str(exc)}
        ) from None
    except uploads.ObjectGoneError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "OBJECT_GONE", "message": str(exc)}
        ) from None
    except uploads.UploadStateError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "NOT_VERIFYING", "message": str(exc)}
        ) from None
    return _queued(job)
