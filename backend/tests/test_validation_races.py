"""Unlock decisions and rule-set version bumps under concurrency (T-0347, audit item 9).

An unlock request is closed by a guarded ``UPDATE … WHERE status = 'pending'``: the loser of
an approve/reject race gets ``RuleStoreError`` (409) with nothing applied and nothing audited.
Every rule-set version bump locks the set's row first, so concurrent edits get consecutive
versions. The SQLite tests check the guarded logic with a decision committed between the read
and the write; the PostgreSQL tests race two real sessions. All data is synthetic.
"""

import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.db import Database, utcnow
from app.modules.validation import service as rule_store
from app.modules.validation.models import Rule, RuleSet, UnlockRequest
from app.modules.validation.service import (
    RuleStoreError,
    approve_unlock,
    get_rule_set,
    reject_unlock,
    request_unlock,
    save_rule,
    set_rule_enabled,
    sync_system_layer,
)
from tests.audit_support import migrated_database
from tests.db_support import ON_POSTGRES
from tests.test_validation_rule_layers import (
    CTX_T1,
    OPS,
    QA,
    events,
    rule,
    rules_of,
    seed,
    seeded,
    set_of,
    spec,
)

on_postgres = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL row locks; runs in CI's PostgreSQL step (TD-052)"
)
DECISIONS = {"unlock_approved", "unlock_rejected", "unlock_cancelled", "rule_unlocked"}


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def pending(db: Database) -> tuple[uuid.UUID, uuid.UUID]:
    """A tenant set whose locked ``calibration`` rule has a pending unlock: (set, request)."""
    seeded(db)
    tenant_set = set_of(db, CTX_T1, "tenant")
    with db.unit_of_work(CTX_T1) as s:
        request_id = request_unlock(s, tenant_set, "calibration", adr_reference="ADR 9").id
    return tenant_set, request_id


def request_of(db: Database, request_id: uuid.UUID) -> UnlockRequest:
    with db.unit_of_work(CTX_T1) as s:
        request = s.get(UnlockRequest, request_id)
        assert request is not None
        s.expunge(request)
    return request


def set_version(db: Database, set_id: uuid.UUID) -> int:
    with db.unit_of_work(CTX_T1) as s:
        return get_rule_set(s, set_id, "tenant").version


def decided_meanwhile(
    db: Database, monkeypatch: pytest.MonkeyPatch, decide: Callable[[Session], Any]
) -> None:
    """The request is read as ``pending``; then, before this decision writes, another
    session's ``decide`` commits."""
    read_pending = rule_store._pending_unlock

    def then_another_decides(session: Session, request_id: uuid.UUID) -> UnlockRequest:
        request = read_pending(session, request_id)
        assert request.status == "pending"
        monkeypatch.setattr(rule_store, "_pending_unlock", read_pending)
        with db.unit_of_work(CTX_T1) as other:
            decide(other)
        return request

    monkeypatch.setattr(rule_store, "_pending_unlock", then_another_decides)


# --- the guarded decision (SQLite and PostgreSQL) ---------------------------------------------


def test_an_approve_after_a_concurrent_reject_applies_and_audits_nothing(
    db: Database, pending: tuple[uuid.UUID, uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_set, request_id = pending
    before = set_version(db, tenant_set)
    decided_meanwhile(db, monkeypatch, lambda s: reject_unlock(s, request_id, actor_id=QA))
    with (
        pytest.raises(RuleStoreError, match="decided meanwhile"),
        db.unit_of_work(CTX_T1) as s,
    ):
        approve_unlock(s, request_id, actor_id=OPS)
    request = request_of(db, request_id)
    assert (request.status, request.decided_by) == ("rejected", QA)
    calibration = rules_of(db, CTX_T1, tenant_set)["calibration"]
    assert (calibration.locked, calibration.version) == (True, 1)
    assert set_version(db, tenant_set) == before
    assert [e for e in events(db, CTX_T1, tenant_set) if e in DECISIONS] == ["unlock_rejected"]


def test_a_reject_after_a_concurrent_approve_leaves_the_rule_unlocked_and_approved(
    db: Database, pending: tuple[uuid.UUID, uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_set, request_id = pending
    decided_meanwhile(db, monkeypatch, lambda s: approve_unlock(s, request_id, actor_id=OPS))
    with (
        pytest.raises(RuleStoreError, match="decided meanwhile"),
        db.unit_of_work(CTX_T1) as s,
    ):
        reject_unlock(s, request_id, actor_id=QA)
    request = request_of(db, request_id)
    assert (request.status, request.decided_by) == ("approved", OPS)
    assert rules_of(db, CTX_T1, tenant_set)["calibration"].locked is False
    decisions = [e for e in events(db, CTX_T1, tenant_set) if e in DECISIONS]
    assert decisions == ["unlock_approved", "rule_unlocked"]


def test_a_stale_pending_row_makes_the_guarded_update_touch_nothing(
    db: Database, pending: tuple[uuid.UUID, uuid.UUID]
) -> None:
    _tenant_set, request_id = pending
    with db.unit_of_work(CTX_T1) as s:
        request = s.get(UnlockRequest, request_id)
        assert request is not None
        # The row changes under the loaded object; the object still says "pending".
        s.execute(
            update(UnlockRequest)
            .where(UnlockRequest.id == request_id)
            .values(status="cancelled", decided_by=request.requested_by, decided_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        assert request.status == "pending"
        with pytest.raises(RuleStoreError, match="decided meanwhile"):
            rule_store._decide(s, request, "rejected", OPS)
        s.expire_all()
        assert (request.status, request.decided_by) == ("cancelled", request.requested_by)


def test_a_decision_bumps_the_request_revision(
    db: Database, pending: tuple[uuid.UUID, uuid.UUID]
) -> None:
    _tenant_set, request_id = pending
    revision = request_of(db, request_id).revision
    with db.unit_of_work(CTX_T1) as s:
        decided = reject_unlock(s, request_id, actor_id=OPS)
        assert (decided.status, decided.decided_by, decided.updated_by) == ("rejected", OPS, OPS)
        assert decided.decided_at is not None
    assert request_of(db, request_id).revision == revision + 1


# --- the locked version bump --------------------------------------------------------------------


def test_an_edit_bumps_the_version_the_database_holds_not_a_stale_one(db: Database) -> None:
    seeded(db)
    tenant_set = set_of(db, CTX_T1, "tenant")
    start = set_version(db, tenant_set)
    with db.unit_of_work(CTX_T1) as s:
        stale = s.get(RuleSet, tenant_set)  # read before the other edit commits
        assert stale is not None
        assert stale.version == start
        with db.unit_of_work(CTX_T1) as other:
            save_rule(other, tenant_set, spec("photo", "fields.photos > 1"))
        set_rule_enabled(s, tenant_set, "photo", False)
        assert stale.version == start + 2
    assert set_version(db, tenant_set) == start + 2
    with db.unit_of_work(CTX_T1) as s:
        chain = rule_store.audit_chain(s.get_one(RuleSet, tenant_set))
        versions = [
            e.payload.get("rule_set_version")
            for e in rule_store.audit.chain_entries(s, chain)
            if e.payload["event"] in {"rule_saved", "rule_disabled"}
        ]
    assert versions == [start + 1, start + 2]


def test_a_seed_load_that_lost_the_race_writes_nothing(db: Database) -> None:
    seeded(db)
    system_set = set_of(db, CTX_T1, "system")
    newer = seed(2, [rule("photo", "true")])
    with db.unit_of_work(CTX_T1) as s:
        stale = s.get(RuleSet, system_set)  # still at seed version 1 in this session
        assert stale is not None
        with db.unit_of_work(CTX_T1) as other:
            assert sync_system_layer(other, [newer]) == [newer.key]
        assert sync_system_layer(s, [newer]) == []
        version = stale.version
    with db.unit_of_work(CTX_T1) as s:
        assert s.get_one(RuleSet, system_set).version == version


# --- real concurrent sessions (PostgreSQL) -----------------------------------------------


def race(db: Database, *steps: Callable[[Session], Any]) -> list[Any]:
    """Run each step in its own session and thread, all released together by a barrier; return
    what each step returned or raised."""
    barrier = threading.Barrier(len(steps), timeout=20)
    outcome: list[Any] = [None] * len(steps)

    def run(index: int, step: Callable[[Session], Any]) -> None:
        try:
            with db.unit_of_work(CTX_T1) as s:
                barrier.wait()
                outcome[index] = step(s)
        except Exception as exc:
            outcome[index] = exc

    threads = [threading.Thread(target=run, args=(i, step)) for i, step in enumerate(steps)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not any(thread.is_alive() for thread in threads)
    return outcome


@on_postgres
def test_concurrent_approve_and_reject_have_exactly_one_winner(
    db: Database, pending: tuple[uuid.UUID, uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_set, request_id = pending
    # Both sessions read the request as pending before either writes: the race of audit item 9.
    read_pending = rule_store._pending_unlock
    both_read = threading.Barrier(2, timeout=20)

    def read_then_wait(session: Session, rid: uuid.UUID) -> UnlockRequest:
        request = read_pending(session, rid)
        both_read.wait()
        return request

    monkeypatch.setattr(rule_store, "_pending_unlock", read_then_wait)
    approved, rejected = race(
        db,
        lambda s: approve_unlock(s, request_id, actor_id=OPS),
        lambda s: reject_unlock(s, request_id, actor_id=QA),
    )
    errors = [o for o in (approved, rejected) if isinstance(o, Exception)]
    assert len(errors) == 1, (approved, rejected)
    assert isinstance(errors[0], RuleStoreError)
    assert "decided meanwhile" in str(errors[0])
    request = request_of(db, request_id)
    locked = rules_of(db, CTX_T1, tenant_set)["calibration"].locked
    decisions = [e for e in events(db, CTX_T1, tenant_set) if e in DECISIONS]
    if request.status == "approved":
        assert not isinstance(approved, Exception)
        assert locked is False
        assert decisions == ["unlock_approved", "rule_unlocked"]
    else:
        assert request.status == "rejected"
        assert not isinstance(rejected, Exception)
        assert locked is True
        assert decisions == ["unlock_rejected"]


@on_postgres
@pytest.mark.parametrize("first", ["approve", "reject"])
def test_the_second_decision_waits_for_the_first_and_then_loses(
    db: Database, pending: tuple[uuid.UUID, uuid.UUID], first: str
) -> None:
    tenant_set, request_id = pending
    steps: dict[str, Callable[[Session], Any]] = {
        "approve": lambda s: approve_unlock(s, request_id, actor_id=OPS),
        "reject": lambda s: reject_unlock(s, request_id, actor_id=QA),
    }
    second = "reject" if first == "approve" else "approve"
    outcome: dict[str, Any] = {}
    done = threading.Event()

    def run() -> None:
        try:
            with db.unit_of_work(CTX_T1) as s:
                outcome["value"] = steps[second](s)
        except Exception as exc:
            outcome["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=run)
    with db.unit_of_work(CTX_T1) as s:
        steps[first](s)
        thread.start()
        assert not done.wait(0.5), f"the second decision did not wait: {outcome}"
    thread.join(20)
    assert done.is_set()
    assert isinstance(outcome.get("error"), RuleStoreError), outcome
    assert request_of(db, request_id).status == ("approved" if first == "approve" else "rejected")
    locked = rules_of(db, CTX_T1, tenant_set)["calibration"].locked
    assert locked is (first == "reject")


@on_postgres
def test_concurrent_edits_get_consecutive_rule_set_versions(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded(db)
    tenant_set = set_of(db, CTX_T1, "tenant")
    start = set_version(db, tenant_set)
    # Both sessions have read the set (version N) before either bumps it.
    plain_read = rule_store.get_or_404
    both_read = threading.Barrier(2, timeout=20)

    def read_then_wait(session: Session, model: Any, record_id: uuid.UUID) -> Any:
        found = plain_read(session, model, record_id)
        if model is RuleSet:
            both_read.wait()
        return found

    monkeypatch.setattr(rule_store, "get_or_404", read_then_wait)
    outcome = race(
        db,
        lambda s: save_rule(s, tenant_set, spec("photo", "fields.photos > 1")),
        lambda s: save_rule(s, tenant_set, spec("extra", "fields.extra == true")),
    )
    monkeypatch.setattr(rule_store, "get_or_404", plain_read)
    assert all(isinstance(o, Rule) for o in outcome), outcome
    assert set_version(db, tenant_set) == start + 2
    with db.unit_of_work(CTX_T1) as s:
        chain = rule_store.audit_chain(s.get_one(RuleSet, tenant_set))
        versions = sorted(
            e.payload["rule_set_version"]
            for e in rule_store.audit.chain_entries(s, chain)
            if e.payload["event"] == "rule_saved"
        )
    assert versions == [start + 1, start + 2]


@on_postgres
def test_a_second_edit_waits_for_the_rule_set_lock(db: Database) -> None:
    seeded(db)
    tenant_set = set_of(db, CTX_T1, "tenant")
    start = set_version(db, tenant_set)
    done = threading.Event()
    outcome: dict[str, Any] = {}

    def second() -> None:
        try:
            with db.unit_of_work(CTX_T1) as s:
                outcome["rule"] = set_rule_enabled(s, tenant_set, "photo", False)
        except Exception as exc:
            outcome["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=second)
    with db.unit_of_work(CTX_T1) as s:
        save_rule(s, tenant_set, spec("photo", "fields.photos > 1"))
        thread.start()
        assert not done.wait(0.5), f"the second edit did not wait: {outcome}"
    thread.join(20)
    assert "error" not in outcome, outcome
    assert set_version(db, tenant_set) == start + 2
    with db.unit_of_work(CTX_T1) as s:
        photo = s.scalars(
            select(Rule).where(Rule.rule_set_id == tenant_set, Rule.rule_key == "photo")
        ).one()
        assert (photo.enabled, photo.expression, photo.version) == (False, "fields.photos > 1", 3)
