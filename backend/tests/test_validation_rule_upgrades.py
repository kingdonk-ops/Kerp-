"""Default-upgrade offers, the tenant drift report and "Try it" (T-0036).

ADR 0004 §3, §4, §6 and Consequences (drift risk mitigation). Service level, against the
migrated schema on SQLite and, in CI, on PostgreSQL under RLS. Seeds, rules, tenants and
projects are synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, func, select

from app.core.audit.service import chain_entries, verify_chain
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.validation.compiler import RuleCompileError
from app.modules.validation.defaults import SeedRuleSet
from app.modules.validation.models import Finding, Rule, RuleSet
from app.modules.validation.schemas import RuleSpec
from app.modules.validation.service import (
    RuleLockedError,
    RuleNotFoundError,
    RuleStoreError,
    audit_chain,
    create_rule_set,
    delete_rule,
    list_rule_sets,
    list_rules,
    reset_rule_set,
    save_rule,
    seed_tenant,
    set_rule_enabled,
    sync_system_defaults,
    sync_system_layer,
)
from app.modules.validation.upgrades import (
    MAX_SAMPLE_CONTEXT_BYTES,
    MAX_SAMPLE_DEPTH,
    DriftPage,
    SampleContextError,
    accept_update,
    drift_report,
    drift_report_page,
    pending_updates,
    try_rule,
)
from tests.audit_support import migrated_database
from tests.tenancy_support import T1

QD = uuid.UUID("00000000-0000-4000-8000-00000000d013")  # PER-13 Quality Director
QA = uuid.UUID("00000000-0000-4000-8000-00000000d006")  # PER-06 QA/QC Lead
CTX = TenancyContext(tenant_id=T1, user_id=QD)
GATE = "demo.submit"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def rule(key: str, expression: str = "fields.ok == true", **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "rule_key": key,
        "engine": "cel",
        "expression": expression,
        "action": "block",
    }
    values.update(overrides)
    return values


def seed(version: int = 1, rules: list[dict[str, Any]] | None = None, **extra: Any) -> SeedRuleSet:
    if rules is None:
        rules = [
            rule("calibration", "fields.calibrated == true", locked=True),
            rule("photo", "fields.photos > 0", action="warn", severity="warning"),
        ]
    body = {"module": "projects", "key": GATE, "kind": "check", "version": version}
    return SeedRuleSet.model_validate({**body, "rules": rules, **extra})


def spec(key: str, expression: str = "fields.ok == true", **overrides: Any) -> RuleSpec:
    return RuleSpec.model_validate(rule(key, expression, **overrides))


def in_project(project_id: uuid.UUID) -> TenancyContext:
    return TenancyContext(tenant_id=T1, project_id=project_id, user_id=QA)


def new_project(db: Database, code: str = "SYN-1") -> uuid.UUID:
    with db.unit_of_work(CTX) as s:
        create = ProjectCreate.model_validate({"code": code, "name": f"Synthetic {code}"})
        return projects.create_project(s, create, actor_id=QD).id


def set_of(db: Database, ctx: TenancyContext, layer: str, key: str = GATE) -> uuid.UUID:
    with db.unit_of_work(ctx) as s:
        return next(r.id for r in list_rule_sets(s, layer) if r.key == key)


def rules_of(db: Database, ctx: TenancyContext, set_id: uuid.UUID) -> dict[str, Rule]:
    with db.unit_of_work(ctx) as s:
        rule_set = s.get(RuleSet, set_id)
        assert rule_set is not None
        found = list_rules(s, rule_set)
        s.expunge_all()
    return {r.rule_key: r for r in found}


def ledger(db: Database, ctx: TenancyContext, set_id: uuid.UUID) -> list[dict[str, Any]]:
    with db.unit_of_work(ctx) as s:
        rule_set = s.get(RuleSet, set_id)
        assert rule_set is not None
        chain = audit_chain(rule_set)
        assert verify_chain(s, chain).ok
        return [dict(e.payload) for e in chain_entries(s, chain)]


def changes(db: Database, ctx: TenancyContext, set_id: uuid.UUID) -> dict[str, str]:
    with db.unit_of_work(ctx) as s:
        return {u.rule_key: u.change for u in pending_updates(s, set_id).updates}


@pytest.fixture
def project(db: Database) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A seeded tenant and a project copy: (project id, project set id, tenant set id)."""
    with db.unit_of_work(CTX) as s:
        seed_tenant(s, [seed()])
    project_id = new_project(db)
    return project_id, set_of(db, in_project(project_id), "project"), set_of(db, CTX, "tenant")


# --- upgrade offers (ADR 0004 §4) ---------------------------------------------------------------


def test_no_updates_while_the_copy_matches_its_default(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, _ = project
    with db.unit_of_work(in_project(project_id)) as s:
        save_rule(s, set_id, spec("photo", "fields.photos > 5", action="warn"))  # own edit
        save_rule(s, set_id, spec("mine"))  # own rule
        offer = pending_updates(s, set_id)
    assert offer.updates == []
    assert offer.available == 0
    assert (offer.default_layer, offer.default_version) == ("tenant", 1)


def test_a_new_tenant_default_version_is_offered_per_rule_and_nothing_is_applied(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, tenant_set = project
    ctx = in_project(project_id)
    with db.unit_of_work(CTX) as s:  # the tenant default gains a new version
        save_rule(s, tenant_set, spec("photo", "fields.photos > 2", action="warn"))
        save_rule(s, tenant_set, spec("witness", "fields.witnessed == true"))
    before = rules_of(db, ctx, set_id)
    with db.unit_of_work(ctx) as s:
        offer = pending_updates(s, set_id)
        by_key = {u.rule_key: u for u in offer.updates}
    assert sorted(by_key) == ["photo", "witness"]
    assert by_key["witness"].change == "new"
    assert by_key["witness"].current is None
    assert by_key["witness"].default is not None
    assert by_key["photo"].change == "changed"
    assert by_key["photo"].fields == ["expression", "severity"]
    assert by_key["photo"].current is not None
    assert by_key["photo"].current.expression == "fields.photos > 0"
    assert by_key["photo"].default is not None
    assert by_key["photo"].default.expression == "fields.photos > 2"
    assert all(u.acceptable for u in offer.updates)
    assert offer.available == 2
    # Nothing was applied: the project copy is exactly as before.
    after = rules_of(db, ctx, set_id)
    assert sorted(after) == sorted(before) == ["calibration", "photo"]
    assert [(r.version, r.expression) for r in after.values()] == [
        (r.version, r.expression) for r in before.values()
    ]


def test_a_default_removed_upstream_is_offered_as_removed(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, tenant_set = project
    ctx = in_project(project_id)
    with db.unit_of_work(CTX) as s:
        delete_rule(s, tenant_set, "photo")
    assert changes(db, ctx, set_id) == {"photo": "removed"}
    with db.unit_of_work(ctx) as s:
        accepted = accept_update(s, set_id, "photo", actor_id=QA)
        assert accepted.deleted_at is not None
    assert changes(db, ctx, set_id) == {}
    entry = ledger(db, ctx, set_id)[-1]
    assert (entry["event"], entry["change"], entry["rule_key"]) == (
        "rule_update_accepted",
        "removed",
        "photo",
    )


def test_accepting_one_rule_applies_only_that_rule_versioned_and_audited(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, tenant_set = project
    ctx = in_project(project_id)
    with db.unit_of_work(CTX) as s:
        save_rule(s, tenant_set, spec("photo", "fields.photos > 2", action="warn"))
        save_rule(s, tenant_set, spec("witness", "fields.witnessed == true"))
    with db.unit_of_work(ctx) as s:
        set_version = s.get(RuleSet, set_id).version  # type: ignore[union-attr]
        photo = accept_update(s, set_id, "photo", actor_id=QA)
        assert (photo.expression, photo.severity, photo.version) == (
            "fields.photos > 2",
            "error",
            2,
        )
        assert (photo.origin_rule_key, photo.origin_version) == ("photo", 2)
        assert s.get(RuleSet, set_id).version == set_version + 1  # type: ignore[union-attr]
    # Only "photo" changed: "witness" is still on offer, not in the copy.
    assert changes(db, ctx, set_id) == {"witness": "new"}
    assert "witness" not in rules_of(db, ctx, set_id)
    with db.unit_of_work(ctx) as s:
        witness = accept_update(s, set_id, "witness", actor_id=QA)
        assert (witness.origin_rule_key, witness.origin_version, witness.version) == (
            "witness",
            1,
            1,
        )
    assert changes(db, ctx, set_id) == {}
    entries = ledger(db, ctx, set_id)
    assert [e["event"] for e in entries] == [
        "rule_set_copied",
        "rule_update_accepted",
        "rule_update_accepted",
    ]
    assert entries[1]["change"] == "changed"
    assert entries[1]["default_version"] == 2
    assert entries[1]["from_layer"] == "tenant"
    assert entries[1]["rule"]["expression"] == "fields.photos > 2"
    assert entries[2]["change"] == "new"


def test_a_rule_the_project_deleted_is_shown_and_never_re_added(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, tenant_set = project
    ctx = in_project(project_id)
    with db.unit_of_work(ctx) as s:
        delete_rule(s, set_id, "photo")
    assert changes(db, ctx, set_id) == {}  # the default did not change: nothing to show
    with db.unit_of_work(CTX) as s:
        save_rule(s, tenant_set, spec("photo", "fields.photos > 2", action="warn"))
    with db.unit_of_work(ctx) as s:
        offer = pending_updates(s, set_id)
        (update,) = offer.updates
        assert (update.rule_key, update.change, update.acceptable) == (
            "photo",
            "removed_by_project",
            False,
        )
        assert offer.available == 0
        with pytest.raises(RuleStoreError, match="deleted on this layer"):
            accept_update(s, set_id, "photo")
    assert rules_of(db, ctx, set_id)["photo"].deleted_at is not None


def test_a_project_rule_holding_a_new_defaults_key_is_shown_but_not_accepted(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, tenant_set = project
    ctx = in_project(project_id)
    with db.unit_of_work(ctx) as s:
        save_rule(s, set_id, spec("witness", "fields.signed == true"))
    with db.unit_of_work(CTX) as s:
        save_rule(s, tenant_set, spec("witness", "fields.witnessed == true"))
    assert changes(db, ctx, set_id) == {"witness": "key_in_use"}
    with db.unit_of_work(ctx) as s, pytest.raises(RuleStoreError, match="added on this layer"):
        accept_update(s, set_id, "witness")


def test_accepting_something_not_on_offer_is_refused(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, _ = project
    with db.unit_of_work(in_project(project_id)) as s:
        with pytest.raises(RuleNotFoundError, match="no update"):
            accept_update(s, set_id, "photo")
        with pytest.raises(RuleStoreError, match="human confirmation"):
            accept_update(s, set_id, "photo", source="ai")


def test_a_locked_copy_takes_the_new_locked_default(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, _ = project
    ctx = in_project(project_id)
    stricter = seed(
        2,
        [
            rule("calibration", "fields.calibrated == true && fields.cert != ''", locked=True),
            rule("photo", "fields.photos > 0", action="warn", severity="warning"),
        ],
    )
    with db.unit_of_work(CTX) as s:
        sync_system_layer(s, [stricter])
    assert changes(db, ctx, set_id) == {}  # the tenant has not taken it yet
    tenant_set = set_of(db, CTX, "tenant")
    assert changes(db, CTX, tenant_set) == {"calibration": "changed"}
    with db.unit_of_work(CTX) as s:
        assert accept_update(s, tenant_set, "calibration").locked
    assert changes(db, ctx, set_id) == {"calibration": "changed"}
    with db.unit_of_work(ctx) as s:
        accepted = accept_update(s, set_id, "calibration")
        assert accepted.locked
        assert "cert" in accepted.expression


def test_the_tenant_layer_cannot_skip_the_two_person_unlock_by_accepting(db: Database) -> None:
    with db.unit_of_work(CTX) as s:
        seed_tenant(s, [seed()])
    tenant_set = set_of(db, CTX, "tenant")
    # The tenant locked "photo" itself; the system default of "photo" is unlocked.
    with db.unit_of_work(CTX) as s:
        save_rule(s, tenant_set, spec("photo", "fields.photos > 0", action="warn", locked=True))
    loosened = seed(
        2,
        [
            rule("calibration", "fields.calibrated == true", locked=True),
            rule("photo", "fields.photos >= 0", action="warn", severity="warning"),
        ],
    )
    with db.unit_of_work(CTX) as s:
        sync_system_layer(s, [loosened])
    assert changes(db, CTX, tenant_set)["photo"] == "changed"
    with db.unit_of_work(CTX) as s, pytest.raises(RuleLockedError, match="two-person"):
        accept_update(s, tenant_set, "photo")
    with db.unit_of_work(CTX) as s:
        sync_system_layer(s, [seed(3, [rule("calibration", "fields.calibrated == true")])])
    # The system default dropped "photo"; deleting the tenant's locked rule needs an unlock too.
    assert changes(db, CTX, tenant_set)["photo"] == "removed"
    with db.unit_of_work(CTX) as s, pytest.raises(RuleLockedError, match="two-person"):
        accept_update(s, tenant_set, "photo")


def test_updates_need_a_default_set(db: Database) -> None:
    with db.unit_of_work(CTX) as s:
        seed_tenant(s, [seed()])
    project_id = new_project(db)
    system_set = set_of(db, CTX, "system")
    with db.unit_of_work(CTX) as s, pytest.raises(RuleStoreError, match="system layer"):
        pending_updates(s, system_set)
    with db.unit_of_work(in_project(project_id)) as s:
        own = create_rule_set(s, key="project.only", kind="check", layer="project")
        with pytest.raises(RuleStoreError, match="no tenant default set"):
            pending_updates(s, own.id)


def test_a_reset_set_has_no_updates_left(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, tenant_set = project
    ctx = in_project(project_id)
    with db.unit_of_work(CTX) as s:
        save_rule(s, tenant_set, spec("photo", "fields.photos > 2", action="warn"))
        save_rule(s, tenant_set, spec("witness", "fields.witnessed == true"))
    with db.unit_of_work(ctx) as s:
        reset_rule_set(s, set_id)
    assert changes(db, ctx, set_id) == {}


# --- new projects get the latest defaults (ADR 0004 §4) -----------------------------------------


def test_new_projects_get_the_latest_defaults_after_an_upgrade(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    old_project, old_set, tenant_set = project
    upgraded = seed(
        2,
        [
            rule("calibration", "fields.calibrated == true && fields.cert != ''", locked=True),
            rule("photo", "fields.photos > 0", action="warn", severity="warning"),
            rule("witness", "fields.witnessed == true"),
        ],
    )
    welding = seed(1, [rule("preheat", "fields.preheat == true")], key="demo.weld")
    sync_system_defaults(db, [T1], [upgraded, welding])
    with db.unit_of_work(CTX) as s:
        for key in ("calibration", "witness"):
            accept_update(s, tenant_set, key)
    later = new_project(db, "SYN-2")
    new_ctx = in_project(later)
    copied = rules_of(db, new_ctx, set_of(db, new_ctx, "project"))
    assert sorted(copied) == ["calibration", "photo", "witness"]
    assert "cert" in copied["calibration"].expression
    assert copied["calibration"].origin_version == 2
    # A set first shipped with the upgrade reaches the tenant layer and so new projects.
    assert sorted(r.key for r in _sets(db, new_ctx, "project")) == ["demo.submit", "demo.weld"]
    # The running project is untouched and is offered the change instead.
    assert sorted(rules_of(db, in_project(old_project), old_set)) == ["calibration", "photo"]
    assert changes(db, in_project(old_project), old_set) == {
        "calibration": "changed",
        "witness": "new",
    }


def _sets(db: Database, ctx: TenancyContext, layer: str) -> list[RuleSet]:
    with db.unit_of_work(ctx) as s:
        found = list_rule_sets(s, layer)
        s.expunge_all()
    return found


# --- tenant drift report (ADR 0004 Consequences) ------------------------------------------------


def test_the_drift_report_lists_project_rules_that_differ_from_the_tenant_defaults(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, tenant_set = project
    clean = new_project(db, "SYN-2")
    ctx = in_project(project_id)
    with db.unit_of_work(ctx) as s:
        set_rule_enabled(s, set_id, "photo", False)
        save_rule(s, set_id, spec("mine", "fields.mine == true"))
        own = create_rule_set(s, key="project.only", kind="check", layer="project")
        save_rule(s, own.id, spec("only"))
    with db.unit_of_work(CTX) as s:
        save_rule(s, tenant_set, spec("witness", "fields.witnessed == true"))
    with db.unit_of_work(TenancyContext(tenant_id=T1, user_id=QD)) as s:
        assert drift_report(s) == []  # no project is readable without a portfolio scope
    readable = TenancyContext(
        tenant_id=T1, user_id=QD, allowed_project_ids=frozenset({project_id, clean})
    )
    with db.unit_of_work(readable) as s:
        report = drift_report(s)
    found = [(str(e.project_id), e.rule_set_key, e.rule_key, e.drift, e.fields) for e in report]
    first, second = str(project_id), str(clean)
    expected = [
        (first, GATE, "mine", "added", []),
        (first, GATE, "photo", "modified", ["enabled"]),
        (first, GATE, "witness", "missing", []),
        (first, "project.only", "only", "added", []),
        (second, GATE, "witness", "missing", []),
    ]
    assert sorted(found) == sorted(expected)


def test_the_drift_report_shows_deleted_and_orphaned_rules(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, _ = project
    ctx = in_project(project_id)
    with db.unit_of_work(ctx) as s:
        delete_rule(s, set_id, "photo")
        save_rule(s, set_id, spec("gone_soon"))
        delete_rule(s, set_id, "gone_soon")  # an own rule deleted again is no drift
    readable = TenancyContext(tenant_id=T1, user_id=QD, allowed_project_ids=frozenset({project_id}))
    with db.unit_of_work(readable) as s:
        report = {e.rule_key: e for e in drift_report(s)}
    assert {k: e.drift for k, e in report.items()} == {"photo": "deleted"}
    assert (report["photo"].project_version, report["photo"].default_version) == (2, 1)


def test_the_drift_report_shows_a_default_the_tenant_removed(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, _, tenant_set = project
    with db.unit_of_work(CTX) as s:
        delete_rule(s, tenant_set, "photo")
    readable = TenancyContext(tenant_id=T1, user_id=QD, allowed_project_ids=frozenset({project_id}))
    with db.unit_of_work(readable) as s:
        assert [(e.rule_key, e.drift) for e in drift_report(s)] == [("photo", "default_removed")]


def test_the_drift_report_needs_a_tenant_level_session(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, _, _ = project
    with (
        db.unit_of_work(in_project(project_id)) as s,
        pytest.raises(RuleStoreError, match="tenant-level session"),
    ):
        drift_report(s)


def _drift_world(db: Database, count: int, prefix: str = "D") -> list[uuid.UUID]:
    """``count`` projects copied from the seeded tenant, each with one disabled rule."""
    project_ids = [new_project(db, f"SYN-{prefix}{n}") for n in range(count)]
    for project_id in project_ids:
        set_id = set_of(db, in_project(project_id), "project")
        with db.unit_of_work(in_project(project_id)) as s:
            set_rule_enabled(s, set_id, "photo", False)
    return sorted(project_ids, key=str)


def _drift_selects(db: Database, ctx: TenancyContext, **page: Any) -> tuple[int, DriftPage]:
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT") and "set_config" not in statement:
            statements.append(statement)

    with db.unit_of_work(ctx) as s:
        s.connection()  # the transaction (and its tenancy binding) is not counted
        event.listen(db.engine, "before_cursor_execute", record)
        try:
            found = drift_report_page(s, **page)
        finally:
            event.remove(db.engine, "before_cursor_execute", record)
    return len(statements), found


def test_the_drift_report_query_count_does_not_grow_with_the_projects(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    """Audit 2026-11-21 item 19 (T-0348, TD-289): a fixed number of queries per report."""
    del project  # seeds the tenant; its own project has no drift
    few = _drift_world(db, 2)
    many = few + _drift_world(db, 6, prefix="E")
    with db.unit_of_work(CTX) as s:
        own = create_rule_set(s, key="tenant.only", kind="check", layer="tenant")
        save_rule(s, own.id, spec("only"))
    small = TenancyContext(tenant_id=T1, user_id=QD, allowed_project_ids=frozenset(few))
    large = TenancyContext(tenant_id=T1, user_id=QD, allowed_project_ids=frozenset(many))
    small_count, small_page = _drift_selects(db, small)
    large_count, large_page = _drift_selects(db, large)
    assert [e.project_id for e in small_page.entries] == few
    assert [e.project_id for e in large_page.entries] == sorted(many, key=str)
    assert small_count == large_count <= 4


def test_the_drift_report_pages_by_whole_projects(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, _ = project
    with db.unit_of_work(in_project(project_id)) as s:
        save_rule(s, set_id, spec("mine"))  # two entries for this project
    project_ids = sorted([*_drift_world(db, 6), project_id], key=str)
    readable = TenancyContext(tenant_id=T1, user_id=QD, allowed_project_ids=frozenset(project_ids))
    with db.unit_of_work(readable) as s:
        whole = drift_report(s)
        pages: list[DriftPage] = []
        offset: int | None = 0
        while offset is not None:
            pages.append(drift_report_page(s, limit=3, offset=offset))
            offset = pages[-1].next_offset
        beyond = drift_report_page(s, limit=3, offset=99)
    assert [p.next_offset for p in pages] == [3, 6, None]
    assert [e for p in pages for e in p.entries] == whole
    assert [len({e.project_id for e in p.entries}) for p in pages] == [3, 3, 1]
    assert {e.project_id for e in whole} == set(project_ids)
    assert (beyond.entries, beyond.next_offset) == ([], None)
    with db.unit_of_work(readable) as s:  # an exact last page has no next page
        assert drift_report_page(s, limit=7).next_offset is None
        assert drift_report_page(s, limit=6).next_offset == 6


# --- "Try it" (ADR 0004 §6) ---------------------------------------------------------------------


def _counts(db: Database, ctx: TenancyContext, set_id: uuid.UUID) -> tuple[int, int, int]:
    with db.unit_of_work(ctx) as s:
        findings = s.scalar(select(func.count()).select_from(Finding)) or 0
        rule_set = s.get(RuleSet, set_id)
        assert rule_set is not None
        return findings, rule_set.version, len(ledger(db, ctx, set_id))


def test_try_a_stored_rule_returns_the_result_and_finding_without_persisting(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, _ = project
    ctx = in_project(project_id)
    with db.unit_of_work(ctx) as s:
        set_rule_enabled(s, set_id, "photo", False)  # tried before it is enabled again
    before = _counts(db, ctx, set_id)
    with db.unit_of_work(ctx) as s:
        failed = try_rule(s, set_id, "photo", {"fields": {"photos": 0.0}})
        passed = try_rule(s, set_id, "photo", {"fields": {"photos": 3.0}})
        broken = try_rule(s, set_id, "photo", {"fields": {}})
    assert (failed.outcome, failed.value, failed.rule_version) == ("fail", False, 2)
    assert failed.finding is not None
    assert (failed.finding.rule_key, failed.finding.action, failed.finding.outcome) == (
        "photo",
        "warn",
        "fail",
    )
    assert failed.finding.message == "photo: check failed"
    assert (passed.outcome, passed.value, passed.finding) == ("pass", True, None)
    assert broken.outcome == "error"
    assert broken.error is not None
    assert broken.error.code == "NO_SUCH_KEY"
    assert broken.finding is not None
    assert broken.finding.outcome == "error"
    assert _counts(db, ctx, set_id) == before  # no finding, no version, no ledger entry


def test_try_a_draft_compiles_it_first(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, _ = project
    ctx = in_project(project_id)
    before = _counts(db, ctx, set_id)
    draft = spec("draft", "fields.count >= params.min", params={"min": 2.0, "message": "Too few"})
    with db.unit_of_work(ctx) as s:
        result = try_rule(s, set_id, "draft", {"fields": {"count": 1.0}}, draft=draft)
        assert result.rule_version is None
        assert result.finding is not None
        assert result.finding.message == "Too few"
        with pytest.raises(RuleCompileError, match="undeclared reference"):
            try_rule(s, set_id, "draft", {}, draft=spec("draft", "event.kind == 'x'"))
        with pytest.raises(SampleContextError, match="reserved"):
            try_rule(s, set_id, "photo", {"params": {}})
        with pytest.raises(SampleContextError, match="KERP CEL JSON"):
            try_rule(s, set_id, "photo", {"fields": {"when": {"$timestamp": "yesterday"}}})
        with pytest.raises(RuleNotFoundError):
            try_rule(s, set_id, "ghost", {})
    assert "draft" not in rules_of(db, ctx, set_id)
    assert _counts(db, ctx, set_id) == before


def test_try_bounds_the_cel_sample_context(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, _ = project
    ctx = in_project(project_id)
    nested: dict[str, Any] = {"photos": 1.0}
    for _ in range(MAX_SAMPLE_DEPTH - 3):  # context > fields > deep: exactly at the limit
        nested = {"n": nested}
    at_limit = {"fields": {"photos": 1.0, "deep": nested}}
    too_deep = {"fields": {"photos": 1.0, "deep": {"n": nested}}}
    in_a_list = {"fields": {"photos": 1.0, "deep": [nested]}}
    filler = "x" * (MAX_SAMPLE_CONTEXT_BYTES - 64)
    fits = {"fields": {"photos": 1.0, "note": filler}}
    too_big = {"fields": {"photos": 1.0, "note": filler + "x" * 64}}
    with db.unit_of_work(ctx) as s:
        assert try_rule(s, set_id, "photo", at_limit).outcome == "pass"
        assert try_rule(s, set_id, "photo", fits).outcome == "pass"
        with pytest.raises(SampleContextError, match="nested deeper"):
            try_rule(s, set_id, "photo", too_deep)
        with pytest.raises(SampleContextError, match="nested deeper"):
            try_rule(s, set_id, "photo", in_a_list)
        with pytest.raises(SampleContextError, match="bytes as compact JSON"):
            try_rule(s, set_id, "photo", too_big)


def test_try_a_non_check_cel_rule_returns_its_value_without_a_finding(db: Database) -> None:
    digest = SeedRuleSet.model_validate(
        {
            "module": "projects",
            "key": "demo.activity",
            "kind": "digest",
            "version": 1,
            "rules": [rule("punch", "event.kind == 'punch'", action="include")],
        }
    )
    with db.unit_of_work(CTX) as s:
        seed_tenant(s, [digest])
    set_id = set_of(db, CTX, "tenant", "demo.activity")
    with db.unit_of_work(CTX) as s:
        result = try_rule(s, set_id, "punch", {"event": {"kind": "ncr"}})
    assert (result.kind, result.outcome, result.value, result.finding) == (
        "digest",
        "fail",
        False,
        None,
    )


def test_try_regex_rules_against_sample_text(db: Database) -> None:
    def regex_set(key: str, kind: str, pattern: str) -> SeedRuleSet:
        body = rule("pattern", pattern, engine="regex", action="link")
        return SeedRuleSet.model_validate(
            {"module": "projects", "key": key, "kind": kind, "version": 1, "rules": [body]}
        )

    with db.unit_of_work(CTX) as s:
        seed_tenant(
            s,
            [
                regex_set("demo.autolink", "autolink", r"NCR-(?P<number>\d+)"),
                regex_set("demo.grammar", "p6_grammar", r"(?P<anchor_code>[A-Z]+)-(?P<suffix>\d+)"),
            ],
        )
    autolink = set_of(db, CTX, "tenant", "demo.autolink")
    grammar = set_of(db, CTX, "tenant", "demo.grammar")
    with db.unit_of_work(CTX) as s:
        links = try_rule(s, autolink, "pattern", {"text": "See NCR-12 and NCR-7."})
        none = try_rule(s, autolink, "pattern", {"text": "nothing here"})
        full = try_rule(s, grammar, "pattern", {"text": "RSW-10"})
        partial = try_rule(s, grammar, "pattern", {"text": "RSW-10 extra"})
        with pytest.raises(SampleContextError, match="text"):
            try_rule(s, autolink, "pattern", {"fields": {}})
        with pytest.raises(SampleContextError, match="characters"):
            try_rule(s, autolink, "pattern", {"text": "x" * 20_001})
    assert links.outcome == "pass"
    assert [(m.text, m.start, m.end, m.groups) for m in links.matches] == [
        ("NCR-12", 4, 10, {"number": "12"}),
        ("NCR-7", 15, 20, {"number": "7"}),
    ]
    assert (none.outcome, none.matches) == ("fail", [])
    assert full.outcome == "pass"
    assert full.matches[0].groups == {"anchor_code": "RSW", "suffix": "10"}
    assert (partial.outcome, partial.matches) == ("fail", [])
    assert links.finding is None
    with db.unit_of_work(CTX) as s:
        many = try_rule(s, autolink, "pattern", {"text": "NCR-1 " * 150})
    assert len(many.matches) == 100  # capped


def test_a_default_changed_and_changed_back_offers_nothing(
    db: Database, project: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    project_id, set_id, tenant_set = project
    with db.unit_of_work(CTX) as s:
        save_rule(s, tenant_set, spec("photo", "fields.photos > 2", action="warn"))
        original = spec("photo", "fields.photos > 0", action="warn", severity="warning")
        save_rule(s, tenant_set, original)
    assert changes(db, in_project(project_id), set_id) == {}
