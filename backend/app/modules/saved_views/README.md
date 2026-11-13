# saved_views — saved register views (T-0020)

Storage and rules for saved views of every register (register-views spec §3). A view saves
the register state — columns, filter, sort, group-by, conditional formatting, density — per
register and per project. The column catalogue, filter compiler, grouping and sharing UI
are epic E-01-01; the `RegisterPage` shell is T-0025.

| File | What |
|---|---|
| `models.py` | `oe_saved_view` (`BaseRecordMixin`, project RLS, migration `0011`) |
| `schemas.py` | `ViewConfig` (columns, §2 filter AST, sort, group-by, conditional format, density), `SavedViewCreate` / `SavedViewUpdate` / `SavedViewRead` |
| `service.py` | `Viewer`, `create_view`, `get_view`, `list_views`, `default_view`, `update_view`, `delete_view`, `present`, `load_config`, `register_catalogue`, `known_columns` |
| `router.py` | `/api/v1/saved-views/projects/{project_id}/views...` |

## Record

| Column | Rule |
|---|---|
| `register_key` | the register, e.g. `asset_management.work_anchor` (`[a-z][a-z0-9_.]{1,63}`); never changes |
| `name` | 1-120 chars; rename with `PATCH` |
| `owner_user_id` | the creator; never changes |
| `visibility` | `private`, `team` (+ `team_id`), `project`, `portal` |
| `team_id` | set exactly when `visibility = team` (CHECK; TD-244) |
| `is_default` | owner's default on a `private` view; the project default on a `project` view (CHECK; TD-244) |
| `columns` | ordered `{id, width (24-2000 or null), pinned (left/right/null), visible}`, ids unique |
| `filter` | the §2 AST or `null`: `{op: and|or, conditions: [...]}` / `{column, op, value}` |
| `sort` | ordered `{column, direction asc|desc}`, at most 10, columns unique |
| `group_by` | `{columns (1-5), aggregates [{column, fn}], totals}` or `null` |
| `conditional_format` | `[{condition (CEL, compiled on save), style {tone, bold}}]` |
| `density` | `compact`, `standard`, `comfortable` |
| `schema_version` | `1`, set by the server; a later version adds an upgrade step in `load_config` |

Filter ops and their values: `eq neq contains starts_with gt lt` a scalar; `in not_in` a
non-empty list of scalars; `between` two scalars; `within_next` a duration (`7d`, `12h`,
`2w`, `3m`); `subtree_of` a node id; `is_empty overdue me my_teams` no value. At most 6
levels and 200 nodes. Column ids are catalogue ids (never SQL); whether a column exists or
supports an op is checked by the filter compiler against the catalogue (E-01-01).

## Rules (`Viewer`)

The router builds the `Viewer` from the caller's grants in the path's project: any member
(`V`) saves private views; `C` may share (`team` / `project` / `portal`); `admin` (PER-09)
is the Project Administrator.

- **See**: private — owner; team — owner + members of `team_id`; project — every member;
  portal — owner + Project Administrators (portal layouts: TD-243). Anything else is 404.
- **Edit / delete**: owner; a shared view also a Project Administrator (403 otherwise).
- **Defaults**: one per owner and register among private views, one project default per
  register; setting one clears the previous (service-enforced, TD-245). Setting, removing
  or deleting the project default needs a Project Administrator.
- `GET .../views/default?register_key=` returns the caller's default, else the project
  default, else `null`.

**Teams** (T-0013) do not exist yet: the caller's `team_ids` are empty, so a team view is
visible to its owner only and only a Project Administrator can create one; `team_id` is not
checked against a teams table (TD-242).

## Missing columns

A register declares its current column ids with
`register_catalogue(register_key, lambda session: {...})` (the column catalogue of E-01-01
will do this). `present` flags every saved column the catalogue no longer has
(`columns[].missing`) and lists every referenced id that is gone (`missing_columns`, from
columns, filter, sort and group-by). The view still loads and keeps the saved config as is;
the register skips missing columns. A register without a catalogue flags nothing (TD-246).

## Using it from another module

```python
from app.modules.saved_views import service as views

viewer = views.Viewer(user_id=user_id, can_share=True)
view = views.default_view(session, viewer, "asset_management.work_anchor")
config = views.load_config(view) if view else None   # ViewConfig
```

## Tests

`tests/test_saved_views.py` (shape, round trip, missing columns, visibility, defaults, API;
SQLite and PostgreSQL).
