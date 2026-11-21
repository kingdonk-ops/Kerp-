"""Job handlers of the uploads module (T-0016, ADR 0007 rule 4).

Both read object storage through boto3, which blocks, so the work runs in a thread.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Final

from app.modules.jobs.registry import (
    JobContext,
    register_startup_check,
    schedule_hourly_per_project,
)
from app.modules.jobs.service import JobError, RetryLaterError, register_job
from app.modules.uploads import service
from app.modules.uploads.scan import ScannerUnavailableError, get_scanner, require_real_scanner
from app.modules.uploads.storage import get_storage

# Production never verifies uploads with the always-clean stub (T-0213).
register_startup_check(require_real_scanner)

# Scanner down or slow (T-0218): retry with back-off, 30 s doubling up to 30 min (SAQ adds
# jitter), for longer than ``service.VERIFY_STUCK_AFTER``. The orphan sweep rejects the file
# with ``verify_failed`` at that limit; a retry after that finds it no longer ``verifying``.
VERIFY_ATTEMPTS: Final = 48
VERIFY_RETRY_DELAY_S: Final = 30.0
VERIFY_RETRY_MAX_DELAY_S: Final = 30 * 60.0


@register_job(
    service.VERIFY_LARGE_KIND,
    queue="batch",
    max_attempts=VERIFY_ATTEMPTS,
    retry_delay_s=VERIFY_RETRY_DELAY_S,
    retry_max_delay_s=VERIFY_RETRY_MAX_DELAY_S,
)
@register_job(
    service.VERIFY_KIND,
    queue="interactive",
    max_attempts=VERIFY_ATTEMPTS,
    retry_delay_s=VERIFY_RETRY_DELAY_S,
    retry_max_delay_s=VERIFY_RETRY_MAX_DELAY_S,
)
async def verify(job: JobContext) -> dict[str, Any]:
    """Quarantine, check and publish one completed upload.

    Interactive for ordinary files (the user is waiting); batch above
    ``service.LARGE_FILE_BYTES`` so a long hash never delays interactive jobs. An unavailable
    scanner leaves the file ``verifying`` and the job is retried later (T-0218).
    """
    try:
        file_id = uuid.UUID(str(job.payload["file_id"]))
    except (KeyError, ValueError):
        raise JobError("No file to verify") from None
    try:
        return await asyncio.to_thread(
            service.verify_upload,
            job.db,
            job.tenancy,
            file_id,
            storage=get_storage(),
            scanner=get_scanner(),
            job_id=job.job_id,  # a stale job of the file does nothing (T-0346)
        )
    except ScannerUnavailableError as exc:
        # The message never names the scanner's host or socket (``scan.py``).
        raise RetryLaterError(f"{exc}; the check is retried") from None


# The worker's hourly cron queues one sweep in every live project (T-0215, TD-212).
schedule_hourly_per_project(service.SWEEP_KIND)


@register_job(service.SWEEP_KIND, queue="batch")
async def sweep_orphans(job: JobContext) -> dict[str, Any]:
    """Expire abandoned uploads and delete orphaned staging objects of the job's project."""
    result = await asyncio.to_thread(
        service.sweep_orphans, job.db, job.tenancy, storage=get_storage()
    )
    return dict(result)
