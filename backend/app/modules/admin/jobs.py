"""Job handlers of the admin module (the module loader imports this file, T-0015)."""

from app.modules.admin import audit_jobs as _audit_jobs  # noqa: F401  (T-0222: verify-all)
