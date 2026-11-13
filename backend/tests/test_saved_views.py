"""Saved views module: record contract, view shape, service rules, API (T-0020).

Register-views spec §3 acceptance: save + restore round trip, visibility, defaults, a
removed column flagged missing. Runs on SQLite and, in CI, on PostgreSQL under project RLS
against the migrated schema. All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.auth.access import Persona
from app.core.auth.models import ProjectMembership
from app.core.db import Base, Database, contract_violations
from app.core.module_loader.loader import discover_modules
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import RLS_EXEMPT, rls_tables
from app.core.tenancy.scope import OutOfScopeError
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from app.modules.saved_views import service
from app.modules.saved_views.models import TABLE, SavedView
from app.modules.saved_views.schemas import (
    MAX_FILTER_DEPTH,
    SavedViewCreate,
    SavedViewUpdate,
    ViewConfig,
)
from app.modules.saved_views.service import Viewer
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, T1, T2, auth_app, make_settings, seed_user
from tests.db_support import database_url

CTX_T1 = TenancyContext(tenant_id=T1)
CTX_T2 = TenancyContext(tenant_id=T2)
CAROL = "carol@example.test"
DAVE = "dave@example.test"
REGISTER = "asset_management.work_anchor"
OWNER = uuid.UUID("00000000-0000-4000-8000-00000000a001")
OTHER = uuid.UUID("00000000-0000-4000-8000-00000000a002")
ADMIN = uuid.UUID("00000000-0000-4000-8000-00000000a003")
TEAM = uuid.UUID("00000000-0000-4000-8000-00000000b001")
SITE_NODE = "00000000-0000-4000-8000-00000000c001"

ME = Viewer(user_id=OWNER, can_share=True)
PEER = Viewer(user_id=OTHER, can_share=True)
TEAMMATE = Viewer(user_id=OTHER, team_ids=frozenset({TEAM}), can_share=True)
PER09 = Viewer(user_id=ADMIN, can_share=True, is_admin=True)

# A synthetic front-end-planning style view: RSW rows with their Site, budget hours split by
# the Shutdown field, sorted by priority; "Scoped == No" rows in amber.
CONFIG: dict[str, Any] = {
    "columns": [
        {"id": "code", "width": 120, "pinned": "left", "visible": True},
        {"id": "ancestor.site.code", "width": 90, "pinned": None, "visible": True},
        {"id": "custom_fields.budget_hrs", "width": 110, "pinned": None, "visible": True},
        {"id": "custom_fields.shutdown", "width": None, "pinned": None, "visible": False},
    ],
    "filter": {
        "op": "and",
        "conditions": [
            {"column": "custom_fields.budget_hrs", "op": "between", "value": [10, 500]},
            {
                "op": "or",
                "conditions": [
                    {"column": "ancestor.site.code", "op": "in", "value": ["SITE-A", "SITE-B"]},
                    {"column": "status", "op": "is_empty", "value": None},
                ],
            },
        ],
    },
    "sort": [
        {"column": "custom_fields.priority", "direction": "desc"},
        {"column": "code", "direction": "asc"},
    ],
    "group_by": {
        "columns": ["ancestor.asset.code", "custom_fields.shutdown"],
        "aggregates": [{"column": "custom_fields.budget_hrs", "fn": "sum"}],
        "totals": True,
    },
    "conditional_format": [
        {"condition": 'Scoped == "No"', "style": {"tone": "amber", "bold": False}}
    ],
    "density": "compact",
}


def _create(**fields: Any) -> SavedViewCreate:
    values: dict[str, Any] = {"register_key": REGISTER, "name": "FEP - RSW only", **CONFIG}
    values.update(fields)
    return SavedViewCreate.model_validate(values)


def _config(view: dict[str, Any]) -> dict[str, Any]:
    """The saved configuration of a loaded view (without the ``missing`` flags)."""
    config = {key: view[key] for key in CONFIG}
    config["columns"] = [{k: v for k, v in c.items() if k != "missing"} for c in view["columns"]]
    return config


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


def _project(db: Database, code: str, ctx: TenancyContext = CTX_T1) -> TenancyContext:
    with db.unit_of_work(ctx) as s:
        project_id = projects.create_project(
            s, ProjectCreate.model_validate({"code": code, "name": code})
        ).id
    return TenancyContext(tenant_id=ctx.tenant_id, project_id=project_id)


@pytest.fixture
def home(db: Database) -> TenancyContext:
    return _project(db, "HOME")


@pytest.fixture
def catalogue() -> Iterator[set[str]]:
    columns = set(ViewConfig.model_validate(CONFIG).referenced_columns()) | {"name"}
    service.register_catalogue(REGISTER, lambda _session: columns)
    yield columns
    service.register_catalogue(REGISTER, None)


def _save(db: Database, ctx: TenancyContext, viewer: Viewer = ME, **fields: Any) -> uuid.UUID:
    with db.unit_of_work(ctx) as s:
        return service.create_view(s, viewer, _create(**fields)).id


def _load(db: Database, ctx: TenancyContext, view_id: uuid.UUID, viewer: Viewer = ME) -> Any:
    with db.unit_of_work(ctx) as s:
        return service.present(s, service.get_view(s, viewer, view_id))


def _update(
    db: Database, ctx: TenancyContext, view_id: uuid.UUID, viewer: Viewer = ME, **fields: Any
) -> SavedView:
    with db.unit_of_work(ctx) as s:
        return service.update_view(s, viewer, view_id, SavedViewUpdate.model_validate(fields))


def _visible(db: Database, ctx: TenancyContext, viewer: Viewer) -> list[str]:
    with db.unit_of_work(ctx) as s:
        return [v.name for v in service.list_views(s, viewer, REGISTER)]


def _default(db: Database, ctx: TenancyContext, viewer: Viewer) -> str | None:
    with db.unit_of_work(ctx) as s:
        view = service.default_view(s, viewer, REGISTER)
        return None if view is None else view.name


# --- contract -------------------------------------------------------------------------------


def test_view_table_meets_the_record_contract_and_is_under_rls() -> None:
    assert TABLE == "oe_saved_view"
    assert TABLE in Base.metadata.tables
    assert [p for p in contract_violations(Base.metadata) if p.startswith(TABLE)] == []
    assert TABLE in rls_tables(Base.metadata)
    assert TABLE not in RLS_EXEMPT


def test_saved_views_module_is_loaded() -> None:
    manifest = discover_modules().get("saved_views")
    assert manifest.toggleable is False
    assert manifest.depends_on == ("projects",)
    assert manifest.route_prefix == "/saved-views"


# --- view shape -----------------------------------------------------------------------------


def test_the_reference_view_parses_and_lists_every_referenced_column() -> None:
    config = ViewConfig.model_validate(CONFIG)
    assert config.referenced_columns() == [
        "code",
        "ancestor.site.code",
        "custom_fields.budget_hrs",
        "custom_fields.shutdown",
        "status",
        "custom_fields.priority",
        "ancestor.asset.code",
    ]
    assert config.model_dump(mode="json") == CONFIG


def test_an_empty_view_has_sensible_defaults() -> None:
    view = SavedViewCreate.model_validate({"register_key": REGISTER, "name": "All"})
    assert (view.visibility, view.is_default, view.density) == ("private", False, "standard")
    assert (view.columns, view.filter, view.sort, view.group_by) == ([], None, [], None)


@pytest.mark.parametrize(
    "condition",
    [
        {"column": "status", "op": "eq", "value": "OPEN"},
        {"column": "status", "op": "neq", "value": "OPEN"},
        {"column": "code", "op": "contains", "value": "RSW"},
        {"column": "code", "op": "starts_with", "value": "RSW"},
        {"column": "custom_fields.budget_hrs", "op": "gt", "value": 4.5},
        {"column": "custom_fields.budget_hrs", "op": "lt", "value": 40},
        {"column": "custom_fields.shutdown", "op": "eq", "value": True},
        {"column": "status", "op": "not_in", "value": ["CLOSED"]},
        {"column": "due_date", "op": "within_next", "value": "7d"},
        {"column": "due_date", "op": "overdue"},
        {"column": "assignee", "op": "me"},
        {"column": "team", "op": "my_teams"},
        {"column": "node", "op": "subtree_of", "value": SITE_NODE},
    ],
)
def test_every_spec_filter_op_is_accepted(condition: dict[str, Any]) -> None:
    config = ViewConfig.model_validate({"filter": condition})
    assert config.filter is not None
    assert config.filter.model_dump(exclude_none=True) == {
        k: v for k, v in condition.items() if v is not None
    }


@pytest.mark.parametrize(
    "condition",
    [
        {"column": "status", "op": "like", "value": "x"},
        {"column": "status", "op": "eq"},
        {"column": "status", "op": "eq", "value": ["a"]},
        {"column": "status", "op": "eq", "value": {"a": 1}},
        {"column": "status", "op": "in", "value": []},
        {"column": "status", "op": "in", "value": "OPEN"},
        {"column": "status", "op": "in", "value": [["nested"]]},
        {"column": "hrs", "op": "between", "value": [1]},
        {"column": "hrs", "op": "between", "value": [1, 2, 3]},
        {"column": "due", "op": "within_next", "value": "7 days"},
        {"column": "due", "op": "within_next", "value": 7},
        {"column": "due", "op": "overdue", "value": True},
        {"column": "node", "op": "subtree_of", "value": "not-a-uuid"},
        {"column": "node", "op": "subtree_of", "value": 5},
        {"column": "1bad", "op": "eq", "value": "x"},
        {"column": "a; drop table", "op": "eq", "value": "x"},
        {"column": "status", "op": "eq", "value": "x", "sql": "1=1"},
        {"op": "and", "conditions": []},
        {"op": "xor", "conditions": [{"column": "a", "op": "me"}]},
    ],
)
def test_malformed_filters_are_rejected(condition: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ViewConfig.model_validate({"filter": condition})


def test_filters_are_bounded_in_depth_and_size() -> None:
    node: dict[str, Any] = {"column": "a", "op": "me"}
    for _ in range(MAX_FILTER_DEPTH - 1):
        node = {"op": "and", "conditions": [node]}
    ViewConfig.model_validate({"filter": node})
    with pytest.raises(ValidationError, match="deeper"):
        ViewConfig.model_validate({"filter": {"op": "or", "conditions": [node]}})
    wide = {"op": "and", "conditions": [{"column": "a", "op": "me"}] * 50}
    with pytest.raises(ValidationError, match="nodes"):
        ViewConfig.model_validate({"filter": {"op": "or", "conditions": [wide] * 5}})


@pytest.mark.parametrize(
    "fields",
    [
        {"columns": [{"id": "code"}, {"id": "code"}]},
        {"columns": [{"id": "code", "width": 5}]},
        {"columns": [{"id": "code", "pinned": "top"}]},
        {"sort": [{"column": "code"}, {"column": "code", "direction": "desc"}]},
        {"sort": [{"column": "code", "direction": "up"}]},
        {"group_by": {"columns": []}},
        {"group_by": {"columns": ["a", "a"]}},
        {"group_by": {"columns": ["a"], "aggregates": [{"column": "b", "fn": "median"}]}},
        {"conditional_format": [{"condition": "Scoped ==", "style": {"tone": "amber"}}]},
        {"conditional_format": [{"condition": "x", "style": {"tone": "#ff0000"}}]},
        {"conditional_format": [{"condition": "x", "style": {"tone": "red", "css": "x"}}]},
        {"density": "huge"},
        {"schema_version": 2},
        {"tenant_id": str(T2)},
    ],
)
def test_malformed_view_configs_are_rejected(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SavedViewCreate.model_validate({"register_key": REGISTER, "name": "x", **fields})


@pytest.mark.parametrize("key", ["", "A", "Assets", "a b", "x" * 65, "1abc"])
def test_register_keys_are_identifiers(key: str) -> None:
    with pytest.raises(ValidationError):
        SavedViewCreate.model_validate({"register_key": key, "name": "x"})


@pytest.mark.parametrize(
    "fields", [{}, {"name": None}, {"columns": None}, {"visibility": None}, {"register_key": "x"}]
)
def test_bad_updates_are_rejected(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SavedViewUpdate.model_validate(fields)


# --- save + restore -------------------------------------------------------------------------


def test_save_and_restore_reproduces_the_register_state(
    db: Database, home: TenancyContext, catalogue: set[str]
) -> None:
    view_id = _save(db, home)
    loaded = _load(db, home, view_id).model_dump(mode="json")
    assert _config(loaded) == CONFIG  # columns + widths, filter, sort, group-by + sum
    assert loaded["register_key"] == REGISTER
    assert loaded["owner_user_id"] == str(OWNER)
    assert loaded["project_id"] == str(home.project_id)
    assert (loaded["visibility"], loaded["is_default"], loaded["schema_version"]) == (
        "private",
        False,
        1,
    )
    assert loaded["missing_columns"] == []
    assert not any(c["missing"] for c in loaded["columns"])


def test_a_removed_column_still_loads_flagged_missing(
    db: Database, home: TenancyContext, catalogue: set[str]
) -> None:
    view_id = _save(db, home)
    # The Shutdown field def and the Site ancestor column go away; Status too (filter only).
    catalogue -= {"custom_fields.shutdown", "ancestor.site.code", "status"}
    loaded = _load(db, home, view_id)
    assert loaded.missing_columns == ["ancestor.site.code", "custom_fields.shutdown", "status"]
    assert [(c.id, c.missing) for c in loaded.columns] == [
        ("code", False),
        ("ancestor.site.code", True),
        ("custom_fields.budget_hrs", False),
        ("custom_fields.shutdown", True),
    ]
    assert _config(loaded.model_dump(mode="json"))["filter"] == CONFIG["filter"]  # kept as saved


def test_without_a_catalogue_nothing_is_flagged(db: Database, home: TenancyContext) -> None:
    assert _load(db, home, _save(db, home)).missing_columns == []


def test_edit_merges_into_the_saved_view(db: Database, home: TenancyContext) -> None:
    view_id = _save(db, home)
    columns = [{"id": "name", "width": 300}, *CONFIG["columns"][:2]]
    _update(db, home, view_id, name="Renamed", columns=columns, filter=None, density="comfortable")
    loaded = _load(db, home, view_id)
    assert loaded.name == "Renamed"
    assert [c.id for c in loaded.columns] == ["name", "code", "ancestor.site.code"]
    assert (loaded.filter, loaded.density, loaded.revision) == (None, "comfortable", 2)
    assert loaded.model_dump(mode="json")["sort"] == CONFIG["sort"]  # untouched


def test_an_edit_that_breaks_the_view_is_refused(db: Database, home: TenancyContext) -> None:
    view_id = _save(db, home)
    with pytest.raises(service.InvalidViewError):
        _update(db, home, view_id, columns=[{"id": "a"}, {"id": "a"}])
    with pytest.raises(service.InvalidViewError, match="default"):
        _update(db, home, view_id, visibility="portal", is_default=True)
    assert _load(db, home, view_id).name == "FEP - RSW only"


def test_delete_hides_the_view(db: Database, home: TenancyContext) -> None:
    view_id = _save(db, home)
    with db.unit_of_work(home) as s:
        service.delete_view(s, ME, view_id)
    with pytest.raises(OutOfScopeError):
        _load(db, home, view_id)
    assert _visible(db, home, ME) == []


# --- scope and visibility -------------------------------------------------------------------


def test_views_are_per_project_and_per_register(db: Database, home: TenancyContext) -> None:
    other = _project(db, "OTHER")
    view_id = _save(db, home, visibility="project")
    _save(db, home, name="Scaffolds", register_key="scaffolds.tag")
    _save(db, other, name="Elsewhere", visibility="project")
    assert _visible(db, home, PEER) == ["FEP - RSW only"]
    assert _visible(db, other, PEER) == ["Elsewhere"]
    with pytest.raises(OutOfScopeError):
        _load(db, other, view_id, PEER)


def test_another_tenant_never_sees_a_view(db: Database, home: TenancyContext) -> None:
    view_id = _save(db, home, visibility="project")
    elsewhere = _project(db, "HOME", CTX_T2)
    stranger = TenancyContext(tenant_id=T2, project_id=home.project_id)
    for ctx in (elsewhere, stranger):
        with pytest.raises(OutOfScopeError):
            _load(db, ctx, view_id, PEER)


def test_visibility_private_team_project_portal(db: Database, home: TenancyContext) -> None:
    _save(db, home, name="1 private")
    _save(db, home, PER09, name="2 team", visibility="team", team_id=str(TEAM))
    _save(db, home, name="3 project", visibility="project")
    _save(db, home, name="4 portal", visibility="portal")
    assert _visible(db, home, ME) == ["1 private", "3 project", "4 portal"]
    assert _visible(db, home, PEER) == ["3 project"]
    assert _visible(db, home, TEAMMATE) == ["2 team", "3 project"]
    assert _visible(db, home, PER09) == ["2 team", "3 project", "4 portal"]


def test_sharing_rules(db: Database, home: TenancyContext) -> None:
    reader = Viewer(user_id=OTHER)  # view-only member (e.g. a client representative)
    _save(db, home, reader, name="mine")
    for visibility in ("project", "portal"):
        with pytest.raises(service.ViewPermissionError, match="share"):
            _save(db, home, reader, visibility=visibility)
    with pytest.raises(service.ViewPermissionError, match="team you belong to"):
        _save(db, home, ME, visibility="team", team_id=str(TEAM))
    _save(db, home, TEAMMATE, name="team", visibility="team", team_id=str(TEAM))
    with pytest.raises(service.InvalidViewError, match="team_id"):
        _save(db, home, PER09, visibility="team")
    with pytest.raises(service.InvalidViewError, match="team_id"):
        _save(db, home, PER09, visibility="project", team_id=str(TEAM))
    with pytest.raises(service.InvalidViewError, match="default"):
        _save(db, home, PER09, visibility="portal", is_default=True)


def test_shared_views_are_edited_by_their_owner_or_a_project_administrator(
    db: Database, home: TenancyContext
) -> None:
    view_id = _save(db, home, visibility="project")
    with pytest.raises(service.ViewPermissionError):
        _update(db, home, view_id, PEER, name="Hijacked")
    with db.unit_of_work(home) as s, pytest.raises(service.ViewPermissionError):
        service.delete_view(s, PEER, view_id)
    _update(db, home, view_id, PER09, name="Tidied by PER-09")
    _update(db, home, view_id, ME, name="Owner's again", visibility="private")
    with pytest.raises(OutOfScopeError):  # now private: gone for everyone else
        _update(db, home, view_id, PER09, name="x")


# --- defaults -------------------------------------------------------------------------------


def test_one_default_per_user_and_one_project_default(db: Database, home: TenancyContext) -> None:
    assert _default(db, home, ME) is None
    _save(db, home, PER09, name="Project A", visibility="project", is_default=True)
    assert _default(db, home, ME) == "Project A"
    project_b = _save(db, home, PER09, name="Project B", visibility="project")
    _update(db, home, project_b, PER09, is_default=True)
    assert _default(db, home, PEER) == "Project B"  # A is no longer the default

    _save(db, home, name="Mine 1", is_default=True)
    _save(db, home, name="Mine 2", is_default=True)
    _save(db, home, PEER, name="Theirs", is_default=True)
    assert _default(db, home, ME) == "Mine 2"
    assert _default(db, home, PEER) == "Theirs"
    assert _default(db, home, PER09) == "Project B"
    with db.unit_of_work(home) as s:
        defaults = [v.name for v in service.list_views(s, PER09, REGISTER) if v.is_default]
    assert defaults == ["Project B"]


def _defaults(db: Database, ctx: TenancyContext) -> dict[str, tuple[str, bool]]:
    with db.unit_of_work(ctx) as s:
        views = s.query(SavedView).filter(SavedView.register_key == REGISTER).all()
        return {v.name: (v.visibility, v.is_default) for v in views}


def test_an_admin_sharing_a_personal_default_leaves_the_project_default_alone(
    db: Database, home: TenancyContext
) -> None:
    _save(db, home, PER09, name="Project", visibility="project", is_default=True)
    mine = _save(db, home, PER09, name="Admin's own", is_default=True)
    shared = _update(db, home, mine, PER09, visibility="project")
    assert (shared.visibility, shared.is_default) == ("project", False)
    assert _defaults(db, home) == {
        "Project": ("project", True),
        "Admin's own": ("project", False),
    }
    assert _default(db, home, PEER) == "Project"


@pytest.mark.parametrize(
    ("visibility", "team"), [("project", None), ("portal", None), ("team", str(TEAM))]
)
def test_a_member_shares_a_personal_default_as_a_plain_view(
    db: Database, home: TenancyContext, visibility: str, team: str | None
) -> None:
    mine = _save(db, home, TEAMMATE, name="Mine", is_default=True)
    fields: dict[str, Any] = {"visibility": visibility}
    if team is not None:
        fields["team_id"] = team
    shared = _update(db, home, mine, TEAMMATE, **fields)
    assert (shared.visibility, shared.is_default) == (visibility, False)
    assert _default(db, home, TEAMMATE) is None


def test_an_admin_unsharing_the_project_default_leaves_the_owners_default_alone(
    db: Database, home: TenancyContext
) -> None:
    project = _save(db, home, PER09, name="Project", visibility="project", is_default=True)
    _save(db, home, PER09, name="Admin's own", is_default=True)
    unshared = _update(db, home, project, PER09, visibility="private")
    assert (unshared.visibility, unshared.is_default) == ("private", False)
    assert _defaults(db, home) == {
        "Project": ("private", False),
        "Admin's own": ("private", True),
    }
    assert _default(db, home, PER09) == "Admin's own"
    assert _default(db, home, PEER) is None


def test_an_explicit_default_with_a_visibility_change_is_still_checked(
    db: Database, home: TenancyContext
) -> None:
    mine = _save(db, home, ME, name="Mine", is_default=True)
    with pytest.raises(service.ViewPermissionError, match="Administrator"):
        _update(db, home, mine, ME, visibility="project", is_default=True)
    with pytest.raises(service.InvalidViewError, match="default"):
        _update(db, home, mine, ME, visibility="portal", is_default=True)
    admin_own = _save(db, home, PER09, name="Admin's own", is_default=True)
    made = _update(db, home, admin_own, PER09, visibility="project", is_default=True)
    assert made.is_project_default


def test_only_a_project_administrator_changes_the_project_default(
    db: Database, home: TenancyContext
) -> None:
    with pytest.raises(service.ViewPermissionError, match="Administrator"):
        _save(db, home, ME, visibility="project", is_default=True)
    mine = _save(db, home, ME, name="Mine", visibility="project")
    with pytest.raises(service.ViewPermissionError, match="Administrator"):
        _update(db, home, mine, ME, is_default=True)
    _update(db, home, mine, PER09, is_default=True)
    # The owner may still edit it, but not quietly stop it being the project default.
    _update(db, home, mine, ME, density="comfortable")
    with pytest.raises(service.ViewPermissionError, match="Administrator"):
        _update(db, home, mine, ME, visibility="private")
    with db.unit_of_work(home) as s, pytest.raises(service.ViewPermissionError):
        service.delete_view(s, ME, mine)
    with db.unit_of_work(home) as s:
        service.delete_view(s, PER09, mine)
    assert _default(db, home, ME) is None


def test_the_service_needs_a_project_session(db: Database) -> None:
    with db.unit_of_work(CTX_T1) as s, pytest.raises(RuntimeError, match="bound to a project"):
        service.list_views(s, ME, REGISTER)


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
        self.users = {email: seed_user(db, T1, email) for email in (ALICE, BOB, CAROL, DAVE)}
        self.home = _project(db, "HOME")
        assert self.home.project_id is not None
        _grant(db, self.users[ALICE], self.home.project_id, Persona.FIELD_WORKER)
        _grant(db, self.users[BOB], self.home.project_id, Persona.CLIENT_REPRESENTATIVE)
        _grant(db, self.users[DAVE], self.home.project_id, Persona.PROJECT_ADMINISTRATOR)

    def headers(self, email: str) -> dict[str, str]:
        body = {"email": email, "password": PASSWORD}
        response = self.client.post("/api/v1/auth/login", json=body)
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def views(self) -> str:
        return f"/api/v1/saved-views/projects/{self.home.project_id}/views"


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    settings = make_settings(database_url=database_url(tmp_path))
    app, _runtime = auth_app(settings)
    with TestClient(app) as client:
        yield Api(client, db)


def _body(**fields: Any) -> dict[str, Any]:
    return {"register_key": REGISTER, "name": "FEP - RSW only", **CONFIG, **fields}


def test_the_api_round_trip(api: Api, catalogue: set[str]) -> None:
    alice = api.headers(ALICE)
    created = api.client.post(api.views(), json=_body(is_default=True), headers=alice)
    assert created.status_code == 201, created.text
    view = created.json()
    assert _config(view) == CONFIG
    url = f"{api.views()}/{view['id']}"
    params = {"register_key": REGISTER}

    assert _config(api.client.get(url, headers=alice).json()) == CONFIG
    listed = api.client.get(api.views(), params=params, headers=alice).json()
    assert [v["id"] for v in listed] == [view["id"]]
    default = api.client.get(f"{api.views()}/default", params=params, headers=alice)
    assert default.json()["id"] == view["id"]

    catalogue.discard("custom_fields.shutdown")
    patched = api.client.patch(url, json={"name": "Renamed"}, headers=alice)
    assert patched.status_code == 200, patched.text
    assert (patched.json()["name"], patched.json()["missing_columns"]) == (
        "Renamed",
        ["custom_fields.shutdown"],
    )

    assert api.client.delete(url, headers=alice).status_code == 204
    assert api.client.get(url, headers=alice).status_code == 404
    default = api.client.get(f"{api.views()}/default", params=params, headers=alice)
    assert (default.status_code, default.json()) == (200, None)


def test_the_api_applies_membership_and_sharing_rules(api: Api) -> None:
    alice, bob, carol, dave = (api.headers(e) for e in (ALICE, BOB, CAROL, DAVE))
    private = api.client.post(api.views(), json=_body(), headers=alice).json()
    shared = api.client.post(api.views(), json=_body(visibility="project"), headers=alice)
    assert shared.status_code == 201
    params = {"register_key": REGISTER}
    # Bob (client representative, view only) sees the project view, saves private views,
    # cannot share or edit Alice's view, and never sees her private one.
    assert [v["visibility"] for v in api.client.get(api.views(), params=params, headers=bob).json()]
    assert api.client.post(api.views(), json=_body(), headers=bob).status_code == 201
    refused = api.client.post(api.views(), json=_body(visibility="project"), headers=bob)
    assert refused.status_code == 403
    shared_url = f"{api.views()}/{shared.json()['id']}"
    assert api.client.patch(shared_url, json={"name": "x"}, headers=bob).status_code == 403
    assert api.client.delete(shared_url, headers=bob).status_code == 403
    assert api.client.get(f"{api.views()}/{private['id']}", headers=bob).status_code == 404
    # Dave (Project Administrator) makes Alice's shared view the project default.
    made = api.client.patch(shared_url, json={"is_default": True}, headers=dave)
    assert made.status_code == 200
    default = api.client.get(f"{api.views()}/default", params=params, headers=bob).json()
    assert default["id"] == shared.json()["id"]
    # Carol is no member: the project does not exist for her. No token: 401.
    assert api.client.get(api.views(), params=params, headers=carol).status_code == 404
    assert api.client.post(api.views(), json=_body(), headers=carol).status_code == 404
    assert api.client.get(api.views(), params=params).status_code == 401


def test_the_api_rejects_bad_requests(api: Api) -> None:
    alice, dave = api.headers(ALICE), api.headers(DAVE)
    bad_filter = _body(filter={"column": "status", "op": "like", "value": "x"})
    assert api.client.post(api.views(), json=bad_filter, headers=alice).status_code == 422
    no_team = _body(visibility="team")
    assert api.client.post(api.views(), json=no_team, headers=dave).status_code == 422
    assert api.client.get(api.views(), headers=alice).status_code == 422  # register_key needed
    url = f"{api.views()}/{api.client.post(api.views(), json=_body(), headers=alice).json()['id']}"
    duplicate = {"sort": [{"column": "code"}, {"column": "code"}]}
    assert api.client.patch(url, json=duplicate, headers=alice).status_code == 422
    portal_default = {"visibility": "portal", "is_default": True}
    assert api.client.patch(url, json=portal_default, headers=alice).status_code == 422
    assert api.client.get(f"{api.views()}/{uuid.uuid4()}", headers=alice).status_code == 404


def test_a_deleted_project_is_404(api: Api) -> None:
    alice = api.headers(ALICE)
    with api.db.unit_of_work(CTX_T1) as s:
        projects.delete_project(s, api.home.project_id)  # type: ignore[arg-type]
    assert api.client.post(api.views(), json=_body(), headers=alice).status_code == 404
