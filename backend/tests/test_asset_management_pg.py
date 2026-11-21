"""Asset hierarchy on PostgreSQL: ltree + GiST, PostGIS, project RLS, the tree lock (T-0101).

Runs as the NOSUPERUSER NOBYPASSRLS runtime role against the migrated schema. All data is
synthetic.
"""

import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import Database
from app.core.tenancy.context import TenancyContext
from app.modules.asset_management import service
from app.modules.asset_management.schemas import LevelDefUpdate
from tests.audit_support import migrated_database
from tests.db_support import ON_POSTGRES
from tests.test_asset_management import Tree, project, seed_levels

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES, reason="needs PostgreSQL; runs in CI's PostgreSQL step (TD-052)"
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    yield from migrated_database(tmp_path)


@pytest.fixture
def home(db: Database) -> TenancyContext:
    return project(db, "HOME")


@pytest.fixture
def levels(db: Database, home: TenancyContext) -> dict[str, uuid.UUID]:
    with db.unit_of_work(home) as s:
        return seed_levels(s)


def test_the_schema_is_ltree_gist_and_pointz(db: Database) -> None:
    with db.engine.connect() as conn:
        assert "ltree" in set(conn.scalars(text("SELECT extname FROM pg_extension")))
        column = conn.execute(
            text(
                "SELECT udt_name, is_nullable FROM information_schema.columns"
                " WHERE table_name = 'oe_asset_node' AND column_name = 'tree_path'"
            )
        ).one()
        assert tuple(column) == ("ltree", "NO")
        index = conn.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_oe_asset_node_tree_path'")
        )
        assert "USING gist (tree_path)" in index
        geometry = conn.execute(
            text(
                "SELECT type, srid, coord_dimension FROM geometry_columns"
                " WHERE f_table_name = 'oe_asset_node' AND f_geometry_column = 'spatial_location'"
            )
        ).one()
        assert tuple(geometry) == ("POINT", 4326, 3)


def test_a_move_rewrites_the_whole_subtree_as_ltree(
    db: Database, home: TenancyContext, levels: dict[str, uuid.UUID]
) -> None:
    with db.unit_of_work(home) as s:
        tree = Tree(s, levels)
        site, asset, rsw = tree.remediation()
        site_b = tree.add("site", "SI-002")
        for n in range(30):
            tree.add("package", f"PK-{n:02}", rsw)
        point = {"type": "Point", "coordinates": [115.86, -31.95, 4.25]}
        located = tree.add("asset", "102", site, spatial_location=point)
        service.move_node(s, asset.id, site_b.id)
    with db.unit_of_work(home) as s:
        counts = s.execute(
            text(
                "SELECT count(*) FILTER (WHERE tree_path <@ CAST(:new AS ltree)),"
                " count(*) FILTER (WHERE tree_path <@ CAST(:old AS ltree)),"
                " count(*) FILTER (WHERE nlevel(tree_path) = 4"
                "   AND subpath(tree_path, 0, 3) = CAST(:moved AS ltree))"
                " FROM oe_asset_node"
            ),
            {
                "new": site_b.id.hex,
                "old": site.id.hex,
                "moved": f"{site_b.id.hex}.{asset.id.hex}.{rsw.id.hex}",
            },
        ).one()
        assert tuple(counts) == (33, 2, 30)  # site B + asset + rsw + 30; site A + asset 102
        # Every path is still exactly its chain of parents (parent_id is the source of truth).
        broken = s.scalar(
            text(
                "SELECT count(*) FROM oe_asset_node n LEFT JOIN oe_asset_node p"
                " ON p.id = n.parent_id WHERE n.tree_path IS DISTINCT FROM"
                " CASE WHEN p.id IS NULL THEN text2ltree(replace(n.id::text, '-', ''))"
                " ELSE p.tree_path || replace(n.id::text, '-', '') END"
            )
        )
        assert broken == 0
        assert service.get_node(s, located.id).spatial_location == point
        z = s.scalar(
            text("SELECT ST_Z(spatial_location) FROM oe_asset_node WHERE id = :id"),
            {"id": located.id},
        )
        assert z == 4.25


def test_rls_keeps_the_tree_inside_its_project(
    db: Database, home: TenancyContext, levels: dict[str, uuid.UUID]
) -> None:
    with db.unit_of_work(home) as s:
        site, _asset, _rsw = Tree(s, levels).remediation()
    away = project(db, "AWAY")
    count = text("SELECT count(*) FROM oe_asset_node")
    with db.engine.connect() as conn:  # unbound: fail closed
        assert conn.scalar(count) == 0
        assert conn.scalar(text("SELECT count(*) FROM oe_asset_level_def")) == 0
    with db.unit_of_work(away) as s:
        assert s.scalar(count) == 0
        touched = s.execute(text("UPDATE oe_asset_node SET code = 'X'"))
        assert touched.rowcount == 0
    with db.unit_of_work(home) as s:
        assert s.scalar(count) == 3
        assert service.get_node(s, site.id).code == "SI-001"


def after_the_first_commits(
    db: Database,
    ctx: TenancyContext,
    first: Callable[[Session], Any],
    second: Callable[[Session], Any],
) -> Any:
    """Run ``first`` in a transaction left open, start ``second`` on another connection and
    check it waits for the tree lock; then commit ``first`` and return what ``second`` did."""
    outcome: dict[str, Any] = {}
    done = threading.Event()

    def run() -> None:
        try:
            with db.unit_of_work(ctx) as s:
                outcome["value"] = second(s)
        except Exception as exc:
            outcome["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=run)
    with db.unit_of_work(ctx) as s:
        first(s)
        thread.start()
        assert not done.wait(0.5), f"the second writer did not wait: {outcome}"
    thread.join(20)
    assert done.is_set()
    return outcome.get("error", outcome.get("value"))


def test_a_concurrent_create_cannot_nest_a_work_anchor_under_a_moving_subtree(
    db: Database, home: TenancyContext, levels: dict[str, uuid.UUID]
) -> None:
    with db.unit_of_work(home) as s:
        tree = Tree(s, levels)
        site, _asset, rsw = tree.remediation()
        package = tree.add("package", "PK-1", tree.add("asset", "102", site))
    # Writer 1 moves the (anchor-free) package under the RSW; writer 2 meanwhile makes the
    # package the parent of a work anchor. Each alone is fine; together they would nest.
    outcome = after_the_first_commits(
        db,
        home,
        lambda s: service.move_node(s, package.id, rsw.id),
        lambda s: Tree(s, levels).add("task", "T-1", package),
    )
    assert isinstance(outcome, service.TreeRuleError)
    assert "never nested" in str(outcome)
    with db.unit_of_work(home) as s:
        assert service.work_anchor_for(s, package.id).id == rsw.id
        assert [n.code for n in service.list_nodes(s, subtree_of=package.id)] == ["PK-1"]


def test_concurrent_creates_cannot_take_the_same_project_wide_code(
    db: Database, home: TenancyContext, levels: dict[str, uuid.UUID]
) -> None:
    with db.unit_of_work(home) as s:
        _site, asset, _rsw = Tree(s, levels).remediation()
    # Different scope keys (per_project vs per_parent), so no unique index stands in the way.
    outcome = after_the_first_commits(
        db,
        home,
        lambda s: Tree(s, levels).add("rsw", "X-1", asset),
        lambda s: Tree(s, levels).add("package", "X-1", asset),
    )
    assert isinstance(outcome, service.DuplicateCodeError)


def test_a_level_cannot_become_a_work_anchor_while_its_first_node_is_being_created(
    db: Database, home: TenancyContext, levels: dict[str, uuid.UUID]
) -> None:
    with db.unit_of_work(home) as s:
        _site, _asset, rsw = Tree(s, levels).remediation()
    # A package under an RSW, and "package" turned into a work-anchor level: nested, if both won.
    outcome = after_the_first_commits(
        db,
        home,
        lambda s: Tree(s, levels).add("package", "PK-1", rsw),
        lambda s: service.update_level_def(
            s, levels["package"], LevelDefUpdate(roles={"work_anchor"})
        ),
    )
    assert isinstance(outcome, service.LevelInUseError)
    assert "work_anchor role" in str(outcome)
