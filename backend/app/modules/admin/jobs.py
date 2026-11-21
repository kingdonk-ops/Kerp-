"""Job handlers of the admin module (the module loader imports this file, T-0015).

* T-0222: the audit explorer's verify-all job (``audit_jobs.py``).
* T-0219: the System console's object-store totals, a ``batch`` job. Listing the bucket is
  slow (paginated), so it never runs in a request. The job is tenant-level (no project): the
  worker binds the job's bare tenant, and the uploads service lists only that tenant's key
  prefixes. Its ``result`` (on the job row) is the cache the stats endpoint shows with the
  job's ``finished_at`` as its "as of" time.
"""

from __future__ import annotations

import asyncio
from typing import Any, Final

from app.modules.admin import audit_jobs as _audit_jobs  # noqa: F401  (T-0222: verify-all)
from app.modules.jobs.registry import JobContext
from app.modules.jobs.service import JobError, register_job
from app.modules.uploads import service as uploads

OBJECT_STORE_KIND: Final = "admin.object_store_totals"


@register_job(OBJECT_STORE_KIND, queue="batch", timeout_s=3600)
async def object_store_totals(job: JobContext) -> dict[str, Any]:
    if job.tenancy.project_id is not None:
        raise JobError("The object-store totals are tenant-level")
    await job.update(5, "Listing the object store")
    totals = await asyncio.to_thread(uploads.object_store_totals, job.tenancy.tenant_id)
    return {"storage_classes": totals}
