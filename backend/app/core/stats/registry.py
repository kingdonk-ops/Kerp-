"""Stats-provider registry: every module contributes its own counts (T-0219, diff §4.7).

The System console's deployment stats (``GET /api/v1/admin/system/stats``) show what each
module counts without the admin module ever reading another module's tables (CLAUDE.md §6):
a module registers a provider in its own ``service.py``::

    @register_stats_provider("asset_management")
    def stats(session: Session) -> dict[str, int]:
        return {"asset_nodes": ...}

A provider gets the caller's tenant-level request session and counts under its normal RLS
(a ``system_admin`` session reads every project of its tenant, ADR 0016); it filters on the
session's tenant explicitly ("visibility is not scope"). It returns **numbers only**: metric
name -> non-negative ``int``, never ids, names or rows. Anything else is a bug and raises.
Core never imports a module, so the registry lives here and modules import it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping
from typing import Final

from sqlalchemy.orm import Session

StatsProvider = Callable[[Session], Mapping[str, int]]
NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class StatsRegistry:
    """Module name -> its provider. One process-wide instance: ``STATS_REGISTRY``."""

    def __init__(self) -> None:
        self._providers: dict[str, StatsProvider] = {}

    def add(self, module: str, provider: StatsProvider) -> StatsProvider:
        if not NAME_PATTERN.match(module):
            raise ValueError(f"stats provider name {module!r} must be a module name")
        known = self._providers.get(module)
        if known is not None and _identity(known) != _identity(provider):
            raise ValueError(f"module {module!r} already has a stats provider")
        self._providers[module] = provider  # a re-imported module replaces its own
        return provider

    def modules(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def collect(
        self, session: Session, *, modules: Collection[str] | None = None
    ) -> dict[str, dict[str, int]]:
        """Every provider's counts by module (only ``modules``, if given), checked."""
        counts: dict[str, dict[str, int]] = {}
        for module in self.modules():
            if modules is not None and module not in modules:
                continue
            counts[module] = checked(module, self._providers[module](session))
        return counts


def _identity(provider: StatsProvider) -> tuple[str, str]:
    module = getattr(provider, "__module__", "")
    return str(module), str(getattr(provider, "__qualname__", repr(provider)))


def checked(module: str, values: Mapping[str, int]) -> dict[str, int]:
    """``values`` as a plain dict if every key is a metric name and every value a count."""
    result: dict[str, int] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not NAME_PATTERN.match(key):
            raise ValueError(f"stats of {module!r}: {key!r} is not a metric name")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"stats of {module!r}: {key!r} is not a count")
        result[key] = value
    return result


STATS_REGISTRY: Final = StatsRegistry()


def register_stats_provider(module: str) -> Callable[[StatsProvider], StatsProvider]:
    """Register the decorated function as ``module``'s stats provider (in its ``service.py``)."""

    def decorate(provider: StatsProvider) -> StatsProvider:
        return STATS_REGISTRY.add(module, provider)

    return decorate
