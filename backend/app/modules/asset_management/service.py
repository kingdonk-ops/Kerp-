"""Breakdown levels and the node tree (T-0101, ADR 0003). Other modules call this, never the tables.

Every call works on a session **bound to a project** and touches only that project: each
query and statement filters ``project_id`` explicitly: a bound session may *read* further
projects (the allowed list), a System admin session reads and writes every project of its
tenant (ADR 0016), and "visible" is not "mine".

Invariants, on every create and move (asset-and-tagging.md §Anchor invariants):

* a parent's level key is in the child level's ``allowed_parent_keys`` (none = a root level);
* **at most one work-anchor node on a root-to-leaf path, never nested**; a path may have none;
* a code is unique in the scope its level's ``code_uniqueness`` names (``_scope_key``);
* a move never puts a node under itself or its own subtree.

**Concurrency.** Every mutation takes the project's tree lock first (``_lock_tree``) and holds
it to the end of its transaction, then re-reads what it checks (``populate_existing``), so the
checks of two writers never interleave. This needs ``READ COMMITTED`` (the default): each
statement after the lock sees what earlier lock holders committed. Callers composing several
mutations in one transaction keep the lock throughout.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Collection, Mapping
from typing import Any, Final

from sqlalchemy import Select, bindparam, case, exists, func, literal, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import service as audit
from app.core.db import GUID, utcnow
from app.core.stats.registry import register_stats_provider
from app.core.tenancy.rls import session_tenancy
from app.core.tenancy.scope import OutOfScopeError
from app.modules.asset_management.models import (
    CODE_INDEX,
    LEVEL_KEY_INDEX,
    LEVEL_TABLE,
    PROGRESS_UNIT,
    WORK_ANCHOR,
    AssetLevelDef,
    AssetNode,
)
from app.modules.asset_management.schemas import LevelDefCreate, LevelDefUpdate, NodeCreate
from app.modules.asset_management.tree import child_path, contains, is_within, rebased, within

__all__ = [
    "DuplicateCodeError",
    "LevelInUseError",
    "NoWorkAnchor",
    "TreeRuleError",
    "create_level_def",
    "create_node",
    "deployment_stats",
    "get_node",
    "list_level_defs",
    "list_nodes",
    "move_node",
    "update_level_def",
    "work_anchor_for",
]

SEPARATOR: Final = " / "  # of ``display_path``
PROJECT_SCOPE: Final = "project"  # ``code_scope_key`` of a ``per_project`` level
NODE_CHAIN: Final = "asset_node:"
LEVEL_CHAIN: Final = "asset_level_def:"
_PLACEHOLDER: Final = re.compile(r"\{([a-z][a-z0-9_]{0,63})\}")
# What may change on a level once nodes use it: nothing that could break a stored node.
_FROZEN_IN_USE: Final = ("key", "depth", "code_uniqueness")
_TEMPLATE_FIELDS: Final = (
    "key",
    "depth",
    "allowed_parent_keys",
    "code_pattern",
    "display_code_template",
)
_BUDGET_FIELDS: Final = ("budget_qty", "uom", "budget_hours")
_RELOAD_BATCH: Final = 1000
_SQLITE_WRITE_LOCK: Final = text(f"UPDATE {LEVEL_TABLE} SET revision = revision WHERE 0")
Levels = Mapping[uuid.UUID, AssetLevelDef]


class TreeRuleError(ValueError):
    """The change breaks a rule of the breakdown or the anchor model (the API answers 422)."""


class DuplicateCodeError(Exception):
    """The code (or level key) is taken in its uniqueness scope (the API answers 409)."""


class LevelInUseError(Exception):
    """Nodes use the level, so this part of it can no longer change (the API answers 409)."""


class NoWorkAnchor(Exception):  # noqa: N818  (the name is the spec's, TD-113)
    """No work-anchor node on the node's path (ancestor-or-self). Routers answer 422: the
    user picks or creates the work-anchor node before booking work."""


# --- scope, lock, loads -----------------------------------------------------------------------


def _project(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    context = session_tenancy(session)
    if context is None or context.project_id is None:
        raise RuntimeError("the asset_management service needs a session bound to a project")
    return context.tenant_id, context.project_id


def _lock_tree(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    """Serialise the project's tree mutations until the transaction ends (module docstring).

    PostgreSQL: a transaction-scoped advisory lock per (tenant, project). SQLite (dev and the
    fast suite): the database write lock, taken by a write that matches no row.
    """
    tenant_id, project_id = _project(session)
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        digest = hashlib.sha256(f"oe_asset_tree|{tenant_id}|{project_id}".encode()).digest()
        key = int.from_bytes(digest[:8], "big", signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    elif dialect == "sqlite":
        session.execute(_SQLITE_WRITE_LOCK)
    return tenant_id, project_id


def _fresh[S: Select[Any]](statement: S) -> S:
    """Re-read rows already in the identity map: they may predate the tree lock."""
    return statement.execution_options(populate_existing=True)


def _levels(session: Session, project_id: uuid.UUID) -> dict[uuid.UUID, AssetLevelDef]:
    rows = session.scalars(
        _fresh(select(AssetLevelDef).where(AssetLevelDef.project_id == project_id))
    )
    return {level.id: level for level in rows}


def _node(session: Session, project_id: uuid.UUID, node_id: uuid.UUID) -> AssetNode | None:
    return session.scalars(
        _fresh(select(AssetNode).where(AssetNode.id == node_id, AssetNode.project_id == project_id))
    ).first()


def _flush(session: Session, what: str) -> None:
    """Flush; a unique-index violation (the backstop of the checks) becomes a 409."""
    try:
        session.flush()
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint in (CODE_INDEX, LEVEL_KEY_INDEX) or "UNIQUE constraint" in str(exc.orig):
            raise DuplicateCodeError(f"{what} already exists") from exc
        raise


# --- level defs -------------------------------------------------------------------------------


def _compile_pattern(pattern: str) -> Any:  # noqa: ANN401  (google-re2 ships no stubs)
    import re2  # type: ignore[import-untyped]  # lazy: native library, no stubs (CLAUDE.md §6)

    options = re2.Options()
    options.log_errors = False
    try:
        return re2.compile(pattern, options)
    except re2.error as exc:
        raise TreeRuleError("code_pattern is not a valid RE2 pattern") from exc


def _check_template(levels: Collection[AssetLevelDef]) -> None:
    """The project's levels form a layered template: a root level has depth 0 and no parents;
    every other level names existing parents that sit above it."""
    by_key = {level.key: level for level in levels}
    for level in levels:
        if (level.depth == 0) != (not level.allowed_parent_keys):
            raise TreeRuleError(
                f"level {level.key!r}: depth 0 means no allowed_parent_keys, and the reverse"
            )
        for key in level.allowed_parent_keys:
            parent = by_key.get(key)
            if parent is None:
                raise TreeRuleError(f"level {level.key!r}: unknown parent level {key!r}")
            if parent.depth >= level.depth:
                raise TreeRuleError(f"level {level.key!r}: parent level {key!r} is not above it")
        if level.code_pattern is not None:
            _compile_pattern(level.code_pattern)
        template = level.display_code_template
        if template is not None and re.search(r"[{}]", _PLACEHOLDER.sub("", template)):
            raise TreeRuleError("display_code_template takes only {level_key} placeholders")


def _in_use(session: Session, project_id: uuid.UUID, level_id: uuid.UUID) -> bool:
    """Whether any node, live or deleted, has ever used the level."""
    used = exists().where(AssetNode.level_def_id == level_id, AssetNode.project_id == project_id)
    return bool(session.scalar(select(used), execution_options={"include_deleted": True}))


def list_level_defs(session: Session) -> list[AssetLevelDef]:
    _tenant_id, project_id = _project(session)
    levels = _levels(session, project_id).values()
    return sorted(levels, key=lambda level: (level.depth, level.key))


def create_level_def(
    session: Session, data: LevelDefCreate, *, actor_id: uuid.UUID | None = None
) -> AssetLevelDef:
    tenant_id, project_id = _lock_tree(session)
    levels = _levels(session, project_id)
    if any(level.key == data.key for level in levels.values()):
        raise DuplicateCodeError(f"level {data.key!r} already exists")
    values = data.model_dump()
    values["roles"] = sorted(data.roles)
    level = AssetLevelDef(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        project_id=project_id,
        created_by=actor_id,
        updated_by=actor_id,
        **values,
    )
    _check_template([*levels.values(), level])
    session.add(level)
    _flush(session, f"level {data.key!r}")
    _audit(session, LEVEL_CHAIN, level.id, actor_id, action="create", key=level.key)
    return level


def update_level_def(
    session: Session,
    level_def_id: uuid.UUID,
    data: LevelDefUpdate,
    *,
    actor_id: uuid.UUID | None = None,
) -> AssetLevelDef:
    """Change a level. Once nodes use it (ADR 0003: additive only, never re-keyed): its key,
    depth, code uniqueness and ``work_anchor`` role are frozen and ``allowed_parent_keys`` may
    only gain keys (409). A key another level names as a parent cannot change either."""
    _tenant_id, project_id = _lock_tree(session)
    levels = _levels(session, project_id)
    level = levels.get(level_def_id)
    if level is None:
        raise OutOfScopeError("AssetLevelDef not found")
    changes = data.model_dump(exclude_unset=True)
    if "roles" in changes:
        changes["roles"] = sorted(changes["roles"])
    changes = {name: value for name, value in changes.items() if getattr(level, name) != value}
    if not changes:
        return level
    if _in_use(session, project_id, level.id):
        frozen: list[str] = [name for name in _FROZEN_IN_USE if name in changes]
        if "roles" in changes and (WORK_ANCHOR in changes["roles"]) != (WORK_ANCHOR in level.roles):
            frozen.append("the work_anchor role")
        parents = changes.get("allowed_parent_keys", level.allowed_parent_keys)
        if not set(level.allowed_parent_keys) <= set(parents):
            frozen.append("allowed_parent_keys (it may only gain keys)")
        if frozen:
            raise LevelInUseError(
                f"nodes use level {level.key!r}; cannot change: {', '.join(frozen)}"
            )
    if "key" in changes:
        if any(level.key in other.allowed_parent_keys for other in levels.values()):
            raise LevelInUseError(f"other levels name {level.key!r} as a parent")
        if any(other.key == changes["key"] for other in levels.values()):
            raise DuplicateCodeError(f"level {changes['key']!r} already exists")
    # Checked on a detached copy, so a refused change leaves nothing pending in the session.
    candidate = AssetLevelDef(
        **{name: changes.get(name, getattr(level, name)) for name in _TEMPLATE_FIELDS}
    )
    _check_template([*(other for other in levels.values() if other is not level), candidate])
    for name, value in changes.items():
        setattr(level, name, value)
    level.updated_by = actor_id
    _flush(session, f"level {level.key!r}")
    if "display_code_template" in changes:
        roots = select(AssetNode).where(
            AssetNode.project_id == project_id, AssetNode.parent_id.is_(None)
        )
        for root in session.scalars(_fresh(roots)):
            _refresh_display_codes(session, project_id, levels, root)
    changed = sorted(changes)
    _audit(
        session, LEVEL_CHAIN, level.id, actor_id, action="update", key=level.key, changed=changed
    )
    return level


# --- nodes: reads -----------------------------------------------------------------------------


def get_node(session: Session, node_id: uuid.UUID) -> AssetNode:
    """The live node of the session's project, else ``OutOfScopeError`` (404)."""
    _tenant_id, project_id = _project(session)
    node = _node(session, project_id, node_id)
    if node is None:
        raise OutOfScopeError("AssetNode not found")
    return node


def list_nodes(
    session: Session,
    *,
    parent_id: uuid.UUID | None = None,
    subtree_of: uuid.UUID | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[AssetNode]:
    """The children of ``parent_id`` (the roots without one) by code, or with ``subtree_of``
    that node and everything below it in tree order (one GiST-indexed ltree query)."""
    _tenant_id, project_id = _project(session)
    query = select(AssetNode).where(AssetNode.project_id == project_id)
    if subtree_of is not None:
        root = get_node(session, subtree_of)
        query = query.where(within(AssetNode.tree_path, root.tree_path)).order_by(
            AssetNode.tree_path
        )
    else:
        query = query.where(
            AssetNode.parent_id.is_(None) if parent_id is None else AssetNode.parent_id == parent_id
        ).order_by(AssetNode.code, AssetNode.id)
    return list(session.scalars(query.limit(limit).offset(offset)))


def _anchor_ids(levels: Levels) -> list[uuid.UUID]:
    return [level.id for level in levels.values() if WORK_ANCHOR in level.roles]


def _anchor_on_path(
    session: Session, project_id: uuid.UUID, levels: Levels, path: str
) -> AssetNode | None:
    """The work-anchor node at ``path`` or above it (at most one: the invariant)."""
    return session.scalars(
        select(AssetNode).where(
            AssetNode.project_id == project_id,
            AssetNode.deleted_at.is_(None),
            AssetNode.level_def_id.in_(_anchor_ids(levels)),
            contains(AssetNode.tree_path, path),
        )
    ).first()


def work_anchor_for(session: Session, node_id: uuid.UUID) -> AssetNode:
    """The work-anchor node on the node's path: the node itself or an ancestor (TD-113).

    The seam for booking modules (progress, time lines, NCRs, completion; callers: TD-125).
    Raises ``NoWorkAnchor`` when the path has none, ``OutOfScopeError`` for an unknown node.
    """
    _tenant_id, project_id = _project(session)
    node = get_node(session, node_id)
    anchor = _anchor_on_path(session, project_id, _levels(session, project_id), node.tree_path)
    if anchor is None:
        raise NoWorkAnchor(f"no work-anchor node on the path of node {node.code!r}")
    return anchor


# --- nodes: rules -----------------------------------------------------------------------------


def _check_parent(level: AssetLevelDef, parent: AssetNode | None, levels: Levels) -> None:
    if parent is None:
        if level.allowed_parent_keys:
            raise TreeRuleError(f"a {level.key!r} node needs a parent")
        return
    parent_key = levels[parent.level_def_id].key
    if parent_key not in level.allowed_parent_keys:
        raise TreeRuleError(f"a {level.key!r} node cannot sit under a {parent_key!r} node")


def _scope_key(level: AssetLevelDef, parent_id: uuid.UUID | None) -> str:
    """Where the node's code must be unique (``uq_oe_asset_node_code_scope``): among the
    level's nodes under one parent, among the level's nodes, or in the whole project."""
    if level.code_uniqueness == "per_project":
        return PROJECT_SCOPE
    if level.code_uniqueness == "per_level":
        return f"level:{level.id}"
    return f"parent:{parent_id or 'root'}:{level.id}"


def _check_code_free(
    session: Session, project_id: uuid.UUID, code: str, scope_key: str, node_id: uuid.UUID
) -> None:
    """A ``per_project`` code is unique across every level, both ways: it may not repeat any
    node's code, and no node may repeat it. The unique index covers one scope key only; the
    cross-scope half holds because every writer is under the tree lock."""
    taken = select(AssetNode.id).where(
        AssetNode.project_id == project_id,
        AssetNode.deleted_at.is_(None),
        AssetNode.code == code,
        AssetNode.id != node_id,
    )
    if scope_key != PROJECT_SCOPE:
        taken = taken.where(AssetNode.code_scope_key.in_([scope_key, PROJECT_SCOPE]))
    if session.scalars(taken.limit(1)).first() is not None:
        raise DuplicateCodeError(f"code {code!r} already exists in its uniqueness scope")


def _display_code(level: AssetLevelDef, codes: Mapping[str, str]) -> str:
    """The level's ``display_code_template`` with each ``{level_key}`` replaced by the code of
    the node of that level on the path ('' when the path has none); no template = the code."""
    if level.display_code_template is None:
        return codes[level.key]
    return _PLACEHOLDER.sub(lambda m: codes.get(m[1], ""), level.display_code_template)


def _path_codes(
    session: Session, project_id: uuid.UUID, levels: Levels, path: str | None
) -> dict[str, str]:
    """``{level key: code}`` of the nodes at ``path`` and above it."""
    if path is None:
        return {}
    rows = session.execute(
        select(AssetNode.level_def_id, AssetNode.code)
        .where(AssetNode.project_id == project_id, contains(AssetNode.tree_path, path))
        .order_by(AssetNode.tree_path)
    )
    return {levels[level_id].key: code for level_id, code in rows}


def _refresh_display_codes(
    session: Session, project_id: uuid.UUID, levels: Levels, root: AssetNode
) -> None:
    """Recompute ``display_code`` of ``root`` and its subtree (a move, a template change)."""
    if all(level.display_code_template is None for level in levels.values()):
        return
    parent_path = root.tree_path.rpartition(".")[0] or None
    above = _path_codes(session, project_id, levels, parent_path)
    codes: dict[uuid.UUID, dict[str, str]] = {}
    subtree = select(AssetNode).where(
        AssetNode.project_id == project_id, within(AssetNode.tree_path, root.tree_path)
    )
    for node in session.scalars(_fresh(subtree.order_by(AssetNode.tree_path))):
        level = levels[node.level_def_id]
        inherited = codes.get(node.parent_id, above) if node.parent_id else above
        codes[node.id] = {**inherited, level.key: node.code}
        display_code = _display_code(level, codes[node.id])
        if node.display_code != display_code:
            node.display_code = display_code
    session.flush()


def _audit(
    session: Session,
    chain: str,
    record_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    **payload: object,
) -> None:
    audit.append(session, f"{chain}{record_id}", payload, actor_id=actor_id, source="api")


# --- nodes: writes ----------------------------------------------------------------------------


def create_node(
    session: Session, data: NodeCreate, *, actor_id: uuid.UUID | None = None
) -> AssetNode:
    tenant_id, project_id = _lock_tree(session)
    levels = _levels(session, project_id)
    level = levels.get(data.level_def_id)
    if level is None:
        raise TreeRuleError("unknown level_def_id")
    parent = None
    if data.parent_id is not None:
        parent = _node(session, project_id, data.parent_id)
        if parent is None:
            raise TreeRuleError("unknown parent_id")
    _check_parent(level, parent, levels)
    if level.code_pattern is not None and not _compile_pattern(level.code_pattern).fullmatch(
        data.code
    ):
        raise TreeRuleError(f"code {data.code!r} does not match the level's code_pattern")
    if PROGRESS_UNIT not in level.roles and any(
        getattr(data, name) is not None for name in _BUDGET_FIELDS
    ):
        raise TreeRuleError(f"level {level.key!r} is not a progress_unit level: no budget fields")
    if WORK_ANCHOR in level.roles and parent is not None:
        anchor = _anchor_on_path(session, project_id, levels, parent.tree_path)
        if anchor is not None:
            raise TreeRuleError(
                f"work anchors are never nested: {anchor.code!r} is already the work anchor"
                " of this path"
            )
    node_id = uuid.uuid4()
    scope_key = _scope_key(level, data.parent_id)
    _check_code_free(session, project_id, data.code, scope_key, node_id)
    codes = _path_codes(session, project_id, levels, parent.tree_path if parent else None)
    node = AssetNode(
        id=node_id,
        tenant_id=tenant_id,
        project_id=project_id,
        level_def_id=level.id,
        parent_id=data.parent_id,
        tree_path=child_path(parent.tree_path if parent else None, node_id),
        display_path=data.code if parent is None else parent.display_path + SEPARATOR + data.code,
        code=data.code,
        display_code=_display_code(level, {**codes, level.key: data.code}),
        code_scope_key=scope_key,
        name=data.name,
        spatial_location=data.spatial_location.as_geojson() if data.spatial_location else None,
        custom_fields=data.custom_fields,
        budget_qty=data.budget_qty,
        uom=data.uom,
        budget_hours=data.budget_hours,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(node)
    _flush(session, f"code {data.code!r}")
    created = {"action": "create", "code": node.code, "level": level.key}
    _audit(session, NODE_CHAIN, node.id, actor_id, **created, tree_path=node.tree_path)
    return node


def move_node(
    session: Session,
    node_id: uuid.UUID,
    new_parent_id: uuid.UUID | None,
    *,
    actor_id: uuid.UUID | None = None,
) -> AssetNode:
    """Move the node, with its whole subtree, under ``new_parent_id`` (``None`` = the root).

    Same node id; the subtree's ``tree_path`` and ``display_path`` change in **one** statement,
    and the old and new path go to the audit ledger (§Restructure with history). Every rule of
    ``create_node`` is re-checked for the new place.
    """
    _tenant_id, project_id = _lock_tree(session)
    session.flush()
    levels = _levels(session, project_id)
    node = _node(session, project_id, node_id)
    if node is None:
        raise OutOfScopeError("AssetNode not found")
    if new_parent_id == node.parent_id:
        return node
    parent = None
    if new_parent_id is not None:
        parent = _node(session, project_id, new_parent_id)
        if parent is None:
            raise TreeRuleError("unknown new_parent_id")
        if is_within(parent.tree_path, node.tree_path):
            raise TreeRuleError("a node cannot move under itself or its own subtree")
    level = levels[node.level_def_id]
    _check_parent(level, parent, levels)
    if parent is not None:
        anchored = exists().where(
            AssetNode.project_id == project_id,
            AssetNode.deleted_at.is_(None),
            AssetNode.level_def_id.in_(_anchor_ids(levels)),
            within(AssetNode.tree_path, node.tree_path),
        )
        anchor = _anchor_on_path(session, project_id, levels, parent.tree_path)
        if anchor is not None and session.scalar(select(anchored)):
            raise TreeRuleError(
                f"work anchors are never nested: the new path already has the work anchor"
                f" {anchor.code!r} and the moved subtree has one too"
            )
    scope_key = _scope_key(level, new_parent_id)
    _check_code_free(session, project_id, node.code, scope_key, node.id)

    old_parent_id, old_path, old_display = node.parent_id, node.tree_path, node.display_path
    new_path = child_path(parent.tree_path if parent else None, node.id)
    new_display = node.code if parent is None else parent.display_path + SEPARATOR + node.code
    table = AssetNode
    moved = table.id == node.id
    tail = func.substr(table.display_path, len(old_display) + 1)
    try:
        session.execute(
            update(table)
            .execution_options(synchronize_session=False)
            .where(table.project_id == project_id, within(table.tree_path, old_path))
            .values(
                tree_path=rebased(table.tree_path, old_path, new_path),
                display_path=literal(new_display).concat(tail),
                parent_id=case(
                    (moved, bindparam("new_parent", new_parent_id, GUID())),
                    else_=table.parent_id,
                ),
                code_scope_key=case((moved, scope_key), else_=table.code_scope_key),
                updated_by=case(
                    (moved, bindparam("actor", actor_id, GUID())), else_=table.updated_by
                ),
                updated_at=utcnow(),
                revision=table.revision + 1,
            )
        )
    except IntegrityError as exc:  # the unique-index backstop of ``_check_code_free``
        raise DuplicateCodeError(f"code {node.code!r} already exists under the new parent") from exc
    # The statement went around the ORM: re-read the moved nodes the session already holds.
    stale = [
        loaded.id
        for loaded in session.identity_map.values()
        if isinstance(loaded, AssetNode) and is_within(loaded.tree_path, old_path)
    ]
    for start in range(0, len(stale), _RELOAD_BATCH):
        batch = stale[start : start + _RELOAD_BATCH]
        reload = select(AssetNode).where(
            AssetNode.project_id == project_id, AssetNode.id.in_(batch)
        )
        session.scalars(_fresh(reload)).all()
    _refresh_display_codes(session, project_id, levels, node)
    _audit(
        session,
        NODE_CHAIN,
        node.id,
        actor_id,
        action="move",
        code=node.code,
        old_parent_id=str(old_parent_id) if old_parent_id else None,
        new_parent_id=str(new_parent_id) if new_parent_id else None,
        old_tree_path=old_path,
        new_tree_path=node.tree_path,
        old_display_path=old_display,
        new_display_path=node.display_path,
    )
    return node


# --- deployment stats (T-0219) ----------------------------------------------------------------


@register_stats_provider("asset_management")
def deployment_stats(session: Session) -> dict[str, int]:
    """Live asset nodes and level definitions of every project the caller's RLS shows (a
    ``system_admin``: every project of the tenant, ADR 0016); counts only, own tenant."""
    context = session_tenancy(session)
    if context is None:
        raise RuntimeError("the asset_management stats need a session bound to a tenant")
    tenant = context.tenant_id
    nodes = session.scalar(select(func.count(AssetNode.id)).where(AssetNode.tenant_id == tenant))
    levels = session.scalar(
        select(func.count(AssetLevelDef.id)).where(AssetLevelDef.tenant_id == tenant)
    )
    return {"asset_nodes": int(nodes or 0), "level_defs": int(levels or 0)}
