"""Projects module: record, settings validation, service, API (T-0012).

Runs on SQLite and, in CI, on PostgreSQL under RLS against the migrated schema, where
``spatial_bounds`` is a PostGIS ``geometry(Polygon, 4326)`` (the PostGIS-only checks skip
elsewhere, TD-052). All data is synthetic.
"""

import importlib.util
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Column, MetaData, Table, event, select, text
from sqlalchemy import column as sa_column
from sqlalchemy import table as sa_table

from app.core.audit.service import chain_entries
from app.core.auth.access import CREATE_PROJECTS, Capability, Grant, Persona, load_grants
from app.core.auth.models import MEMBERSHIP_TABLE, ProjectMembership
from app.core.db import Base, Database, contract_violations
from app.core.geo import GEOJSON_MAX_DECIMALS, GeoJSONGeometry
from app.core.module_loader.loader import discover_modules
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import RLS_EXEMPT, bind_connection, rls_tables, session_tenancy
from app.core.tenancy.scope import OutOfScopeError
from app.modules.admin import service as admin
from app.modules.projects import service
from app.modules.projects.models import DEFAULT_STATUS, DEFAULT_TIMEZONE, TABLE, Project
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
# Provisioning context: a System admin session reads every project row (T-0014, TD-053).
CTX_T1 = TenancyContext(tenant_id=T1, is_system_admin=True)
CTX_T2 = TenancyContext(tenant_id=T2, is_system_admin=True)
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


def test_defaults_are_left_to_the_service() -> None:
    """Omitted settings stay unset; ``create_project`` fills the deployment defaults (T-0044)."""
    body = _create()
    assert (body.site_timezone, body.currency_code, body.locale) == (None, None, None)
    assert (body.status, body.spatial_bounds) == (None, None)


def test_created_project_gets_the_shipped_defaults(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s:
        project = service.create_project(s, _create())
        assert (project.site_timezone, project.currency_code, project.locale) == (
            "Australia/Perth",
            "AUD",
            "en-AU",
        )
        assert (project.status, project.spatial_bounds) == ("ACTIVE", None)


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


def test_postgis_reads_geojson_with_explicit_full_precision() -> None:
    """``ST_AsGeoJSON`` defaults to 9 decimals; the column asks for the PostGIS maximum (15),
    so a stored position comes back as sent (``app.core.geo.GEOJSON_MAX_DECIMALS``)."""
    from sqlalchemy.dialects import postgresql

    column = Column("spatial_bounds", GeoJSONGeometry("Polygon"))
    compiled = str(select(column).compile(dialect=postgresql.dialect()))
    assert f"ST_AsGeoJSON(spatial_bounds, {GEOJSON_MAX_DECIMALS})" in compiled
    assert GEOJSON_MAX_DECIMALS == 15


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


PRECISE_BOUNDS: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [
        [
            [115.123456789012, -32.987654321098],
            [116.000000000001, -32.987654321098],
            [116.000000000001, -31.5],
            [115.123456789012, -32.987654321098],
        ]
    ],
}


def test_spatial_bounds_round_trip_beyond_nine_decimals(db: Database) -> None:
    """On PostgreSQL the read goes through ``ST_AsGeoJSON`` (default 9 decimals would round
    116.000000000001 to 116); the explicit precision keeps every position as sent."""
    with db.unit_of_work(CTX_T1) as s:
        project_id = service.create_project(s, _create(spatial_bounds=PRECISE_BOUNDS)).id
    with db.unit_of_work(CTX_T1) as s:
        assert service.get_project(s, project_id).spatial_bounds == PRECISE_BOUNDS


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


def test_a_project_bound_session_cannot_update_the_project_row(db: Database) -> None:
    """The row is tenant-level: even a session bound to this very project cannot change it."""
    with db.unit_of_work(CTX_T1) as s:
        project_id = service.create_project(s, _create()).id
    for bound_to in (project_id, uuid.uuid4()):
        project_ctx = TenancyContext(tenant_id=T1, project_id=bound_to)
        with pytest.raises(OutOfScopeError), db.unit_of_work(project_ctx) as s:
            service.update_project(s, project_id, ProjectUpdate(name="Renamed"))
    with db.unit_of_work(CTX_T1) as s:
        project = service.get_project(s, project_id)
        assert (project.name, project.revision) == ("Synthetic plant", 1)


class _Diag:
    def __init__(self, constraint_name: str | None) -> None:
        self.constraint_name = constraint_name


class _PgError(Exception):
    """Stands in for a psycopg ``UniqueViolation``: ``diag.constraint_name`` names the index."""

    def __init__(self, message: str, constraint_name: str | None) -> None:
        super().__init__(message)
        self.diag = _Diag(constraint_name)


@pytest.mark.parametrize(
    ("dialect", "orig", "duplicate"),
    [
        ("postgresql", _PgError("duplicate key", service.CODE_CONSTRAINT), True),
        # Only the constraint name counts on PostgreSQL, never the message text.
        ("postgresql", _PgError('violates "uq_x_code"', "uq_oe_projects_other_code"), False),
        ("postgresql", _PgError("duplicate key", None), False),
        ("postgresql", Exception(f"{service.CODE_CONSTRAINT} without diag"), False),
        (
            "sqlite",
            Exception(f"UNIQUE constraint failed: {TABLE}.tenant_id, {TABLE}.code"),
            True,
        ),
        ("sqlite", Exception(f"NOT NULL constraint failed: {TABLE}.code_name"), False),
        ("sqlite", Exception(f"UNIQUE constraint failed: {TABLE}.id"), False),
    ],
)
def test_duplicate_codes_are_told_by_constraint_name_on_postgresql(
    dialect: str, orig: Exception, duplicate: bool
) -> None:
    assert service.is_duplicate_code(dialect, orig) is duplicate


def test_the_code_constraint_name_is_the_models() -> None:
    names = {c.name for c in Project.__table__.constraints}
    assert service.CODE_CONSTRAINT in names


def test_the_database_fills_status_and_site_timezone_when_a_write_leaves_them_out(
    db: Database,
) -> None:
    """Spec §4.1 server-side defaults (migration 0033): the shipped fallback literals, which
    equal the deployment's shipped defaults (``test_deployment_settings``). A plain table
    clause carries no ORM defaults, so the insert below leaves both columns to the database."""
    columns = (
        "id", "tenant_id", "code", "name", "currency_code", "locale",
        "created_at", "updated_at", "custom_fields", "source", "revision",
    )  # fmt: skip
    project = Project.__table__
    bare = sa_table(TABLE, *(sa_column(name, project.c[name].type) for name in columns))
    project_id = uuid.uuid4()
    now = datetime.now(UTC)
    values = {
        "id": project_id,
        "tenant_id": T1,
        "code": "RAW",
        "name": "Raw insert",
        "currency_code": "AUD",
        "locale": "en-AU",
        "created_at": now,
        "updated_at": now,
        "custom_fields": {},
        "source": "system",
        "revision": 1,
    }
    with db.unit_of_work(CTX_T1) as s:
        s.execute(bare.insert().values(values))
    with db.unit_of_work(CTX_T1) as s:
        project_row = service.get_project(s, project_id)
        assert (project_row.status, project_row.site_timezone) == (
            DEFAULT_STATUS,
            DEFAULT_TIMEZONE,
        )
    assert project.c.status.server_default is not None
    assert project.c.site_timezone.server_default is not None
    assert project.c.currency_code.server_default is None  # tenant settings (TD-185, T-0014)
    assert project.c.locale.server_default is None


def test_migration_0033_freezes_the_models_fallback_literals() -> None:
    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    spec = importlib.util.spec_from_file_location("m0033", next(versions.glob("0033_*.py")))
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.DEFAULTS == {"status": DEFAULT_STATUS, "site_timezone": DEFAULT_TIMEZONE}
    # The SQLite copy is the table at 0032 plus the defaults: the live table without what
    # later revisions added (0038, legal hold: ``test_project_legal_hold``).
    frozen = migration._frozen_project(with_defaults=True)
    live = Project.__table__
    later_columns = {"legal_hold", "legal_hold_reason", "legal_hold_set_by", "legal_hold_set_at"}
    later_constraints = {"ck_oe_projects_project_legal_hold_reason"}
    assert {c.name for c in frozen.columns} == {c.name for c in live.columns} - later_columns
    assert {c.name for c in frozen.constraints} == (
        {c.name for c in live.constraints} - later_constraints
    )
    assert {i.name for i in frozen.indexes} == {i.name for i in live.indexes}


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
        with db.unit_of_work(CTX_T1) as s:  # ALICE is a Project Admin (T-0039)
            admin.grant(s, self.users[ALICE], CREATE_PROJECTS, actor_id=None)

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


def test_creating_needs_the_create_projects_grant(api: Api) -> None:
    """TD-184: PER-09 on a project is not enough; only Project Admins create projects."""
    _grant(api.db, api.users[BOB], api.home, Persona.PROJECT_ADMINISTRATOR)
    body = {"code": "NOPE", "name": "Nope"}
    for email in (BOB, CAROL):  # PER-02 + PER-09 of HOME / no project
        assert api.client.post(PROJECTS, json=body, headers=api.headers(email)).status_code == 403
    with api.db.unit_of_work(CTX_T1) as s:
        assert s.scalars(select(Project).where(Project.code == "NOPE")).first() is None


def test_the_creator_administers_the_new_project_at_once(api: Api) -> None:
    """The creator is PER-09 on the new project, granted in the creating transaction."""
    headers = api.headers(ALICE)
    created = api.client.post(PROJECTS, json={"code": "NEW", "name": "New"}, headers=headers)
    assert created.status_code == 201, created.text
    url = f"{PROJECTS}/{created.json()['id']}"
    assert api.client.get(url, headers=headers).json()["code"] == "NEW"
    renamed = api.client.patch(url, json={"name": "Renamed"}, headers=headers)
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Renamed"
    project = uuid.UUID(created.json()["id"])
    with api.db.unit_of_work(CTX_T1) as s:
        rows = s.scalars(
            select(ProjectMembership).where(ProjectMembership.member_project_id == project)
        ).all()
        assert [(r.user_id, r.persona, r.created_by) for r in rows] == [
            (api.users[ALICE], "PER-09", api.users[ALICE])
        ]
        entry, *_ = chain_entries(s, f"project:{project}")
    assert entry.payload == {"action": "create", "code": "NEW", "creator_grants": ["PER-09"]}


def test_a_failed_creation_grants_nothing(api: Api) -> None:
    duplicate = {"code": "HOME", "name": "Again"}
    assert api.client.post(PROJECTS, json=duplicate, headers=api.headers(ALICE)).status_code == 409
    with api.db.unit_of_work(CTX_T1) as s:
        rows = s.scalars(
            select(ProjectMembership).where(ProjectMembership.user_id == api.users[ALICE])
        ).all()
        assert [r.member_project_id for r in rows] == [api.home]


def test_deleting_a_project_revokes_its_memberships(api: Api) -> None:
    assert (
        api.client.delete(f"{PROJECTS}/{api.home}", headers=api.headers(ALICE)).status_code == 204
    )
    with api.db.unit_of_work(CTX_T1) as s:
        assert s.scalars(select(ProjectMembership)).all() == []
        [*_, entry] = chain_entries(s, f"project:{api.home}")
    assert entry.payload == {
        "action": "delete",
        "code": "HOME",
        "revoked_memberships": sorted([f"{api.users[ALICE]}:PER-09", f"{api.users[BOB]}:PER-02"]),
    }


def test_invalid_settings_are_422_and_a_duplicate_code_is_409(api: Api) -> None:
    headers = api.headers(ALICE)
    bad = {"code": "TZ", "name": "Bad zone", "site_timezone": "Mars/Base"}
    assert api.client.post(PROJECTS, json=bad, headers=headers).status_code == 422
    duplicate = {"code": "HOME", "name": "Again"}
    response = api.client.post(PROJECTS, json=duplicate, headers=headers)
    assert response.status_code == 409
    assert "HOME" in response.json()["detail"]
    second = api.client.post(PROJECTS, json={"code": "SECOND", "name": "Second"}, headers=headers)
    url = f"{PROJECTS}/{second.json()['id']}"  # the creator is PER-09 on it
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


def test_a_non_member_cannot_delete_a_project(api: Api) -> None:
    response = api.client.delete(f"{PROJECTS}/{api.home}", headers=api.headers(CAROL))
    assert response.status_code == 404
    with api.db.unit_of_work(CTX_T1) as s:
        assert service.get_project(s, api.home).deleted_at is None


def test_an_administrator_of_another_project_cannot_touch_this_one(api: Api) -> None:
    """ALICE is PER-09 of HOME (and a Project Admin), not a member of FOREIGN: 404, not 403."""
    with api.db.unit_of_work(CTX_T1) as s:
        foreign = service.create_project(s, _create(code="FOREIGN", name="Foreign")).id
    headers, url = api.headers(ALICE), f"{PROJECTS}/{foreign}"
    assert api.client.patch(url, json={"name": "Hijacked"}, headers=headers).status_code == 404
    assert api.client.delete(url, headers=headers).status_code == 404
    lobby = api.client.put(f"{url}/lobby", json={"visible_in_lobby": True}, headers=headers)
    assert lobby.status_code == 404
    with api.db.unit_of_work(CTX_T1) as s:
        project = service.get_project(s, foreign)
        assert (project.name, project.deleted_at, project.visible_in_lobby) == (
            "Foreign",
            None,
            False,
        )
        assert project.revision == 1


def test_an_administrator_deletes_a_project(api: Api) -> None:
    headers = api.headers(ALICE)
    assert api.client.delete(f"{PROJECTS}/{api.home}", headers=headers).status_code == 204
    assert api.client.get(f"{PROJECTS}/{api.home}", headers=headers).status_code == 404
    assert api.client.delete(f"{PROJECTS}/{api.home}", headers=headers).status_code == 404


# --- bind_project: the one project-binding sequence of every project router (T-0217) ----------


def _bind_case(db: Database) -> tuple[uuid.UUID, uuid.UUID, Any]:
    """A project HOME with a PER-15 member (V + S, no C); returns (project, gone, principal)."""
    from types import SimpleNamespace

    user = seed_user(db, T1, ALICE)
    root = TenancyContext(tenant_id=T1, is_system_admin=True)
    with db.unit_of_work(root) as s:
        home = service.create_project(s, _create(code="HOME")).id
        gone = service.create_project(s, _create(code="GONE")).id
        for project in (home, gone):
            s.add(
                ProjectMembership(
                    tenant_id=T1,
                    user_id=user,
                    member_project_id=project,
                    persona=Persona.CLIENT_REPRESENTATIVE.value,
                )
            )
    with db.unit_of_work(root) as s:
        service.delete_project(s, gone)
    return home, gone, SimpleNamespace(tenant_id=T1, user_id=user)


def _grants(db: Database, principal: Any) -> list[Grant]:
    with db.unit_of_work(TenancyContext(tenant_id=T1, user_id=principal.user_id)) as s:
        return load_grants(s, T1, principal.user_id)


def test_bind_project_binds_a_member_and_checks_the_capability_first(db: Database) -> None:
    home, _gone, principal = _bind_case(db)
    grants = _grants(db, principal)
    base = TenancyContext(tenant_id=T1, user_id=principal.user_id)
    with db.unit_of_work(base) as s:
        access = service.bind_project(s, principal, grants, home, require=Capability.VIEW)
        assert access.personas == {Persona.CLIENT_REPRESENTATIVE}
        bound = session_tenancy(s)
        assert bound is not None
        assert (bound.project_id, bound.user_id) == (home, principal.user_id)
    with db.unit_of_work(base) as s, pytest.raises(HTTPException) as refused:
        service.bind_project(s, principal, grants, home, require=Capability.CREATE)
    assert refused.value.status_code == 403
    with db.unit_of_work(base) as s:  # refused before the session was rebound
        assert session_tenancy(s) == base


def test_bind_project_hides_other_and_deleted_projects(db: Database) -> None:
    _home, gone, principal = _bind_case(db)
    grants = _grants(db, principal)
    base = TenancyContext(tenant_id=T1, user_id=principal.user_id)
    with db.unit_of_work(base) as s, pytest.raises(OutOfScopeError):
        service.bind_project(s, principal, grants, uuid.uuid4())  # not a member
    with db.unit_of_work(base) as s, pytest.raises(OutOfScopeError):
        service.bind_project(s, principal, grants, gone)  # a deleted project
