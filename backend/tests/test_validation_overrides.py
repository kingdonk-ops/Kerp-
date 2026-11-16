"""Per-record override of a blocked check (T-0035, ADR 0004 Amendment A).

Real tokens and memberships from the store, on SQLite and, in CI, PostgreSQL under RLS.
All users, projects, records and rules are synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.core.audit.service import chain_entries, verify_chain
from app.core.auth.access import Persona
from app.core.auth.models import ProjectMembership
from app.core.db import Base, Database, contract_violations, utcnow
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import rls_tables
from app.modules.notifications import service as notifications
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.validation import overrides
from app.modules.validation.compiler import RuleCompileError, compile_rule
from app.modules.validation.defaults import SeedRuleSet
from app.modules.validation.models import OVERRIDE_DECISIONS, OVERRIDE_TABLE, Finding, Override
from app.modules.validation.schemas import CheckResult, RuleSpec
from app.modules.validation.service import list_rule_sets, run_checks, save_rule, seed_tenant
from tests.audit_support import migrated_database
from tests.auth_support import PASSWORD, T1, T2, auth_app, make_settings, seed_user
from tests.db_support import database_url

API = "/api/v1/validation"
CTX = TenancyContext(tenant_id=T1)
P = Persona
USERS = {
    "qa": P.QAQC_LEAD,
    "qa2": P.QAQC_LEAD,
    "pm": P.PROJECT_MANAGER,
    "fw": P.FIELD_WORKER,
    "viewer": P.CLIENT_REPRESENTATIVE,  # portal tier: view only
}
SET = "demo.submit"
CAL_MESSAGE = "Torque wrench TW-12: calibration expired"
BINDING = ["fields.equipment_id", "fields.spec_revision", "fields.inspection_date"]
# Synthetic inspection values: the calibration ran out before the inspection date.
EXPIRED: dict[str, Any] = {
    "equipment_id": "TW-12",
    "spec_revision": "B",
    "inspection_date": "2026-02-01",
    "cal_due": "2026-01-01",
    "note": "first",
}

# The spec's table (quality-and-inspection.md §Per-record override): the single source.
SPEC_COLUMNS = {
    "record_type",
    "record_id",
    "rule_set_key",
    "rule_key",
    "rule_version",
    "finding_id",
    "requested_by",
    "requested_at",
    "reason",
    "approver_persona",
    "approver_user_id",
    "decision",
    "decided_by",
    "decided_at",
    "decision_comment",
    "checked_values_hash",
    "audit_chain_key",
}
MIXIN = {
    "id",
    "tenant_id",
    "project_id",
    "created_at",
    "updated_at",
    "deleted_at",
    "custom_fields",
    "created_by",
    "updated_by",
    "source",
    "revision",
}


def _seeds() -> list[SeedRuleSet]:
    rules = [
        {
            "rule_key": "calibration",
            "engine": "cel",
            "expression": "fields.cal_due >= fields.inspection_date",
            "action": "block",
            "params": {"message": CAL_MESSAGE, "override_binding": BINDING},
        },
        {
            # No binding: the whole record and fields are bound.
            "rule_key": "signed",
            "engine": "cel",
            "expression": "has(fields.signature)",
            "action": "block",
            "sort_order": 1,
        },
        {
            "rule_key": "photo",
            "engine": "cel",
            "expression": "has(fields.photo)",
            "action": "warn",
            "sort_order": 2,
        },
    ]
    body = {"module": "projects", "key": SET, "kind": "check", "version": 1, "rules": rules}
    return [SeedRuleSet.model_validate(body)]


class Api:
    def __init__(self, client: TestClient, db: Database) -> None:
        self.client, self.db = client, db
        with db.unit_of_work(CTX) as s:
            seed_tenant(s, _seeds())
        with db.unit_of_work(CTX) as s:
            create = ProjectCreate.model_validate({"code": "SYN-OV", "name": "Synthetic"})
            self.project = projects.create_project(s, create).id
            other = ProjectCreate.model_validate({"code": "SYN-OT", "name": "Synthetic other"})
            self.other = projects.create_project(s, other).id
        self.ids = {name: seed_user(db, T1, f"{name}@example.test") for name in USERS}
        self.ids["outsider"] = seed_user(db, T1, "outsider@example.test")
        with db.unit_of_work(CTX) as s:
            for name, persona in USERS.items():
                self._member(s, name, self.project, persona)
            self._member(s, "outsider", self.other, P.QAQC_LEAD)
        self._tokens: dict[str, dict[str, str]] = {}

    def _member(self, s: Any, name: str, project: uuid.UUID, persona: Persona) -> None:
        s.add(
            ProjectMembership(
                tenant_id=T1,
                user_id=self.ids[name],
                member_project_id=project,
                persona=persona.value,
            )
        )

    @property
    def ctx(self) -> TenancyContext:
        return TenancyContext(tenant_id=T1, project_id=self.project)

    def as_(self, name: str) -> dict[str, str]:
        if name not in self._tokens:
            body = {"email": f"{name}@example.test", "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            self._tokens[name] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._tokens[name]

    def check(
        self, record: uuid.UUID, fields: dict[str, Any], *, subject: str | None = None
    ) -> CheckResult:
        context: dict[str, Any] = {"record": {"id": str(record)}, "fields": fields}
        with self.db.unit_of_work(self.ctx) as s:
            return run_checks(context, SET, session=s, subject=subject or f"inspection:{record}")

    def finding(self, result: CheckResult, rule_key: str = "calibration") -> str:
        ids = [f.finding_id for f in result.findings if f.rule_key == rule_key]
        assert len(ids) == 1, result
        assert ids[0] is not None
        return str(ids[0])

    def request(
        self, who: str, finding_id: str, reason: str = "Recalibrated on site", **kw: Any
    ) -> httpx.Response:
        body = {"finding_id": finding_id, "reason": reason, **kw}
        return self.client.post(
            f"{API}/projects/{self.project}/overrides", json=body, headers=self.as_(who)
        )

    def decide(
        self, who: str, override_id: str, action: str, comment: str | None = None
    ) -> httpx.Response:
        body = {} if comment is None else {"comment": comment}
        return self.client.post(
            f"{API}/projects/{self.project}/overrides/{override_id}/{action}",
            json=body,
            headers=self.as_(who),
        )

    def notes(self, name: str) -> list[tuple[str, str | None, uuid.UUID | None]]:
        with self.db.unit_of_work(self.ctx) as s:
            rows = notifications.list_notifications(s, self.ids[name])
            return [(n.kind, n.subject_type, n.subject_id) for n in rows]

    def override(self, override_id: str) -> Override:
        with self.db.unit_of_work(self.ctx) as s:
            row = s.get(Override, uuid.UUID(override_id))
            assert row is not None
            s.expunge(row)
            return row


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        yield Api(client, db)


def blocked(api: Api, record: uuid.UUID, **changes: Any) -> CheckResult:
    result = api.check(record, {**EXPIRED, "signature": "x", **changes})
    assert result.blocked, result
    return result


# --- shape (quality-and-inspection.md §Per-record override) --------------------------------


def test_the_table_has_exactly_the_spec_columns_and_meets_the_contract() -> None:
    table = Base.metadata.tables[OVERRIDE_TABLE]
    assert set(table.columns.keys()) == SPEC_COLUMNS | MIXIN
    assert (
        contract_violations(
            Base.metadata, exempt=frozenset(Base.metadata.tables) - {OVERRIDE_TABLE}
        )
        == []
    )
    assert OVERRIDE_TABLE in rls_tables(Base.metadata)
    assert OVERRIDE_DECISIONS == ("pending", "approved", "rejected", "void")
    assert table.columns["project_id"].nullable is False


def test_the_migrated_table_matches(db: Database) -> None:
    columns = {c["name"] for c in inspect(db.engine).get_columns(OVERRIDE_TABLE)}
    assert columns == SPEC_COLUMNS | MIXIN
    finding_columns = {c["name"] for c in inspect(db.engine).get_columns("oe_validation_finding")}
    assert "checked_values_hash" in finding_columns


# --- the request (Amendment A.1-A.2) -------------------------------------------------------


def test_a_request_needs_the_token_and_a_member_with_create(api: Api) -> None:
    record = uuid.uuid4()
    finding = api.finding(blocked(api, record))
    url = f"{API}/projects/{api.project}/overrides"
    assert api.client.post(url, json={"finding_id": finding, "reason": "x"}).status_code == 401
    assert api.request("viewer", finding).status_code == 403
    assert api.request("outsider", finding).status_code == 404


@pytest.mark.parametrize("reason", ["", "   ", "\t\n"])
def test_an_empty_or_blank_reason_is_422(api: Api, reason: str) -> None:
    finding = api.finding(blocked(api, uuid.uuid4()))
    response = api.request("fw", finding, reason=reason)
    assert response.status_code == 422, response.text


def test_the_service_refuses_a_blank_reason_too(api: Api) -> None:
    finding = api.finding(blocked(api, uuid.uuid4()))
    ctx = TenancyContext(tenant_id=T1, project_id=api.project, user_id=api.ids["fw"])
    with api.db.unit_of_work(ctx) as s, pytest.raises(overrides.OverrideInputError):
        overrides.request_override(s, uuid.UUID(finding), "  \n ")
    with api.db.unit_of_work(ctx) as s, pytest.raises(overrides.OverrideError, match="AI"):
        overrides.request_override(s, uuid.UUID(finding), "ok", source="ai")


def test_a_request_needs_a_current_blocking_finding_of_the_record(api: Api) -> None:
    record = uuid.uuid4()
    result = api.check(record, EXPIRED | {"signature": "x"})
    warn = api.finding(result, "photo")
    assert api.request("fw", warn).status_code == 409  # a warning never blocks
    assert api.request("fw", str(uuid.uuid4())).status_code == 404
    # A finding of a run without a record subject cannot be overridden.
    loose = api.check(record, EXPIRED | {"signature": "x"}, subject="free text")
    assert api.request("fw", api.finding(loose)).status_code == 409
    # A finding superseded by a later run of the record is not current.
    first = api.finding(blocked(api, record))
    blocked(api, record)
    assert api.request("fw", first).status_code == 409
    current = api.finding(blocked(api, record))
    assert api.request("fw", current).status_code == 201
    # One pending (or approved) override per record and rule.
    again = api.finding(blocked(api, record))
    assert api.request("fw", again).status_code == 409


def test_another_projects_finding_is_404(api: Api) -> None:
    other = TenancyContext(tenant_id=T1, project_id=api.other)
    with api.db.unit_of_work(other) as s:
        result = run_checks(
            {"fields": EXPIRED | {"signature": "x"}}, SET, session=s, subject=f"x:{uuid.uuid4()}"
        )
    assert api.request("fw", api.finding(result)).status_code == 404


def test_a_request_records_the_finding_rule_and_hash(api: Api) -> None:
    record = uuid.uuid4()
    result = blocked(api, record)
    finding_id = api.finding(result)
    response = api.request("fw", finding_id, reason="  Recalibrated on site  ")
    assert response.status_code == 201, response.text
    body = response.json()
    with api.db.unit_of_work(api.ctx) as s:
        finding = s.get(Finding, uuid.UUID(finding_id))
        assert finding is not None
        values_hash = finding.checked_values_hash
    assert body["record_type"] == "inspection"
    assert body["record_id"] == str(record)
    assert (body["rule_set_key"], body["rule_key"], body["rule_version"]) == (
        SET,
        "calibration",
        1,
    )
    assert body["reason"] == "Recalibrated on site"
    assert body["decision"] == "pending"
    assert body["checked_values_hash"] == values_hash
    assert len(values_hash or "") == 64
    assert body["audit_chain_key"] == f"inspection:{record}"
    assert body["requested_by"] == str(api.ids["fw"])


# --- routing (Amendment A.4) ----------------------------------------------------------------


def test_default_routing_goes_to_qaqc_and_the_requester_cannot_approve(api: Api) -> None:
    record = uuid.uuid4()
    override = api.request("fw", api.finding(blocked(api, record))).json()
    assert override["approver_persona"] == "PER-06"
    assert override["approver_user_id"] is None  # two QA/QC Leads: either may decide
    assert api.decide("fw", override["id"], "approve").status_code == 403
    assert api.decide("pm", override["id"], "approve").status_code == 403  # not PER-06
    assert api.decide("viewer", override["id"], "approve").status_code == 403
    response = api.decide("qa2", override["id"], "approve", "Certificate checked")
    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "approved"
    assert response.json()["decided_by"] == str(api.ids["qa2"])
    assert response.json()["decision_comment"] == "Certificate checked"
    assert api.decide("qa", override["id"], "reject").status_code == 409  # already decided


def test_a_qaqc_requester_is_routed_to_the_project_manager(api: Api) -> None:
    override = api.request("qa", api.finding(blocked(api, uuid.uuid4()))).json()
    assert override["approver_persona"] == "PER-02"
    assert override["approver_user_id"] == str(api.ids["pm"])
    assert api.decide("qa", override["id"], "approve").status_code == 403  # own request
    assert api.decide("qa2", override["id"], "approve").status_code == 403  # not PER-02
    assert api.decide("pm", override["id"], "approve").status_code == 200


def test_a_project_manager_requester_is_routed_to_qaqc(api: Api) -> None:
    override = api.request("pm", api.finding(blocked(api, uuid.uuid4()))).json()
    assert override["approver_persona"] == "PER-06"
    assert api.decide("pm", override["id"], "approve").status_code == 403  # own request
    assert api.decide("qa", override["id"], "reject", "Use another wrench").status_code == 200
    assert api.override(override["id"]).decision == "rejected"


def test_a_named_approver_must_hold_the_persona_and_not_be_the_requester(api: Api) -> None:
    finding = api.finding(blocked(api, uuid.uuid4()))
    assert api.request("fw", finding, approver_user_id=str(api.ids["fw"])).status_code == 403
    assert api.request("fw", finding, approver_user_id=str(api.ids["pm"])).status_code == 422
    override = api.request("fw", finding, approver_user_id=str(api.ids["qa"])).json()
    assert override["approver_user_id"] == str(api.ids["qa"])
    assert api.decide("qa2", override["id"], "approve").status_code == 403  # routed to qa
    assert api.decide("qa", override["id"], "approve").status_code == 200


def test_without_another_holder_of_the_persona_the_request_is_409(api: Api) -> None:
    admin = TenancyContext(tenant_id=T1, is_system_admin=True)
    with api.db.unit_of_work(admin) as s:
        rows = s.scalars(
            select(ProjectMembership).where(ProjectMembership.persona == P.PROJECT_MANAGER.value)
        ).all()
        assert rows
        for row in rows:
            row.soft_delete()
    response = api.request("qa", api.finding(blocked(api, uuid.uuid4())))
    assert response.status_code == 409
    assert "PER-02" in response.json()["detail"]


def test_the_database_refuses_a_self_decision(api: Api) -> None:
    override = api.request("fw", api.finding(blocked(api, uuid.uuid4()))).json()

    def self_approve() -> None:
        with api.db.unit_of_work(api.ctx) as s:
            row = s.get(Override, uuid.UUID(override["id"]))
            assert row is not None
            row.decision, row.decided_by, row.decided_at = "approved", row.requested_by, utcnow()

    with pytest.raises(IntegrityError):
        self_approve()


# --- notifications (Amendment A.3) and audit (A.5) -------------------------------------------


def test_the_approver_is_notified_and_the_requester_learns_the_decision(api: Api) -> None:
    first = api.request("fw", api.finding(blocked(api, uuid.uuid4()))).json()
    subject = ("validation.override", uuid.UUID(first["id"]))
    requested = ("validation.override_requested", *subject)
    assert api.notes("qa") == [requested]
    assert api.notes("qa2") == [requested]
    assert api.notes("pm") == []
    assert api.notes("fw") == []
    api.decide("qa", first["id"], "approve")
    assert api.notes("fw") == [("validation.override_approved", *subject)]

    second = api.request("qa", api.finding(blocked(api, uuid.uuid4()))).json()
    other = ("validation.override", uuid.UUID(second["id"]))
    assert api.notes("pm") == [("validation.override_requested", *other)]
    api.decide("pm", second["id"], "reject")
    assert ("validation.override_rejected", *other) in api.notes("qa")


def test_request_and_decision_go_to_the_records_audit_chain(api: Api) -> None:
    record = uuid.uuid4()
    override = api.request("fw", api.finding(blocked(api, record)), reason="Spare wrench").json()
    api.decide("qa", override["id"], "approve", "OK")
    chain = f"inspection:{record}"
    with api.db.unit_of_work(api.ctx) as s:
        entries = [e.payload for e in chain_entries(s, chain)]
        assert verify_chain(s, chain).ok
    assert [e["event"] for e in entries] == [
        "validation_override_requested",
        "validation_override_approved",
    ]
    requested, decided = entries
    assert requested["reason"] == "Spare wrench"
    assert requested["approver_persona"] == "PER-06"
    assert requested["requested_by"] == str(api.ids["fw"])
    assert requested["requested_at"]
    assert requested["checked_values_hash"]
    assert decided["decided_by"] == str(api.ids["qa"])
    assert decided["decided_at"]
    assert decided["decision_comment"] == "OK"


# --- bound to the checked values (Amendment A.6) --------------------------------------------


def _approved(api: Api, record: uuid.UUID) -> str:
    override = api.request("fw", api.finding(blocked(api, record))).json()
    assert api.decide("qa", override["id"], "approve").status_code == 200
    return str(override["id"])


def test_an_approved_override_lets_the_record_pass_that_rule_only(api: Api) -> None:
    record = uuid.uuid4()
    override_id = _approved(api, record)
    result = api.check(record, EXPIRED | {"signature": "x"})
    assert result.passed
    assert [(o.override_id, o.rule_key) for o in result.overrides] == [
        (uuid.UUID(override_id), "calibration")
    ]
    assert [f.rule_key for f in result.findings] == ["photo"]  # the warning still shows
    # A value the rule does not check may change.
    assert api.check(record, EXPIRED | {"signature": "x", "note": "second"}).passed
    # Another rule still blocks on its own.
    unsigned = api.check(record, EXPIRED | {"note": "second"})
    assert unsigned.blocked
    assert next(f.rule_key for f in unsigned.findings) == "signed"
    # Another record is not covered.
    assert api.check(uuid.uuid4(), EXPIRED | {"signature": "x"}).blocked
    assert api.override(override_id).decision == "approved"


@pytest.mark.parametrize(
    "change",
    [{"equipment_id": "TW-13"}, {"spec_revision": "C"}, {"inspection_date": "2026-02-02"}],
)
def test_changing_a_checked_value_voids_the_override_and_the_block_returns(
    api: Api, change: dict[str, str]
) -> None:
    record = uuid.uuid4()
    override_id = _approved(api, record)
    changed = api.check(record, EXPIRED | {"signature": "x"} | change)
    assert changed.blocked
    assert changed.reason == CAL_MESSAGE
    assert changed.overrides == ()
    row = api.override(override_id)
    assert row.decision == "void"
    assert row.decision_comment == "checked_values_changed"
    assert row.decided_by is None
    assert row.decided_at is not None
    # Void is final: changing the values back does not revive it.
    assert api.check(record, EXPIRED | {"signature": "x"}).blocked
    with api.db.unit_of_work(api.ctx) as s:
        events = [e.payload["event"] for e in chain_entries(s, f"inspection:{record}")]
        assert verify_chain(s, f"inspection:{record}").ok
    assert events[-1] == "validation_override_voided"
    # A new request is possible on the new finding.
    fresh = api.finding(blocked(api, record))
    assert api.request("fw", fresh).status_code == 201


def test_without_a_binding_any_change_to_record_or_fields_voids(api: Api) -> None:
    record = uuid.uuid4()
    unsigned = api.check(record, dict(EXPIRED))
    response = api.request("fw", api.finding(unsigned, "signed"))
    assert response.status_code == 201
    override_id = response.json()["id"]
    assert api.decide("qa", override_id, "approve").status_code == 200
    assert api.check(record, dict(EXPIRED)).overrides[0].rule_key == "signed"
    assert api.check(record, EXPIRED | {"note": "edited"}).blocked
    assert api.override(override_id).decision == "void"


def test_a_pending_request_is_voided_when_the_values_change(api: Api) -> None:
    record = uuid.uuid4()
    override = api.request("fw", api.finding(blocked(api, record))).json()
    api.check(record, EXPIRED | {"signature": "x", "equipment_id": "TW-99"})
    assert api.override(override["id"]).decision == "void"
    assert api.decide("qa", override["id"], "approve").status_code == 409


def test_a_changed_rule_voids_the_override(api: Api) -> None:
    record = uuid.uuid4()
    override_id = _approved(api, record)
    with api.db.unit_of_work(api.ctx) as s:
        set_id = next(r.id for r in list_rule_sets(s, "project") if r.key == SET)
        spec = RuleSpec.model_validate(
            {
                "rule_key": "calibration",
                "engine": "cel",
                "expression": "fields.cal_due > fields.inspection_date",
                "action": "block",
                "params": {"message": CAL_MESSAGE, "override_binding": BINDING},
            }
        )
        save_rule(s, set_id, spec, actor_id=api.ids["qa"])
    assert api.check(record, EXPIRED | {"signature": "x"}).blocked
    row = api.override(override_id)
    assert (row.decision, row.decision_comment) == ("void", "rule_changed")


def test_a_rejected_override_never_lets_the_record_pass(api: Api) -> None:
    record = uuid.uuid4()
    override = api.request("fw", api.finding(blocked(api, record))).json()
    api.decide("qa", override["id"], "reject")
    assert api.check(record, EXPIRED | {"signature": "x"}).blocked
    assert api.override(override["id"]).decision == "rejected"


# --- reading --------------------------------------------------------------------------------


def test_members_list_and_read_overrides_of_their_project_only(api: Api) -> None:
    record = uuid.uuid4()
    override = api.request("fw", api.finding(blocked(api, record))).json()
    base = f"{API}/projects/{api.project}/overrides"
    listed = api.client.get(base, headers=api.as_("viewer"))
    assert [o["id"] for o in listed.json()] == [override["id"]]
    filtered = api.client.get(
        base, params={"decision": "approved", "record_id": str(record)}, headers=api.as_("qa")
    )
    assert filtered.json() == []
    one = api.client.get(f"{base}/{override['id']}", headers=api.as_("pm"))
    assert one.status_code == 200
    assert one.json()["reason"] == "Recalibrated on site"
    assert api.client.get(base, headers=api.as_("outsider")).status_code == 404
    other = f"{API}/projects/{api.other}/overrides/{override['id']}"
    assert api.client.get(other, headers=api.as_("outsider")).status_code == 404


def test_other_projects_and_tenants_see_no_overrides(api: Api) -> None:
    # Project RLS on PostgreSQL, the ORM scope on SQLite.
    api.request("fw", api.finding(blocked(api, uuid.uuid4())))
    with api.db.unit_of_work(api.ctx) as s:
        assert len(s.scalars(select(Override)).all()) == 1
    for ctx in (
        TenancyContext(tenant_id=T1, project_id=api.other),
        TenancyContext(tenant_id=T1),
        TenancyContext(tenant_id=T2, project_id=api.project),
    ):
        with api.db.unit_of_work(ctx) as s:
            assert s.scalars(select(Override)).all() == []


def test_the_manifest_declares_the_notifications_dependency() -> None:
    from app.modules.validation.manifest import MANIFEST

    assert set(MANIFEST.depends_on) == {"projects", "notifications"}
    assert MANIFEST.toggleable is False


# --- binding paths are compiled with the rule -----------------------------------------------


@pytest.mark.parametrize(
    "binding",
    [[], "fields.x", [1], ["now"], ["params.x"], ["fields..x"], ["fields.x", "fields.x"]],
)
def test_an_invalid_override_binding_does_not_compile(binding: object) -> None:
    with pytest.raises(RuleCompileError, match="override_binding"):
        compile_rule("check", "cel", "true", {"override_binding": binding}, "block")


def test_a_valid_override_binding_compiles() -> None:
    compile_rule("check", "cel", "true", {"override_binding": BINDING}, "block")


def test_the_hash_is_canonical_and_covers_only_the_bound_values() -> None:
    params = {"override_binding": ["fields.a", "fields.b.c"]}
    one = overrides.checked_values_hash(params, {"fields": {"a": 1, "b": {"c": "x"}, "z": 1}})
    two = overrides.checked_values_hash(params, {"fields": {"z": 2, "b": {"c": "x"}, "a": 1}})
    three = overrides.checked_values_hash(params, {"fields": {"a": 2, "b": {"c": "x"}}})
    assert one == two != three
    assert overrides.parse_subject("inspection:not-a-uuid") is None
    record = uuid.uuid4()
    assert overrides.parse_subject(f"inspection:{record}") == ("inspection", record)
