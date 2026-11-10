"""Hash-chained audit ledger (T-0006, diff §4): append, verify, tamper detection, ORM guard.

These run on SQLite and on PostgreSQL against the migrated schema. The database-level
guard (REVOKE + triggers), RLS and concurrency are in ``test_audit_ledger_pg.py``.
"""

import dataclasses
import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.audit.guard import ledger_guard_drop_sql, ledger_guard_problems, ledger_guard_sql
from app.core.audit.hashing import (
    GENESIS_HASH,
    canonical_json,
    compute_content_hash,
    verify_entries,
)
from app.core.audit.models import AppendOnlyError, AuditEntry
from app.core.audit.service import ChainScopeError, append, chain_entries, verify_chain
from app.core.db import Base, Database, contract_violations
from app.core.tenancy.rls import rls_tables
from tests.audit_support import OWNER_URL, migrated_database, tamper
from tests.db_support import ON_POSTGRES
from tests.tenancy_support import CTX_A, CTX_T1, PA, T1

USER = uuid.UUID("00000000-0000-4000-8000-00000000f001")
KEY = "inspection:00000000-0000-4000-8000-000000000123"

needs_tamper_role = pytest.mark.skipif(
    ON_POSTGRES and not OWNER_URL,
    reason="tampering on PostgreSQL needs KERP_TEST_OWNER_URL (set in CI) (TD-052)",
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def _append_three(db: Database, key: str = KEY) -> list[AuditEntry]:
    with db.unit_of_work(CTX_A) as s:
        return [append(s, key, {"step": n, "by": "inspector"}, actor_id=USER) for n in (1, 2, 3)]


# --- contract ------------------------------------------------------------------------------


def test_ledger_table_meets_the_record_contract_and_is_under_rls() -> None:
    assert "audit_ledger" in Base.metadata.tables
    assert [p for p in contract_violations(Base.metadata) if p.startswith("audit_ledger")] == []
    assert "audit_ledger" in rls_tables(Base.metadata)


# --- append ----------------------------------------------------------------------------------


def test_append_starts_a_chain_at_genesis(db: Database) -> None:
    with db.unit_of_work(CTX_A) as s:
        entry = append(s, KEY, {"result": "pass"}, actor_id=USER)
    assert (entry.seq, entry.prev_hash, entry.chain_key) == (1, GENESIS_HASH, KEY)
    assert (entry.tenant_id, entry.project_id, entry.created_by) == (T1, PA, USER)
    assert entry.source == "system"
    assert len(entry.content_hash) == 64
    assert entry.content_hash == compute_content_hash(entry)


def test_append_links_each_entry_to_the_previous_one(db: Database) -> None:
    first, second, third = _append_three(db)
    assert [e.seq for e in (first, second, third)] == [1, 2, 3]
    assert second.prev_hash == first.content_hash
    assert third.prev_hash == second.content_hash
    with db.unit_of_work(CTX_A) as s:
        other = append(s, "inspection:other", {"result": "fail"}, source="ai")
        later = append(s, KEY, {"step": 4})
    assert (other.seq, other.prev_hash, other.source) == (1, GENESIS_HASH, "ai")
    assert (later.seq, later.prev_hash) == (4, third.content_hash)


def test_append_takes_the_scope_from_the_session_never_from_arguments(db: Database) -> None:
    with pytest.raises(RuntimeError, match="bound"), db.sessionmaker() as s:
        append(s, KEY, {"x": 1})
    with db.unit_of_work(CTX_A) as s:
        assert verify_chain(s, KEY).length == 0


def test_append_rejects_an_invalid_source(db: Database) -> None:
    with pytest.raises(ValueError, match="source"), db.unit_of_work(CTX_A) as s:
        append(s, KEY, {"x": 1}, source="robot")


@pytest.mark.parametrize("key", ["", "k" * 201])
def test_append_rejects_an_invalid_chain_key(db: Database, key: str) -> None:
    with pytest.raises(ValueError, match="chain_key"), db.unit_of_work(CTX_A) as s:
        append(s, key, {"x": 1})


def test_guard_sql_revokes_and_adds_triggers() -> None:
    statements = ledger_guard_sql()
    assert statements[:2] == [
        "REVOKE UPDATE, DELETE, TRUNCATE ON audit_ledger FROM PUBLIC",
        "REVOKE UPDATE, DELETE, TRUNCATE ON audit_ledger FROM CURRENT_USER",
    ]
    assert any("BEFORE UPDATE OR DELETE ON audit_ledger" in s for s in statements)
    assert any("BEFORE TRUNCATE ON audit_ledger" in s for s in statements)
    assert all("audit_ledger" in s or "FUNCTION" in s for s in ledger_guard_drop_sql())


def test_guard_check_is_a_no_op_off_postgresql(tmp_path: Path) -> None:
    db = Database("sqlite:///" + (tmp_path / "plain.db").as_posix())
    try:
        with db.engine.connect() as conn:
            assert ledger_guard_problems(conn) == []
    finally:
        db.dispose()


def test_a_chain_belongs_to_one_scope(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        append(s, "library:checklists", {"v": 1})
    # A project session can read the tenant-level chain, but must not extend it.
    with pytest.raises(ChainScopeError), db.unit_of_work(CTX_A) as s:
        append(s, "library:checklists", {"v": 2})


def test_duplicate_seq_in_a_chain_is_rejected_by_the_database(db: Database) -> None:
    (first, *_) = _append_three(db)
    clone = AuditEntry(
        tenant_id=T1,
        project_id=PA,
        chain_key=KEY,
        seq=2,
        prev_hash=first.content_hash,
        content_hash="f" * 64,
        payload={"fork": True},
    )
    with pytest.raises(IntegrityError), db.unit_of_work(CTX_A) as s:
        s.add(clone)


# --- verify ------------------------------------------------------------------------------------


def test_verify_accepts_an_intact_chain(db: Database) -> None:
    *_, last = _append_three(db)
    with db.unit_of_work(CTX_A) as s:
        report = verify_chain(s, KEY)
        assert [e.seq for e in chain_entries(s, KEY)] == [1, 2, 3]
        empty = verify_chain(s, "inspection:none")
    assert (report.ok, report.length, report.problems) == (True, 3, [])
    assert report.head_hash == last.content_hash
    assert (empty.ok, empty.length, empty.head_hash) == (True, 0, GENESIS_HASH)


def test_verify_entries_detects_tamper_gap_and_reorder(db: Database) -> None:
    entries = [_snapshot(e) for e in _append_three(db)]
    assert verify_entries(entries) == []

    tampered = [*entries]
    tampered[1] = dataclasses.replace(entries[1], payload={"step": 2, "by": "someone-else"})
    assert verify_entries(tampered) == ["seq 2: content_hash does not match the entry"]

    assert verify_entries([entries[0], entries[2]]) == [
        "seq 3: expected seq 2 (entry missing or out of order)",
    ]
    assert verify_entries([entries[0], entries[2], entries[1]]) == [
        "seq 3: expected seq 2 (entry missing or out of order)",
        "seq 2: expected seq 4 (entry missing or out of order)",
    ]
    relinked = [entries[0], dataclasses.replace(entries[1], prev_hash="0" * 63 + "1"), entries[2]]
    assert verify_entries(relinked) == [
        "seq 2: prev_hash does not match the previous entry",
        "seq 2: content_hash does not match the entry",
    ]
    assert verify_entries(entries[1:]) == ["seq 2: expected seq 1 (entry missing or out of order)"]


@needs_tamper_role
def test_verify_detects_a_payload_edited_in_the_database(db: Database) -> None:
    _append_three(db)
    payload = '{"step": 2, "by": "forger"}'
    cast = "CAST(:p AS jsonb)" if ON_POSTGRES else ":p"
    changed = tamper(db, f"UPDATE audit_ledger SET payload = {cast} WHERE seq = 2", {"p": payload})
    assert changed == 1
    with db.unit_of_work(CTX_A) as s:
        report = verify_chain(s, KEY)
    assert not report.ok
    assert report.problems == ["seq 2: content_hash does not match the entry"]


@needs_tamper_role
def test_verify_detects_a_deleted_row(db: Database) -> None:
    _append_three(db)
    assert tamper(db, "DELETE FROM audit_ledger WHERE seq = 2") == 1
    with db.unit_of_work(CTX_A) as s:
        report = verify_chain(s, KEY)
    assert report.problems == ["seq 3: expected seq 2 (entry missing or out of order)"]


@needs_tamper_role
def test_verify_detects_reordered_rows(db: Database) -> None:
    _append_three(db)
    # Swap seq 2 and 3 (via a free value, the unique constraint holds at every step).
    tamper(db, "UPDATE audit_ledger SET seq = 99 WHERE seq = 2")
    tamper(db, "UPDATE audit_ledger SET seq = 2 WHERE seq = 3")
    tamper(db, "UPDATE audit_ledger SET seq = 3 WHERE seq = 99")
    with db.unit_of_work(CTX_A) as s:
        report = verify_chain(s, KEY)
    assert not report.ok
    assert "seq 2: prev_hash does not match the previous entry" in report.problems
    assert "seq 2: content_hash does not match the entry" in report.problems


@needs_tamper_role
def test_verify_flags_a_soft_deleted_entry(db: Database) -> None:
    _append_three(db)
    tamper(db, "UPDATE audit_ledger SET deleted_at = created_at WHERE seq = 1")
    with db.unit_of_work(CTX_A) as s:
        report = verify_chain(s, KEY)
    assert report.problems == ["seq 1: entry is marked deleted"]


# --- append-only at the ORM level ----------------------------------------------------------------


def test_orm_refuses_to_update_or_delete_an_entry(db: Database) -> None:
    _append_three(db)
    with db.unit_of_work(CTX_A) as s:
        entry = s.scalars(select(AuditEntry).where(AuditEntry.seq == 1)).one()
        entry.payload = {"rewritten": True}
        with pytest.raises(AppendOnlyError):
            s.flush()
        s.rollback()
        s.delete(s.scalars(select(AuditEntry).where(AuditEntry.seq == 1)).one())
        with pytest.raises(AppendOnlyError):
            s.flush()
        s.rollback()
    with db.unit_of_work(CTX_A) as s:
        assert verify_chain(s, KEY).ok


def test_soft_delete_does_not_apply_to_audit_entries(db: Database) -> None:
    _append_three(db)
    with db.unit_of_work(CTX_A) as s:
        entry = s.scalars(select(AuditEntry).where(AuditEntry.seq == 1)).one()
        with pytest.raises(AppendOnlyError):
            entry.soft_delete()
        assert entry.deleted_at is None
    born_deleted = AuditEntry(
        tenant_id=T1, project_id=PA, chain_key="x", seq=1, prev_hash=GENESIS_HASH,
        content_hash="0" * 64, payload={}, deleted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )  # fmt: skip
    with pytest.raises(AppendOnlyError), db.unit_of_work(CTX_A) as s:
        s.add(born_deleted)
    with db.unit_of_work(CTX_A) as s:
        assert s.scalar(select(func.count()).select_from(AuditEntry)) == 3


# --- hashing ---------------------------------------------------------------------------------


def test_canonical_json_is_key_order_independent_and_compact() -> None:
    expected = '{"a":"é","b":[1,true,null]}'.encode()
    assert canonical_json({"b": (1, True, None), "a": "é"}) == expected
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ({"x": 1.5}, "float"),
        ({"x": float("nan")}, "float"),
        ({1: "x"}, "keys"),
        ({"x": "a\x00b"}, "NUL"),
        ({"x": {1, 2}}, "JSON"),
    ],
)
def test_canonical_json_rejects_values_that_do_not_round_trip(value: object, error: str) -> None:
    with pytest.raises((TypeError, ValueError), match=error):
        canonical_json(value)


def test_payload_must_be_a_json_object(db: Database) -> None:
    with pytest.raises(TypeError, match="object"), db.unit_of_work(CTX_A) as s:
        append(s, KEY, ["not", "an", "object"])  # type: ignore[arg-type]


def test_content_hash_known_answer() -> None:
    # Pins the algorithm: changing what is hashed, or how, breaks every stored chain.
    entry = _Fields(
        tenant_id=T1,
        project_id=PA,
        chain_key=KEY,
        seq=1,
        prev_hash=GENESIS_HASH,
        created_at=datetime(2026, 11, 10, 8, 30, 0, 123456, tzinfo=UTC),
        created_by=None,
        source="system",
        payload={"result": "pass"},
    )
    canonical = (
        '{"chain_key":"' + KEY + '","created_at":"2026-11-10T08:30:00.123456+00:00",'
        '"created_by":null,"payload":{"result":"pass"},"prev_hash":"' + GENESIS_HASH + '",'
        '"project_id":"' + str(PA) + '","seq":1,"source":"system",'
        '"tenant_id":"' + str(T1) + '","v":1}'
    )
    expected = hashlib.sha256(b"kerp-audit-ledger\n" + canonical.encode()).hexdigest()
    assert compute_content_hash(entry) == expected
    naive = dataclasses.replace(entry, created_at=entry.created_at.replace(tzinfo=None))
    assert compute_content_hash(naive) == expected  # SQLite returns naive UTC


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


def _snapshot(entry: AuditEntry) -> _Fields:
    return _Fields(**{f.name: getattr(entry, f.name) for f in dataclasses.fields(_Fields)})
