"""Asset hierarchy: level defs, the node tree, anchor invariant, code uniqueness, move (T-0101).

Runs on SQLite and, in CI, on PostgreSQL (ltree, project RLS) against the migrated schema;
the PostgreSQL-only behaviour is in ``test_asset_management_pg.py``. Two breakdown templates
sit side by side in one project: remediation Site/Asset/RSW and CUI Scope/Job Package/Line.
All data is synthetic.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.auth.access import Persona
from app.core.auth.models import ProjectMembership
from app.core.db import Base, Database, contract_violations
from app.core.module_loader.loader import discover_modules
from app.core.tenancy.context import TenancyContext
from app.core.tenancy.rls import rls_tables
from app.core.tenancy.scope import OutOfScopeError
from app.modules.asset_management import service
from app.modules.asset_management.models import (
    LEVEL_TABLE,
    NODE_TABLE,
    ROLES,
    AssetLevelDef,
    AssetNode,
)
from app.modules.asset_management.schemas import LevelDefCreate, LevelDefUpdate, NodeCreate
from app.modules.asset_management.tree import contains, rebased, within
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectCreate
from tests.audit_support import migrated_database
from tests.auth_support import ALICE, BOB, PASSWORD, T1, auth_app, make_settings, seed_user
from tests.db_support import database_url

CTX_T1 = TenancyContext(tenant_id=T1, is_system_admin=True)
ANCHOR = ["work_anchor", "inspection_anchor"]
# (key, depth, allowed parents, roles, code uniqueness)
PACKS: list[tuple[str, int, list[str], list[str], str]] = [
    ("site", 0, [], ["grouping", "physical"], "per_parent"),
    ("asset", 1, ["site"], ["physical", "inspection_anchor"], "per_parent"),
    ("rsw", 2, ["asset"], ANCHOR, "per_project"),
    ("scope", 0, [], ["grouping"], "per_parent"),
    ("job_package", 1, ["scope"], ["grouping"], "per_level"),
    ("line", 2, ["job_package"], [*ANCHOR, "progress_unit"], "per_level"),
    # Not from a pack: levels that may sit above or below a work anchor (invariant tests).
    ("package", 3, ["asset", "rsw"], ["grouping"], "per_parent"),
    ("task", 4, ["package"], ["work_anchor"], "per_parent"),
]


def project(db: Database, code: str) -> TenancyContext:
    with db.unit_of_work(CTX_T1) as s:
        created = projects.create_project(
            s, ProjectCreate.model_validate({"code": code, "name": code})
        )
        return TenancyContext(tenant_id=T1, project_id=created.id)


def seed_levels(s: Session) -> dict[str, uuid.UUID]:
    ids = {}
    for key, depth, parents, roles, uniqueness in PACKS:
        body = {
            "key": key,
            "depth": depth,
            "allowed_parent_keys": parents,
            "label_key": f"level.{key}",
            "roles": roles,
            "code_uniqueness": uniqueness,
        }
        ids[key] = service.create_level_def(s, LevelDefCreate.model_validate(body)).id
    return ids


class Tree:
    """Builds nodes by level key in one session."""

    def __init__(self, session: Session, levels: dict[str, uuid.UUID]) -> None:
        self.s, self.levels = session, levels

    def add(
        self, level: str, code: str, parent: AssetNode | None = None, **fields: Any
    ) -> AssetNode:
        body = {"level_def_id": self.levels[level], "code": code, **fields}
        body["parent_id"] = parent.id if parent else None
        return service.create_node(self.s, NodeCreate.model_validate(body))

    def remediation(self) -> tuple[AssetNode, AssetNode, AssetNode]:
        site = self.add("site", "SI-001")
        asset = self.add("asset", "101", site)
        return site, asset, self.add("rsw", "101.1", asset)

    def cui(self) -> tuple[AssetNode, AssetNode, AssetNode]:
        scope = self.add("scope", "CUI-2026")
        pack = self.add("job_package", "JP-01", scope)
        return scope, pack, self.add("line", "L-100-XX001", pack)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def home(db: Database) -> TenancyContext:
    return project(db, "HOME")


@pytest.fixture
def tree(db: Database, home: TenancyContext) -> Iterator[Tree]:
    with db.unit_of_work(home) as s:
        yield Tree(s, seed_levels(s))


# --- contract ---------------------------------------------------------------------------------


def test_tables_meet_the_record_contract_and_are_under_rls() -> None:
    assert [p for p in contract_violations(Base.metadata) if "oe_asset" in p] == []
    assert {LEVEL_TABLE, NODE_TABLE} <= set(rls_tables(Base.metadata))
    for name in (LEVEL_TABLE, NODE_TABLE):
        assert Base.metadata.tables[name].c.project_id.index
    assert discover_modules().get("asset_management").toggleable is False


def test_no_boq_anchor_anywhere() -> None:
    for name in (LEVEL_TABLE, NODE_TABLE):
        table = Base.metadata.tables[name]
        assert [c.name for c in table.columns if "boq" in c.name.lower()] == []
        targets = {fk.column.table.name for fk in table.foreign_keys}
        assert targets <= {"tenants", LEVEL_TABLE, NODE_TABLE}


def test_path_expressions_compile_to_ltree_operators_on_postgresql() -> None:
    column = AssetNode.__table__.c.tree_path

    def sql(expression: Any) -> str:
        return str(expression.compile(dialect=postgresql.dialect()))

    assert "<@ CAST(" in sql(within(column, "a.b"))
    assert "@> CAST(" in sql(contains(column, "a.b"))
    assert "subpath(oe_asset_node.tree_path, nlevel(" in sql(rebased(column, "a", "b"))
    assert (
        postgresql.dialect().type_compiler.process(column.type.dialect_impl(postgresql.dialect()))
        == "ltree"
    )


# --- level defs -------------------------------------------------------------------------------


def test_level_defs_hold_the_template(tree: Tree) -> None:
    levels = service.list_level_defs(tree.s)
    assert [level.key for level in levels][:3] == ["scope", "site", "asset"]
    rsw = next(level for level in levels if level.key == "rsw")
    assert (rsw.depth, rsw.allowed_parent_keys, rsw.code_uniqueness) == (
        2,
        ["asset"],
        "per_project",
    )
    assert rsw.roles == ["inspection_anchor", "work_anchor"]
    assert rsw.label_key == "level.rsw"


def test_roles_are_limited_to_the_frozen_set() -> None:
    body = {"key": "x", "depth": 0, "label_key": "level.x"}
    assert LevelDefCreate.model_validate({**body, "roles": list(ROLES)}).roles == set(ROLES)
    with pytest.raises(ValidationError):
        LevelDefCreate.model_validate({**body, "roles": ["boq_anchor"]})
    with pytest.raises(ValidationError):
        LevelDefCreate.model_validate({**body, "key": "Bad Key"})


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"key": "site"}, service.DuplicateCodeError),
        ({"allowed_parent_keys": ["nowhere"]}, service.TreeRuleError),
        ({"allowed_parent_keys": ["task"]}, service.TreeRuleError),  # not above depth 1
        ({"depth": 0}, service.TreeRuleError),  # depth 0 with a parent
        ({"allowed_parent_keys": []}, service.TreeRuleError),  # no parent below depth 0
        ({"code_pattern": "("}, service.TreeRuleError),
        ({"display_code_template": "{site} / {Bad}"}, service.TreeRuleError),
    ],
)
def test_a_level_must_fit_the_template(
    tree: Tree, change: dict[str, Any], error: type[Exception]
) -> None:
    body = {"key": "zone", "depth": 1, "allowed_parent_keys": ["site"], "label_key": "level.zone"}
    with pytest.raises(error):
        service.create_level_def(tree.s, LevelDefCreate.model_validate({**body, **change}))


def test_a_level_in_use_is_never_rekeyed_and_changes_additively(tree: Tree) -> None:
    def update(level: str, **changes: Any) -> Any:
        return service.update_level_def(
            tree.s, tree.levels[level], LevelDefUpdate.model_validate(changes)
        )

    # Unused: the key may change, unless another level names it as a parent.
    assert update("task", key="job").key == "job"
    with pytest.raises(service.LevelInUseError, match="name 'package' as a parent"):
        update("package", key="pack")
    with pytest.raises(service.DuplicateCodeError):
        update("task", key="site")
    with pytest.raises(OutOfScopeError):
        service.update_level_def(tree.s, uuid.uuid4(), LevelDefUpdate(depth=1))

    tree.remediation()
    for frozen in (
        {"key": "work_scope"},
        {"depth": 3},
        {"code_uniqueness": "per_parent"},
        {"roles": ["inspection_anchor"]},  # drops work_anchor
        {"allowed_parent_keys": ["site"]},  # drops asset
    ):
        with pytest.raises(service.LevelInUseError):
            update("rsw", **frozen)
    with pytest.raises(service.LevelInUseError):
        update("asset", roles=["work_anchor"])  # would nest the existing RSW
    changed = update("rsw", roles=[*ANCHOR, "schedule_link"], label_key="level.work_scope")
    assert (changed.key, changed.roles[-1], changed.label_key) == (
        "rsw",
        "work_anchor",
        "level.work_scope",
    )
    assert update("asset", allowed_parent_keys=["site"]).revision == 1  # nothing changed
    # A refused change leaves nothing pending: the level is as it was after the next flush.
    with pytest.raises(service.TreeRuleError):
        update("scope", depth=1)
    tree.s.flush()
    assert (tree.s.get(AssetLevelDef, tree.levels["scope"]).depth, len(tree.s.dirty)) == (0, 0)
    entries = audit.chain_entries(tree.s, f"asset_level_def:{tree.levels['rsw']}")
    assert [e.payload["action"] for e in entries] == ["create", "update"]


# --- nodes ------------------------------------------------------------------------------------


def test_nodes_form_one_tree_with_paths_and_breadcrumbs(tree: Tree) -> None:
    site, asset, rsw = tree.remediation()
    assert site.tree_path == site.id.hex
    assert rsw.tree_path == f"{site.id.hex}.{asset.id.hex}.{rsw.id.hex}"
    assert rsw.parent_id == asset.id
    assert rsw.display_path == "SI-001 / 101 / 101.1"
    assert (rsw.code, rsw.display_code, rsw.status) == ("101.1", "101.1", "active")
    assert (rsw.superseded_by, rsw.derived_from) == ([], [])
    assert [n.code for n in service.list_nodes(tree.s)] == ["SI-001"]
    assert [n.code for n in service.list_nodes(tree.s, parent_id=site.id)] == ["101"]
    assert [n.code for n in service.list_nodes(tree.s, subtree_of=site.id)] == [
        "SI-001",
        "101",
        "101.1",
    ]
    assert [n.code for n in service.list_nodes(tree.s, subtree_of=asset.id, limit=1, offset=1)] == [
        "101.1"
    ]
    assert service.get_node(tree.s, rsw.id) is rsw
    created = audit.chain_entries(tree.s, f"asset_node:{rsw.id}")[0].payload
    assert (created["action"], created["tree_path"]) == ("create", rsw.tree_path)


def test_the_parent_level_must_be_allowed(tree: Tree) -> None:
    site, asset, rsw = tree.remediation()
    scope, pack, _line = tree.cui()
    for level, parent in (("rsw", site), ("asset", None), ("line", asset), ("site", site)):
        with pytest.raises(service.TreeRuleError):
            tree.add(level, "X-1", parent)
    with pytest.raises(service.TreeRuleError, match="unknown parent_id"):
        service.create_node(
            tree.s, NodeCreate(level_def_id=tree.levels["asset"], parent_id=uuid.uuid4(), code="X")
        )
    with pytest.raises(service.TreeRuleError, match="unknown level_def_id"):
        service.create_node(tree.s, NodeCreate(level_def_id=uuid.uuid4(), code="X"))
    for node, target in ((rsw, site), (rsw, pack), (asset, None), (pack, site), (asset, rsw)):
        with pytest.raises(service.TreeRuleError):
            service.move_node(tree.s, node.id, target.id if target else None)
    assert scope.parent_id is None


def test_codes_are_text_and_follow_the_level_pattern(tree: Tree) -> None:
    site = tree.add("site", "SI-001")
    for code in ("101.1", "007", "1e5", "L-100-XX001-1AA1-C1 C1EA"):
        assert tree.add("asset", code, site).code == code
    for bad in ("", " 101", "101 ", "a\tb", "x" * 201):
        with pytest.raises(ValidationError):
            NodeCreate(level_def_id=tree.levels["asset"], code=bad)
    service.update_level_def(tree.s, tree.levels["site"], LevelDefUpdate(code_pattern=r"SI-\d{3}"))
    assert tree.add("site", "SI-002").code == "SI-002"
    for bad in ("SI-2", "XSI-002", "SI-0029"):
        with pytest.raises(service.TreeRuleError, match="code_pattern"):
            tree.add("site", bad)


def test_budget_fields_and_location(tree: Tree) -> None:
    scope, pack, _line = tree.cui()
    point = {"type": "Point", "coordinates": [115.8605, -31.9505, 12.5]}
    line = tree.add(
        "line",
        "L-200",
        pack,
        budget_qty="120.5",
        uom="m",
        budget_hours="16",
        name="Line 200",
        spatial_location=point,
        custom_fields={"priority": 2},
    )
    tree.s.expire(line)
    assert (str(line.budget_qty), line.uom, line.earned_qty) == ("120.5000", "m", None)
    assert (line.spatial_location, line.custom_fields) == (point, {"priority": 2})
    with pytest.raises(service.TreeRuleError, match="progress_unit"):
        tree.add("job_package", "JP-02", scope, uom="m")
    for bad in ([200.0, 0.0, 1.0], [0.0, 91.0, 1.0], [1.0, 2.0]):
        with pytest.raises(ValidationError):
            NodeCreate.model_validate(
                {
                    "level_def_id": str(uuid.uuid4()),
                    "code": "X",
                    "spatial_location": {"type": "Point", "coordinates": bad},
                }
            )


# --- the anchor invariant ---------------------------------------------------------------------


def test_at_most_one_work_anchor_per_path_never_nested(tree: Tree) -> None:
    site, asset, rsw = tree.remediation()
    _scope, pack, line = tree.cui()
    # Side by side: sibling work anchors are separate paths, in both templates.
    tree.add("rsw", "101.2", asset)
    tree.add("line", "L-101", pack)
    # A path without a work anchor is allowed (TD-113): an asset with nothing below it.
    bare = tree.add("asset", "102", site)
    loose = tree.add("package", "PK-1", bare)
    task = tree.add("task", "T-1", loose)  # the anchor of the 102 path
    # Never nested on create: under a work anchor, directly or deeper.
    under_rsw = tree.add("package", "PK-2", rsw)
    with pytest.raises(service.TreeRuleError, match="never nested"):
        tree.add("task", "T-2", under_rsw)
    # Never nested on move: a subtree holding an anchor cannot go under one...
    with pytest.raises(service.TreeRuleError, match="never nested"):
        service.move_node(tree.s, loose.id, rsw.id)
    # ...but a subtree without one can, and an anchored subtree can move between free paths.
    other = tree.add("asset", "103", site)
    assert service.move_node(tree.s, under_rsw.id, other.id).parent_id == other.id
    assert service.move_node(tree.s, loose.id, other.id).parent_id == other.id
    assert service.work_anchor_for(tree.s, task.id).id == task.id
    assert line.parent_id == pack.id


def test_work_anchor_for_resolves_the_anchor_of_the_path_in_both_packs(tree: Tree) -> None:
    site, asset, rsw = tree.remediation()
    scope, pack, line = tree.cui()
    below = tree.add("package", "PK-1", rsw)
    assert service.work_anchor_for(tree.s, rsw.id).id == rsw.id  # self
    assert service.work_anchor_for(tree.s, below.id).id == rsw.id  # an ancestor
    assert service.work_anchor_for(tree.s, line.id).id == line.id
    # Ancestor-or-self only: a node above the anchors has none (the user picks one first).
    for node in (site, asset, scope, pack):
        with pytest.raises(service.NoWorkAnchor):
            service.work_anchor_for(tree.s, node.id)
    with pytest.raises(OutOfScopeError):
        service.work_anchor_for(tree.s, uuid.uuid4())


# --- code uniqueness --------------------------------------------------------------------------


def test_code_uniqueness_follows_the_level(tree: Tree) -> None:
    site, asset, _rsw = tree.remediation()
    scope, _pack, _line = tree.cui()
    site_b = tree.add("site", "SI-002")
    asset_b = tree.add("asset", "101", site_b)  # per_parent: free under another parent
    with pytest.raises(service.DuplicateCodeError):
        tree.add("asset", "101", site)
    with pytest.raises(service.DuplicateCodeError):
        tree.add("site", "SI-001")  # per_parent among the roots
    pack_b = tree.add("job_package", "JP-02", scope)
    with pytest.raises(service.DuplicateCodeError):
        tree.add("line", "L-100-XX001", pack_b)  # per_level: taken under another parent
    tree.add("package", "L-100-XX001", asset)  # ...but free on another level
    with pytest.raises(service.DuplicateCodeError):
        tree.add("rsw", "101.1", asset_b)  # per_project: taken anywhere
    # per_project is unique across levels, both ways.
    with pytest.raises(service.DuplicateCodeError):
        tree.add("rsw", "SI-002", asset_b)
    with pytest.raises(service.DuplicateCodeError):
        tree.add("package", "101.1", asset_b)
    # A move re-checks the per_parent scope.
    with pytest.raises(service.DuplicateCodeError):
        service.move_node(tree.s, asset_b.id, site.id)


def test_the_unique_index_backs_the_code_check(
    db: Database, home: TenancyContext, tree: Tree
) -> None:
    site, _asset, rsw = tree.remediation()
    clone = {c.name: getattr(rsw, c.name) for c in AssetNode.__table__.columns}
    clone.update(id=uuid.uuid4(), tree_path=f"{site.id.hex}.{uuid.uuid4().hex}")
    tree.s.add(AssetNode(**clone))
    with pytest.raises(service.DuplicateCodeError):
        service._flush(tree.s, "code")
    tree.s.rollback()


# --- move -------------------------------------------------------------------------------------


def test_move_takes_the_subtree_and_writes_both_paths_to_the_ledger(tree: Tree) -> None:
    site, asset, rsw = tree.remediation()
    below = tree.add("package", "PK-1", rsw)
    site_b = tree.add("site", "SI-002")
    old_path, revisions = asset.tree_path, (asset.revision, below.revision)
    moved = service.move_node(tree.s, asset.id, site_b.id, actor_id=None)
    assert moved.id == asset.id  # same node, new parent
    assert (moved.parent_id, moved.tree_path) == (site_b.id, f"{site_b.id.hex}.{asset.id.hex}")
    assert moved.display_path == "SI-002 / 101"
    rows = service.list_nodes(tree.s, subtree_of=site_b.id)
    assert [n.display_path for n in rows] == [
        "SI-002",
        "SI-002 / 101",
        "SI-002 / 101 / 101.1",
        "SI-002 / 101 / 101.1 / PK-1",
    ]
    assert rows[-1].tree_path == f"{site_b.id.hex}.{asset.id.hex}.{rsw.id.hex}.{below.id.hex}"
    assert rows[-1].parent_id == rsw.id  # only the moved node changes parent
    assert (rows[1].revision, rows[-1].revision) == (revisions[0] + 1, revisions[1] + 1)
    assert service.list_nodes(tree.s, subtree_of=site.id) == [site]
    assert service.work_anchor_for(tree.s, below.id).id == rsw.id
    entry = audit.chain_entries(tree.s, f"asset_node:{asset.id}")[-1].payload
    assert entry == {
        "action": "move",
        "code": "101",
        "old_parent_id": str(site.id),
        "new_parent_id": str(site_b.id),
        "old_tree_path": old_path,
        "new_tree_path": moved.tree_path,
        "old_display_path": "SI-001 / 101",
        "new_display_path": "SI-002 / 101",
    }
    assert audit.verify_chain(tree.s, f"asset_node:{asset.id}").ok
    # The same parent again is a no-op: no ledger entry.
    service.move_node(tree.s, asset.id, site_b.id)
    assert len(audit.chain_entries(tree.s, f"asset_node:{asset.id}")) == 2
    with pytest.raises(OutOfScopeError):
        service.move_node(tree.s, uuid.uuid4(), site.id)
    with pytest.raises(service.TreeRuleError, match="unknown new_parent_id"):
        service.move_node(tree.s, asset.id, uuid.uuid4())


def test_display_code_follows_the_template_through_a_move(tree: Tree) -> None:
    template = LevelDefUpdate(display_code_template="{asset} / {rsw} [{line}]")
    site, asset, rsw = tree.remediation()
    service.update_level_def(tree.s, tree.levels["rsw"], template)
    assert rsw.display_code == "101 / 101.1 []"  # existing nodes follow a template change
    other = tree.add("asset", "205", site)
    assert tree.add("rsw", "205.1", other).display_code == "205 / 205.1 []"
    package = tree.add("package", "PK-1", rsw)
    service.move_node(tree.s, rsw.id, other.id)
    assert service.get_node(tree.s, rsw.id).display_code == "205 / 101.1 []"
    assert service.get_node(tree.s, package.id).display_code == "PK-1"
    assert asset.display_code == "101"


# --- "visible" is not "mine" ------------------------------------------------------------------


@pytest.mark.parametrize("system_admin", [False, True])
def test_a_session_reading_another_project_never_resolves_its_rows(
    db: Database, home: TenancyContext, system_admin: bool
) -> None:
    """An allowed-list session reads the other project; a System admin session (no membership
    needed, ADR 0016) could even write it. Neither resolves or changes its nodes here."""
    away = project(db, "AWAY")
    with db.unit_of_work(away) as s:
        foreign_site, foreign_asset, foreign_rsw = Tree(s, seed_levels(s)).remediation()
    assert away.project_id is not None
    wide = TenancyContext(
        tenant_id=T1,
        project_id=home.project_id,
        allowed_project_ids=frozenset() if system_admin else frozenset({away.project_id}),
        is_system_admin=system_admin,
    )
    with db.unit_of_work(wide) as s:
        mine = Tree(s, seed_levels(s))
        site, asset, _rsw = mine.remediation()  # the same codes, another project: no clash
        assert s.get(AssetNode, foreign_rsw.id) is not None  # readable...
        for call in (service.get_node, service.work_anchor_for):  # ...but never resolved
            with pytest.raises(OutOfScopeError):
                call(s, foreign_rsw.id)
        with pytest.raises(OutOfScopeError):
            service.move_node(s, foreign_asset.id, site.id)
        with pytest.raises(service.TreeRuleError, match="unknown new_parent_id"):
            service.move_node(s, asset.id, foreign_site.id)
        with pytest.raises(service.TreeRuleError, match="unknown parent_id"):
            mine.add("asset", "X", foreign_site)
        with pytest.raises(service.TreeRuleError, match="unknown level_def_id"):
            service.create_node(s, NodeCreate(level_def_id=foreign_rsw.level_def_id, code="X"))
        assert service.list_nodes(s, parent_id=foreign_site.id) == []
        assert len(service.list_level_defs(s)) == len(PACKS)
    with db.unit_of_work(away) as s:  # untouched by the wide session
        rows = service.list_nodes(s, subtree_of=foreign_site.id)
        assert [(n.code, n.parent_id, n.revision) for n in rows] == [
            ("SI-001", None, 1),
            ("101", foreign_site.id, 1),
            ("101.1", foreign_asset.id, 1),
        ]
        assert len(service.list_level_defs(s)) == len(PACKS)
    with db.unit_of_work(TenancyContext(tenant_id=T1)) as s, pytest.raises(RuntimeError):
        service.list_level_defs(s)


# --- API --------------------------------------------------------------------------------------


class Api:
    def __init__(self, client: TestClient, db: Database) -> None:
        self.client = client
        self.home = project(db, "HOME")
        self.away = project(db, "AWAY")
        users = {ALICE: Persona.PROJECT_ADMINISTRATOR, BOB: Persona.PROJECT_VIEWER}
        for email, persona in users.items():
            user_id = seed_user(db, T1, email)
            with db.unit_of_work(CTX_T1) as s:
                for scope in (self.home, self.away):
                    s.add(
                        ProjectMembership(
                            tenant_id=T1,
                            user_id=user_id,
                            member_project_id=scope.project_id,
                            persona=persona,
                        )
                    )
        self.base = f"/api/v1/asset-management/projects/{self.home.project_id}"
        self.elsewhere = f"/api/v1/asset-management/projects/{self.away.project_id}"

    def headers(self, email: str) -> dict[str, str]:
        body = {"email": email, "password": PASSWORD}
        token = self.client.post("/api/v1/auth/login", json=body).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def level(
        self, headers: dict[str, str], key: str, depth: int, parents: list[str], **more: Any
    ) -> str:
        body = {
            "key": key,
            "depth": depth,
            "allowed_parent_keys": parents,
            "label_key": f"level.{key}",
            **more,
        }
        response = self.client.post(f"{self.base}/level-defs", json=body, headers=headers)
        assert response.status_code == 201, response.text
        return str(response.json()["id"])


@pytest.fixture
def api(tmp_path: Path, db: Database) -> Iterator[Api]:
    app, _runtime = auth_app(make_settings(database_url=database_url(tmp_path)))
    with TestClient(app) as client:
        yield Api(client, db)


def test_the_api_round_trip(api: Api) -> None:
    alice, bob = api.headers(ALICE), api.headers(BOB)
    post, get = api.client.post, api.client.get
    site_level = api.level(alice, "site", 0, [])
    asset_level = api.level(alice, "asset", 1, ["site"])
    rsw_level = api.level(alice, "rsw", 2, ["asset"], roles=ANCHOR, code_uniqueness="per_project")
    assert [lv["key"] for lv in get(f"{api.base}/level-defs", headers=bob).json()] == [
        "site",
        "asset",
        "rsw",
    ]
    assert (
        post(
            f"{api.base}/level-defs",
            json={"key": "site", "depth": 0, "label_key": "level.site"},
            headers=alice,
        ).status_code
        == 409
    )

    def node(level: str, code: str, parent: str | None = None, expect: int = 201) -> dict[str, Any]:
        body = {"level_def_id": level, "code": code, "parent_id": parent}
        response = post(f"{api.base}/nodes", json=body, headers=alice)
        assert response.status_code == expect, response.text
        return dict(response.json())

    site, site_b = node(site_level, "SI-001"), node(site_level, "SI-002")
    asset = node(asset_level, "101", site["id"])
    rsw = node(rsw_level, "101.1", asset["id"])
    assert rsw["display_path"] == "SI-001 / 101 / 101.1"
    assert "tree_path" not in rsw
    node(rsw_level, "101.1", asset["id"], expect=409)  # duplicate code
    node(rsw_level, "101.9", site["id"], expect=422)  # parent level not allowed
    node(asset_level, " 101", site["id"], expect=422)

    url = f"{api.base}/nodes/{asset['id']}"
    assert get(url, headers=bob).json()["code"] == "101"
    assert [
        n["code"]
        for n in get(f"{api.base}/nodes", params={"subtree_of": site["id"]}, headers=bob).json()
    ] == ["SI-001", "101", "101.1"]
    assert [n["code"] for n in get(f"{api.base}/nodes", headers=bob).json()] == ["SI-001", "SI-002"]
    moved = post(f"{url}/move", json={"new_parent_id": site_b["id"]}, headers=alice)
    assert (moved.status_code, moved.json()["display_path"]) == (200, "SI-002 / 101")
    assert (
        get(f"{api.base}/nodes/{rsw['id']}", headers=bob).json()["display_path"]
        == "SI-002 / 101 / 101.1"
    )
    assert post(f"{url}/move", json={"new_parent_id": rsw["id"]}, headers=alice).status_code == 422
    assert post(f"{url}/move", json={}, headers=alice).status_code == 422

    # The seam: 422 while the path has no work anchor.
    assert get(f"{api.base}/nodes/{rsw['id']}/work-anchor", headers=bob).json()["id"] == rsw["id"]
    assert get(f"{url}/work-anchor", headers=bob).status_code == 422
    # A level in use is never re-keyed.
    rekey = api.client.patch(
        f"{api.base}/level-defs/{rsw_level}", json={"key": "line"}, headers=alice
    )
    assert rekey.status_code == 409
    relabel = api.client.patch(
        f"{api.base}/level-defs/{rsw_level}", json={"label_key": "level.line"}, headers=alice
    )
    assert (relabel.status_code, relabel.json()["key"]) == (200, "rsw")


def test_the_api_checks_capabilities_and_the_project_in_the_path(api: Api) -> None:
    alice, bob = api.headers(ALICE), api.headers(BOB)
    level = api.level(alice, "site", 0, [])
    site = api.client.post(
        f"{api.base}/nodes", json={"level_def_id": level, "code": "SI-001"}, headers=alice
    ).json()
    # A viewer reads but changes nothing.
    assert (
        api.client.post(
            f"{api.base}/nodes", json={"level_def_id": level, "code": "SI-9"}, headers=bob
        ).status_code
        == 403
    )
    assert (
        api.client.post(
            f"{api.base}/nodes/{site['id']}/move", json={"new_parent_id": None}, headers=bob
        ).status_code
        == 403
    )
    body = {"key": "zone", "depth": 0, "label_key": "level.zone"}
    assert api.client.post(f"{api.base}/level-defs", json=body, headers=bob).status_code == 403
    assert (
        api.client.patch(
            f"{api.base}/level-defs/{level}", json={"depth": 0}, headers=bob
        ).status_code
        == 403
    )
    assert api.client.get(f"{api.base}/nodes").status_code == 401
    # Another project of the same member: its path never resolves this project's rows.
    assert api.client.get(f"{api.elsewhere}/nodes/{site['id']}", headers=alice).status_code == 404
    assert (
        api.client.get(f"{api.elsewhere}/nodes/{site['id']}/work-anchor", headers=alice).status_code
        == 404
    )
    assert (
        api.client.post(
            f"{api.elsewhere}/nodes/{site['id']}/move", json={"new_parent_id": None}, headers=alice
        ).status_code
        == 404
    )
    assert (
        api.client.patch(
            f"{api.elsewhere}/level-defs/{level}", json={"depth": 0}, headers=alice
        ).status_code
        == 404
    )
    assert (
        api.client.post(
            f"{api.elsewhere}/nodes", json={"level_def_id": level, "code": "X"}, headers=alice
        ).status_code
        == 422
    )
    assert (
        api.client.get(
            f"{api.elsewhere}/nodes", params={"subtree_of": site["id"]}, headers=alice
        ).status_code
        == 404
    )
    unknown = f"/api/v1/asset-management/projects/{uuid.uuid4()}/nodes"
    assert api.client.get(unknown, headers=alice).status_code == 404
