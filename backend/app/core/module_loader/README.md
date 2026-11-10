# app/core/module_loader — module loader, manifest contract, per-project toggles (T-0011)

Adapted from Inspection-ERP `core/module_loader.py` (ADR 0001): manifest discovery,
topological sort, fail-fast on unknown or cyclic dependencies. Inspection-ERP's marketplace
entitlement gate is **not** ported; per-project enable/disable replaces it.

| File | What |
|---|---|
| `manifest.py` | `ModuleManifest`, `REQUIRED_FILES`, `RESERVED_NAMES`, `manifest_problems()` |
| `registry.py` | `ModuleRegistry` (modules in dependency order), `order_by_dependencies()`, `ModuleLoadError` |
| `loader.py` | `discover_modules()`, `mount_modules()`, `install_modules()` (called by `create_app`) |
| `models.py` | `project_module_setting` table (one row per project per toggled module; migration `0003`) |
| `service.py` | `project_states()`, `is_enabled()`, `set_enabled()` |
| `gate.py` | `module_gate(name)` route dependency (404 while off), `registry_of(app)` |
| `router.py`, `schemas.py` | Toggle API: `GET /api/v1/modules`, `PUT /api/v1/modules/{name}` |

## Writing a module

`app/modules/<name>/` must contain `manifest.py`, `models.py`, `router.py`, `service.py`,
`schemas.py` and `README.md` (CLAUDE.md §6). `manifest.py` defines:

```python
from app.core.module_loader.manifest import ModuleManifest

MANIFEST = ModuleManifest(
    name="quality",            # = the directory name, snake_case, 2-40 chars
    version="0.1.0",           # MAJOR.MINOR.PATCH
    depends_on=("assets",),    # modules whose service.py this one calls
    toggleable=True,           # False: platform module a project cannot switch off
    default_enabled=True,      # state for a project that never toggled it
)
```

`router.py` defines `router = APIRouter()` with paths relative to the module. The loader
mounts it at `/api/v1/<name>` (`_` becomes `-`, e.g. `field_time` → `/api/v1/field-time`).
`modules` is reserved (the toggle API).

## Start-up checks (fail fast)

`create_app()` calls `install_modules(app)`. It refuses to start, with **every** problem in
one `ModuleLoadError`, when a module:

- lacks a required file, lacks `MANIFEST`, or breaks the manifest contract
  (name ≠ directory, bad version, self/duplicate dependency, wrong types);
- depends on an unknown module, or modules depend on each other in a cycle;
- cannot be disabled but depends on a module that can (it could be switched off through it);
- has a `router.py` without an `APIRouter` named `router`.

Modules are mounted in dependency order; ties are broken by name.
`tests/test_module_loader.py::test_shipped_modules_meet_the_manifest_contract` loads the real
`app.modules`, so a broken module fails CI as well as start-up.

## Per-project enable/disable

A module is **enabled** for a project when its own setting is on (the stored row, else
`default_enabled`; always on when `toggleable=False`) **and** every module it depends on is
enabled. `set_enabled()` refuses (`ModuleToggleError`, 409) to enable a module whose
dependencies are off, to disable one an enabled module depends on (the message names which
to change first), and to touch a module that cannot be disabled. Repeating the current
setting writes nothing; every change is appended to the project's `project_modules` audit
chain (T-0006).

The tenant and project come from the session's tenancy binding (T-0007), never from
arguments or the URL. `project_module_setting` has `project_id NOT NULL` and is under
project RLS, so a project can only see and change its own settings.

**Gate.** Every route of a toggleable module gets `module_gate(name)` as a router dependency.
For a request whose context has a project, a disabled module answers
`404 {"detail": "Not Found"}`: the same body FastAPI gives an unknown path, so a disabled
module is indistinguishable from an absent one. The gate shares the request's `DbSession`.
Requests without a project in context (tenant-level) are not gated. Modules with
`toggleable=False` are never gated and need no database to serve.

**Toggle API.** `GET /api/v1/modules` lists every module (dependency order) with its state
for the caller's project; `PUT /api/v1/modules/{name}` with `{"enabled": bool}` changes it
and returns the module's new state. Both answer 404 without a project in context and 404 for
an unknown module. The admin capability guard comes with RBAC (TD-125); the UI is TD-127.
