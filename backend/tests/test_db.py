"""BaseRecordMixin behaviour, portable types and the session unit of work (T-0005)."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import (
    GUID,
    Database,
    JSONType,
    get_db,
    import_all_models,
    include_object,
    utcnow,
)
from app.main import create_app
from tests.db_support import ScratchBase, Widget, database_url

TENANT = uuid.UUID("00000000-0000-4000-8000-000000000001")
PROJECT = uuid.UUID("00000000-0000-4000-8000-0000000000a1")


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(database_url(tmp_path))
    ScratchBase.metadata.create_all(database.engine)
    try:
        yield database
    finally:
        ScratchBase.metadata.drop_all(database.engine)
        database.dispose()


def _add_widget(db: Database, **overrides: object) -> uuid.UUID:
    with db.sessionmaker.begin() as s:
        widget = Widget(tenant_id=TENANT, name="w", **overrides)
        s.add(widget)
        s.flush()
        return widget.id


def _as_utc(value: datetime) -> datetime:
    # SQLite drops the offset on read; PostgreSQL returns an aware value.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def test_insert_fills_contract_defaults(db: Database) -> None:
    widget_id = _add_widget(db, project_id=PROJECT)
    with db.sessionmaker() as s:
        w = s.get_one(Widget, widget_id)
        assert isinstance(w.id, uuid.UUID)
        assert w.tenant_id == TENANT
        assert w.project_id == PROJECT
        assert _as_utc(w.updated_at) >= _as_utc(w.created_at)
        assert w.deleted_at is None
        assert w.custom_fields == {}
        assert w.source == "api"
        assert w.revision == 1
        assert not w.is_deleted


def test_custom_fields_round_trip_json(db: Database) -> None:
    bag = {"insulation": {"thickness_mm": 50, "material": "rockwool"}, "tags": ["a", "b"]}
    widget_id = _add_widget(db, custom_fields=bag)
    with db.sessionmaker() as s:
        assert s.get_one(Widget, widget_id).custom_fields == bag


def test_update_bumps_revision_and_updated_at(db: Database) -> None:
    widget_id = _add_widget(db)
    with db.sessionmaker.begin() as s:
        w = s.get_one(Widget, widget_id)
        first_updated = _as_utc(w.updated_at)
        w.name = "renamed"
    with db.sessionmaker.begin() as s:
        w = s.get_one(Widget, widget_id)
        assert w.revision == 2
        assert _as_utc(w.updated_at) >= first_updated
        w.name = "again"
    with db.sessionmaker() as s:
        assert s.get_one(Widget, widget_id).revision == 3


def test_unmodified_flush_keeps_revision(db: Database) -> None:
    widget_id = _add_widget(db)
    with db.sessionmaker.begin() as s:
        w = s.get_one(Widget, widget_id)
        w.name = "w"  # same value: not a real change
    with db.sessionmaker() as s:
        assert s.get_one(Widget, widget_id).revision == 1


def test_soft_delete_marks_once(db: Database) -> None:
    widget_id = _add_widget(db)
    first = datetime(2026, 1, 1, tzinfo=UTC)
    with db.sessionmaker.begin() as s:
        w = s.get_one(Widget, widget_id)
        w.soft_delete(at=first)
        w.soft_delete(at=datetime(2027, 1, 1, tzinfo=UTC))
        assert w.is_deleted
    with db.sessionmaker() as s:
        deleted_at = s.get_one(Widget, widget_id).deleted_at
        assert deleted_at is not None
        assert _as_utc(deleted_at) == first
        # Soft-deleted rows are still physically present.
        assert s.scalar(select(Widget.id).where(Widget.id == widget_id)) == widget_id


def test_soft_delete_defaults_to_now() -> None:
    w = Widget(tenant_id=TENANT, name="x")
    before = utcnow()
    w.soft_delete()
    assert w.deleted_at is not None
    assert w.deleted_at >= before


@pytest.mark.parametrize("source", ["ui", "api", "import", "sync", "ai", "system"])
def test_known_sources_accepted(db: Database, source: str) -> None:
    widget_id = _add_widget(db, source=source)
    with db.sessionmaker() as s:
        assert s.get_one(Widget, widget_id).source == source


def test_unknown_source_rejected_on_insert(db: Database) -> None:
    with pytest.raises(ValueError, match="source must be one of"):
        _add_widget(db, source="robot")


def test_unknown_source_rejected_on_update(db: Database) -> None:
    widget_id = _add_widget(db)
    with pytest.raises(ValueError, match="source must be one of"), db.sessionmaker.begin() as s:
        s.get_one(Widget, widget_id).source = "robot"


def test_tenant_id_is_mandatory(db: Database) -> None:
    with pytest.raises(IntegrityError), db.sessionmaker.begin() as s:
        s.add(Widget(name="orphan"))


# --- portable types --------------------------------------------------------------------


def test_guid_binds_per_dialect() -> None:
    value = uuid.uuid4()
    guid = GUID()
    assert guid.process_bind_param(value, postgresql.dialect()) == value
    assert guid.process_bind_param(str(value), postgresql.dialect()) == value
    assert guid.process_bind_param(value, sqlite.dialect()) == str(value)
    assert guid.process_bind_param(None, sqlite.dialect()) is None
    assert guid.process_result_value(str(value), sqlite.dialect()) == value
    assert guid.process_result_value(value, postgresql.dialect()) == value
    assert guid.process_result_value(None, sqlite.dialect()) is None


def test_types_compile_natively_on_postgres() -> None:
    pg = postgresql.dialect()
    assert GUID().load_dialect_impl(pg).compile(dialect=pg) == "UUID"
    assert JSONType().load_dialect_impl(pg).compile(dialect=pg) == "JSONB"
    lite = sqlite.dialect()
    assert GUID().load_dialect_impl(lite).compile(dialect=lite) == "CHAR(36)"
    assert JSONType().load_dialect_impl(lite).compile(dialect=lite) == "JSON"


@pytest.mark.parametrize("name", ["spatial_ref_sys", "topology", "layer"])
def test_include_object_skips_db_only_extension_tables(name: str) -> None:
    assert include_object(None, name, "table", True, None) is False


def test_include_object_keeps_everything_else() -> None:
    assert include_object(None, "oe_asset_node", "table", True, None) is True
    # A model table that reuses an extension name is still compared.
    assert include_object(None, "layer", "table", False, None) is True
    assert include_object(None, "layer", "table", True, object()) is True
    assert include_object(None, "layer", "column", True, None) is True


# --- sessions / app wiring -------------------------------------------------------------


def test_session_commits_on_success(db: Database) -> None:
    gen = db.session()
    s = next(gen)
    s.add(Widget(tenant_id=TENANT, name="kept"))
    with pytest.raises(StopIteration):
        next(gen)
    with db.sessionmaker() as check:
        assert check.scalar(select(Widget.name)) == "kept"


def test_session_rolls_back_on_error(db: Database) -> None:
    gen = db.session()
    s = next(gen)
    s.add(Widget(tenant_id=TENANT, name="discarded"))
    s.flush()
    with pytest.raises(RuntimeError, match="boom"):
        gen.throw(RuntimeError("boom"))
    with db.sessionmaker() as check:
        assert check.scalar(select(Widget.name)) is None


def test_from_settings_requires_url() -> None:
    with pytest.raises(RuntimeError, match="KERP_DATABASE_URL"):
        Database.from_settings(Settings(database_url=None))


def test_from_settings_uses_url(tmp_path: Path) -> None:
    database = Database.from_settings(Settings(database_url=database_url(tmp_path)))
    try:
        with database.engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        database.dispose()


def test_get_db_dependency_yields_session(tmp_path: Path) -> None:
    app = create_app(Settings(environment="test", database_url=database_url(tmp_path)))

    @app.get("/_probe")
    def probe(session: Session = Depends(get_db)) -> dict[str, int]:  # noqa: B008
        return {"one": int(session.execute(text("SELECT 1")).scalar_one())}

    with TestClient(app) as client:
        assert client.get("/_probe").json() == {"one": 1}


def test_get_db_without_database_fails_loudly() -> None:
    app: FastAPI = create_app(Settings(environment="test", database_url=None))
    assert app.state.db is None

    @app.get("/_probe")
    def probe(session: Session = Depends(get_db)) -> dict[str, bool]:  # noqa: B008
        return {"ok": True}  # pragma: no cover - never reached

    # The request-context middleware turns the RuntimeError into a logged, generic 500.
    with TestClient(app) as client:
        assert client.get("/_probe").status_code == 500


def test_import_all_models_with_no_models_yet() -> None:
    # No domain module ships models yet; the call must still succeed (Alembic depends on it).
    assert import_all_models() == []


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_import_all_models_imports_module_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = tmp_path / "fakemods"
    _write(pkg / "__init__.py")
    _write(pkg / "alpha" / "__init__.py")
    _write(pkg / "alpha" / "models.py", "LOADED = True\n")
    _write(pkg / "beta" / "__init__.py")  # module without models
    _write(pkg / "loose.py")  # not a package: ignored
    monkeypatch.syspath_prepend(str(tmp_path))
    assert import_all_models("fakemods") == ["fakemods.alpha.models"]


def test_import_all_models_surfaces_broken_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = tmp_path / "brokenmods"
    _write(pkg / "__init__.py")
    _write(pkg / "gamma" / "__init__.py")
    _write(pkg / "gamma" / "models.py", "import does_not_exist_kerp\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ModuleNotFoundError, match="does_not_exist_kerp"):
        import_all_models("brokenmods")
