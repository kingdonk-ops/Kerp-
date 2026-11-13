"""Malware scan seam (T-0016). The stub says every object is clean.

A real scanner (e.g. ClamAV over ``clamd``) implements ``Scanner`` and is installed with
``set_scanner`` at process start; the verification job calls ``get_scanner().scan(...)``
after the SHA-256 check and before the object becomes available. A real scanner is a
follow-up TODO.
"""

from __future__ import annotations

from typing import Protocol

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
