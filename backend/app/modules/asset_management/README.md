# asset_management — breakdown levels and the node tree (T-0101)

One ltree tree whose levels are per-project configuration (ADR 0003 as amended,
`docs/spec/modules/asset-and-tagging.md`). Rules bind to a level's **roles**, never its name.
Items, the field registry, collections, split / merge / retire, terminology and packs are
later tasks (T-0102, T-0103, T-0134, T-0135, T-0136, T-0108).

| File | What |
|---|---|
| `models.py` | `oe_asset_level_def`, `oe_asset_node` (`BaseRecordMixin`, project RLS, migration `0036`) |
| `tree.py` | `LtreePath` column type and the path expressions `within` / `contains` / `rebased` (ltree operators on PostgreSQL, text prefixes on SQLite) |
| `schemas.py` | `LevelDefCreate` / `LevelDefUpdate` / `LevelDefRead`, `NodeCreate` / `NodeMove` / `NodeRead`, `PointZGeoJSON` |
| `service.py` | `create_level_def`, `update_level_def`, `list_level_defs`, `create_node`, `get_node`, `list_nodes`, `move_node`, **`work_anchor_for`**; errors `TreeRuleError`, `DuplicateCodeError`, `LevelInUseError`, `NoWorkAnchor` |
| `router.py` | `/api/v1/asset-management/projects/{project_id}/level-defs...` and `/nodes...` |

## `oe_asset_level_def` — the breakdown template

A project may run several templates side by side (remediation Site/Asset/RSW and CUI
Scope/Job Package/Line under one contract).

| Column | Rule |
|---|---|
| `key` | `[a-z][a-z0-9_]{0,63}`; unique `(project_id, key)`; **never changes once a node (live or deleted) uses the level** (409), nor while another level names it as a parent |
| `depth`, `allowed_parent_keys` | the levels are layered: depth 0 ⇔ no parents (a root level); every named parent exists and has a smaller depth. A level may name several parents (optional or skipped levels). No level nests under itself (TD-478) |
| `label_key` | i18n key (`level.rsw`); the label comes from the terminology pack (T-0136) |
| `roles` | subset of `grouping`, `physical`, `work_anchor`, `inspection_anchor`, `progress_unit`, `schedule_link` (frozen at M1); stored sorted |
| `code_pattern` | optional **RE2** pattern (linear time) a node code must match in full; checked when a node is created. Generation is not built (TD-480) |
| `code_uniqueness` | `per_parent` (default) \| `per_level` \| `per_project`, see below |
| `field_schema_id` | the level's JSONSchema in the field registry; no FK until T-0102 creates the registry |
| `display_code_template` | optional, e.g. `{asset} / {rsw}`: only `{level_key}` placeholders (never `str.format`) |

**Once nodes use a level** changes are additive (ADR 0003): `key`, `depth`, `code_uniqueness`
and the `work_anchor` role are frozen and `allowed_parent_keys` may only gain keys (409
otherwise). Labels, other roles, the pattern (new codes only) and the display template (stored
`display_code`s are recomputed) may change.

## `oe_asset_node` — the tree

- `level_def_id` → the level. `parent_id` is the **source of truth**.
- `tree_path ltree NOT NULL`, GiST-indexed, derived from `parent_id`: one label per ancestor
  and the node itself, each label the node id (`uuid.hex`). A path therefore never depends on
  a code, and subtree and ancestor queries are `<@` / `@>`. It is not exposed by the API.
- `display_path` — the breadcrumb of codes (`SI-001 / 101 / 101.1`), maintained by the service,
  read-only, never queried against.
- `code` — free **text**, stored exactly as given (`101.1`, `007`, `SI-001`): 1-200 characters,
  no control characters, no leading or trailing whitespace (refused, never trimmed).
  `display_code` — the level's template filled with the codes of the nodes on the path (`''`
  for a level the path lacks), or the code itself.
- `code_scope_key` — derived from the level's `code_uniqueness`; the partial unique index
  `uq_oe_asset_node_code_scope (project_id, code_scope_key, code)` over live rows makes a
  duplicate a constraint violation:

  | `code_uniqueness` | Unique among | `code_scope_key` |
  |---|---|---|
  | `per_parent` | the level's nodes under the same parent (the level's roots among themselves) | `parent:<parent id or root>:<level id>` |
  | `per_level` | the level's nodes in the project | `level:<level id>` |
  | `per_project` | **every** node of the project, any level, both ways: it repeats no node's code and no node repeats it | `project` |

  The cross-level half of `per_project` is checked by the service under the tree lock. The
  scope semantics are this module's reading of ADR 0003 Amendment 4 (TD-479).
- `name`, `discipline_code`, external keys (`wbs_code`, `activity_id`, `external_source`,
  `external_id`, `synced_at`; references, never FKs), `planned_start` / `planned_finish`,
  `spatial_location` (PostGIS `geometry(PointZ,4326)`; GeoJSON `[lon, lat, elevation]`),
  `custom_fields`. Names and types the spec leaves open: TD-481.
- `budget_qty`, `uom`, `budget_hours`, `earned_qty` — `progress_unit` levels only (422 on
  another level). `earned_qty` is written by progress (E-04-03), never by this API.
- Lineage: `status` (`active` / `retired`), `superseded_by`, `derived_from` (lists of node
  ids). The columns exist; the operations are T-0134.
- **No BOQ column or FK** on either table.

## Invariants (every create and move)

1. The parent's level key is in the child level's `allowed_parent_keys`; a node without a parent
   needs a root level (422).
2. **At most one work-anchor node on a root-to-leaf path, never nested**: a work-anchor node
   has no work-anchor ancestor or descendant (422). A path with none is allowed (TD-113).
3. The code is unique in its scope (409) and matches the level's `code_pattern` (422).
4. A node never moves under itself or its own subtree (422).

### `work_anchor_for(session, node_id)` — the seam for booking modules

Returns the work-anchor node on the node's path: the node itself or an ancestor. Raises
`NoWorkAnchor` when there is none (a node *above* the anchors, such as an Asset with several
RSWs, has none: the user picks one) and `OutOfScopeError` for a node outside the session's
project. Routers answer **422** for `NoWorkAnchor`. Callers: TD-125.

### Move

`move_node` keeps the node id and changes the parent. One `UPDATE` rewrites `tree_path`
(`new || subpath(tree_path, nlevel(old))`) and `display_path` of the whole subtree, bumps each
row's `revision`, and sets the moved node's `parent_id` and `code_scope_key`. `display_code`s are
then recomputed where a template exists. The ledger chain `asset_node:<id>` gets
`{action: move, old/new_parent_id, old/new_tree_path, old/new_display_path}`; node creation and
level changes are ledgered too (`asset_level_def:<id>`).

### Concurrency

Every mutation first takes the project's **tree lock** and keeps it until its transaction ends:
on PostgreSQL `pg_advisory_xact_lock` on a hash of (tenant, project); on SQLite the database
write lock. It then re-reads what it checks. Two writers therefore never interleave their
checks: a move cannot slip a work anchor under a path another transaction is anchoring, and
two creates cannot take the same code. This relies on `READ COMMITTED` (the default). A long
transaction that mutates the tree (a bulk import) blocks the project's other tree writers, not
its readers (TD-483).

### "Visible" is not "mine"

A bound session may *read* other projects (the allowed list) and a System admin session
reads **and writes** every project of its tenant (ADR 0016). Every query and statement here filters
`project_id` to the session's bound project, so a node or level id of another project is never
resolved: 404 in a path, 422 (`unknown ...`) in a body. A session without a bound project is a
programming error (`RuntimeError`).

## API

Mounted at `/api/v1/asset-management` (the loader's prefix; the spec writes `/assets/...`,
TD-477). The project is in the path and bound by `projects.bind_project`.

| Route | Needs | Notes |
|---|---|---|
| `GET /projects/{p}/level-defs` | `V` | by depth, key |
| `POST /projects/{p}/level-defs` | `admin` | 409 duplicate key, 422 template rule |
| `PATCH /projects/{p}/level-defs/{id}` | `admin` | 409 for a frozen change to a level in use |
| `GET /projects/{p}/nodes?parent_id=` | `V` | children by code; the roots without `parent_id` |
| `GET /projects/{p}/nodes?subtree_of=` | `V` | the node and its subtree in tree order; `limit` (≤ 2000, default 500), `offset` |
| `POST /projects/{p}/nodes` | `C` | 422 rule violation, 409 duplicate code |
| `GET /projects/{p}/nodes/{id}` | `V` | |
| `POST /projects/{p}/nodes/{id}/move` | `E` | body `{new_parent_id}` (`null` = to the root) |
| `GET /projects/{p}/nodes/{id}/work-anchor` | `V` | 422 when the path has no work anchor |

Not built here: node edit / delete and level delete (TD-482), Asset-Site scoping of the tree
for PER-07 (TD-150), `custom_fields` validation against the level's schema (T-0102).
