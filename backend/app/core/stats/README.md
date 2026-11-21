# app/core/stats — module stats providers (T-0219)

The System console's deployment stats (`GET /api/v1/admin/system/stats`, diff §4.7) show what
every module counts, and the admin module never reads another module's table (CLAUDE.md §6).
Each module contributes through a provider registered in its **own `service.py`**:

```python
from app.core.stats.registry import register_stats_provider


@register_stats_provider("asset_management")
def deployment_stats(session: Session) -> dict[str, int]:
    tenant = session_tenancy(session).tenant_id
    return {"asset_nodes": session.scalar(select(func.count(AssetNode.id)).where(...))}
```

| Rule | Why |
|---|---|
| The provider gets the caller's tenant-level request session and counts under its **normal RLS** | a `system_admin` session reads every project of its tenant (ADR 0016); no `SECURITY DEFINER` function, no bypass role |
| It filters on the session's tenant explicitly | visibility is not scope (`app/core/tenancy/README.md`) |
| It returns **numbers only**: metric name (`[a-z][a-z0-9_]{0,39}`) -> non-negative `int` | never ids, names or rows; anything else raises (`checked`) |
| One provider per module; a re-imported module replaces its own | a second, different provider for the same name raises |
| Only the modules the app loaded are collected (`collect(session, modules=...)`) | a provider of an unloaded module never runs |

Core never imports a module, so the registry lives here and modules import it. Providers today:
`asset_management`, `jobs`, `teams`, `uploads`; modules that land later add theirs (TD-403).
The metering snapshot (T-0302) is to extend this registry with a per-project, as-of-date call
rather than a second set of count queries: keep providers pure counts.

Tests: `tests/test_admin_system_stats.py`.
