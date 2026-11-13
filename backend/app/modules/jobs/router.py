"""Jobs API, mounted at ``/api/v1/jobs`` (T-0015).

* ``POST /jobs``: enqueue a kind registered with ``api=True`` in the caller's scope (202).
* ``GET /jobs/{id}``: poll it. Another tenant's or project's job is 404 (RLS + ORM scope).

Both need an authenticated user (401 otherwise). Per-kind capability checks arrive with
RBAC (T-0010).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status

from app.core.auth.deps import CurrentPrincipal
from app.core.db import DbSession
from app.modules.jobs import service
from app.modules.jobs.registry import JOB_REGISTRY, JobRegistry
from app.modules.jobs.schemas import JobCreate, JobRead

router = APIRouter(tags=["jobs"])

STATE_KEY = "job_registry"


def registry_of(request: Request) -> JobRegistry:
    """The app's job registry (``app.state.job_registry``, else the process-wide one)."""
    registry = getattr(request.app.state, STATE_KEY, None)
    return registry if isinstance(registry, JobRegistry) else JOB_REGISTRY


@router.post("", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def enqueue_job(
    body: JobCreate, principal: CurrentPrincipal, request: Request, session: DbSession
) -> JobRead:
    """Create the job row; the SAQ job is enqueued once the request's transaction commits."""
    try:
        job_kind = registry_of(request).get(body.kind)
    except service.UnknownJobKindError:
        job_kind = None
    if job_kind is None or not job_kind.api:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown job kind {body.kind!r}",
        )
    job = service.create_job(session, job_kind, body.payload, actor_id=principal.user_id)
    return JobRead.model_validate(job)


@router.get("/{job_id}", response_model=JobRead)
def poll_job(job_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession) -> JobRead:
    return JobRead.model_validate(service.get_job(session, job_id))
