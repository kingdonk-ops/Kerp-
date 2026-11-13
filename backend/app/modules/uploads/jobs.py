"""Job handlers of the uploads module (T-0016, ADR 0007 rule 4).

Both read object storage through boto3, which blocks, so the work runs in a thread.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.modules.jobs.registry import JobContext
from app.modules.jobs.service import JobError, register_job
from app.modules.uploads import service
from app.modules.uploads.scan import get_scanner
from app.modules.uploads.storage import get_storage


@register_job(service.VERIFY_LARGE_KIND, queue="batch")
@register_job(service.VERIFY_KIND, queue="interactive")
async def verify(job: JobContext) -> dict[str, Any]:
    """Quarantine, check and publish one completed upload.

    Interactive for ordinary files (the user is waiting); batch above
    ``service.LARGE_FILE_BYTES`` so a long hash never delays interactive jobs.
    """
    try:
        file_id = uuid.UUID(str(job.payload["file_id"]))
    except (KeyError, ValueError):
        raise JobError("No file to verify") from None
    return await asyncio.to_thread(
        service.verify_upload,
        job.db,
        job.tenancy,
        file_id,
        storage=get_storage(),
        scanner=get_scanner(),
    )


@register_job(service.SWEEP_KIND, queue="batch")
async def sweep_orphans(job: JobContext) -> dict[str, Any]:
    """Expire abandoned uploads and delete orphaned staging objects of the job's project."""
    result = await asyncio.to_thread(
        service.sweep_orphans, job.db, job.tenancy, storage=get_storage()
    )
    return dict(result)
