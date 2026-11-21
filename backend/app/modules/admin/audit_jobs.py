"""The audit explorer's batch job (T-0222, ADR 0007 rule 4): verify every readable chain.

It re-hashes the whole ledger of the tenant, so it runs on the ``batch`` queue, in a thread
(the database driver blocks), never in a request. It needs no heavy library.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.modules.admin import audit_explorer
from app.modules.jobs.registry import JobContext
from app.modules.jobs.service import register_job


@register_job(
    audit_explorer.VERIFY_ALL_KIND, queue="batch", timeout_s=audit_explorer.VERIFY_ALL_TIMEOUT_S
)
async def verify_all(job: JobContext) -> dict[str, Any]:
    """Counts and the first broken chain / seq; stored as the job's result and audited."""
    return await asyncio.to_thread(
        audit_explorer.run_verify_all, job.db, job.tenancy, job.job_id, job.payload
    )
