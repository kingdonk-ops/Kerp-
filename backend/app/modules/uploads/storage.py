"""Object storage: filename and header helpers, type allowlists, the ``Storage`` seam (T-0016).

Adapted from Inspection-ERP ``core/files/storage.py`` (ADR 0007): ``clean_filename``,
``content_disposition`` (RFC 6266 / 5987), the served-type allowlist (fix H-10: active
content such as HTML or SVG is never served as itself or inline), the magic-byte allowlist,
the ``LocalStorage`` traversal guard and the ``Storage`` Protocol. The source repo is not
vendored; these are written from the ADR's description.

Backends are synchronous (boto3 is); async callers use ``asyncio.to_thread``. ``boto3`` is
imported lazily (T-0022): only ``build_s3_storage`` needs it.
"""

from __future__ import annotations

import codecs
import hashlib
import re
import shutil
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final, Literal, Protocol, runtime_checkable
from urllib.parse import quote

from app.core.config import Settings, get_settings

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

CHUNK_SIZE: Final = 1024 * 1024
MAGIC_SNIFF_BYTES: Final = 512
MAX_FILENAME: Final = 255
OCTET_STREAM: Final = "application/octet-stream"
_OOXML: Final = "application/vnd.openxmlformats-officedocument"
_ZIP: Final = (b"PK\x03\x04",)

# Content types an upload may declare, each with the leading bytes its content must have.
# ``None``: text, checked for UTF-8 without NUL bytes instead. Anything not listed (HTML,
# SVG, scripts, executables) cannot be uploaded at all.
MAGIC_BYTES: Final[dict[str, tuple[bytes, ...] | None]] = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),  # plus "WEBP" at offset 8
    "image/tiff": (b"II*\x00", b"MM\x00*"),
    "application/zip": _ZIP,
    f"{_OOXML}.wordprocessingml.document": _ZIP,
    f"{_OOXML}.spreadsheetml.sheet": _ZIP,
    f"{_OOXML}.presentationml.presentation": _ZIP,
    "text/plain": None,
    "text/csv": None,
}
# Served with their own type (H-10); only these are shown inline by the browser.
SERVED_TYPES: Final = frozenset(MAGIC_BYTES)
INLINE_TYPES: Final = frozenset(
    {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}
)

_FORBIDDEN_CHARS: Final = re.compile(r'[<>:"|?*]')
_WINDOWS_RESERVED: Final = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.I)
_RFC5987_SAFE: Final = "!#$&+-.^_`|~"


def clean_filename(name: str, *, max_length: int = MAX_FILENAME) -> str:
    """A display/download name safe on every OS: no path, no control or reserved chars."""
    name = unicodedata.normalize("NFC", name).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if not unicodedata.category(c).startswith("C"))
    name = _FORBIDDEN_CHARS.sub("_", name)
    name = " ".join(name.split()).strip(". ")
    if not name:
        return "file"
    if _WINDOWS_RESERVED.match(name):
        name = "_" + name
    if len(name) > max_length:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 16:
            name = stem[: max_length - len(ext) - 1] + "." + ext
        else:
            name = name[:max_length]
    return name


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """``Content-Disposition`` with an ASCII fallback and the RFC 5987 UTF-8 name."""
    name = clean_filename(filename)
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    fallback = re.sub(r'["\\]', "_", ascii_name).strip() or "file"
    disposition = "inline" if inline else "attachment"
    encoded = quote(name, safe=_RFC5987_SAFE)
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


def _base_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def served_type(content_type: str) -> tuple[str, bool]:
    """The ``Content-Type`` to serve a stored file with, and whether it may be inline."""
    base = _base_type(content_type)
    if base not in SERVED_TYPES:
        return OCTET_STREAM, False
    return base, base in INLINE_TYPES


def is_upload_type(content_type: str) -> bool:
    return _base_type(content_type) in MAGIC_BYTES


def matches_magic(content_type: str, head: bytes) -> bool:
    """Whether ``head`` (the first bytes of the object) fits the declared type."""
    base = _base_type(content_type)
    if base not in MAGIC_BYTES or not head:
        return False
    signatures = MAGIC_BYTES[base]
    if signatures is None:
        if b"\x00" in head:
            return False
        try:  # incremental: a multi-byte character cut at the end of ``head`` is fine
            codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
        except UnicodeDecodeError:
            return False
        return True
    if not head.startswith(signatures):
        return False
    return base != "image/webp" or head[8:12] == b"WEBP"


# --- the Storage seam -------------------------------------------------------------------------


class UnsafeKeyError(ValueError):
    """An object key that could escape its prefix (absolute, ``..``, empty segment, NUL)."""


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    key: str
    size: int
    last_modified: datetime


@dataclass(frozen=True, slots=True)
class PresignedRequest:
    """A time-limited request the client sends straight to object storage."""

    url: str
    method: Literal["PUT", "GET"]
    expires_at: datetime
    headers: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class Storage(Protocol):
    """Object storage as the uploads module uses it (S3/MinIO; ``LocalStorage`` in tests)."""

    def presign_put(
        self, key: str, *, content_type: str, size: int, expires_in: int
    ) -> PresignedRequest: ...

    def presign_get(
        self, key: str, *, filename: str, content_type: str, expires_in: int
    ) -> PresignedRequest: ...

    def head(self, key: str) -> ObjectInfo | None: ...

    def read(self, key: str, *, length: int | None = None) -> bytes: ...

    def chunks(self, key: str) -> Iterator[bytes]: ...

    def copy(self, source: str, destination: str) -> None: ...

    def delete(self, key: str) -> None: ...

    def list(self, prefix: str) -> Iterator[ObjectInfo]: ...


def sha256_of(storage: Storage, key: str) -> str:
    """Hex SHA-256 of an object, streamed in chunks (never loaded whole)."""
    digest = hashlib.sha256()
    for chunk in storage.chunks(key):
        digest.update(chunk)
    return digest.hexdigest()


def _expiry(expires_in: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=expires_in)


def check_key(key: str) -> str:
    """``key`` if it is a relative ``/``-separated path with no empty, ``.`` or ``..`` part."""
    parts = key.split("/")
    if (
        not key
        or "\x00" in key
        or "\\" in key
        or key.startswith("/")
        or any(p in ("", ".", "..") for p in parts)
    ):
        raise UnsafeKeyError(f"unsafe object key {key!r}")
    return key


class LocalStorage:
    """Files under ``root``. For tests and offline work; presigned URLs are ``file://``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        root = self.root.resolve()
        path = (root / PurePosixPath(check_key(key))).resolve()
        if not path.is_relative_to(root):  # a symlink pointing out of the root
            raise UnsafeKeyError(f"unsafe object key {key!r}")
        return path

    def write(self, key: str, data: bytes) -> None:
        """What the client's presigned ``PUT`` does (tests)."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def presign_put(
        self, key: str, *, content_type: str, size: int, expires_in: int
    ) -> PresignedRequest:
        url = self._path(key).as_uri()
        return PresignedRequest(url, "PUT", _expiry(expires_in), {"Content-Type": content_type})

    def presign_get(
        self, key: str, *, filename: str, content_type: str, expires_in: int
    ) -> PresignedRequest:
        return PresignedRequest(self._path(key).as_uri(), "GET", _expiry(expires_in))

    def head(self, key: str) -> ObjectInfo | None:
        path = self._path(key)
        if not path.is_file():
            return None
        stat = path.stat()
        return ObjectInfo(key, stat.st_size, datetime.fromtimestamp(stat.st_mtime, UTC))

    def read(self, key: str, *, length: int | None = None) -> bytes:
        with self._path(key).open("rb") as handle:
            return handle.read(-1 if length is None else length)

    def chunks(self, key: str) -> Iterator[bytes]:
        with self._path(key).open("rb") as handle:
            while chunk := handle.read(CHUNK_SIZE):
                yield chunk

    def copy(self, source: str, destination: str) -> None:
        target = self._path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(source), target)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list(self, prefix: str) -> Iterator[ObjectInfo]:
        root = self.root.resolve()
        if not root.is_dir():
            return
        for path in sorted(root.rglob("*")):
            key = path.relative_to(root).as_posix()
            if path.is_file() and key.startswith(prefix):
                info = self.head(key)
                if info is not None:
                    yield info


class S3Storage:
    """An S3 bucket (AWS in production, MinIO in dev/test; TD-004).

    ``presign_client`` signs URLs for the public endpoint the browser reaches, which can
    differ from the endpoint the API uses (``minio:9000`` inside compose, ``localhost:9000``
    outside it).
    """

    def __init__(self, bucket: str, client: S3Client, presign_client: S3Client) -> None:
        self.bucket, self.client, self.presign_client = bucket, client, presign_client

    def presign_put(
        self, key: str, *, content_type: str, size: int, expires_in: int
    ) -> PresignedRequest:
        # Content-Type and Content-Length are signed: storage refuses any other size.
        params = {"Bucket": self.bucket, "Key": key, "ContentType": content_type}
        url = self.presign_client.generate_presigned_url(
            "put_object", Params={**params, "ContentLength": size}, ExpiresIn=expires_in
        )
        return PresignedRequest(url, "PUT", _expiry(expires_in), {"Content-Type": content_type})

    def presign_get(
        self, key: str, *, filename: str, content_type: str, expires_in: int
    ) -> PresignedRequest:
        served, inline = served_type(content_type)
        params = {
            "Bucket": self.bucket,
            "Key": key,
            "ResponseContentType": served,
            "ResponseContentDisposition": content_disposition(filename, inline=inline),
        }
        url = self.presign_client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires_in
        )
        return PresignedRequest(url, "GET", _expiry(expires_in))

    def head(self, key: str) -> ObjectInfo | None:
        try:
            meta = self.client.head_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return ObjectInfo(key, meta["ContentLength"], meta["LastModified"])

    def read(self, key: str, *, length: int | None = None) -> bytes:
        if length is None:
            body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        else:
            ranged = f"bytes=0-{length - 1}"
            body = self.client.get_object(Bucket=self.bucket, Key=key, Range=ranged)["Body"]
        return body.read()

    def chunks(self, key: str) -> Iterator[bytes]:
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        yield from body.iter_chunks(CHUNK_SIZE)

    def copy(self, source: str, destination: str) -> None:
        self.client.copy_object(
            Bucket=self.bucket, Key=destination, CopySource={"Bucket": self.bucket, "Key": source}
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def list(self, prefix: str) -> Iterator[ObjectInfo]:
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix
        )
        for page in pages:
            for item in page.get("Contents", []):
                yield ObjectInfo(item["Key"], item["Size"], item["LastModified"])


def build_s3_storage(settings: Settings) -> S3Storage:
    """An ``S3Storage`` for the configured bucket (credentials: settings, else the AWS chain)."""
    import boto3  # heavy: lazily (T-0022)
    from botocore.config import Config

    secret = settings.s3_secret_access_key
    config = Config(signature_version="s3v4", s3={"addressing_style": "path"})

    def client(endpoint: str | None) -> S3Client:
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=secret.get_secret_value() if secret else None,
            config=config,
        )

    internal = client(settings.s3_endpoint_url)
    public = settings.s3_public_endpoint_url
    presign = client(public) if public and public != settings.s3_endpoint_url else internal
    return S3Storage(settings.storage_bucket, internal, presign)


_override: Storage | None = None


@lru_cache(maxsize=1)
def default_storage() -> Storage:
    return build_s3_storage(get_settings())


def set_storage(storage: Storage | None) -> None:
    """Replace the process's storage (tests); ``None`` restores the configured S3 bucket."""
    global _override
    _override = storage


def get_storage() -> Storage:
    return _override if _override is not None else default_storage()
