"""Projects module: record, settings validation, service, API (T-0012).

Runs on SQLite and, in CI, on PostgreSQL under RLS against the migrated schema, where
``spatial_bounds`` is a PostGIS ``geometry(Polygon, 4326)`` (the PostGIS-only checks skip
elsewhere, TD-052). All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Column, MetaData, Table, event, text

from app.core.audit.service import chain_entries
from app.core.auth.access import Persona
from app.core.auth.models import MEMBERSHIP_TABLE, ProjectMembership
from app.core.db import Base, Database, contract_violations
from app.core.geo import GeoJSONGeometry
from app.core.module_loader.loader import discover_modules
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import RLS_EXEMPT, bind_connection, rls_tables
from app.core.tenancy.scope import OutOfScopeError
from app.modules.projects import service
from app.modules.projects.models import TABLE
from app.modules.projects.schemas import (
    MAX_RINGS,
    MAX_TOTAL_POSITIONS,
    ProjectCreate,
    ProjectUpdate,
)
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, T1, T2, auth_app, make_settings, seed_user
from tests.db_support import ON_POSTGRES, database_url

PROJECTS = "/api/v1/projects"
CTX_T1 = TenancyContext(tenant_id=T1)
CTX_T2 = TenancyContext(tenant_id=T2)
CAROL = "carol@example.test"
# A synthetic rectangle around a made-up site, counter-clockwise, closed.
BOUNDS: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [
        [[115.5, -32.5], [116.5, -32.5], [116.5, -31.5], [115.5, -31.5], [115.5, -32.5]]
    ],
}


def _create(**fields: Any) -> ProjectCreate:
    values: dict[str, Any] = {"code": "SYN-001", "name": "Synthetic plant"}
    values.update(fields)
    return ProjectCreate.model_validate(values)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


# --- contract -------------------------------------------------------------------------------


def test_project_table_meets_the_record_contract_and_is_under_rls() -> None:
    assert TABLE in Base.metadata.tables
    assert [p for p in contract_violations(Base.metadata) if p.startswith(TABLE)] == []
    assert TABLE in rls_tables(Base.metadata)
    assert TABLE not in RLS_EXEMPT


def test_projects_module_is_loaded_and_cannot_be_switched_off() -> None:
    manifest = discover_modules().get("projects")
    assert manifest.toggleable is False


# --- settings validation --------------------------------------------------------------------


def test_defaults() -> None:
    body = _create()
    assert (body.site_timezone, body.currency_code, body.locale) == (
        "Australia/Perth",
        "AUD",
        "en-AU",
    )
    assert (body.status, body.spatial_bounds) == ("ACTIVE", None)


@pytest.mark.parametrize("zone", ["America/Edmonton", "UTC", "Asia/Kolkata"])
def test_iana_timezones_are_accepted(zone: str) -> None:
    assert _create(site_timezone=zone).site_timezone == zone


@pytest.mark.parametrize(
    "zone",
    [
        "Mars/Olympus_Mons",
        "+08:00",
        "AWST",
        "",
        "../etc/passwd",
        "localtime",
        "Factory",
        "posixrules",
    ],
)
def test_unknown_timezones_are_rejected(zone: str) -> None:
    with pytest.raises(ValidationError, match="IANA"):
        _create(site_timezone=zone)


@pytest.mark.parametrize("code", ["AUD", "CAD", "USD", "EUR", "NOK"])
def test_iso_4217_currencies_are_accepted(code: str) -> None:
    assert _create(currency_code=code).currency_code == code


@pytest.mark.parametrize("code", ["aud", "XYZ", "AU", "AUDD", ""])
def test_unknown_currencies_are_rejected(code: str) -> None:
    with pytest.raises(ValidationError, match="ISO 4217"):
        _create(currency_code=code)


@pytest.mark.parametrize("tag", ["en-AU", "en", "fr-CA", "zh-Hant-TW", "es-419", "de-DE"])
def test_bcp47_locales_are_accepted(tag: str) -> None:
    assert _create(locale=tag).locale == tag


@pytest.mark.parametrize("tag", ["english", "en_AU", "EN-au", "e", "en-AU-", ""])
def test_malformed_locales_are_rejected(tag: str) -> None:
    with pytest.raises(ValidationError, match="BCP 47"):
        _create(locale=tag)


@pytest.mark.parametrize("code", ["", "has space", "x" * 33, "-lead"])
def test_bad_codes_are_rejected(code: str) -> None:
    with pytest.raises(ValidationError):
        _create(code=code)


def test_status_must_be_known() -> None:
    assert _create(status="ON_HOLD").status == "ON_HOLD"
    with pytest.raises(ValidationError):
        _create(status="PAUSED")


def test_a_valid_polygon_is_accepted() -> None:
    body = _create(spatial_bounds=BOUNDS)
    assert body.spatial_bounds is not None
    assert body.spatial_bounds.as_geojson() == BOUNDS


def _ring(*points: tuple[float, float]) -> dict[str, Any]:
    return {"type": "Polygon", "coordinates": [[list(p) for p in points]]}


@pytest.mark.parametrize(
    ("bounds", "message"),
    [
        (_ring((0, 0), (1, 0), (1, 1), (0, 1)), "closed"),
        (_ring((0, 0), (1, 0), (0, 0)), "at least 4"),
        (_ring((0, 0), (181, 0), (1, 1), (0, 0)), "longitude"),
        (_ring((0, 0), (1, -91), (1, 1), (0, 0)), "latitude"),
        ({"type": "Polygon", "coordinates": []}, "at least 1"),
        ({"type": "Point", "coordinates": [0, 0]}, "Polygon"),
        (
            {"type": "Polygon", "coordinates": [[[0, 0, 5], [1, 0, 5], [1, 1, 5], [0, 0, 5]]]},
            "at most 2",
        ),
        ({**BOUNDS, "crs": {"type": "name"}}, "crs"),
        (
            {"type": "Polygon", "coordinates": [[[True, False], [1, 0], [1, 1], [True, False]]]},
            "float",
        ),
        (
            {
                "type": "Polygon",
                "coordinates": [[["116.5", "-32"], [1, 0], [1, 1], ["116.5", "-32"]]],
            },
            "float",
        ),
    ],
)
def test_invalid_polygons_are_rejected(bounds: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _create(spatial_bounds=bounds)


def _square_ring(n: int, offset: float = 0.0) -> list[list[float]]:
    """A closed ring of ``n`` positions (schema-valid; geometric validity is not checked)."""
    side = (n - 1) // 4 + 1
    step = 1.0 / side
    points = [[offset + i * step, 0.0] for i in range(n - 1)]
    return [*points, points[0]]


def test_spatial_bounds_cap_the_total_positions_across_all_rings() -> None:
    # T-0060: each ring may hold MAX_RING_POSITIONS, but all rings together at most
    # MAX_TOTAL_POSITIONS, so a body cannot ask for 100 x 10,000 positions.
    assert MAX_TOTAL_POSITIONS == 10_000
    half = MAX_TOTAL_POSITIONS // 2
    at_cap = {"type": "Polygon", "coordinates": [_square_ring(half), _square_ring(half, 0.001)]}
    body = _create(spatial_bounds=at_cap)
    assert body.spatial_bounds is not None
    assert sum(len(r) for r in body.spatial_bounds.coordinates) == MAX_TOTAL_POSITIONS
    over = {"type": "Polygon", "coordinates": [_square_ring(half), _square_ring(half + 1)]}
    with pytest.raises(ValidationError, match="at most 10000 positions in total"):
        _create(spatial_bounds=over)
    many_rings = {"type": "Polygon", "coordinates": [_square_ring(4)] * MAX_RINGS}
    assert _create(spatial_bounds=many_rings).spatial_bounds is not None
    too_many = {"type": "Polygon", "coordinates": [_square_ring(101)] * MAX_RINGS}
    with pytest.raises(ValidationError, match="in total"):
        _create(spatial_bounds=too_many)


def test_non_finite_coordinates_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _create(spatial_bounds=_ring((0, 0), (float("nan"), 0), (1, 1), (0, 0)))


def test_update_needs_at_least_one_field_and_rejects_unknown_ones() -> None:
    with pytest.raises(ValidationError):
        ProjectUpdate.model_validate({"tenant_id": str(T2)})
    with pytest.raises(ValidationError, match="at least one field"):
        ProjectUpdate.model_validate({})
    assert ProjectUpdate.model_validate({"spatial_bounds": None}).model_fields_set == {
        "spatial_bounds"
    }
    with pytest.raises(ValidationError, match="IANA"):
        ProjectUpdate.model_validate({"site_timezone": "Nowhere/Land"})
    with pytest.raises(ValidationError, match="null"):
        ProjectUpdate.model_validate({"site_timezone": None})


def test_geojson_type_round_trips_on_sqlite_without_postgis() -> None:
    from sqlalchemy.dialects import sqlite

    column_type = GeoJSONGeometry("Polygon")
    bind = column_type.process_bind_param(BOUNDS, sqlite.dialect())
    assert isinstance(bind, str)
    assert column_type.process_result_value(bind, sqlite.dialect()) == BOUNDS
    assert column_type.process_bind_param(None, sqlite.dialect()) is None
    assert column_type.process_result_value(None, sqlite.dialect()) is None


# --- service --------------------------------------------------------------------------------


def test_timezone_and_spatial_bounds_are_persisted(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        created = service.create_project(
            s, _create(site_timezone="America/Edmonton", spatial_bounds=BOUNDS, currency_code="CAD")
        )
        project_id = created.id
    with db.unit_of_work(CTX_T1) as s:
        project = service.get_project(s, project_id)
        assert project.site_timezone == "America/Edmonton"
        assert project.spatial_bounds == BOUNDS
        assert (project.currency_code, project.locale, project.status) == ("CAD", "en-AU", "ACTIVE")
        assert (project.tenant_id, project.project_id) == (T1, None)
        assert str(service.site_zone(project)) == "America/Edmonton"


def test_update_changes_only_the_given_fields_and_can_clear_the_bounds(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        project_id = service.create_project(s, _create(spatial_bounds=BOUNDS)).id
    with db.unit_of_work(CTX_T1) as s:
        service.update_project(s, project_id, ProjectUpdate(site_timezone="Asia/Kolkata"))
    with db.unit_of_work(CTX_T1) as s:
        project = service.get_project(s, project_id)
        assert (project.site_timezone, project.spatial_bounds) == ("Asia/Kolkata", BOUNDS)
        assert project.revision == 2
        service.update_project(
            s, project_id, ProjectUpdate.model_validate({"spatial_bounds": None})
        )
    with db.unit_of_work(CTX_T1) as s:
        assert service.get_project(s, project_id).spatial_bounds is None


def test_codes_are_unique_per_tenant(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        service.create_project(s, _create())
    with pytest.raises(service.DuplicateProjectCodeError), db.unit_of_work(CTX_T1) as s:
        service.create_project(s, _create(name="Again"))
    with db.unit_of_work(CTX_T1) as s:
        other = service.create_project(s, _create(code="SYN-002"))
    with pytest.raises(service.DuplicateProjectCodeError), db.unit_of_work(CTX_T1) as s:
        service.update_project(s, other.id, ProjectUpdate(code="SYN-001"))
    with db.unit_of_work(CTX_T2) as s:  # another tenant may use the same code
        service.create_project(s, _create())


def test_another_tenants_project_does_not_exist(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        project_id = service.create_project(s, _create()).id
    with pytest.raises(OutOfScopeError), db.unit_of_work(CTX_T2) as s:
        service.get_project(s, project_id)
    with db.unit_of_work(CTX_T2) as s:
        assert service.list_projects(s, {project_id}) == []


def test_list_returns_only_the_requested_live_projects_by_code(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        b = service.create_project(s, _create(code="B")).id
        a = service.create_project(s, _create(code="A")).id
        hidden = service.create_project(s, _create(code="C")).id
        gone = service.create_project(s, _create(code="D")).id
    with db.unit_of_work(CTX_T1) as s:
        service.delete_project(s, gone)
    with db.unit_of_work(CTX_T1) as s:
        assert [p.code for p in service.list_projects(s, {a, b, gone})] == ["A", "B"]
        assert hidden not in {p.id for p in service.list_projects(s, set())}
        with pytest.raises(OutOfScopeError):
            service.get_project(s, gone)


def test_a_project_session_cannot_write_the_tenant_level_project_row(db: Database) -> None:
    project_ctx = TenancyContext(tenant_id=T1, project_id=uuid.uuid4())
    with pytest.raises(OutOfScopeError), db.unit_of_work(project_ctx) as s:
        service.create_project(s, _create())


def test_changes_are_audited(db: Database) -> None:
    actor = uuid.uuid4()
    with db.unit_of_work(CTX_T1) as s:
        project_id = service.create_project(s, _create(), actor_id=actor).id
    with db.unit_of_work(CTX_T1) as s:
        service.update_project(s, project_id, ProjectUpdate(name="Renamed"), actor_id=actor)
        service.update_project(s, project_id, ProjectUpdate(name="Renamed"), actor_id=actor)
        service.delete_project(s, project_id, actor_id=actor)
    with db.unit_of_work(CTX_T1) as s:
        entries = chain_entries(s, f"project:{project_id}")
    assert [e.payload["action"] for e in entries] == ["create", "update", "delete"]
    assert entries[1].payload["fields"] == ["name"]
    assert {e.created_by for e in entries} == {actor}


def test_service_needs_a_bound_session(db: Database) -> None:
    session = db.sessionmaker()
    try:
        with pytest.raises(RuntimeError, match="tenant"):
            service.create_project(session, _create())
    finally:
        session.close()


# --- PostGIS (PostgreSQL only) --------------------------------------------------------------

needs_postgis = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL + PostGIS; runs in CI's PostgreSQL step (TD-052)"
)


@needs_postgis
def test_spatial_bounds_are_a_postgis_polygon_in_wgs84(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        project_id = service.create_project(s, _create(spatial_bounds=BOUNDS)).id
    with db.engine.begin() as conn:
        bind_connection(conn, CTX_T1)
        row = conn.execute(
            text(
                f"SELECT ST_GeometryType(spatial_bounds) AS kind, ST_SRID(spatial_bounds) AS srid,"
                f" ST_Contains(spatial_bounds, ST_SetSRID(ST_MakePoint(116, -32), 4326)) AS inside,"
                f" format_type(a.atttypid, a.atttypmod) AS column_type"
                f" FROM {TABLE} JOIN pg_attribute a ON a.attrelid = '{TABLE}'::regclass"
                f" AND a.attname = 'spatial_bounds' WHERE id = :id"
            ),
            {"id": project_id},
        ).one()
    assert (row.kind, row.srid, row.inside) == ("ST_Polygon", 4326, True)
    assert row.column_type == "geometry(Polygon,4326)"


def _type_diff(db: Database, kind: str) -> list[Any]:
    metadata = MetaData()
    Table(TABLE, metadata, Column("spatial_bounds", GeoJSONGeometry(kind)))
    with db.engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"include_name": lambda name, type_, _: type_ != "table" or name == TABLE}
        )
        diff = compare_metadata(ctx, metadata)
    # Column changes come grouped per column: [[("modify_type", ...)], ...].
    return [
        op for group in diff if isinstance(group, list) for op in group if op[0] == "modify_type"
    ]


@needs_postgis
def test_reflection_reads_the_geometry_kind_so_alembic_sees_a_change(db: Database) -> None:
    assert _type_diff(db, "Polygon") == []
    assert len(_type_diff(db, "Point")) == 1


# --- HTTP (real tokens, memberships from the store) -----------------------------------------


def _grant(db: Database, user: uuid.UUID, project: uuid.UUID, persona: Persona) -> None:
    with db.unit_of_work(CTX_T1) as s:
        s.add(
            ProjectMembership(
                tenant_id=T1, user_id=user, member_project_id=project, persona=persona.value
            )
        )


class Api:
    def __init__(self, client: TestClient, db: Database) -> None:
        self.client, self.db = client, db
        self.users = {email: seed_user(db, T1, email) for email in (ALICE, BOB, CAROL)}
        with db.unit_of_work(CTX_T1) as s:
            self.home = service.create_project(s, _create(code="HOME")).id
        _grant(db, self.users[ALICE], self.home, Persona.PROJECT_ADMINISTRATOR)
        _grant(db, self.users[BOB], self.home, Persona.PROJECT_MANAGER)

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
    responses = [
        api.client.get(PROJECTS),
        api.client.post(PROJECTS, json={"code": "X", "name": "X"}),
        api.client.get(f"{PROJECTS}/{api.home}"),
        api.client.patch(f"{PROJECTS}/{api.home}", json={"name": "X"}),
        api.client.delete(f"{PROJECTS}/{api.home}"),
    ]
    assert [r.status_code for r in responses] == [401] * 5


def test_an_administrator_creates_a_project_with_settings(api: Api) -> None:
    body = {
        "code": "SYN-LNG",
        "name": "Synthetic LNG train",
        "site_timezone": "America/Edmonton",
        "spatial_bounds": BOUNDS,
        "currency_code": "CAD",
        "locale": "fr-CA",
    }
    response = api.client.post(PROJECTS, json=body, headers=api.headers(ALICE))
    assert response.status_code == 201, response.text
    created = response.json()
    assert {k: created[k] for k in body} == body
    assert created["status"] == "ACTIVE"
    with api.db.unit_of_work(CTX_T1) as s:
        assert service.get_project(s, uuid.UUID(created["id"])).site_timezone == "America/Edmonton"


def test_creating_needs_the_admin_capability(api: Api) -> None:
    body = {"code": "NOPE", "name": "Nope"}
    assert api.client.post(PROJECTS, json=body, headers=api.headers(BOB)).status_code == 403
    assert api.client.post(PROJECTS, json=body, headers=api.headers(CAROL)).status_code == 403


def test_invalid_settings_are_422_and_a_duplicate_code_is_409(api: Api) -> None:
    headers = api.headers(ALICE)
    bad = {"code": "TZ", "name": "Bad zone", "site_timezone": "Mars/Base"}
    assert api.client.post(PROJECTS, json=bad, headers=headers).status_code == 422
    duplicate = {"code": "HOME", "name": "Again"}
    response = api.client.post(PROJECTS, json=duplicate, headers=headers)
    assert response.status_code == 409
    assert "HOME" in response.json()["detail"]
    second = api.client.post(PROJECTS, json={"code": "SECOND", "name": "Second"}, headers=headers)
    url = f"{PROJECTS}/{second.json()['id']}"
    _grant(api.db, api.users[ALICE], uuid.UUID(second.json()["id"]), Persona.PROJECT_ADMINISTRATOR)
    assert api.client.patch(url, json={"code": "HOME"}, headers=headers).status_code == 409


def test_spatial_bounds_over_the_total_position_cap_are_422(api: Api) -> None:
    rings = [_square_ring(5_000), _square_ring(5_001, 0.001)]
    body = {
        "code": "BIG",
        "name": "Big",
        "spatial_bounds": {"type": "Polygon", "coordinates": rings},
    }
    response = api.client.post(PROJECTS, json=body, headers=api.headers(ALICE))
    assert response.status_code == 422
    assert "in total" in response.text


def test_members_list_and_read_only_their_projects(api: Api) -> None:
    with api.db.unit_of_work(CTX_T1) as s:
        foreign = service.create_project(s, _create(code="FOREIGN")).id
    with api.db.unit_of_work(CTX_T2) as s:
        other_tenant = service.create_project(s, _create(code="T2")).id
    headers = api.headers(BOB)
    listed = api.client.get(PROJECTS, headers=headers).json()
    assert [p["code"] for p in listed] == ["HOME"]
    assert api.client.get(f"{PROJECTS}/{api.home}", headers=headers).json()["code"] == "HOME"
    for hidden in (foreign, other_tenant, uuid.uuid4()):
        assert api.client.get(f"{PROJECTS}/{hidden}", headers=headers).status_code == 404
    assert api.client.get(PROJECTS, headers=api.headers(CAROL)).json() == []


def test_a_request_reads_the_callers_memberships_once(api: Api) -> None:
    """T-0071: the grants loaded to resolve the request's scope are reused by the router."""
    headers = api.headers(ALICE)
    engine = api.client.app.state.db.engine
    reads: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT") and MEMBERSHIP_TABLE in statement:
            reads.append(statement)

    calls = [
        ("GET", PROJECTS, None),
        ("GET", f"{PROJECTS}/{api.home}", None),
        ("PATCH", f"{PROJECTS}/{api.home}", {"name": "Home, renamed"}),
    ]
    event.listen(engine, "before_cursor_execute", record)
    try:
        for method, url, body in calls:
            reads.clear()
            response = api.client.request(method, url, json=body, headers=headers)
            assert response.status_code == 200, response.text
            assert len(reads) == 1, (method, url, reads)
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_an_administrator_edits_the_settings(api: Api) -> None:
    patch = {"site_timezone": "Asia/Kolkata", "spatial_bounds": BOUNDS, "locale": "en-IN"}
    response = api.client.patch(f"{PROJECTS}/{api.home}", json=patch, headers=api.headers(ALICE))
    assert response.status_code == 200, response.text
    assert {k: response.json()[k] for k in patch} == patch
    assert response.json()["revision"] == 2
    cleared = api.client.patch(
        f"{PROJECTS}/{api.home}", json={"spatial_bounds": None}, headers=api.headers(ALICE)
    )
    assert cleared.json()["spatial_bounds"] is None


def test_editing_needs_the_admin_capability_and_membership(api: Api) -> None:
    patch = {"name": "Hijacked"}
    assert (
        api.client.patch(f"{PROJECTS}/{api.home}", json=patch, headers=api.headers(BOB)).status_code
        == 403
    )
    assert (
        api.client.patch(
            f"{PROJECTS}/{api.home}", json=patch, headers=api.headers(CAROL)
        ).status_code
        == 404
    )
    assert api.client.delete(f"{PROJECTS}/{api.home}", headers=api.headers(BOB)).status_code == 403
    invalid = api.client.patch(
        f"{PROJECTS}/{api.home}", json={"currency_code": "XYZ"}, headers=api.headers(ALICE)
    )
    assert invalid.status_code == 422


def test_an_administrator_deletes_a_project(api: Api) -> None:
    headers = api.headers(ALICE)
    assert api.client.delete(f"{PROJECTS}/{api.home}", headers=headers).status_code == 204
    assert api.client.get(f"{PROJECTS}/{api.home}", headers=headers).status_code == 404
    assert api.client.delete(f"{PROJECTS}/{api.home}", headers=headers).status_code == 404
