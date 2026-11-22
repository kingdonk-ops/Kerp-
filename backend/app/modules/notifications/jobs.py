"""The notifications module's jobs (ADR 0007): broadcast dispatch (T-0228).

A broadcast may reach every user of the tenant, so its e-mails are queued on the ``batch``
queue, in a thread (the database driver blocks), never in a request. Each recipient's e-mail is
then delivered by the mail worker like any other send. Resumable: a rerun (a cancelled run, a
lost worker) continues with the recipients still ``pending``.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.modules.jobs.registry import JobContext
from app.modules.jobs.service import JobError, register_job
from app.modules.notifications import broadcasts


@register_job(
    broadcasts.DISPATCH_KIND,
    queue="batch",
    timeout_s=broadcasts.DISPATCH_TIMEOUT_S,
    max_attempts=3,
    retry_delay_s=60,
)
async def dispatch_broadcast(job: JobContext) -> dict[str, Any]:
    """Queue one ``notifications.broadcast`` e-mail per recipient; counts as the result."""
    if job.tenancy.project_id is not None:
        raise JobError("A broadcast is a tenant-level job")
    try:
        broadcast_id = uuid.UUID(str(job.payload["broadcast_id"]))
    except (KeyError, ValueError):
        raise JobError("The job names no broadcast") from None
    return await asyncio.to_thread(broadcasts.dispatch, job.db, job.tenancy, broadcast_id)
