"""Ledger hash v3: ``impersonated_by_user_id`` in the content hash (T-0279, ADR 0015 §6).

While a ``system_admin`` impersonates, every ledger entry names both identities inside the
hash: ``created_by`` the acting (target) user, ``impersonated_by_user_id`` the admin. Outside
impersonation entries stay v2. v1 and v2 entries keep verifying. Runs on SQLite and, in CI, on
PostgreSQL against the migrated schema. All data is synthetic.
"""

import dataclasses
import hashlib
import inspect
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import DateTime, bindparam, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError

from app.core.audit.hashing import (
    GENESIS_HASH,
    HASH_VERSION,
    HASH_VERSIONS,
    IMPERSONATION_HASH_VERSION,
    compute_content_hash,
    verify_entries,
)
from app.core.audit.models import TENANT_SCOPE
from app.core.audit.service import append, chain_entries, verify_chain
from app.core.db import Database
from app.core.tenancy.context import current_device_id, current_impersonator_id
from tests import pg_databases
from tests.audit_support import ALEMBIC_INI, OWNER_URL, migrated_database, tamper
from tests.db_support import ON_POSTGRES, database_url, seed_tenants
from tests.tenancy_support import CTX_A, CTX_T1, PA, T1

TARGET = uuid.UUID("00000000-0000-4000-8000-00000000f0b0")
ADMIN = uuid.UUID("00000000-0000-4000-8000-00000000fa11")
DEVICE = uuid.UUID("00000000-0000-4000-8000-0000000de71c")
KEY = "inspection:00000000-0000-4000-8000-000000000279"
AT = datetime(2026, 11, 22, 8, 30, 0, 123456, tzinfo=UTC)
BEFORE = "0042"  # the revision before the hash v3 migration
AFTER = "0043"  # the hash v3 migration (pinned: a later head must not change these tests)

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
    hash_version: int = IMPERSONATION_HASH_VERSION


def _entry(**changes: object) -> _Fields:
    base = _Fields(
        tenant_id=T1,
        project_id=PA,
        chain_key=KEY,
        seq=1,
        prev_hash=GENESIS_HASH,
        created_at=AT,
        created_by=TARGET,
        source="api",
        payload={"result": "pass"},
    )
    return dataclasses.replace(base, **changes)


def _sealed(entry: _Fields) -> _Fields:
    return dataclasses.replace(entry, content_hash=compute_content_hash(entry))


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def impersonating() -> Iterator[None]:
    """The request context of an impersonation request: what ``get_principal`` publishes."""
    token = current_impersonator_id.set(ADMIN)
    try:
        yield
    finally:
        current_impersonator_id.reset(token)


# --- hashing --------------------------------------------------------------------------------


def test_versions() -> None:
    assert (HASH_VERSION, IMPERSONATION_HASH_VERSION) == (2, 3)
    assert frozenset({1, 2, 3}) == HASH_VERSIONS


def test_v3_content_hash_known_answer() -> None:
    # Pins v3: the v2 body plus "impersonated_by_user_id", with "v": 3.
    for device, shown in ((DEVICE, f'"{DEVICE}"'), (None, "null")):
        canonical = (
            '{"chain_key":"' + KEY + '","created_at":"2026-11-22T08:30:00.123456+00:00",'
            '"created_by":"' + str(TARGET) + '","device_id":' + shown + ","
            '"impersonated_by_user_id":"' + str(ADMIN) + '","payload":{"result":"pass"},'
            '"prev_hash":"' + GENESIS_HASH + '","project_id":"' + str(PA) + '","seq":1,'
            '"source":"api","tenant_id":"' + str(T1) + '","v":3}'
        )
        expected = hashlib.sha256(b"kerp-audit-ledger\n" + canonical.encode()).hexdigest()
        entry = _entry(device_id=device, impersonated_by_user_id=ADMIN)
        assert compute_content_hash(entry) == expected


def test_v2_and_v1_hashes_are_unchanged() -> None:
    body: dict[str, object] = {
        "v": 1,
        "tenant_id": str(T1),
        "project_id": str(PA),
        "chain_key": KEY,
        "seq": 1,
        "prev_hash": GENESIS_HASH,
        "created_at": "2026-11-22T08:30:00.123456+00:00",
        "created_by": str(TARGET),
        "source": "api",
        "payload": {"result": "pass"},
    }

    def digest(value: dict[str, object]) -> str:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(b"kerp-audit-ledger\n" + canonical.encode()).hexdigest()

    assert compute_content_hash(_entry(hash_version=1)) == digest(body)
    v2 = body | {"v": 2, "device_id": str(DEVICE)}
    assert compute_content_hash(_entry(hash_version=2, device_id=DEVICE)) == digest(v2)


@pytest.mark.parametrize("version", [1, 2])
def test_an_entry_before_v3_cannot_carry_an_impersonator(version: int) -> None:
    entry = _entry(hash_version=version, impersonated_by_user_id=ADMIN)
    with pytest.raises(ValueError, match=f"v{version} ledger entry cannot carry"):
        compute_content_hash(entry)
    assert verify_entries([entry]) == [
        f"seq 1: a v{version} ledger entry cannot carry an impersonated_by_user_id"
    ]


@pytest.mark.parametrize("version", [0, 4, True])
def test_unknown_hash_versions_are_refused(version: int) -> None:
    with pytest.raises(ValueError, match="unknown ledger hash version"):
        compute_content_hash(_entry(hash_version=version))


def test_a_chain_mixing_v1_v2_and_v3_entries_verifies() -> None:
    first = _sealed(_entry(hash_version=1))
    second = _sealed(_entry(seq=2, prev_hash=first.content_hash, hash_version=2, device_id=DEVICE))
    third = _sealed(_entry(seq=3, prev_hash=second.content_hash, impersonated_by_user_id=ADMIN))
    fourth = _sealed(_entry(seq=4, prev_hash=third.content_hash, hash_version=2))
    assert verify_entries([first, second, third, fourth]) == []
    # The admin cannot be swapped, removed or the entry downgraded without breaking the hash.
    broken = "seq 3: content_hash does not match the entry"
    for tampered in (
        dataclasses.replace(third, impersonated_by_user_id=uuid.uuid4()),
        dataclasses.replace(third, impersonated_by_user_id=None),
        dataclasses.replace(third, created_by=ADMIN),
    ):
        assert verify_entries([first, second, tampered, fourth]) == [broken]
    downgraded = dataclasses.replace(third, hash_version=2, impersonated_by_user_id=None)
    assert verify_entries([first, second, downgraded, fourth]) == [broken]


# --- append -----------------------------------------------------------------------------------


def test_append_takes_no_impersonator_argument() -> None:
    # The admin comes from the request context only, never from a caller (ADR 0015 §6).
    assert "impersonated_by_user_id" not in inspect.signature(append).parameters
    assert not any("imperson" in name for name in inspect.signature(append).parameters)


def test_outside_impersonation_entries_are_written_as_before(db: Database) -> None:
    with db.unit_of_work(CTX_A) as s:
        entry = append(s, KEY, {"n": 1}, actor_id=TARGET, source="api")
    assert (entry.hash_version, entry.impersonated_by_user_id) == (2, None)
    assert entry.created_by == TARGET


@pytest.mark.usefixtures("impersonating")
def test_while_impersonating_the_entry_names_both_identities_at_v3(db: Database) -> None:
    token = current_device_id.set(None)
    try:
        with db.unit_of_work(CTX_A) as s:
            entry = append(s, KEY, {"n": 1}, actor_id=TARGET, source="api")
    finally:
        current_device_id.reset(token)
    assert (entry.created_by, entry.impersonated_by_user_id) == (TARGET, ADMIN)
    assert entry.hash_version == IMPERSONATION_HASH_VERSION
    with db.unit_of_work(CTX_A) as s:
        [stored] = chain_entries(s, KEY)
        assert (stored.created_by, stored.impersonated_by_user_id) == (TARGET, ADMIN)
        assert stored.hash_version == 3
        assert verify_chain(s, KEY).ok


def test_a_chain_written_in_and_out_of_impersonation_verifies(db: Database) -> None:
    with db.unit_of_work(CTX_A) as s:
        append(s, KEY, {"n": 1}, actor_id=TARGET)
        token = current_impersonator_id.set(ADMIN)
        try:
            append(s, KEY, {"n": 2}, actor_id=TARGET)
        finally:
            current_impersonator_id.reset(token)
        append(s, KEY, {"n": 3}, actor_id=TARGET)
    with db.unit_of_work(CTX_A) as s:
        stored = chain_entries(s, KEY)
        assert [(e.hash_version, e.impersonated_by_user_id) for e in stored] == [
            (2, None),
            (3, ADMIN),
            (2, None),
        ]
        assert verify_chain(s, KEY).ok


@needs_tamper_role
@pytest.mark.usefixtures("impersonating")
def test_changing_or_removing_a_stored_impersonator_is_detected(db: Database) -> None:
    with db.unit_of_work(CTX_A) as s:
        append(s, KEY, {"n": 1}, actor_id=TARGET)
    tamper(db, "UPDATE audit_ledger SET impersonated_by_user_id = :a", {"a": str(uuid.uuid4())})
    with db.unit_of_work(CTX_A) as s:
        assert verify_chain(s, KEY).problems == ["seq 1: content_hash does not match the entry"]
    tamper(db, "UPDATE audit_ledger SET impersonated_by_user_id = NULL")
    with db.unit_of_work(CTX_A) as s:
        assert verify_chain(s, KEY).problems == ["seq 1: content_hash does not match the entry"]


@needs_tamper_role
@pytest.mark.usefixtures("impersonating")
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_ledger SET hash_version = 4",
        "UPDATE audit_ledger SET hash_version = 2",  # a v2 row claiming an impersonator
        "UPDATE audit_ledger SET hash_version = 1, device_id = NULL",
    ],
)
def test_the_database_refuses_impossible_versions(db: Database, sql: str) -> None:
    with db.unit_of_work(CTX_A) as s:
        append(s, KEY, {"n": 1}, actor_id=TARGET)
    with pytest.raises(IntegrityError):
        tamper(db, sql)


# --- the migration keeps old entries verifying ----------------------------------------------


def _insert(db: Database, entries: list[_Fields]) -> None:
    """Insert ``entries`` the way the schema of ``BEFORE`` stored them (no impersonator)."""
    with db.unit_of_work(CTX_T1) as s:
        for entry in entries:
            s.execute(
                text(
                    "INSERT INTO audit_ledger (id, tenant_id, project_id, scope_id, chain_key,"
                    " seq, prev_hash, content_hash, payload, created_at, updated_at,"
                    " custom_fields, created_by, source, revision, device_id, hash_version)"
                    " VALUES (:id, :tenant, NULL, :scope, :key, :seq, :prev, :hash, :payload,"
                    " :at, :at, :empty, :by, :source, 1, :device, :version)"
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
                    "by": str(TARGET),
                    "source": entry.source,
                    "device": None if entry.device_id is None else str(entry.device_id),
                    "version": entry.hash_version,
                },
            )


def _old_chain() -> list[_Fields]:
    first = _sealed(_entry(project_id=None, hash_version=1))
    second = _sealed(
        _entry(
            project_id=None, seq=2, prev_hash=first.content_hash, hash_version=2, device_id=DEVICE
        )
    )
    return [first, second]


def _migration(tmp_path: Path) -> tuple[Config, str]:
    url = database_url(tmp_path)
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=url, configure_logger=False)
    return cfg, url


@needs_tamper_role  # PostgreSQL: the v3 entry is removed as the owner before going to base
def test_entries_written_before_the_migration_still_verify_after_it(tmp_path: Path) -> None:
    cfg, url = _migration(tmp_path)
    command.upgrade(cfg, BEFORE)
    db = Database(url)
    try:
        seed_tenants(db, T1)
        _insert(db, _old_chain())
        command.upgrade(cfg, AFTER)
        with db.unit_of_work(CTX_T1) as s:
            stored = chain_entries(s, KEY)
            assert [(e.hash_version, e.impersonated_by_user_id) for e in stored] == [
                (1, None),
                (2, None),
            ]
            assert verify_chain(s, KEY).ok
            token = current_impersonator_id.set(ADMIN)
            try:
                new = append(s, KEY, {"step": 3}, actor_id=TARGET)
            finally:
                current_impersonator_id.reset(token)
            assert (new.seq, new.hash_version, new.impersonated_by_user_id) == (3, 3, ADMIN)
        with db.unit_of_work(CTX_T1) as s:
            report = verify_chain(s, KEY)
            assert report.ok, report.problems
            assert report.length == 3
        # Development only (ADR 0015): a downgrade would strip the admin from v3 entries, so
        # the restored ``hash_version IN (1, 2)`` CHECK refuses it while any exists ...
        with pytest.raises(IntegrityError, match="hash_version"):
            command.downgrade(cfg, BEFORE)
        # ... and leaves the schema, the v3 entry and its admin as they were.
        assert _revision(db) == AFTER
        with db.unit_of_work(CTX_T1) as s:
            stored = chain_entries(s, KEY)
            assert [(e.hash_version, e.impersonated_by_user_id) for e in stored] == [
                (1, None),
                (2, None),
                (3, ADMIN),
            ]
            assert verify_chain(s, KEY).ok
    finally:
        db.dispose()
        _drop(cfg, url)


@needs_tamper_role
def test_the_downgrade_keeps_v1_and_v2_entries(tmp_path: Path) -> None:
    cfg, url = _migration(tmp_path)
    command.upgrade(cfg, BEFORE)
    db = Database(url)
    try:
        seed_tenants(db, T1)
        _insert(db, _old_chain())
        command.upgrade(cfg, AFTER)
        command.downgrade(cfg, BEFORE)
        assert _revision(db) == BEFORE
        with db.unit_of_work(CTX_T1) as s:  # bound: RLS shows the runtime role nothing else
            rows = s.execute(text("SELECT seq, hash_version FROM audit_ledger ORDER BY seq")).all()
            columns = {c["name"] for c in sa_inspect(s.connection()).get_columns("audit_ledger")}
        assert [tuple(r) for r in rows] == [(1, 1), (2, 2)]
        assert "impersonated_by_user_id" not in columns
    finally:
        db.dispose()
        _drop(cfg, url)


def _revision(db: Database) -> str | None:
    with db.engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _drop(cfg: Config, url: str) -> None:
    """Back to base for the shared PostgreSQL database; SQLite files go with ``tmp_path`` and
    a per-test PostgreSQL database is dropped after the test."""
    if not ON_POSTGRES or pg_databases.PER_TEST:
        return
    db = Database(url)
    try:
        tamper(db, "DELETE FROM audit_ledger")
    finally:
        db.dispose()
    command.downgrade(cfg, "base")
