"""Malware scan seam (T-0016) and the ClamAV scanner (T-0218, TD-211).

The verification job calls ``get_scanner().scan(storage, key)`` on the quarantine copy after
the SHA-256 check and before the object becomes available. A scan answers ``"clean"``,
``"infected"`` (T-0016's plain strings; anything else is infected too) or an ``Infected`` with
the scanner's signature name. A scanner that cannot answer (down, timeout, protocol error)
raises ``ScannerUnavailableError``: the file stays ``verifying`` and the job retries with
back-off (``uploads/jobs.py``); it never becomes ``available`` unscanned.

Which scanner runs is deployment configuration (``KERP_UPLOAD_SCANNER``): ``stub`` (always
clean; development and tests) or ``clamd`` (``ClamdScanner``, ``KERP_CLAMD_ADDRESS``). Tests
install their own with ``set_scanner``. The worker refuses the stub in production (T-0213).

``ClamdScanner`` speaks the clamd protocol over the standard library's ``socket`` (no extra
dependency): ``zINSTREAM`` with length-prefixed chunks and a zero-length terminator, and
``zPING`` -> ``PONG`` for the health probe (T-0219). Its errors never name the host, port or
socket path.
"""

from __future__ import annotations

import re
import socket
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final, Protocol
from urllib.parse import urlsplit

from app.core.config import Settings, get_settings
from app.modules.uploads.models import MAX_SIGNATURE
from app.modules.uploads.storage import Storage

# Bytes per INSTREAM chunk; clamd's own default StreamMaxLength is far larger.
CHUNK_BYTES: Final = 64 * 1024
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]+")


@dataclass(frozen=True, slots=True)
class Infected:
    """A detection, with the scanner's signature name (e.g. ``Eicar-Signature``)."""

    signature: str


ScanVerdict = str | Infected


class Scanner(Protocol):
    def scan(self, storage: Storage, key: str) -> ScanVerdict:
        """``"clean"``, ``"infected"`` or ``Infected(signature)``; any other string is
        treated as infected. Raises ``ScannerUnavailableError`` when it cannot answer."""
        ...


class ScannerUnavailableError(RuntimeError):
    """The scanner did not answer (down, timeout, protocol error): retry later.

    The message is safe to store: it never names the scanner's host, port or socket path.
    """


class ScanRefusedError(RuntimeError):
    """The scanner will never scan this object (it is over the stream limit): no retry."""


def outcome(verdict: ScanVerdict) -> tuple[bool, str | None]:
    """``(clean, signature)`` of a scan verdict; the signature is bounded and printable."""
    if isinstance(verdict, Infected):
        return False, bounded_signature(verdict.signature)
    return verdict == "clean", None


def bounded_signature(signature: str) -> str | None:
    """The signature name without control characters, at most ``MAX_SIGNATURE`` long."""
    text = _CONTROL.sub(" ", signature).strip()
    return text[:MAX_SIGNATURE] or None


class StubScanner:
    """No scanning: always ``"clean"`` (development and tests only, T-0213)."""

    def scan(self, storage: Storage, key: str) -> ScanVerdict:
        return "clean"


@dataclass(frozen=True, slots=True)
class ClamdAddress:
    """Where clamd listens: ``tcp`` with ``host``/``port``, or ``unix`` with ``path``."""

    family: str
    host: str = ""
    port: int = 0
    path: str = ""

    @classmethod
    def parse(cls, address: str) -> ClamdAddress:
        parts = urlsplit(address.strip())
        if parts.scheme == "unix" and parts.path and not parts.netloc:
            return cls("unix", path=parts.path)
        if parts.scheme == "tcp" and parts.hostname and parts.port is not None:
            return cls("tcp", host=parts.hostname, port=parts.port)
        raise ValueError("a clamd address is tcp://<host>:<port> or unix:///<path>")


class ClamdScanner:
    """ClamAV over the clamd socket (``INSTREAM``), for the SAQ worker (T-0218)."""

    def __init__(
        self,
        address: ClamdAddress,
        *,
        timeout: float = 30.0,
        max_bytes: int = 100 * 1024 * 1024,
        chunk_bytes: int = CHUNK_BYTES,
    ) -> None:
        if chunk_bytes < 1 or max_bytes < 1 or timeout <= 0:
            raise ValueError("chunk size, size limit and timeout must be positive")
        self.address = address
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.chunk_bytes = chunk_bytes

    def __repr__(self) -> str:  # never the address (logs, errors)
        return f"ClamdScanner(family={self.address.family!r})"

    def _connect(self) -> socket.socket:
        if self.address.family == "unix":
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            target: str | tuple[str, int] = self.address.path
        else:
            sock = socket.socket(socket.AF_INET6 if ":" in self.address.host else socket.AF_INET)
            target = (self.address.host, self.address.port)
        sock.settimeout(self.timeout)
        try:
            sock.connect(target)
        except OSError:
            sock.close()
            raise
        return sock

    def _reply(self, sock: socket.socket) -> str:
        """Read one ``\\0``-terminated reply (the ``z`` command form)."""
        data = bytearray()
        while not data.endswith(b"\0"):
            part = sock.recv(4096)
            if not part:
                break
            data += part
            if len(data) > 64 * 1024:
                raise ScannerUnavailableError("malware scanner sent an oversized reply")
        return data.rstrip(b"\0").decode("utf-8", "replace").strip()

    def ping(self) -> bool:
        """``True`` when clamd answers ``PING`` with ``PONG`` within the timeout."""
        try:
            with self._connect() as sock:
                sock.sendall(b"zPING\0")
                return self._reply(sock) == "PONG"
        except (OSError, ScannerUnavailableError):
            return False

    def _chunks(self, storage: Storage, key: str) -> Iterator[bytes]:
        """The object in pieces of at most ``chunk_bytes``; refuses more than ``max_bytes``."""
        sent = 0
        for chunk in storage.chunks(key):
            for start in range(0, len(chunk), self.chunk_bytes):
                piece = chunk[start : start + self.chunk_bytes]
                sent += len(piece)
                if sent > self.max_bytes:
                    raise ScanRefusedError("the file is larger than the malware scan limit")
                yield piece

    def scan(self, storage: Storage, key: str) -> ScanVerdict:
        info = storage.head(key)
        if info is not None and info.size > self.max_bytes:
            raise ScanRefusedError("the file is larger than the malware scan limit")
        try:
            with self._connect() as sock:
                try:
                    sock.sendall(b"zINSTREAM\0")
                    for piece in self._chunks(storage, key):
                        sock.sendall(struct.pack(">I", len(piece)) + piece)
                    sock.sendall(struct.pack(">I", 0))
                except (BrokenPipeError, ConnectionResetError):
                    pass  # clamd answered early and closed (its stream limit): read why
                reply = self._reply(sock)
        except TimeoutError:
            raise ScannerUnavailableError("malware scanner timed out") from None
        except OSError:
            raise ScannerUnavailableError("malware scanner unavailable") from None
        return _verdict(reply)


def _verdict(reply: str) -> ScanVerdict:
    """``stream: OK`` -> clean; ``stream: <signature> FOUND`` -> infected; else unavailable."""
    _, _, status = reply.partition(": ")
    if reply.startswith("stream:") and status == "OK":
        return "clean"
    if reply.startswith("stream:") and status.endswith(" FOUND"):
        name = bounded_signature(status[: -len(" FOUND")])
        return Infected(name) if name else "infected"
    if "size limit exceeded" in reply:
        raise ScanRefusedError("the file is larger than the malware scanner's stream limit")
    raise ScannerUnavailableError("malware scanner returned an error")


def scanner_from_settings(settings: Settings) -> Scanner:
    """The scanner the deployment configures (``KERP_UPLOAD_SCANNER``)."""
    if settings.upload_scanner == "clamd":
        if settings.clamd_address is None:  # the settings validator refuses this already
            raise RuntimeError("KERP_UPLOAD_SCANNER=clamd needs KERP_CLAMD_ADDRESS")
        return ClamdScanner(
            ClamdAddress.parse(settings.clamd_address),
            timeout=settings.clamd_timeout_seconds,
            max_bytes=settings.upload_scan_max_bytes,
        )
    return StubScanner()


_scanner: Scanner | None = None


def set_scanner(scanner: Scanner | None) -> None:
    """Install a scanner for the process (tests); ``None`` restores the configured one."""
    global _scanner
    _scanner = scanner


def get_scanner(settings: Settings | None = None) -> Scanner:
    """The installed scanner, else the one ``settings`` (default: the process's) configure."""
    if _scanner is not None:
        return _scanner
    return scanner_from_settings(settings or get_settings())


def ping_scanner(settings: Settings | None = None) -> bool | None:
    """The health probe (T-0219): clamd's ``PING``; ``None`` for a scanner without one (stub).

    Answers ``True``/``False`` only, never the address."""
    ping = getattr(get_scanner(settings), "ping", None)
    return bool(ping()) if callable(ping) else None


def require_real_scanner(settings: Settings) -> None:
    """Refuse a production worker whose uploads would be marked clean unscanned (T-0213).

    Registered as a worker startup check by ``uploads/jobs.py``. The stub stays usable in
    development and tests; production configures ``KERP_UPLOAD_SCANNER=clamd`` (T-0218), or
    sets ``KERP_UPLOAD_SCANNER_ALLOW_STUB=true`` to accept unscanned uploads explicitly.
    """
    if settings.environment != "production" or settings.upload_scanner_allow_stub:
        return
    if isinstance(get_scanner(settings), StubScanner):
        raise RuntimeError(
            "uploads would be verified by the always-clean stub scanner: set "
            "KERP_UPLOAD_SCANNER=clamd with KERP_CLAMD_ADDRESS, or set "
            "KERP_UPLOAD_SCANNER_ALLOW_STUB=true to accept unscanned uploads in production"
        )
