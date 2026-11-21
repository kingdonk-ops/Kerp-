"""ClamAV scanner (T-0218, TD-211): ``ClamdScanner`` against a fake clamd socket server.

The fake speaks clamd's ``zINSTREAM`` / ``zPING`` protocol and reports a detection when the
stream contains ``SYNTHETIC_MARKER``, a harmless string made up for these tests (no real
malware and no real EICAR file in the repository). Runs on SQLite and, in CI, on PostgreSQL.
"""

import contextlib
import socket
import struct
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.db import Database, utcnow
from app.core.tenancy.context import TenancyContext
from app.modules.jobs import service as jobs
from app.modules.jobs.broker import JobRef
from app.modules.jobs.registry import JOB_REGISTRY, JobContext
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.uploads import jobs as upload_jobs
from app.modules.uploads import scan, service
from app.modules.uploads.models import MAX_SIGNATURE, UploadedFile
from app.modules.uploads.scan import (
    ClamdAddress,
    ClamdScanner,
    Infected,
    ScannerUnavailableError,
    ScanRefusedError,
    StubScanner,
)
from app.modules.uploads.schemas import UploadCreate
from app.modules.uploads.storage import LocalStorage, set_storage
from tests.audit_support import migrated_database
from tests.auth_support import T1

SYNTHETIC_MARKER = b"KERP-SYNTHETIC-SCAN-MARKER-NOT-MALWARE"
SIGNATURE = "Kerp.Test.Synthetic-Marker"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
ROOT = TenancyContext(tenant_id=T1, is_system_admin=True)


@dataclass
class FakeClamd:
    """A threaded fake clamd on TCP (or a Unix socket). ``mode`` picks its behaviour."""

    mode: str = "ok"  # ok | silent | error | limit | garbage
    stream_max: int = 1 << 30
    signature: str = SIGNATURE
    chunks: list[int] = field(default_factory=list)
    received: bytearray = field(default_factory=bytearray)
    commands: list[bytes] = field(default_factory=list)
    unix_path: str | None = None

    def __post_init__(self) -> None:
        if self.unix_path is not None:
            self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.server.bind(self.unix_path)
        else:
            self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @property
    def address(self) -> ClamdAddress:
        if self.unix_path is not None:
            return ClamdAddress("unix", path=self.unix_path)
        host, port = self.server.getsockname()
        return ClamdAddress("tcp", host=host, port=port)

    def _serve(self) -> None:
        while not self.stopping.is_set():
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn, contextlib.suppress(OSError):
                self._handle(conn)

    def _read_exact(self, conn: socket.socket, n: int) -> bytes:
        data = b""
        while len(data) < n:
            part = conn.recv(n - len(data))
            if not part:
                raise OSError("closed")
            data += part
        return data

    def _handle(self, conn: socket.socket) -> None:
        command = b""
        while not command.endswith(b"\0"):
            part = conn.recv(1)
            if not part:
                return
            command += part
        self.commands.append(command)
        if self.mode == "silent":
            self.stopping.wait(5)
            return
        if command == b"zPING\0":
            conn.sendall(b"PONG\0")
            return
        if command != b"zINSTREAM\0":
            conn.sendall(b"UNKNOWN COMMAND\0")
            return
        total = 0
        while True:
            (size,) = struct.unpack(">I", self._read_exact(conn, 4))
            if size == 0:
                break
            self.chunks.append(size)
            self.received += self._read_exact(conn, size)
            total += size
            if total > self.stream_max:
                conn.sendall(b"INSTREAM size limit exceeded. ERROR\0")
                conn.shutdown(socket.SHUT_WR)
                conn.settimeout(2)
                while conn.recv(65536):  # drain, so the close sends no reset
                    pass
                return
        if self.mode == "error":
            conn.sendall(b"stream: Can't allocate memory ERROR\0")
        elif self.mode == "garbage":
            conn.sendall(b"what?\0")
        elif SYNTHETIC_MARKER in self.received:
            conn.sendall(f"stream: {self.signature} FOUND\0".encode())
        else:
            conn.sendall(b"stream: OK\0")

    def close(self) -> None:
        self.stopping.set()
        self.server.close()


@pytest.fixture
def clamd() -> Iterator[FakeClamd]:
    fake = FakeClamd()
    yield fake
    fake.close()


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[LocalStorage]:
    local = LocalStorage(tmp_path / "vault")
    set_storage(local)
    yield local
    set_storage(None)


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --- protocol ------------------------------------------------------------------------------


def test_a_clean_object_is_streamed_in_bounded_chunks(
    clamd: FakeClamd, storage: LocalStorage
) -> None:
    data = bytes(range(256)) * 40  # 10 KiB
    storage.write("q/clean", data)
    scanner = ClamdScanner(clamd.address, timeout=5, chunk_bytes=4096)
    assert scanner.scan(storage, "q/clean") == "clean"
    assert bytes(clamd.received) == data
    assert clamd.chunks == [4096, 4096, 2048]
    assert clamd.commands == [b"zINSTREAM\0"]


def test_a_detection_returns_the_signature(clamd: FakeClamd, storage: LocalStorage) -> None:
    storage.write("q/bad", PNG + SYNTHETIC_MARKER)
    verdict = ClamdScanner(clamd.address, timeout=5).scan(storage, "q/bad")
    assert verdict == Infected(SIGNATURE)
    assert scan.outcome(verdict) == (False, SIGNATURE)


def test_the_signature_is_bounded_and_printable(clamd: FakeClamd, storage: LocalStorage) -> None:
    clamd.signature = "Sig\x01" + "N" * 400
    storage.write("q/bad", SYNTHETIC_MARKER)
    verdict = ClamdScanner(clamd.address, timeout=5).scan(storage, "q/bad")
    assert isinstance(verdict, Infected)
    assert len(verdict.signature) == MAX_SIGNATURE
    assert verdict.signature.startswith("Sig N")


def test_the_size_limit_is_honoured_before_anything_is_sent(
    clamd: FakeClamd, storage: LocalStorage
) -> None:
    storage.write("q/big", bytes(5000))
    with pytest.raises(ScanRefusedError):
        ClamdScanner(clamd.address, timeout=5, max_bytes=4999).scan(storage, "q/big")
    assert clamd.commands == []


def test_clamd_s_own_stream_limit_refuses_the_scan(
    clamd: FakeClamd, storage: LocalStorage
) -> None:
    clamd.stream_max = 1000
    storage.write("q/big", bytes(5000))
    with pytest.raises(ScanRefusedError):
        ClamdScanner(clamd.address, timeout=5, chunk_bytes=512).scan(storage, "q/big")


@pytest.mark.parametrize("mode", ["error", "garbage"])
def test_a_clamd_error_reply_is_unavailable(
    clamd: FakeClamd, storage: LocalStorage, mode: str
) -> None:
    clamd.mode = mode
    storage.write("q/x", PNG)
    with pytest.raises(ScannerUnavailableError):
        ClamdScanner(clamd.address, timeout=5).scan(storage, "q/x")


def test_a_scanner_that_is_down_or_silent_is_unavailable_without_naming_it(
    clamd: FakeClamd, storage: LocalStorage
) -> None:
    storage.write("q/x", PNG)
    port = _closed_port()
    down = ClamdScanner(ClamdAddress("tcp", host="127.0.0.1", port=port), timeout=2)
    with pytest.raises(ScannerUnavailableError) as refused:
        down.scan(storage, "q/x")
    clamd.mode = "silent"
    _host, silent_port = clamd.server.getsockname()
    with pytest.raises(ScannerUnavailableError, match="timed out") as timed_out:
        ClamdScanner(clamd.address, timeout=0.3).scan(storage, "q/x")
    for exc in (refused.value, timed_out.value):
        text = str(exc) + repr(exc)
        assert "127.0.0.1" not in text
        assert str(port) not in text
        assert str(silent_port) not in text
    assert "127.0.0.1" not in repr(down)


def test_ping_answers_pong_and_false_when_down(clamd: FakeClamd) -> None:
    assert ClamdScanner(clamd.address, timeout=5).ping() is True
    assert clamd.commands == [b"zPING\0"]
    down = ClamdScanner(ClamdAddress("tcp", host="127.0.0.1", port=_closed_port()), timeout=1)
    assert down.ping() is False
    clamd.mode = "silent"
    assert ClamdScanner(clamd.address, timeout=0.3).ping() is False


def test_a_unix_socket_works_too(tmp_path: Path, storage: LocalStorage) -> None:
    path = str(tmp_path / "clamd.sock")
    fake = FakeClamd(unix_path=path)
    try:
        scanner = scan.scanner_from_settings(
            Settings(environment="test", upload_scanner="clamd", clamd_address=f"unix://{path}")
        )
        assert isinstance(scanner, ClamdScanner)
        storage.write("q/x", PNG)
        assert scanner.scan(storage, "q/x") == "clean"
        assert scanner.ping() is True
    finally:
        fake.close()


def test_an_unusable_scanner_configuration_is_refused() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        ClamdScanner(ClamdAddress("tcp", host="h", port=1), chunk_bytes=0)
    with pytest.raises(ValueError, match="tcp://<host>:<port>"):
        ClamdAddress.parse("http://clamd:3310")


# --- configuration -------------------------------------------------------------------------


def test_the_scanner_is_chosen_by_configuration() -> None:
    assert isinstance(scan.scanner_from_settings(Settings(environment="test")), StubScanner)
    configured = scan.scanner_from_settings(
        Settings(
            environment="test",
            upload_scanner="clamd",
            clamd_address="tcp://clamd.internal.example:3310",
            clamd_timeout_seconds=7,
            upload_scan_max_bytes=1234,
        )
    )
    assert isinstance(configured, ClamdScanner)
    assert configured.address == ClamdAddress("tcp", host="clamd.internal.example", port=3310)
    assert (configured.timeout, configured.max_bytes) == (7, 1234)
    assert "clamd.internal.example" not in repr(configured)


@pytest.mark.parametrize(
    "address", [None, "", "clamd:3310", "tcp://clamd", "tcp://:3310", "unix://", "udp://c:1"]
)
def test_clamd_needs_a_valid_address(address: str | None) -> None:
    with pytest.raises(ValidationError, match="KERP_CLAMD_ADDRESS"):
        Settings(environment="test", upload_scanner="clamd", clamd_address=address)


def test_production_accepts_the_configured_clamd_scanner_and_still_refuses_the_stub() -> None:
    scan.set_scanner(None)
    clamd = Settings(
        environment="production", upload_scanner="clamd", clamd_address="tcp://clamd:3310"
    )
    scan.require_real_scanner(clamd)
    with pytest.raises(RuntimeError, match="KERP_UPLOAD_SCANNER=clamd"):
        scan.require_real_scanner(Settings(environment="production", upload_scanner="stub"))


def test_the_health_probe_pings_the_configured_scanner(clamd: FakeClamd) -> None:
    scan.set_scanner(None)
    assert scan.ping_scanner(Settings(environment="test")) is None  # the stub has no PING
    scan.set_scanner(ClamdScanner(clamd.address, timeout=5))
    try:
        assert scan.ping_scanner() is True
    finally:
        scan.set_scanner(None)


# --- the verify job with a real protocol --------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def dispatched() -> Iterator[list[JobRef]]:
    refs: list[JobRef] = []
    jobs.set_dispatcher(refs.extend)
    yield refs
    jobs.set_dispatcher(None)


@pytest.fixture
def home(db: Database) -> TenancyContext:
    with db.unit_of_work(ROOT) as s:
        project_id = projects.create_project(
            s, ProjectCreate.model_validate({"code": "HOME", "name": "HOME"})
        ).id
    return TenancyContext(tenant_id=T1, project_id=project_id)


def _verifying(db: Database, home: TenancyContext, storage: LocalStorage, data: bytes) -> uuid.UUID:
    import hashlib

    body = {
        "module": "documents",
        "filename": "photo.png",
        "content_type": "image/png",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    with db.unit_of_work(home) as s:
        file, _ = service.presign_upload(
            s, storage, UploadCreate.model_validate(body), max_bytes=1 << 20, expires_in=900
        )
        file_id, staging = file.id, file.staging_key
    storage.write(staging, data)
    with db.unit_of_work(home) as s:
        assert service.complete_upload(s, storage, file_id).status == "verifying"
    return file_id


def _load(db: Database, home: TenancyContext, file_id: uuid.UUID) -> UploadedFile:
    with db.unit_of_work(home) as s:
        return service.get_file(s, file_id)


def test_a_detection_rejects_the_file_with_the_signature(
    db: Database,
    home: TenancyContext,
    storage: LocalStorage,
    clamd: FakeClamd,
    dispatched: list[JobRef],
) -> None:
    file_id = _verifying(db, home, storage, PNG + SYNTHETIC_MARKER)
    scanner = ClamdScanner(clamd.address, timeout=5)
    result = service.verify_upload(db, home, file_id, storage=storage, scanner=scanner)
    assert result["reason"] == "infected"
    file = _load(db, home, file_id)
    assert (file.status, file.rejection_reason, file.scan_result, file.scan_signature) == (
        "rejected",
        "infected",
        "infected",
        SIGNATURE,
    )
    assert list(storage.list("")) == []  # rejected bytes are deleted at rejection (TD-397)


def test_a_clean_file_is_published_without_a_signature(
    db: Database,
    home: TenancyContext,
    storage: LocalStorage,
    clamd: FakeClamd,
    dispatched: list[JobRef],
) -> None:
    file_id = _verifying(db, home, storage, PNG)
    scanner = ClamdScanner(clamd.address, timeout=5)
    assert service.verify_upload(db, home, file_id, storage=storage, scanner=scanner)[
        "status"
    ] == ("available")
    file = _load(db, home, file_id)
    assert (file.scan_result, file.scan_signature) == ("clean", None)


def test_a_file_over_the_scan_limit_is_never_published(
    db: Database, home: TenancyContext, storage: LocalStorage, dispatched: list[JobRef]
) -> None:
    file_id = _verifying(db, home, storage, PNG)
    scanner = ClamdScanner(ClamdAddress("tcp", host="127.0.0.1", port=1), max_bytes=10)
    result = service.verify_upload(db, home, file_id, storage=storage, scanner=scanner)
    assert result["reason"] == "verify_failed"
    assert _load(db, home, file_id).status == "rejected"


def test_scanner_down_keeps_the_file_verifying_until_the_stuck_limit(
    db: Database,
    home: TenancyContext,
    storage: LocalStorage,
    clamd: FakeClamd,
    dispatched: list[JobRef],
) -> None:
    file_id = _verifying(db, home, storage, PNG)
    down = ClamdScanner(ClamdAddress("tcp", host="127.0.0.1", port=_closed_port()), timeout=1)
    with pytest.raises(ScannerUnavailableError):
        service.verify_upload(db, home, file_id, storage=storage, scanner=down)
    file = _load(db, home, file_id)
    assert (file.status, file.storage_key) == ("verifying", None)  # never available unscanned
    assert storage.head(file.quarantine_key) is not None  # kept for the retry
    assert storage.head(file.staging_key) is None
    # The hourly sweep leaves it while it is young (the quarantine copy is still owned) ...
    swept = service.sweep_orphans(db, home, storage=storage, now=utcnow() + timedelta(hours=2))
    assert swept == {"expired": 0, "deleted": 0, "rejected": 0}
    # ... the retry with the scanner back publishes it ...
    up = ClamdScanner(clamd.address, timeout=5)
    assert service.verify_upload(db, home, file_id, storage=storage, scanner=up)["status"] == (
        "available"
    )
    assert storage.head(file.quarantine_key) is None


def test_scanner_down_past_the_stuck_limit_is_verify_failed(
    db: Database, home: TenancyContext, storage: LocalStorage, dispatched: list[JobRef]
) -> None:
    file_id = _verifying(db, home, storage, PNG)
    down = ClamdScanner(ClamdAddress("tcp", host="127.0.0.1", port=_closed_port()), timeout=1)
    with pytest.raises(ScannerUnavailableError):
        service.verify_upload(db, home, file_id, storage=storage, scanner=down)
    later = utcnow() + service.VERIFY_STUCK_AFTER + timedelta(minutes=1)
    swept = service.sweep_orphans(db, home, storage=storage, now=later)
    assert (swept["rejected"], swept["deleted"]) == (1, 1)  # the quarantine copy goes too
    file = _load(db, home, file_id)
    assert (file.status, file.rejection_reason) == ("rejected", "verify_failed")
    assert list(storage.list("")) == []
    # A late retry finds the file decided and leaves it alone.
    again = service.verify_upload(db, home, file_id, storage=storage, scanner=down)
    assert again == {"file_id": str(file_id), "status": "rejected"}


def test_the_verify_job_asks_for_a_retry_when_the_scanner_is_down(
    db: Database, home: TenancyContext, storage: LocalStorage, dispatched: list[JobRef]
) -> None:
    import asyncio

    file_id = _verifying(db, home, storage, PNG)
    scan.set_scanner(
        ClamdScanner(ClamdAddress("tcp", host="127.0.0.1", port=_closed_port()), timeout=1)
    )

    async def update(progress: int, message: str | None = None) -> None:
        return None

    payload = {"file_id": str(file_id)}
    with db.unit_of_work(home) as s:
        job_id = service.get_file(s, file_id).job_id  # the file's own job (T-0346)
    assert job_id is not None
    context = JobContext(job_id, service.VERIFY_KIND, home, payload, None, update, db)
    try:
        with pytest.raises(jobs.RetryLaterError, match="malware scanner unavailable") as exc:
            asyncio.run(upload_jobs.verify(context))
    finally:
        scan.set_scanner(None)
    assert "127.0.0.1" not in jobs.sanitise_error(exc.value)
    for kind in (service.VERIFY_KIND, service.VERIFY_LARGE_KIND):
        registered = JOB_REGISTRY.get(kind)
        assert registered.max_attempts == upload_jobs.VERIFY_ATTEMPTS > 1
        assert registered.retry_delay_s > 0
        assert registered.retry_max_delay_s == upload_jobs.VERIFY_RETRY_MAX_DELAY_S
    # The back-off reaches past the stuck limit: the sweep decides, not the retry budget.
    capped = upload_jobs.VERIFY_RETRY_MAX_DELAY_S * (upload_jobs.VERIFY_ATTEMPTS - 8) / 2
    assert capped > service.VERIFY_STUCK_AFTER.total_seconds()


def test_a_stub_verdict_string_still_works(clamd: FakeClamd) -> None:
    assert scan.outcome("clean") == (True, None)
    assert scan.outcome("infected") == (False, None)
    assert scan.outcome("EICAR found") == (False, None)
    data: Any = scan.outcome(Infected(""))
    assert data == (False, None)
