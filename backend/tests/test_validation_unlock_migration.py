"""Migration ``0020``: at most one pending unlock request per rule (T-0216).

Existing duplicates (two ``pending`` requests of one rule) are resolved first: the oldest
stays pending, the others become ``cancelled`` (decided by their own requester, as a
withdrawal) and their ids are logged. Runs the real Alembic migrations on SQLite and, in CI's
PostgreSQL step, as the ``NOSUPERUSER NOBYPASSRLS`` role ``kerp_app`` (the table's RLS is
forced). All data synthetic.
"""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.core.db import Database, utcnow
from app.core.tenancy.context import TenancyContext
from app.modules.validation import service as rule_store
from app.modules.validation.defaults import SeedRuleSet
from app.modules.validation.models import Rule, RuleSet, UnlockRequest
from tests.audit_support import ALEMBIC_INI
from tests.db_support import database_url, seed_tenants
from tests.tenancy_support import T1

QD = uuid.UUID("00000000-0000-4000-8000-00000000d013")
QD2 = uuid.UUID("00000000-0000-4000-8000-00000000d113")
OPS = uuid.UUID("00000000-0000-4000-8000-00000000d001")
CTX = TenancyContext(tenant_id=T1, user_id=QD)
GATE = "projects.unlock_migration"


@pytest.fixture
def migrated(tmp_path: Path) -> Iterator[tuple[Config, Database]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url=database_url(tmp_path), configure_logger=False)
    command.upgrade(cfg, "head")
    db = Database(database_url(tmp_path))
    try:
        seed_tenants(db, T1)
        yield cfg, db
    finally:
        db.dispose()
        command.downgrade(cfg, "base")


def _locked_rule(db: Database) -> tuple[uuid.UUID, Rule]:
    seed = SeedRuleSet.model_validate(
        {
            "module": "projects",
            "key": GATE,
            "kind": "check",
            "version": 1,
            "rules": [
                {
                    "rule_key": "calibration",
                    "engine": "cel",
                    "expression": "fields.calibrated == true",
                    "action": "block",
                    "locked": True,
                }
            ],
        }
    )
    with db.unit_of_work(CTX) as s:
        rule_store.seed_tenant(s, [seed])
    with db.unit_of_work(CTX) as s:
        tenant_set = s.scalars(
            select(RuleSet).where(RuleSet.key == GATE, RuleSet.layer == "tenant")
        ).one()
        rule = s.scalars(select(Rule).where(Rule.rule_set_id == tenant_set.id)).one()
        s.expunge(rule)
        return tenant_set.id, rule


def _request(db: Database, set_id: uuid.UUID, rule: Rule, by: uuid.UUID, age_h: int) -> uuid.UUID:
    with db.unit_of_work(CTX) as s:
        row = UnlockRequest(
            tenant_id=T1,
            project_id=None,
            rule_set_id=set_id,
            rule_id=rule.id,
            rule_key=rule.rule_key,
            rule_version=rule.version,
            adr_reference="ADR 7",
            status="pending",
            requested_by=by,
        )
        s.add(row)
        s.flush()
        row.created_at = utcnow() - timedelta(hours=age_h)
        return row.id


def _rows(db: Database) -> dict[uuid.UUID, tuple[str, uuid.UUID | None]]:
    with db.unit_of_work(CTX) as s:
        return {r.id: (r.status, r.decided_by) for r in s.scalars(select(UnlockRequest))}


def test_the_upgrade_keeps_the_oldest_pending_request_and_cancels_the_others(
    migrated: tuple[Config, Database], caplog: pytest.LogCaptureFixture
) -> None:
    cfg, db = migrated
    set_id, rule = _locked_rule(db)
    command.downgrade(cfg, "0019")  # before the index: duplicates can exist
    oldest = _request(db, set_id, rule, QD, age_h=3)
    newer = _request(db, set_id, rule, QD2, age_h=2)
    newest = _request(db, set_id, rule, QD, age_h=1)
    with caplog.at_level("WARNING"):
        command.upgrade(cfg, "head")
    rows = _rows(db)
    assert rows[oldest] == ("pending", None)
    assert rows[newer] == ("cancelled", QD2)  # withdrawn: decided by its own requester
    assert rows[newest] == ("cancelled", QD)
    assert str(newer) in caplog.text
    assert str(newest) in caplog.text


def test_the_downgrade_drops_the_index(migrated: tuple[Config, Database]) -> None:
    cfg, db = migrated
    set_id, rule = _locked_rule(db)
    command.downgrade(cfg, "0019")
    first = _request(db, set_id, rule, QD, age_h=2)
    second = _request(db, set_id, rule, OPS, age_h=1)  # allowed again without the index
    assert {k: v[0] for k, v in _rows(db).items()} == {first: "pending", second: "pending"}
    command.upgrade(cfg, "head")
