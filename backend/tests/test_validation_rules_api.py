"""Rules settings API: permissions per ADR 0004 §7, locked rules, two-person unlock (T-0034).

Real tokens and memberships from the store, on SQLite and, in CI, PostgreSQL under RLS.
All users, projects and rules are synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.auth.access import Persona
from app.core.auth.models import ProjectMembership
from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.validation.defaults import SeedRuleSet
from app.modules.validation.service import list_rule_sets, seed_tenant
from tests.audit_support import migrated_database
from tests.auth_support import PASSWORD, T1, auth_app, make_settings, seed_user
from tests.db_support import database_url

API = "/api/v1/validation"
CTX = TenancyContext(tenant_id=T1)
P = Persona
USERS = {
    "qd": P.QUALITY_DIRECTOR,
    "qd2": P.QUALITY_DIRECTOR,
    "ops": P.OPERATIONS_MANAGEMENT,
    "qa": P.QAQC_LEAD,
    "pa": P.PROJECT_ADMINISTRATOR,
    "pm": P.PROJECT_MANAGER,
}
CHECK = "demo.submit"
DIGEST = "demo.activity"
RULE: dict[str, Any] = {
    "engine": "cel",
    "expression": "fields.ok == true",
    "action": "block",
    "label": "OK flag set",
}
DIGEST_RULE: dict[str, Any] = {**RULE, "expression": "event.kind == 'punch'", "action": "include"}


def _seeds() -> list[SeedRuleSet]:
    def make(key: str, kind: str, rules: list[dict[str, Any]]) -> SeedRuleSet:
        body = {"module": "projects", "key": key, "kind": kind, "version": 1, "rules": rules}
        return SeedRuleSet.model_validate(body)

    return [
        make(
            CHECK,
            "check",
            [
                {**RULE, "rule_key": "calibration", "locked": True},
                {**RULE, "rule_key": "photo", "action": "warn"},
            ],
        ),
        make(
            DIGEST,
            "digest",
            [{"rule_key": "events", "engine": "cel", "expression": "true", "action": "include"}],
        ),
    ]


class Api:
    def __init__(self, client: TestClient, db: Database) -> None:
        self.client, self.db = client, db
        with db.unit_of_work(CTX) as s:
            seed_tenant(s, _seeds())
        with db.unit_of_work(CTX) as s:
            create = ProjectCreate.model_validate({"code": "SYN-R", "name": "Synthetic rules"})
            self.project = projects.create_project(s, create).id
            other = ProjectCreate.model_validate({"code": "SYN-O", "name": "Synthetic other"})
            self.other = projects.create_project(s, other).id
        self.ids = {name: seed_user(db, T1, f"{name}@example.test") for name in USERS}
        self.ids["outsider"] = seed_user(db, T1, "outsider@example.test")
        with db.unit_of_work(CTX) as s:
            for name, persona in USERS.items():
                s.add(
                    ProjectMembership(
                        tenant_id=T1,
                        user_id=self.ids[name],
                        member_project_id=self.project,
                        persona=persona.value,
                    )
                )
            s.add(
                ProjectMembership(
                    tenant_id=T1,
                    user_id=self.ids["outsider"],
                    member_project_id=self.other,
                    persona=P.QAQC_LEAD.value,
                )
            )
        self.tenant = self._sets(CTX, "tenant")
        self.project_sets = self._sets(
            TenancyContext(tenant_id=T1, project_id=self.project), "project"
        )
        self._tokens: dict[str, dict[str, str]] = {}

    def _sets(self, ctx: TenancyContext, layer: str) -> dict[str, str]:
        with self.db.unit_of_work(ctx) as s:
            return {r.key: str(r.id) for r in list_rule_sets(s, layer)}

    def as_(self, name: str) -> dict[str, str]:
        if name not in self._tokens:
            body = {"email": f"{name}@example.test", "password": PASSWORD}
            response = self.client.post("/api/v1/auth/login", json=body)
            assert response.status_code == 200, response.text
            self._tokens[name] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return self._tokens[name]

    def tenant_rule(self, key: str, rule_key: str) -> str:
        return f"{API}/rule-sets/{self.tenant[key]}/rules/{rule_key}"

    def project_rule(self, key: str, rule_key: str) -> str:
        return f"{API}/projects/{self.project}/rule-sets/{self.project_sets[key]}/rules/{rule_key}"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        yield Api(client, db)


def test_the_api_needs_a_token(api: Api) -> None:
    responses = [
        api.client.get(f"{API}/rule-sets"),
        api.client.put(api.tenant_rule(CHECK, "x"), json=RULE),
        api.client.get(f"{API}/projects/{api.project}/rule-sets"),
        api.client.get(f"{API}/unlock-requests"),
    ]
    assert [r.status_code for r in responses] == [401] * 4


# --- reading ---------------------------------------------------------------------------------


def test_tenant_defaults_are_read_by_per13_and_per01(api: Api) -> None:
    for name in ("qd", "ops"):
        response = api.client.get(f"{API}/rule-sets", headers=api.as_(name))
        assert response.status_code == 200, response.text
        assert [s["key"] for s in response.json()] == [DIGEST, CHECK]
    for name in ("qa", "pa", "pm"):
        assert api.client.get(f"{API}/rule-sets", headers=api.as_(name)).status_code == 403


def test_project_members_read_the_project_copy(api: Api) -> None:
    url = f"{API}/projects/{api.project}/rule-sets"
    response = api.client.get(url, headers=api.as_("pm"))
    assert response.status_code == 200, response.text
    check = next(s for s in response.json() if s["key"] == CHECK)
    assert check["layer"] == "project"
    calibration = next(r for r in check["rules"] if r["rule_key"] == "calibration")
    assert (calibration["locked"], calibration["origin_rule_key"], calibration["label"]) == (
        True,
        "calibration",
        "OK flag set",
    )
    assert api.client.get(url, headers=api.as_("outsider")).status_code == 404
    missing = f"{API}/projects/{uuid.uuid4()}/rule-sets"
    assert api.client.get(missing, headers=api.as_("pm")).status_code == 404


# --- permissions (ADR 0004 §7) ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind_key", "allowed", "refused"),
    [(CHECK, "qd", ["ops", "qa", "pa", "pm"]), (DIGEST, "ops", ["qd", "qa", "pa", "pm"])],
)
def test_tenant_layer_editors(api: Api, kind_key: str, allowed: str, refused: list[str]) -> None:
    body = DIGEST_RULE if kind_key == DIGEST else RULE
    url = api.tenant_rule(kind_key, "added")
    for name in refused:
        assert api.client.put(url, json=body, headers=api.as_(name)).status_code == 403, name
    response = api.client.put(url, json=body, headers=api.as_(allowed))
    assert response.status_code == 200, response.text
    added = next(r for r in response.json()["rules"] if r["rule_key"] == "added")
    assert (added["origin_rule_key"], added["version"]) == (None, 1)


@pytest.mark.parametrize(
    ("kind_key", "allowed", "refused"),
    [(CHECK, "qa", ["pa", "pm", "qd", "ops"]), (DIGEST, "pa", ["qa", "pm", "qd", "ops"])],
)
def test_project_layer_editors(api: Api, kind_key: str, allowed: str, refused: list[str]) -> None:
    body = DIGEST_RULE if kind_key == DIGEST else RULE
    url = api.project_rule(kind_key, "added")
    for name in refused:
        assert api.client.put(url, json=body, headers=api.as_(name)).status_code == 403, name
    assert api.client.put(url, json=body, headers=api.as_(allowed)).status_code == 200


def test_per09_never_disables_deletes_or_changes_a_check_rule(api: Api) -> None:
    headers = api.as_("pa")
    photo = api.project_rule(CHECK, "photo")
    project_set = f"{API}/projects/{api.project}/rule-sets/{api.project_sets[CHECK]}"
    responses = [
        api.client.put(photo, json={**RULE, "action": "warn"}, headers=headers),
        api.client.post(f"{photo}/disable", headers=headers),
        api.client.delete(photo, headers=headers),
        api.client.post(f"{photo}/reset", headers=headers),
        api.client.post(f"{project_set}/reset", headers=headers),
    ]
    assert [r.status_code for r in responses] == [403] * 5


def test_a_non_member_and_a_foreign_set_are_404(api: Api) -> None:
    url = api.project_rule(CHECK, "photo")
    assert api.client.post(f"{url}/disable", headers=api.as_("outsider")).status_code == 404
    tenant_set_via_project = (
        f"{API}/projects/{api.project}/rule-sets/{api.tenant[CHECK]}/rules/photo/disable"
    )
    assert api.client.post(tenant_set_via_project, headers=api.as_("qa")).status_code == 404
    project_set_via_tenant = f"{API}/rule-sets/{api.project_sets[CHECK]}/rules/photo/disable"
    assert api.client.post(project_set_via_tenant, headers=api.as_("qd")).status_code == 404


# --- project operations (ADR 0004 §3) ----------------------------------------------------------


def test_the_qaqc_lead_runs_every_project_operation(api: Api) -> None:
    headers = api.as_("qa")
    photo = api.project_rule(CHECK, "photo")
    edited = {**RULE, "action": "warn", "severity": "info", "sort_order": 3, "label": "Photo"}
    response = api.client.put(photo, json=edited, headers=headers)
    assert response.status_code == 200, response.text
    assert api.client.post(f"{photo}/disable", headers=headers).status_code == 200
    assert api.client.post(f"{photo}/enable", headers=headers).status_code == 200
    response = api.client.delete(photo, headers=headers)
    deleted = next(r for r in response.json()["rules"] if r["rule_key"] == "photo")
    assert deleted["deleted_at"] is not None
    response = api.client.post(f"{photo}/reset", headers=headers)
    restored = next(r for r in response.json()["rules"] if r["rule_key"] == "photo")
    assert (restored["deleted_at"], restored["action"], restored["version"]) == (None, "warn", 6)
    project_set = f"{API}/projects/{api.project}/rule-sets/{api.project_sets[CHECK]}"
    response = api.client.post(f"{project_set}/reset", headers=headers)
    assert response.status_code == 200
    assert response.json()["version"] == 6


def test_refusals_are_mapped(api: Api) -> None:
    headers = api.as_("qa")
    calibration = api.project_rule(CHECK, "calibration")
    assert api.client.post(f"{calibration}/disable", headers=headers).status_code == 409
    assert api.client.delete(calibration, headers=headers).status_code == 409
    assert api.client.put(calibration, json=RULE, headers=headers).status_code == 409
    bad = {**RULE, "expression": "nope.x"}
    added = api.project_rule(CHECK, "added")
    assert api.client.put(added, json=bad, headers=headers).status_code == 422
    assert api.client.put(added, json={**RULE, "locked": True}, headers=headers).status_code == 409
    bad_key = api.project_rule(CHECK, "Bad Key")
    assert api.client.put(bad_key, json=RULE, headers=headers).status_code == 422
    ghost = api.project_rule(CHECK, "ghost")
    assert api.client.post(f"{ghost}/disable", headers=headers).status_code == 404
    assert api.client.post(f"{ghost}/reset", headers=headers).status_code == 404


# --- two-person unlock (ADR 0004 §5, Amendment B, TD-116) ----------------------------------------


def test_a_single_person_cannot_complete_an_unlock(api: Api) -> None:
    calibration = api.tenant_rule(CHECK, "calibration")
    body = {"adr_reference": "ADR 0042", "reason": "synthetic"}
    for name in ("ops", "qa", "pa"):
        url = f"{calibration}/unlock-requests"
        assert api.client.post(url, json=body, headers=api.as_(name)).status_code == 403
    assert api.client.put(calibration, json=RULE, headers=api.as_("qd")).status_code == 409
    response = api.client.post(f"{calibration}/unlock-requests", json=body, headers=api.as_("qd"))
    assert response.status_code == 201, response.text
    request_id = response.json()["id"]
    approve = f"{API}/unlock-requests/{request_id}/approve"
    assert api.client.post(approve, headers=api.as_("qd")).status_code == 403  # the requester
    assert api.client.post(approve, headers=api.as_("qa")).status_code == 403  # not PER-13/01
    pending = api.client.get(f"{API}/unlock-requests?status=pending", headers=api.as_("ops"))
    assert [r["id"] for r in pending.json()] == [request_id]
    response = api.client.post(approve, headers=api.as_("qd2"))
    assert response.status_code == 200, response.text
    unlocked = next(r for r in response.json()["rules"] if r["rule_key"] == "calibration")
    assert (unlocked["locked"], unlocked["unlock_adr_reference"]) == (False, "ADR 0042")
    assert api.client.post(approve, headers=api.as_("ops")).status_code == 409
    assert api.client.put(calibration, json=RULE, headers=api.as_("qd")).status_code == 200


def test_an_unlock_request_is_rejected_by_operations(api: Api) -> None:
    calibration = api.tenant_rule(CHECK, "calibration")
    body = {"adr_reference": "ADR 0042"}
    request_id = api.client.post(
        f"{calibration}/unlock-requests", json=body, headers=api.as_("qd")
    ).json()["id"]
    reject = f"{API}/unlock-requests/{request_id}/reject"
    assert api.client.post(reject, headers=api.as_("pm")).status_code == 403
    response = api.client.post(reject, headers=api.as_("ops"))
    assert (response.status_code, response.json()["status"]) == (200, "rejected")
    blank = {"adr_reference": "   "}
    url = f"{calibration}/unlock-requests"
    assert api.client.post(url, json=blank, headers=api.as_("qd")).status_code == 422
    photo = api.tenant_rule(CHECK, "photo")
    assert (
        api.client.post(f"{photo}/unlock-requests", json=body, headers=api.as_("qd")).status_code
        == 409
    )
    unknown = f"{API}/unlock-requests/{uuid.uuid4()}/approve"
    assert api.client.post(unknown, headers=api.as_("ops")).status_code == 404
    assert api.client.get(f"{API}/unlock-requests", headers=api.as_("qa")).status_code == 403


def test_tenant_rule_operations(api: Api) -> None:
    headers = api.as_("qd")
    photo = api.tenant_rule(CHECK, "photo")
    tenant_set = f"{API}/rule-sets/{api.tenant[CHECK]}"
    assert api.client.post(f"{photo}/disable", headers=headers).status_code == 200
    assert api.client.post(f"{photo}/enable", headers=headers).status_code == 200
    assert api.client.delete(photo, headers=headers).status_code == 200
    assert api.client.post(f"{photo}/reset", headers=headers).status_code == 200
    response = api.client.post(f"{tenant_set}/reset", headers=headers)
    assert response.status_code == 200, response.text


def test_a_tenant_reset_all_cannot_remove_a_locked_rule_the_tenant_added(api: Api) -> None:
    headers = api.as_("qd")
    gate = api.tenant_rule(CHECK, "extra_gate")
    tenant_set = f"{API}/rule-sets/{api.tenant[CHECK]}"
    response = api.client.put(gate, json={**RULE, "locked": True}, headers=headers)
    assert response.status_code == 200, response.text
    assert api.client.delete(gate, headers=headers).status_code == 409
    response = api.client.post(f"{tenant_set}/reset", headers=headers)
    assert response.status_code == 409
    assert "extra_gate" in response.json()["detail"]
    sets = api.client.get(f"{API}/rule-sets", headers=headers).json()
    rules = next(s for s in sets if s["id"] == api.tenant[CHECK])["rules"]
    kept = next(r for r in rules if r["rule_key"] == "extra_gate")
    assert (kept["locked"], kept["deleted_at"]) == (True, None)
