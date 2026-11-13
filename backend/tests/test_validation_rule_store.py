"""Single rule store, compile-on-save and ``run_checks`` (T-0018, ADR 0004 §1, §3, §6, Amd. E).

Runs against the migrated schema on SQLite and, in CI, on PostgreSQL under project RLS.
All rules and contexts are synthetic.
"""

import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit.service import chain_entries, verify_chain
from app.core.db import Base, Database, contract_violations
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import bind_tenancy, rls_tables
from app.core.tenancy.scope import OutOfScopeError
from app.modules.validation.compiler import MAX_PATTERN_LENGTH, RuleCompileError, compile_rule
from app.modules.validation.models import (
    FINDING_TABLE,
    RULE_KINDS,
    RULE_SET_TABLE,
    RULE_TABLE,
    Finding,
    Rule,
    RuleSet,
)
from app.modules.validation.schemas import RuleSpec
from app.modules.validation.service import (
    RuleNotFoundError,
    RuleSetNotFoundError,
    RuleStoreError,
    audit_chain,
    create_rule_set,
    delete_rule,
    resolve_rule_set,
    run_checks,
    save_rule,
)
from tests.audit_support import migrated_database
from tests.db_support import ON_POSTGRES
from tests.tenancy_support import CTX_A, CTX_B, CTX_C, CTX_T1

BACKEND = Path(__file__).resolve().parents[1]
USER = uuid.UUID("00000000-0000-4000-8000-00000000e001")
GRAMMAR = (
    r"^(?P<asset>.+?) / (?P<activity_type>[^/]+?) / (?P<anchor_code>\d+(\.\d+)?)"
    r"(?:-(?P<suffix>\d+))?(?: (?P<window>Online|Shut))?$"
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def cel_rule(key: str, expression: str, **overrides: object) -> RuleSpec:
    values: dict[str, object] = {
        "rule_key": key,
        "engine": "cel",
        "expression": expression,
        "action": "block",
    }
    values.update(overrides)
    return RuleSpec.model_validate(values)


def make_set(db: Database, ctx: TenancyContext, key: str = "demo.submit", **kw: str) -> uuid.UUID:
    layer = kw.pop("layer", "project" if ctx.project_id else "tenant")
    kind = kw.pop("kind", "check")
    with db.unit_of_work(ctx) as s:
        return create_rule_set(s, key=key, kind=kind, layer=layer, actor_id=USER).id


def add(db: Database, ctx: TenancyContext, set_id: uuid.UUID, *specs: RuleSpec) -> None:
    with db.unit_of_work(ctx) as s:
        for spec in specs:
            save_rule(s, set_id, spec, actor_id=USER)


def test_tables_meet_the_record_contract_and_are_under_rls() -> None:
    tables = {RULE_SET_TABLE, RULE_TABLE, FINDING_TABLE}
    assert tables <= set(Base.metadata.tables)
    assert [p for p in contract_violations(Base.metadata) if p.split(":")[0] in tables] == []
    assert tables <= set(rls_tables(Base.metadata))


def test_the_rule_store_does_not_import_re2_eagerly() -> None:
    code = (
        "import sys, app.modules.validation.service, app.modules.validation.compiler;"
        " assert 're2' not in sys.modules, 're2 imported eagerly'"
    )
    subprocess.run([sys.executable, "-c", code], cwd=BACKEND, check=True)


# --- kinds, engines, compile on save -------------------------------------------------------


def check_compiles(kind: str, spec: RuleSpec) -> None:
    compile_rule(kind, spec.engine, spec.expression, spec.params, spec.action)


@pytest.mark.parametrize("kind", RULE_KINDS)
@pytest.mark.parametrize("engine", ["cel", "regex"])
def test_regex_only_for_autolink_and_p6_grammar(kind: str, engine: str) -> None:
    expression = {"cel": "params.on == true", "regex": r"(?P<anchor_code>\d+)"}[engine]
    action = {"check": "block", "p6_eligibility": "placeholder"}.get(kind, "link")
    spec = cel_rule("r1", expression, engine=engine, action=action, params={"on": True})
    if engine == ("regex" if kind in ("autolink", "p6_grammar") else "cel"):
        check_compiles(kind, spec)
    else:
        with pytest.raises(RuleCompileError, match=f"must use the .* engine, not '{engine}'"):
            check_compiles(kind, spec)


@pytest.mark.parametrize(
    ("kind", "spec", "error"),
    [
        ("check", cel_rule("r", "fields.t >"), "CEL PARSE_ERROR"),
        ("check", cel_rule("r", "inspection.ok"), "CEL UNDECLARED_REFERENCE: .*'inspection'"),
        ("check", cel_rule("r", "true || 'a'"), "CEL NO_MATCHING_OVERLOAD"),
        ("check", cel_rule("r", "fields.ok", action="stop"), "action must be one of"),
        ("check", cel_rule("r", "fields.ok", params={"n": {"$int": "x"}}), "KERP CEL JSON"),
        ("digest", cel_rule("r", "fields.ok", action="include"), "'fields'"),
        ("p6_eligibility", cel_rule("r", "task_type == 'TT_Mile'", action="skip"), "action"),
        ("autolink", cel_rule("r", "(?=TK)\\d+", engine="regex", action="link"), "RE2: invalid"),
        ("autolink", cel_rule("r", "(a)\\1", engine="regex", action="link"), "RE2: invalid"),
        (
            "autolink",
            cel_rule("r", "a" * (MAX_PATTERN_LENGTH + 1), engine="regex", action="link"),
            "longer than 1024",
        ),
        ("p6_grammar", cel_rule("r", "(?P<code>\\d+)", engine="regex", action="parse"), "'code'"),
        ("p6_grammar", cel_rule("r", "(?P<asset>.+)", engine="regex", action="parse"), "needs"),
    ],
)
def test_an_invalid_rule_is_rejected_with_the_compile_error(
    kind: str, spec: RuleSpec, error: str
) -> None:
    with pytest.raises(RuleCompileError, match=error):
        check_compiles(kind, spec)


def test_save_compiles_first_and_stores_nothing_on_error(db: Database) -> None:
    set_id = make_set(db, CTX_A)
    with db.unit_of_work(CTX_A) as s, pytest.raises(RuleCompileError, match="PARSE_ERROR"):
        save_rule(s, set_id, cel_rule("r", "fields.t >"))
    with db.unit_of_work(CTX_A) as s:
        assert s.scalars(select(Rule)).all() == []


def test_valid_rules_of_every_kind_compile(db: Database) -> None:
    cases = [
        ("check", cel_rule("gap", "fields.t >= fields.dew + params.gap", params={"gap": 3})),
        ("digest", cel_rule("ncr", "event.type == 'ncr_raised'", action="include")),
        ("notify", cel_rule("hold", "event.type == 'hold'", action="notify")),
        ("autolink", cel_rule("tk", "TK-?\\d{4}", engine="regex", action="link")),
        ("p6_grammar", cel_rule("name", GRAMMAR, engine="regex", action="parse")),
        (
            "p6_eligibility",
            cel_rule("ms", "task_type == 'TT_Mile'", action="no_placeholder"),
        ),
    ]
    for kind, spec in cases:
        set_id = make_set(db, CTX_A, key=f"demo.{kind}", kind=kind)
        add(db, CTX_A, set_id, spec)


# --- run_checks ----------------------------------------------------------------------------


def test_run_checks_evaluates_enabled_rules_in_sort_order_and_logs_findings(db: Database) -> None:
    set_id = make_set(db, CTX_A)
    add(
        db,
        CTX_A,
        set_id,
        cel_rule("z_cal", "fields.cal_ok", sort_order=1, params={"message": "Calibration expired"}),
        cel_rule("a_temp", "fields.t >= params.min", sort_order=2, params={"min": 10}),
        cel_rule("b_off", "false", sort_order=0, enabled=False),
        cel_rule("c_note", "has(fields.note)", sort_order=3, action="warn", severity="warning"),
    )
    context = {"fields": {"cal_ok": False, "t": 5.0}}
    with db.unit_of_work(CTX_A) as s:
        result = run_checks(context, "demo.submit", session=s, subject="inspection:1")
    assert result.evaluated == 3
    assert [f.rule_key for f in result.findings] == ["z_cal", "a_temp", "c_note"]
    assert result.blocked
    assert not result.passed
    assert result.reason == "Calibration expired; a_temp: check failed"
    assert result.rule_set == "demo.submit"
    with db.unit_of_work(CTX_A) as s:
        rows = s.scalars(select(Finding).order_by(Finding.rule_key)).all()
        assert [(r.rule_key, r.rule_version, r.outcome, r.action) for r in rows] == [
            ("a_temp", 1, "fail", "block"),
            ("c_note", 1, "fail", "warn"),
            ("z_cal", 1, "fail", "block"),
        ]
        assert {(r.run_id, r.subject, r.rule_set_version) for r in rows} == {
            (result.run_id, "inspection:1", result.rule_set_version)
        }
        assert {r.project_id for r in rows} == {CTX_A.project_id}


def test_warnings_do_not_block_and_passing_rules_leave_no_finding(db: Database) -> None:
    set_id = make_set(db, CTX_A)
    add(db, CTX_A, set_id, cel_rule("ok", "fields.ok"), cel_rule("w", "false", action="warn"))
    with db.unit_of_work(CTX_A) as s:
        result = run_checks({"fields": {"ok": True}}, "demo.submit", session=s)
    assert result.passed
    assert result.reason is None
    assert [(f.rule_key, f.action) for f in result.findings] == [("w", "warn")]


@pytest.mark.parametrize(
    ("expression", "outcome"),
    [("fields.missing", "error"), ("fields.n", "fail"), ("'yes'", "fail"), ("null", "fail")],
)
def test_run_checks_fails_closed(db: Database, expression: str, outcome: str) -> None:
    set_id = make_set(db, CTX_A)
    add(db, CTX_A, set_id, cel_rule("r", expression))
    with db.unit_of_work(CTX_A) as s:
        result = run_checks({"fields": {"n": 1.0}}, "demo.submit", session=s)
    assert result.blocked
    assert [f.outcome for f in result.findings] == [outcome]


class TransitionBlockedError(Exception):
    pass


def test_findings_survive_when_the_caller_rolls_back_the_blocked_transition(
    db: Database,
) -> None:
    set_id = make_set(db, CTX_A)
    add(db, CTX_A, set_id, cel_rule("cal", "false", params={"message": "Calibration expired"}))
    run_ids = []

    def submit() -> None:  # the documented gate pattern: raise, so the caller rolls back
        with db.unit_of_work(CTX_A) as s:
            result = run_checks({}, "demo.submit", session=s, subject="inspection:9")
            run_ids.append(result.run_id)
            if result.blocked:
                raise TransitionBlockedError(result.reason)

    with pytest.raises(TransitionBlockedError, match="Calibration expired"):
        submit()
    with db.unit_of_work(CTX_A) as s:
        rows = s.scalars(select(Finding)).all()
    assert [(r.run_id, r.subject, r.rule_key, r.message) for r in rows] == [
        (run_ids[0], "inspection:9", "cal", "Calibration expired")
    ]


def test_findings_persist_when_the_callers_pool_is_exhausted(db: Database) -> None:
    # The caller holds the single connection of its request pool throughout run_checks. If the
    # findings log reused that pool it would block waiting for a second connection and time out
    # (TD review round 2); the dedicated side-write pool must let it through.
    set_id = make_set(db, CTX_A)
    add(db, CTX_A, set_id, cel_rule("cal", "false", params={"message": "Calibration expired"}))
    url = db.engine.url.render_as_string(hide_password=False)
    constrained = Database(url, pool_size=1, max_overflow=0, pool_timeout=3)
    try:
        with constrained.unit_of_work(CTX_A) as s:
            result = run_checks({}, "demo.submit", session=s, subject="inspection:11")
            assert result.blocked
            run_id = result.run_id
        with db.unit_of_work(CTX_A) as s:
            rows = s.scalars(select(Finding).where(Finding.subject == "inspection:11")).all()
        assert [(r.run_id, r.rule_key, r.message) for r in rows] == [
            (run_id, "cal", "Calibration expired")
        ]
    finally:
        constrained.dispose()


def test_a_new_rule_takes_effect_without_a_deploy(db: Database) -> None:
    set_id = make_set(db, CTX_A)
    with db.unit_of_work(CTX_A) as s:
        assert run_checks({"fields": {}}, "demo.submit", session=s).evaluated == 0
    add(db, CTX_A, set_id, cel_rule("new", "has(fields.sig)", params={"message": "Sign first"}))
    with db.unit_of_work(CTX_A) as s:
        assert run_checks({"fields": {}}, "demo.submit", session=s).reason == "Sign first"


def test_a_gated_transition_is_blocked_with_a_readable_reason(db: Database) -> None:
    set_id = make_set(db, CTX_A, key="demo.complete")
    add(
        db,
        CTX_A,
        set_id,
        cel_rule(
            "cal",
            "record.instrument.cal_due >= record.inspected_on",
            params={"message": "Torque wrench TW-12: calibration expired"},
        ),
    )

    def submit(session: Session, record: dict[str, object]) -> str:
        result = run_checks({"record": record}, "demo.complete", session=session)
        return "submitted" if result.passed else f"blocked: {result.reason}"

    expired = {"instrument": {"cal_due": "2026-01-01"}, "inspected_on": "2026-02-01"}
    valid = {"instrument": {"cal_due": "2026-03-01"}, "inspected_on": "2026-02-01"}
    with db.unit_of_work(CTX_A) as s:
        assert submit(s, expired) == "blocked: Torque wrench TW-12: calibration expired"
        assert submit(s, valid) == "submitted"


def test_run_checks_errors(db: Database) -> None:
    make_set(db, CTX_A, key="demo.digest", kind="digest")
    with db.unit_of_work(CTX_A) as s:
        with pytest.raises(RuleSetNotFoundError):
            run_checks({}, "demo.unknown", session=s)
        with pytest.raises(RuleSetNotFoundError):
            run_checks({}, "demo.digest", session=s)  # not a check set
        with pytest.raises(ValueError, match="reserved"):
            run_checks({"params": {}}, "demo.digest", session=s)
    with Session() as unbound, pytest.raises(RuntimeError, match="bound to a tenant"):
        run_checks({}, "demo.digest", session=unbound)


# --- versions and audit --------------------------------------------------------------------


def test_every_rule_change_is_versioned_and_audited(db: Database) -> None:
    set_id = make_set(db, CTX_A)
    spec = cel_rule("r", "fields.t >= params.min", params={"min": 2.5})
    add(db, CTX_A, set_id, spec)
    add(db, CTX_A, set_id, spec)  # identical: no new version, no audit entry
    add(db, CTX_A, set_id, spec.model_copy(update={"severity": "warning"}))
    with db.unit_of_work(CTX_A) as s:
        rule = delete_rule(s, set_id, "r", actor_id=USER)
        assert (rule.version, rule.is_deleted) == (3, True)
    with db.unit_of_work(CTX_A) as s:
        rule_set = resolve_rule_set(s, "demo.submit", "check")
        assert rule_set.version == 4
        entries = chain_entries(s, audit_chain(rule_set))
        assert verify_chain(s, audit_chain(rule_set)).ok
    assert [e.payload["event"] for e in entries] == [
        "rule_set_created",
        "rule_saved",
        "rule_saved",
        "rule_deleted",
    ]
    assert [e.payload.get("rule_version") for e in entries] == [None, 1, 2, 3]
    assert entries[2].payload["rule"]["severity"] == "warning"
    assert entries[2].payload["rule"]["params_json"] == '{"min":2.5}'
    assert {e.created_by for e in entries} == {USER}


def test_deleted_rules_keep_their_key_and_are_not_run(db: Database) -> None:
    set_id = make_set(db, CTX_A)
    add(db, CTX_A, set_id, cel_rule("r", "false"))
    with db.unit_of_work(CTX_A) as s:
        delete_rule(s, set_id, "r")
    with db.unit_of_work(CTX_A) as s:
        assert run_checks({}, "demo.submit", session=s).evaluated == 0
        with pytest.raises(RuleStoreError, match="was deleted"):
            save_rule(s, set_id, cel_rule("r", "true"))
        with pytest.raises(RuleNotFoundError):
            delete_rule(s, set_id, "r")


# --- layers and scope ----------------------------------------------------------------------


def test_the_most_specific_layer_wins_and_projects_are_isolated(db: Database) -> None:
    system_id = make_set(db, CTX_T1, layer="system")
    tenant_id = make_set(db, CTX_T1, layer="tenant")
    project_id = make_set(db, CTX_A)
    add(db, CTX_T1, system_id, cel_rule("sys", "false", params={"message": "system"}))
    add(db, CTX_T1, tenant_id, cel_rule("ten", "false", params={"message": "tenant"}))
    add(db, CTX_A, project_id, cel_rule("prj", "false", params={"message": "project"}))
    reasons = {}
    for name, ctx in {"a": CTX_A, "b": CTX_B, "t1": CTX_T1}.items():
        with db.unit_of_work(ctx) as s:
            reasons[name] = run_checks({}, "demo.submit", session=s).reason
    assert reasons == {"a": "project", "b": "tenant", "t1": "tenant"}
    with db.unit_of_work(CTX_C) as s, pytest.raises(RuleSetNotFoundError):
        run_checks({}, "demo.submit", session=s)  # another tenant sees nothing


def test_a_project_cannot_change_tenant_or_other_project_rules(db: Database) -> None:
    tenant_id = make_set(db, CTX_T1)
    project_id = make_set(db, CTX_A)
    with pytest.raises(OutOfScopeError), db.unit_of_work(CTX_A) as s:
        save_rule(s, tenant_id, cel_rule("r", "true"))
    with pytest.raises(OutOfScopeError), db.unit_of_work(CTX_B) as s:
        save_rule(s, project_id, cel_rule("r", "true"))


def test_create_rule_set_errors(db: Database) -> None:
    make_set(db, CTX_A)
    with db.unit_of_work(CTX_A) as s:
        for kwargs, error in [
            ({"kind": "verify", "layer": "project"}, "kind"),
            ({"kind": "check", "layer": "global"}, "layer"),
            ({"kind": "check", "layer": "tenant"}, "tenant session"),
            ({"kind": "check", "layer": "project", "key": "Bad Key"}, "key"),
            ({"kind": "check", "layer": "project"}, "already exists"),
        ]:
            key = str(kwargs.pop("key", "demo.submit"))
            with pytest.raises(RuleStoreError, match=error):
                create_rule_set(s, key=key, **kwargs)
    with db.unit_of_work(CTX_T1) as s, pytest.raises(RuleStoreError, match="project session"):
        create_rule_set(s, key="demo.x", kind="check", layer="project")


def test_ai_initiated_rule_changes_are_refused(db: Database) -> None:
    set_id = make_set(db, CTX_A)
    with db.unit_of_work(CTX_A) as s:
        with pytest.raises(RuleStoreError, match="human confirmation"):
            save_rule(s, set_id, cel_rule("r", "true"), source="ai")
        with pytest.raises(RuleStoreError, match="human confirmation"):
            create_rule_set(s, key="demo.ai", kind="check", layer="project", source="ai")


def test_the_service_needs_a_bound_session(db: Database) -> None:
    with Session(db.engine) as s:
        bind_tenancy(s, None)
        with pytest.raises(RuntimeError, match="bound to a tenant"):
            create_rule_set(s, key="demo.x", kind="check", layer="tenant")


@pytest.mark.skipif(
    not ON_POSTGRES,
    reason="needs PostgreSQL NULLS NOT DISTINCT; runs in CI's PostgreSQL step (TD-052)",
)
def test_tenant_level_sets_are_unique_in_the_database(db: Database) -> None:
    # Bypasses the service's duplicate check: only the constraint stops the second row.
    def tenant_set() -> RuleSet:
        return RuleSet(
            tenant_id=CTX_T1.tenant_id, key="demo.dup", kind="check", layer="tenant", version=1
        )

    with db.unit_of_work(CTX_T1) as s:
        s.add(tenant_set())
    with pytest.raises(IntegrityError), db.unit_of_work(CTX_T1) as s:
        s.add(tenant_set())
