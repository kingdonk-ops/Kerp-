"""Storage helpers and backends of the uploads module (T-0016, ADR 0007).

The helpers (``clean_filename``, ``content_disposition``, the served-type and magic-byte
allowlists, the ``LocalStorage`` traversal guard) are written fresh from ADR 0007's
description of Inspection-ERP ``core/files/storage.py`` and its ``test_files`` /
``test_hardening`` H-10 cases (the source repo is not vendored). ``S3Storage`` is tested
offline with botocore's ``Stubber``; the real-MinIO round trip needs ``KERP_TEST_S3_ENDPOINT``.
"""

import hashlib
import io
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.core.config import Settings
from app.modules.uploads import storage as storage_module
from app.modules.uploads.scan import StubScanner, get_scanner, set_scanner
from app.modules.uploads.storage import (
    MAGIC_BYTES,
    LocalStorage,
    S3Storage,
    Storage,
    UnsafeKeyError,
    build_s3_storage,
    clean_filename,
    content_disposition,
    get_storage,
    is_upload_type,
    matches_magic,
    served_type,
    set_storage,
    sha256_of,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
PDF = b"%PDF-1.7\n%synthetic\n"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


# --- clean_filename -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\photo.jpg", "photo.jpg"),
        ("a\x00b\x1f.txt", "ab.txt"),
        ('bad<>:"|?*.png', "bad_______.png"),
        ("  .hidden. ", "hidden"),
        ("multi   space.pdf", "multi space.pdf"),
        ("", "file"),
        ("..", "file"),
        ("/", "file"),
        ("CON.txt", "_CON.txt"),
        ("lpt1", "_lpt1"),
        ("Prüfbericht ü.pdf", "Prüfbericht ü.pdf"),
    ],
)
def test_clean_filename(raw: str, clean: str) -> None:
    assert clean_filename(raw) == clean


def test_clean_filename_keeps_the_extension_when_truncating() -> None:
    name = clean_filename("x" * 400 + ".pdf")
    assert len(name) == 255
    assert name.endswith(".pdf")
    assert clean_filename("y" * 400) == "y" * 255


def test_clean_filename_normalises_unicode() -> None:
    assert clean_filename("Pru\u0308f.pdf") == "Pr\u00fcf.pdf"


# --- content_disposition (RFC 6266 / 5987) --------------------------------------------------


def test_content_disposition_ascii() -> None:
    assert content_disposition("report.pdf") == (
        "attachment; filename=\"report.pdf\"; filename*=UTF-8''report.pdf"
    )
    assert content_disposition("a.png", inline=True).startswith("inline; ")


def test_content_disposition_escapes_quotes_and_non_ascii() -> None:
    header = content_disposition('Prüf "1"\r\n.pdf')
    assert "\r" not in header
    assert "\n" not in header
    assert 'filename="Pruf _1_.pdf"' in header
    assert "filename*=UTF-8''Pr%C3%BCf%20_1_.pdf" in header


def test_content_disposition_never_has_an_empty_fallback() -> None:
    assert 'filename="file"' in content_disposition("測試")
    assert "filename*=UTF-8''%E6%B8%AC%E8%A9%A6" in content_disposition("測試")


# --- served types (H-10) and magic bytes ----------------------------------------------------


@pytest.mark.parametrize(
    ("declared", "served"),
    [
        ("application/pdf", ("application/pdf", True)),
        ("image/png", ("image/png", True)),
        ("text/csv", ("text/csv", False)),
        # H-10: active content is never served as itself, and never inline.
        ("text/html", ("application/octet-stream", False)),
        ("image/svg+xml", ("application/octet-stream", False)),
        ("application/javascript", ("application/octet-stream", False)),
        ("IMAGE/PNG; charset=x", ("image/png", True)),
        ("", ("application/octet-stream", False)),
    ],
)
def test_served_type(declared: str, served: tuple[str, bool]) -> None:
    assert served_type(declared) == served


def test_upload_types_are_the_magic_byte_allowlist() -> None:
    assert is_upload_type("application/pdf")
    assert is_upload_type("text/csv")
    assert not is_upload_type("text/html")
    assert not is_upload_type("image/svg+xml")
    assert set(MAGIC_BYTES) >= {"application/pdf", "image/png", "image/jpeg"}


@pytest.mark.parametrize(
    ("content_type", "head", "ok"),
    [
        ("image/png", PNG, True),
        ("image/png", JPEG, False),
        ("image/jpeg", JPEG, True),
        ("application/pdf", PDF, True),
        ("application/pdf", b"<html><script>", False),
        ("image/gif", b"GIF89a....", True),
        ("image/webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ", True),
        ("image/webp", b"RIFF\x00\x00\x00\x00WAVEfmt ", False),
        ("image/tiff", b"II*\x00rest", True),
        (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            b"PK\x03\x04xl",
            True,
        ),
        ("text/plain", "plain text, ünïcode\n".encode(), True),
        ("text/csv", b"a,b\n1,2\n", True),
        ("text/csv", b"a,b\x00\n", False),  # binary disguised as text
        ("text/plain", b"\xff\xfe\xfa", False),  # not UTF-8
        ("text/plain", "abc€".encode()[:-1], True),  # cut inside a multi-byte character
        ("text/html", b"<html>", False),  # not an upload type at all
        ("image/png", b"", False),
    ],
)
def test_matches_magic(content_type: str, head: bytes, ok: bool) -> None:
    assert matches_magic(content_type, head) is ok


# --- LocalStorage (traversal guard) ---------------------------------------------------------


@pytest.fixture
def local(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "vault")


@pytest.mark.parametrize(
    "key",
    ["", "/etc/passwd", "../outside", "a/../../outside", "a/./b", "a//b", "a\\b", "a\x00b", "a/"],
)
def test_local_storage_rejects_unsafe_keys(local: LocalStorage, key: str) -> None:
    with pytest.raises(UnsafeKeyError):
        local.head(key)


def test_local_storage_rejects_a_symlink_out_of_the_root(
    local: LocalStorage, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"x")
    local.root.mkdir(parents=True)
    (local.root / "link").symlink_to(outside)
    with pytest.raises(UnsafeKeyError):
        local.read("link/secret")


def test_local_storage_round_trip(local: LocalStorage) -> None:
    assert local.head("t/p/a") is None
    local.write("t/p/a", PNG)
    info = local.head("t/p/a")
    assert info is not None
    assert (info.key, info.size) == ("t/p/a", len(PNG))
    assert local.read("t/p/a", length=4) == PNG[:4]
    assert local.read("t/p/a") == PNG
    assert sha256_of(local, "t/p/a") == hashlib.sha256(PNG).hexdigest()
    local.copy("t/p/a", "t/p/m/b")
    assert local.read("t/p/m/b") == PNG
    assert sorted(o.key for o in local.list("t/p/")) == ["t/p/a", "t/p/m/b"]
    assert list(local.list("nothing/")) == []
    local.delete("t/p/a")
    local.delete("t/p/a")  # deleting a missing object is not an error
    assert local.head("t/p/a") is None


def test_local_storage_presigns_file_urls(local: LocalStorage) -> None:
    put = local.presign_put("t/p/a", content_type="image/png", size=10, expires_in=60)
    assert put.method == "PUT"
    assert put.url.startswith("file://")
    assert put.headers == {"Content-Type": "image/png"}
    assert put.expires_at > datetime.now(UTC)
    get = local.presign_get("t/p/a", filename="a.png", content_type="image/png", expires_in=60)
    assert get.method == "GET"


def test_storage_backends_meet_the_protocol(local: LocalStorage) -> None:
    assert isinstance(local, Storage)


# --- S3Storage (offline: botocore Stubber) ----------------------------------------------------


def _settings(**values: object) -> Settings:
    base: dict[str, object] = {
        "environment": "test",
        "s3_endpoint_url": "http://minio.internal:9000",
        "s3_public_endpoint_url": "http://localhost:9000",
        "s3_access_key_id": "synthetic-key",
        "s3_secret_access_key": "synthetic-secret",
        "storage_bucket": "kerp-test",
    }
    base.update(values)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def s3() -> S3Storage:
    return build_s3_storage(_settings())


def test_presigned_put_signs_type_and_length_on_the_public_endpoint(s3: S3Storage) -> None:
    put = s3.presign_put("t/p/a", content_type="image/png", size=12, expires_in=120)
    url = urlsplit(put.url)
    assert (url.scheme, url.netloc, url.path) == ("http", "localhost:9000", "/kerp-test/t/p/a")
    query = parse_qs(url.query)
    assert query["X-Amz-Expires"] == ["120"]
    assert query["X-Amz-SignedHeaders"] == ["content-length;content-type;host"]
    assert put.headers == {"Content-Type": "image/png"}
    assert timedelta(seconds=100) < put.expires_at - datetime.now(UTC) <= timedelta(seconds=120)


def test_the_configured_region_reaches_the_boto3_clients() -> None:
    assert Settings(environment="test").s3_region == "ap-southeast-2"  # TD-171, diff §4.4
    default = build_s3_storage(_settings())
    assert default.client.meta.region_name == "ap-southeast-2"
    s3 = build_s3_storage(_settings(s3_region="eu-central-1"))
    assert s3.client.meta.region_name == "eu-central-1"
    assert s3.presign_client.meta.region_name == "eu-central-1"
    url = s3.presign_put("t/p/a", content_type="image/png", size=1, expires_in=60).url
    assert "%2Feu-central-1%2Fs3%2F" in url  # the signing scope


def test_presigned_get_forces_the_served_type_and_disposition(s3: S3Storage) -> None:
    get = s3.presign_get("t/p/m/h", filename="x.html", content_type="text/html", expires_in=60)
    query = parse_qs(urlsplit(get.url).query)
    assert query["response-content-type"] == ["application/octet-stream"]
    assert query["response-content-disposition"][0].startswith("attachment; ")


def test_s3_operations(s3: S3Storage) -> None:
    from botocore.response import StreamingBody  # heavy: lazily (T-0022)
    from botocore.stub import Stubber

    stub = Stubber(s3.client)
    modified = datetime(2026, 11, 1, tzinfo=UTC)
    stub.add_response(
        "head_object",
        {"ContentLength": 3, "LastModified": modified},
        {"Bucket": "kerp-test", "Key": "k"},
    )
    stub.add_client_error("head_object", "404", http_status_code=404)
    stub.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(b"abc"), 3)},
        {"Bucket": "kerp-test", "Key": "k", "Range": "bytes=0-1"},
    )
    stub.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(b"abc"), 3)},
        {"Bucket": "kerp-test", "Key": "k"},
    )
    stub.add_response(
        "copy_object",
        {},
        {"Bucket": "kerp-test", "Key": "d", "CopySource": {"Bucket": "kerp-test", "Key": "k"}},
    )
    stub.add_response("delete_object", {}, {"Bucket": "kerp-test", "Key": "k"})
    stub.add_response(
        "list_objects_v2",
        {"Contents": [{"Key": "s/1", "Size": 3, "LastModified": modified}], "IsTruncated": False},
        {"Bucket": "kerp-test", "Prefix": "s/"},
    )
    stub.add_client_error("head_object", "403", http_status_code=403)
    with stub:
        info = s3.head("k")
        assert info is not None
        assert (info.size, info.last_modified) == (3, modified)
        assert s3.head("missing") is None
        assert s3.read("k", length=2) == b"abc"  # the stub ignores the range; the call is checked
        assert sha256_of(s3, "k") == hashlib.sha256(b"abc").hexdigest()
        s3.copy("k", "d")
        s3.delete("k")
        assert [o.key for o in s3.list("s/")] == ["s/1"]
        with pytest.raises(Exception, match="403"):
            s3.head("forbidden")  # only "not found" is None; other errors propagate
        stub.assert_no_pending_responses()


def test_get_storage_builds_s3_from_settings_once(monkeypatch: pytest.MonkeyPatch) -> None:
    set_storage(None)
    storage_module.default_storage.cache_clear()
    monkeypatch.setattr(storage_module, "get_settings", _settings)
    try:
        first = get_storage()
        assert isinstance(first, S3Storage)
        assert get_storage() is first
    finally:
        storage_module.default_storage.cache_clear()


def test_set_storage_overrides(local: LocalStorage) -> None:
    set_storage(local)
    try:
        assert get_storage() is local
    finally:
        set_storage(None)


def test_scanner_seam_defaults_to_the_clean_stub(local: LocalStorage) -> None:
    assert isinstance(get_scanner(), StubScanner)
    local.write("k", b"x")
    assert get_scanner().scan(local, "k") == "clean"

    class Infected:
        def scan(self, storage: Storage, key: str) -> str:
            return "infected"

    set_scanner(Infected())
    try:
        assert get_scanner().scan(local, "k") == "infected"
    finally:
        set_scanner(None)
    assert isinstance(get_scanner(), StubScanner)


# --- real MinIO / S3 (opt-in) ---------------------------------------------------------------

S3_ENDPOINT = os.environ.get("KERP_TEST_S3_ENDPOINT", "")


@pytest.mark.skipif(
    not S3_ENDPOINT,
    reason="needs MinIO/S3 (KERP_TEST_S3_ENDPOINT + KERP_S3_* credentials); not in CI (TD-052)",
)
def test_presigned_round_trip_against_minio() -> None:
    import urllib.request

    settings = Settings(  # type: ignore[call-arg]
        environment="test", s3_endpoint_url=S3_ENDPOINT, s3_public_endpoint_url=None
    )
    s3 = build_s3_storage(settings)
    key = f"staging/test/{uuid.uuid4()}"
    put = s3.presign_put(key, content_type="image/png", size=len(PNG), expires_in=60)
    request = urllib.request.Request(put.url, data=PNG, method="PUT", headers=put.headers)
    with urllib.request.urlopen(request) as response:
        assert response.status == 200
    assert sha256_of(s3, key) == hashlib.sha256(PNG).hexdigest()
    get = s3.presign_get(key, filename="a.png", content_type="image/png", expires_in=60)
    with urllib.request.urlopen(get.url) as response:
        assert response.read() == PNG
    s3.delete(key)
