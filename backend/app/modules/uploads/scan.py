"""Malware scan seam (T-0016). The stub says every object is clean.

A real scanner (e.g. ClamAV over ``clamd``) implements ``Scanner`` and is installed with
``set_scanner`` at process start; the verification job calls ``get_scanner().scan(...)``
after the SHA-256 check and before the object becomes available. A real scanner is
TD-211.
"""

from __future__ import annotations

from typing import Protocol

from app.core.config import Settings
from app.modules.uploads.storage import Storage


class Scanner(Protocol):
    def scan(self, storage: Storage, key: str) -> str:
        """``"clean"`` or ``"infected"``; anything else is treated as infected."""
        ...


class StubScanner:
    """No scanning yet: always ``"clean"``."""

    def scan(self, storage: Storage, key: str) -> str:
        return "clean"


_scanner: Scanner | None = None


def set_scanner(scanner: Scanner | None) -> None:
    """Install a scanner for the process; ``None`` restores the stub."""
    global _scanner
    _scanner = scanner


def get_scanner() -> Scanner:
    return _scanner if _scanner is not None else StubScanner()


def require_real_scanner(settings: Settings) -> None:
    """Refuse a production worker whose uploads would be marked clean unscanned (T-0213).

    Registered as a worker startup check by ``uploads/jobs.py``. The stub stays usable in
    development and tests; production needs a real scanner installed with ``set_scanner``
    before the worker starts (TD-211), or ``KERP_UPLOAD_SCANNER_ALLOW_STUB=true``.
    """
    if settings.environment != "production" or settings.upload_scanner_allow_stub:
        return
    if isinstance(get_scanner(), StubScanner):
        raise RuntimeError(
            "uploads would be verified by the always-clean stub scanner: install a real "
            "scanner (TD-211) or set KERP_UPLOAD_SCANNER_ALLOW_STUB=true to accept unscanned "
            "uploads in production"
        )
