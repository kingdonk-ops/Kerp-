"""Metadata contract test (T-0005): every mapped table carries the BaseRecordMixin columns.

Adapted from Inspection-ERP ``tests/test_contract.py`` (ADR 0001). A table may skip the
mixin only when it is listed in ``MIXIN_EXEMPT`` with its reason. The hash-chained audit
ledger (T-0006) carries the mixin too, so nothing is exempt.
"""

from sqlalchemy import Column, Integer, MetaData, String, Table

from app.core.db import MIXIN_COLUMNS, Base, contract_violations, import_all_models
from tests.db_support import ScratchBase

MIXIN_EXEMPT: frozenset[str] = frozenset()


def test_every_table_carries_the_record_mixin() -> None:
    import_all_models()
    assert contract_violations(Base.metadata, exempt=MIXIN_EXEMPT) == []


def test_mixin_model_satisfies_the_contract() -> None:
    assert contract_violations(ScratchBase.metadata) == []


def test_contract_flags_a_table_without_the_mixin() -> None:
    md = MetaData()
    Table("rogue", md, Column("id", Integer, primary_key=True), Column("name", String))
    problems = contract_violations(md)
    assert "rogue: missing mixin column 'tenant_id'" in problems
    assert "rogue: missing mixin column 'custom_fields'" in problems
    assert contract_violations(md, exempt=frozenset({"rogue"})) == []


def test_contract_flags_nullable_tenant_and_non_pk_id() -> None:
    md = MetaData()
    Table(
        "loose",
        md,
        Column("pk", Integer, primary_key=True),
        *(Column(name, String, nullable=True) for name in MIXIN_COLUMNS),
    )
    problems = contract_violations(md)
    assert "loose: 'tenant_id' must be NOT NULL" in problems
    assert "loose: 'id' must be the primary key" in problems
    assert not any("project_id" in p or "deleted_at" in p for p in problems)
