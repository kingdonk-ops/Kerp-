"""Module loader, manifest contract and per-project enable/disable (T-0011).

See ``README.md`` in this package. Submodules are imported explicitly; this package imports
nothing, so ``app.core.db`` can register the settings table without an import cycle.
"""
