"""Domain modules live here as ``app/modules/<name>/`` (see CLAUDE.md section 6).

Each module provides ``manifest.py``, ``models.py``, ``router.py``, ``service.py``,
``schemas.py`` and ``README.md``. The module loader (``app.core.module_loader``, T-0011)
discovers them through ``manifest.py``, mounts each router under ``/api/v1/<name>`` and
gates it by the per-project enable/disable setting.
"""
