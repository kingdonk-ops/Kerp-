"""Tenant/project isolation: request context, PostgreSQL RLS and the ORM scope (T-0007).

See ``README.md`` in this package. Submodules are imported explicitly; this package
imports nothing, so ``app.core.db`` can register the ORM listeners without a cycle.
"""
