"""Ledger hash v2: ``device_id`` in the content hash, v1 entries still verify (T-0043, ADR 0010).

Runs on SQLite and, in CI, on PostgreSQL against the migrated schema.
"""

import dataclasses
import hashlib
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import DateTime, bindparam, text
from sqlalchemy.exc import IntegrityError

from app.core.audit.hashing import (
    GENESIS_HASH,
    HASH_VERSION,
    compute_content_hash,
    verify_entries,
)
from app.core.audit.models import TENANT_SCOPE
from app.core.audit.service import append, chain_entries, verify_chain
from app.core.db import Database
from app.core.tenancy.context import current_device_id
from tests.audit_support import ALEMBIC_INI, OWNER_URL, migrated_database, tamper
from tests.db_support import ON_POSTGRES, database_url
from tests.tenancy_support import CTX_A, CTX_T1, PA, T1

USER = uuid.UUID("00000000-0000-4000-8000-00000000f001")
DEVICE = uuid.UUID("00000000-0000-4000-8000-0000000de71c")
KEY = "inspection:00000000-0000-4000-8000-000000000123"

needs_tamper_role = pytest.mark.skipif(
    ON_POSTGRES and not OWNER_URL,
    reason="tampering on PostgreSQL needs KERP_TEST_OWNER_URL (set in CI) (TD-052)",
)


@dataclasses.dataclass(frozen=True)
class _Fields:
    tenant_id: uuid.UUID
    project_id: uuid.UUID | None
    chain_key: str
    seq: int
    prev_hash: str
    created_at: datetime
    created_by: uuid.UUID | None
    source: str
    payload: dict[str, object]
    content_hash: str = ""
    deleted_at: datetime | None = None
    device_id: uuid.UUID | None = None
    impersonated_by_user_id: uuid.UUID | None = None
    hash_version: int = HASH_VERSION


AT = datetime(2026, 11, 15, 8, 30, 0, 123456, tzinfo=UTC)


def _entry(**changes: object) -> _Fields:
    base = _Fields(
        tenant_id=T1,
        project_id=PA,
        chain_key=KEY,
        seq=1,
        prev_hash=GENESIS_HASH,
        created_at=AT,
        created_by=None,
        source="system",
        payload={"result": "pass"},
    )
    return dataclasses.replace(base, **changes)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


# --- hashing --------------------------------------------------------------------------------


def test_v2_content_hash_known_answer() -> None:
    # Pins v2: the v1 body plus "device_id" (null without a device), with "v": 2.
    for device, shown in ((DEVICE, f'"{DEVICE}"'), (None, "null")):
        canonical = (
            '{"chain_key":"' + KEY + '","created_at":"2026-11-15T08:30:00.123456+00:00",'
            '"created_by":null,"device_id":' + shown + ',"payload":{"result":"pass"},'
            '"prev_hash":"' + GENESIS_HASH + '","project_id":"' + str(PA) + '","seq":1,'
            '"source":"system","tenant_id":"' + str(T1) + '","v":2}'
        )
        expected = hashlib.sha256(b"kerp-audit-ledger\n" + canonical.encode()).hexdigest()
        assert compute_content_hash(_entry(device_id=device, hash_version=2)) == expected


def test_v1_hash_is_unchanged_and_never_covers_a_device() -> None:
    v1 = _entry(hash_version=1)
    body = {
        "v": 1,
        "tenant_id": str(T1),
        "project_id": str(PA),
        "chain_key": KEY,
        "seq": 1,
        "prev_hash": GENESIS_HASH,
        "created_at": "2026-11-15T08:30:00.123456+00:00",
        "created_by": None,
        "source": "system",
        "payload": {"result": "pass"},
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected = hashlib.sha256(b"kerp-audit-ledger\n" + canonical.encode()).hexdigest()
    assert compute_content_hash(v1) == expected
    assert compute_content_hash(v1) != compute_content_hash(_entry(hash_version=2))
    with pytest.raises(ValueError, match="v1 ledger entry cannot carry a device_id"):
        compute_content_hash(_entry(hash_version=1, device_id=DEVICE))


@pytest.mark.parametrize("version", [0, 4, True])
def test_unknown_hash_versions_are_refused(version: int) -> None:
    with pytest.raises(ValueError, match="unknown ledger hash version"):
        compute_content_hash(_entry(hash_version=version))
    entry = _entry(hash_version=version)
    assert verify_entries([entry]) == [f"seq 1: unknown ledger hash version {version!r}"]


def test_a_chain_mixing_v1_and_v2_entries_verifies() -> None:
    first = _entry(hash_version=1)
    first = dataclasses.replace(first, content_hash=compute_content_hash(first))
    second = _entry(seq=2, prev_hash=first.content_hash, device_id=DEVICE)
    second = dataclasses.replace(second, content_hash=compute_content_hash(second))
    assert verify_entries([first, second]) == []
    # Moving the device to another entry, or dropping it, breaks the hash.
    moved = dataclasses.replace(second, device_id=uuid.uuid4())
    assert verify_entries([first, moved]) == ["seq 2: content_hash does not match the entry"]
    downgraded = dataclasses.replace(second, hash_version=1, device_id=None)
    assert verify_entries([first, downgraded]) == ["seq 2: content_hash does not match the entry"]


# --- append -----------------------------------------------------------------------------------


def test_append_records_the_device_explicitly_or_from_the_request(db: Database) -> None:
    with db.unit_of_work(CTX_A) as s:
        plain = append(s, KEY, {"n": 1}, actor_id=USER)
        explicit = append(s, KEY, {"n": 2}, actor_id=USER, device_id=DEVICE)
        token = current_device_id.set(DEVICE)
        try:
            ambient = append(s, KEY, {"n": 3}, actor_id=USER)
        finally:
            current_device_id.reset(token)
    assert [e.device_id for e in (plain, explicit, ambient)] == [None, DEVICE, DEVICE]
    assert {e.hash_version for e in (plain, explicit, ambient)} == {HASH_VERSION}
    with db.unit_of_work(CTX_A) as s:
        assert verify_chain(s, KEY).ok
        assert [e.device_id for e in chain_entries(s, KEY)] == [None, DEVICE, DEVICE]


@needs_tamper_role
def test_changing_a_stored_device_id_is_detected(db: Database) -> None:
    with db.unit_of_work(CTX_A) as s:
        append(s, KEY, {"n": 1}, device_id=DEVICE)
    other = uuid.uuid4()
    value = str(other)
    tamper(db, "UPDATE audit_ledger SET device_id = :d", {"d": value})
    with db.unit_of_work(CTX_A) as s:
        report = verify_chain(s, KEY)
    assert report.problems == ["seq 1: content_hash does not match the entry"]


@needs_tamper_role
@pytest.mark.parametrize(
    ("sql", "params"),
    [
        ("UPDATE audit_ledger SET hash_version = 4", {}),
        ("UPDATE audit_ledger SET hash_version = 1", {}),  # a v1 row with a device
    ],
)
def test_the_database_refuses_impossible_versions(
    db: Database, sql: str, params: dict[str, object]
) -> None:
    with db.unit_of_work(CTX_A) as s:
        append(s, KEY, {"n": 1}, device_id=DEVICE)
    with pytest.raises(IntegrityError):
        tamper(db, sql, params)


# --- migration 0016 keeps old entries verifying ------------------------------------------


def _v1_rows_at_0015(db: Database) -> list[_Fields]:
    """Two tenant-level v1 entries, inserted the way 0015's schema stored them."""
    rows: list[_Fields] = []
    previous = GENESIS_HASH
    for seq in (1, 2):
        entry = _Fields(
            tenant_id=T1,
            project_id=None,
            chain_key=KEY,
            seq=seq,
            prev_hash=previous,
            created_at=AT,
            created_by=USER,
            source="api",
            payload={"step": seq},
            hash_version=1,
        )
        entry = dataclasses.replace(entry, content_hash=compute_content_hash(entry))
        rows.append(entry)
        previous = entry.content_hash
    with db.unit_of_work(CTX_T1) as s:
        for entry in rows:
            s.execute(
                text(
                    "INSERT INTO audit_ledger (id, tenant_id, project_id, scope_id, chain_key,"
                    " seq, prev_hash, content_hash, payload, created_at, updated_at,"
                    " custom_fields, created_by, source, revision) VALUES (:id, :tenant, NULL,"
                    " :scope, :key, :seq, :prev, :hash, :payload, :at, :at, :empty, :by,"
                    " :source, 1)"
                ).bindparams(bindparam("at", type_=DateTime(timezone=True))),
                {
                    "id": str(uuid.uuid4()),
                    "tenant": str(T1),
                    "scope": str(TENANT_SCOPE),
                    "key": KEY,
                    "seq": entry.seq,
                    "prev": entry.prev_hash,
                    "hash": entry.content_hash,
                    "payload": json.dumps(entry.payload),
                    "at": AT,
                    "empty": "{}",
                    "by": str(USER),
                    "source": entry.source,
                },
            )
    return rows


def test_entries_written_before_0016_still_verify_after_it(tmp_path: Path) -> None:
    url = database_url(tmp_path)
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=url, configure_logger=False)
    command.upgrade(cfg, "0015")
    db = Database(url)
    try:
        _v1_rows_at_0015(db)
        command.upgrade(cfg, "head")
        with db.unit_of_work(CTX_T1) as s:
            stored = chain_entries(s, KEY)
            assert [(e.hash_version, e.device_id) for e in stored] == [(1, None), (1, None)]
            assert verify_chain(s, KEY).ok
            new = append(s, KEY, {"step": 3}, device_id=DEVICE)
            assert (new.seq, new.hash_version) == (3, 2)
        with db.unit_of_work(CTX_T1) as s:
            report = verify_chain(s, KEY)
            assert report.ok, report.problems
            assert report.length == 3
    finally:
        db.dispose()
        command.downgrade(cfg, "base")
