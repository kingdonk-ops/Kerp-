"""Deployment (tenant) settings: record, validation, service, API, project defaults (T-0044).

Diff §4.4 (offline window, new-project defaults, clock skew, offline grace), §4.2, §6.4.1;
master spec §4.1 amendment. Runs on SQLite and, in CI, on PostgreSQL under RLS against the
migrated schema. All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.audit.service import chain_entries
from app.core.auth.access import CREATE_PROJECTS, SYSTEM_ADMIN
from app.core.db import Base, Database, contract_violations
from app.core.module_loader.loader import discover_modules
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import RLS_EXEMPT, rls_tables, table_policy
from app.modules.admin import service as admin
from app.modules.projects import models as project_models
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.settings import service
from app.modules.settings.models import SHIPPED_DEFAULTS, STATUSES, TABLE
from app.modules.settings.schemas import DeploymentSettingsUpdate
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, T1, T2, auth_app, make_settings, seed_user
from tests.db_support import database_url

URL = "/api/v1/settings/deployment"
PROJECTS = "/api/v1/projects"
CTX_T1 = TenancyContext(tenant_id=T1, is_system_admin=True)
CTX_T2 = TenancyContext(tenant_id=T2, is_system_admin=True)
CAROL = "carol@example.test"
SHIPPED = {
    "offline_window_days": 7,
    "offline_grace_hours": 24,
    "max_checkout_clock_skew_s": 5,
    "default_status": "ACTIVE",
    "default_site_timezone": "Australia/Perth",
    "default_currency_code": "AUD",
    "default_locale": "en-AU",
}


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def _update(**fields: Any) -> DeploymentSettingsUpdate:
    return DeploymentSettingsUpdate.model_validate(fields)


# --- contract -------------------------------------------------------------------------------


def test_table_meets_the_record_contract_and_is_under_the_standard_policy() -> None:
    assert TABLE in Base.metadata.tables
    assert [p for p in contract_violations(Base.metadata) if p.startswith(TABLE)] == []
    assert TABLE in rls_tables(Base.metadata)
    assert TABLE not in RLS_EXEMPT
    assert table_policy(Base.metadata.tables[TABLE]) is None


def test_settings_is_a_platform_module_that_projects_depends_on() -> None:
    modules = discover_modules()
    assert modules.get("settings").toggleable is False
    assert modules.get("settings").depends_on == ()
    assert "settings" in modules.get("projects").depends_on


def test_shipped_defaults_are_the_spec_values() -> None:
    assert SHIPPED_DEFAULTS.as_dict() == SHIPPED


def test_project_fallback_literals_equal_the_shipped_defaults() -> None:
    """The projects table's fallback literals (T-0063 DB defaults) equal the shipped ones."""
    assert SHIPPED_DEFAULTS.default_status == project_models.DEFAULT_STATUS
    assert SHIPPED_DEFAULTS.default_site_timezone == project_models.DEFAULT_TIMEZONE
    assert SHIPPED_DEFAULTS.default_currency_code == project_models.DEFAULT_CURRENCY
    assert SHIPPED_DEFAULTS.default_locale == project_models.DEFAULT_LOCALE
    assert STATUSES == project_models.STATUSES


# --- validation -----------------------------------------------------------------------------


@pytest.mark.parametrize("days", [1, 7, 14])
def test_offline_window_accepts_1_to_14(days: int) -> None:
    assert _update(offline_window_days=days).offline_window_days == days


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("offline_window_days", 0),
        ("offline_window_days", 15),
        ("offline_window_days", True),
        ("offline_window_days", "7"),
        ("offline_window_days", 7.5),
        ("max_checkout_clock_skew_s", 0),
        ("max_checkout_clock_skew_s", 301),
        ("offline_grace_hours", 0),
        ("offline_grace_hours", 24 * 14 + 1),
        ("default_status", "ARCHIVED"),
        ("default_status", "active"),
    ],
)
def test_out_of_range_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _update(**{field: value})


@pytest.mark.parametrize(("skew", "grace"), [(1, 1), (300, 24 * 14)])
def test_bounds_are_inclusive(skew: int, grace: int) -> None:
    body = _update(max_checkout_clock_skew_s=skew, offline_grace_hours=grace)
    assert (body.max_checkout_clock_skew_s, body.offline_grace_hours) == (skew, grace)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("default_site_timezone", "Mars/Olympus_Mons", "IANA"),
        ("default_site_timezone", "localtime", "IANA"),
        ("default_currency_code", "XYZ", "ISO 4217"),
        ("default_currency_code", "aud", "ISO 4217"),
        ("default_locale", "en_AU", "BCP 47"),
    ],
)
def test_project_defaults_are_validated(field: str, value: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _update(**{field: value})


def test_grace_may_not_exceed_the_offline_window_in_one_body() -> None:
    with pytest.raises(ValidationError, match="offline_grace_hours"):
        _update(offline_window_days=1, offline_grace_hours=25)
    assert _update(offline_window_days=1, offline_grace_hours=24).offline_grace_hours == 24


@pytest.mark.parametrize(
    "body",
    [{}, {"offline_window_days": None}, {"tenant_id": str(T1)}, {"region": "eu-central-1"}],
)
def test_empty_null_and_unknown_fields_are_rejected(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DeploymentSettingsUpdate.model_validate(body)


def test_project_create_refuses_null_defaults() -> None:
    with pytest.raises(ValidationError, match="cannot be null"):
        ProjectCreate.model_validate({"code": "X", "name": "X", "site_timezone": None})
    body = ProjectCreate(code="X", name="X")
    assert (body.status, body.site_timezone, body.currency_code, body.locale) == (None,) * 4


# --- service --------------------------------------------------------------------------------


def test_without_a_record_the_shipped_defaults_apply_and_nothing_is_written(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        assert service.get_settings(s).as_dict() == SHIPPED
        assert service.stored_record(s) is None


def test_update_stores_one_record_and_audits_the_changes(db: Database) -> None:
    actor = seed_user(db, T1, ALICE)
    with db.unit_of_work(CTX_T1) as s:
        values = service.update_settings(
            s, _update(offline_window_days=10, default_currency_code="NZD"), actor_id=actor
        )
        assert values.offline_window_days == 10
    with db.unit_of_work(CTX_T1) as s:
        service.update_settings(s, _update(default_locale="en-NZ"), actor_id=actor)
    with db.unit_of_work(CTX_T1) as s:
        record = service.stored_record(s)
        assert record is not None
        assert (record.tenant_id, record.project_id, record.updated_by) == (T1, None, actor)
        assert service.get_settings(s).as_dict() == {
            **SHIPPED,
            "offline_window_days": 10,
            "default_currency_code": "NZD",
            "default_locale": "en-NZ",
        }
        entries = chain_entries(s, f"{service.CHAIN_PREFIX}{T1}")
        assert [e.payload["changes"] for e in entries] == [
            {"default_currency_code": ["AUD", "NZD"], "offline_window_days": [7, 10]},
            {"default_locale": ["en-AU", "en-NZ"]},
        ]
        assert all(e.created_by == actor for e in entries)


def test_an_update_that_changes_nothing_writes_nothing(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        service.update_settings(s, _update(offline_window_days=7), actor_id=None)
    with db.unit_of_work(CTX_T1) as s:
        assert service.stored_record(s) is None
        assert chain_entries(s, f"{service.CHAIN_PREFIX}{T1}") == []


def test_lowering_the_window_below_the_grace_is_refused(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        service.update_settings(s, _update(offline_grace_hours=48), actor_id=None)
    with (
        db.unit_of_work(CTX_T1) as s,
        pytest.raises(service.InvalidSettingsError, match="offline_grace_hours"),
    ):
        service.update_settings(s, _update(offline_window_days=1), actor_id=None)
    with db.unit_of_work(CTX_T1) as s:
        assert service.get_settings(s).offline_window_days == 7


def test_each_tenant_has_its_own_settings(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        service.update_settings(s, _update(offline_window_days=3), actor_id=None)
    with db.unit_of_work(CTX_T2) as s:
        assert service.get_settings(s).offline_window_days == 7
        assert service.stored_record(s) is None


def test_a_project_bound_session_reads_but_cannot_update(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        service.update_settings(s, _update(offline_window_days=3), actor_id=None)
        project = projects.create_project(s, ProjectCreate(code="P1", name="Synthetic")).id
    bound = TenancyContext(tenant_id=T1, project_id=project)
    with db.unit_of_work(bound) as s:
        assert service.get_settings(s).offline_window_days == 3
        with pytest.raises(RuntimeError, match="tenant-level"):
            service.update_settings(s, _update(offline_window_days=4), actor_id=None)


def test_project_creation_takes_the_deployment_defaults(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        shipped = projects.create_project(s, ProjectCreate(code="P0", name="Synthetic"))
        assert (
            shipped.status,
            shipped.site_timezone,
            shipped.currency_code,
            shipped.locale,
        ) == ("ACTIVE", "Australia/Perth", "AUD", "en-AU")
        service.update_settings(
            s,
            _update(
                default_status="ON_HOLD",
                default_site_timezone="America/Edmonton",
                default_currency_code="CAD",
                default_locale="fr-CA",
            ),
            actor_id=None,
        )
        configured = projects.create_project(s, ProjectCreate(code="P1", name="Synthetic"))
        assert (
            configured.status,
            configured.site_timezone,
            configured.currency_code,
            configured.locale,
        ) == ("ON_HOLD", "America/Edmonton", "CAD", "fr-CA")
        explicit = projects.create_project(
            s, ProjectCreate(code="P2", name="Synthetic", status="ACTIVE", locale="en-GB")
        )
        assert (explicit.status, explicit.site_timezone, explicit.locale) == (
            "ACTIVE",
            "America/Edmonton",
            "en-GB",
        )


# --- HTTP (real tokens) ---------------------------------------------------------------------


class Api:
    def __init__(self, client: TestClient, db: Database) -> None:
        self.client, self.db = client, db
        self.users = {email: seed_user(db, T1, email) for email in (ALICE, BOB, CAROL)}
        with db.unit_of_work(CTX_T1) as s:  # ALICE runs the deployment, BOB creates projects
            admin.grant(s, self.users[ALICE], SYSTEM_ADMIN, actor_id=None)
            admin.grant(s, self.users[BOB], CREATE_PROJECTS, actor_id=None)

    def headers(self, email: str) -> dict[str, str]:
        body = {"email": email, "password": PASSWORD}
        response = self.client.post("/api/v1/auth/login", json=body)
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        yield Api(client, db)


def test_the_api_needs_a_token(api: Api) -> None:
    responses = [api.client.get(URL), api.client.patch(URL, json={"offline_window_days": 3})]
    assert [r.status_code for r in responses] == [401, 401]


def test_any_signed_in_user_reads_the_settings(api: Api) -> None:
    """CAROL holds no membership and no grant: field clients need the offline window."""
    response = api.client.get(URL, headers=api.headers(CAROL))
    assert response.status_code == 200, response.text
    assert response.json() == SHIPPED


def test_only_a_system_admin_may_patch(api: Api) -> None:
    for email in (BOB, CAROL):
        body = {"offline_window_days": 3}
        response = api.client.patch(URL, json=body, headers=api.headers(email))
        assert response.status_code == 403
    response = api.client.patch(
        URL,
        json={"offline_window_days": 3, "offline_grace_hours": 12, "max_checkout_clock_skew_s": 30},
        headers=api.headers(ALICE),
    )
    assert response.status_code == 200, response.text
    expected = {
        **SHIPPED,
        "offline_window_days": 3,
        "offline_grace_hours": 12,
        "max_checkout_clock_skew_s": 30,
    }
    assert response.json() == expected
    assert api.client.get(URL, headers=api.headers(CAROL)).json() == expected
    with api.db.unit_of_work(CTX_T1) as s:
        [entry] = chain_entries(s, f"{service.CHAIN_PREFIX}{T1}")
        assert entry.created_by == api.users[ALICE]


@pytest.mark.parametrize(
    "body",
    [
        {"offline_window_days": 15},
        {"default_site_timezone": "Nowhere/Town"},
        {"default_currency_code": "ABC"},
        {"default_locale": "english"},
        {"offline_grace_hours": 24 * 7 + 1},  # above 24 x the stored window (7)
        {"tenant_id": str(T2)},
        {},
    ],
)
def test_invalid_patches_are_422(api: Api, body: dict[str, Any]) -> None:
    response = api.client.patch(URL, json=body, headers=api.headers(ALICE))
    assert response.status_code == 422, response.text
    assert api.client.get(URL, headers=api.headers(ALICE)).json() == SHIPPED


def test_projects_created_through_the_api_get_the_deployment_defaults(api: Api) -> None:
    patch = {"default_site_timezone": "Asia/Kolkata", "default_currency_code": "INR"}
    assert api.client.patch(URL, json=patch, headers=api.headers(ALICE)).status_code == 200
    response = api.client.post(
        PROJECTS, json={"code": "SYN-IN", "name": "Synthetic"}, headers=api.headers(BOB)
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert (created["status"], created["site_timezone"], created["currency_code"]) == (
        "ACTIVE",
        "Asia/Kolkata",
        "INR",
    )
    assert created["locale"] == "en-AU"
    assert uuid.UUID(created["id"])
