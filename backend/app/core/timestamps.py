"""The one naive-to-aware timestamp helper (T-0349)."""

from datetime import UTC, datetime


def aware_utc(moment: datetime) -> datetime:
    """``moment`` with UTC attached when it is naive; an aware value is returned unchanged.

    SQLite hands ``TIMESTAMPTZ`` back naive; every value KERP stores is UTC.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
