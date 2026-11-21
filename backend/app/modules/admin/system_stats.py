"""Deployment stats of the System console (T-0219, diff §4.7): counts and byte totals only.

Everything is read in the caller's tenant-level request session under its normal RLS, which
shows a ``system_admin`` every project of the tenant (ADR 0016); no ``SECURITY DEFINER``
function and no bypass role. This module never reads another module's table:

* users and devices: core auth (``user_counts``, ``device_counts``);
* projects: the projects service (``project_counts``);
* everything else: the stats providers modules register in their own ``service.py``
  (``app.core.stats.registry``), limited to the modules this app loaded;
* object-store totals: the newest succeeded ``admin.object_store_totals`` job (jobs service).

Every call is audited in the tenant-level chain ``admin-system:<tenant>``.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth import devices as device_registry
from app.core.auth import service as users
from app.core.stats.registry import STATS_REGISTRY
from app.core.tenancy.rls import session_tenancy
from app.modules.admin.jobs import OBJECT_STORE_KIND
from app.modules.admin.schemas import (
    DeviceCounts,
    ObjectStoreTotals,
    ProjectCounts,
    StorageClassTotals,
    SystemStatsRead,
    UserCounts,
)
from app.modules.jobs import service as jobs
from app.modules.projects import service as projects

STORAGE_CLASS_ORDER = ("STANDARD", "GLACIER_IR", "DEEP_ARCHIVE", "other")


def console_tenant(session: Session) -> uuid.UUID:
    """The tenant of the caller's tenant-level request session."""
    context = session_tenancy(session)
    if context is None or context.project_id is not None:
        raise RuntimeError("the System console needs a tenant-level bound session")
    return context.tenant_id


def audit_call(
    session: Session,
    event: str,
    actor_id: uuid.UUID | None,
    details: Mapping[str, object] | None = None,
) -> None:
    """Append ``event`` to ``admin-system:<tenant>`` (the System console's chain)."""
    chain = jobs.CONSOLE_CHAIN.format(tenant_id=console_tenant(session))
    payload = {"event": event, **(details or {})}
    audit.append(session, chain, payload, actor_id=actor_id, source="api")


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _storage_classes(result: Mapping[str, Any] | None) -> list[StorageClassTotals]:
    """The cached totals, numbers only (anything else in the row is ignored)."""
    classes = (result or {}).get("storage_classes")
    if not isinstance(classes, Mapping):
        return []
    totals: list[StorageClassTotals] = []
    for name in STORAGE_CLASS_ORDER:
        entry = classes.get(name)
        entry = entry if isinstance(entry, Mapping) else {}
        totals.append(
            StorageClassTotals.model_validate(
                {
                    "storage_class": name,
                    "objects": _count(entry.get("objects")),
                    "bytes": _count(entry.get("bytes")),
                }
            )
        )
    return totals


def object_store_totals(session: Session) -> ObjectStoreTotals:
    last = jobs.last_succeeded_job(session, OBJECT_STORE_KIND)
    return ObjectStoreTotals(
        as_of=last.finished_at if last is not None else None,
        refreshing=jobs.pending_job(session, OBJECT_STORE_KIND) is not None,
        storage_classes=_storage_classes(last.result if last is not None else None),
    )


def deployment_stats(session: Session, *, modules: Collection[str]) -> SystemStatsRead:
    """Every count of the deployment the caller's RLS shows (``modules``: the loaded ones)."""
    console_tenant(session)
    return SystemStatsRead(
        projects=ProjectCounts.model_validate(projects.project_counts(session)),
        users=UserCounts.model_validate(users.user_counts(session)),
        devices=DeviceCounts.model_validate(device_registry.device_counts(session)),
        modules=STATS_REGISTRY.collect(session, modules=modules),
        object_store=object_store_totals(session),
    )
