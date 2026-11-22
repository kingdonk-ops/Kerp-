# saved_views — saved register views (T-0020)

Storage and rules for saved views of every register (register-views spec §3). A view saves
the register state — columns, filter, sort, group-by, conditional formatting, density — per
register and per project. The column catalogue, filter compiler, grouping and sharing UI
are epic E-01-01; the `RegisterPage` shell is T-0025.

| File | What |
|---|---|
| `models.py` | `oe_saved_view` (`BaseRecordMixin`, project RLS, migration `0011`); `oe_saved_view_team` (the teams of a `team` view, same RLS, migration `0032`, T-0074) |
| `schemas.py` | `ViewConfig` (columns, §2 filter AST, sort, group-by, conditional format, density), `SavedViewCreate` / `SavedViewUpdate` / `SavedViewRead` |
| `service.py` | `Viewer`, `view_team_ids`, `create_view`, `get_view`, `list_views`, `default_view`, `update_view`, `delete_view`, `present`, `present_many`, `load_config`, `register_catalogue`, `known_columns` |
| `router.py` | `/api/v1/saved-views/projects/{project_id}/views...` |

## Record

| Column | Rule |
|---|---|
| `register_key` | the register, e.g. `asset_management.work_anchor` (`[a-z][a-z0-9_.]{1,63}`); never changes |
| `name` | 1-120 chars; rename with `PATCH` |
| `owner_user_id` | the creator; never changes |
| `visibility` | `private`, `team` (+ `team_ids`), `project`, `portal` |
| `team_id` | **deprecated** (T-0074): the single team of T-0020, copied into `oe_saved_view_team` by `0032`; never read, cleared on the next write, `NULL` on new rows. CHECK (relaxed): only a `team` view may carry one. Dropping it is destructive and needs an ADR (TD-381) |
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

- **See**: private — owner; team — owner + Project Administrators + members of any of the
  view's teams still linked to the project; project — every member; portal — owner +
  Project Administrators (portal layouts: TD-243). Anything else is 404.
- **Edit / delete**: owner; a shared view also a Project Administrator (403 otherwise).
- **Defaults**: one per owner and register among private views, one project default per
  register; setting one clears the previous (service-enforced, TD-245). Setting, removing
  or deleting the project default needs a Project Administrator. A `PATCH` that changes
  `visibility` without `is_default` drops the default flag: sharing a personal default
  does not make it the project default, and un-sharing the project default does not make
  it the owner's personal default.
- `GET .../views/default?register_key=` returns the caller's default, else the project
  default, else `null`.
- **Listing cost (T-0348, TD-387):** `list_views` filters the private views of others in SQL
  and reads the team links of the listed team views in one query; `present_many` builds the
  register's column catalogue once and asks `teams.service` for the project's linked teams
  once. A list request costs the same number of queries however many views there are.

## Teams (T-0074, ADR 0008)

A `team` view names **one or more teams** (`team_ids` on create / update / read), stored as
`oe_saved_view_team` rows (`saved_view_id`, `team_id`; `BaseRecordMixin`, project RLS, one
live row per view and team). `team_id` has no foreign key: the team belongs to the teams
module, reached only through its service contract (CLAUDE.md §6):

- **Sharing**: `team_ids` is required and non-empty for `visibility = team` and must be empty
  for any other visibility (422). Every id must be in `teams.project_team_ids(project)`;
  an unknown, other-project or other-tenant id gets the same 422 body. Repeats count once.
  Anyone who may share (`C`) may name any team linked to the project, member or not.
  `PATCH` with `team_ids` replaces the set; changing `visibility` away from `team` drops the
  teams, and coming back needs `team_ids` again.
- **Seeing**: the router passes `teams.user_team_ids(session, user)` (the caller's teams
  linked to the bound project) into the `Viewer`. A team that is deleted or unlinked stops
  seeing the view; with no named team left the view is the owner's and PER-09's only (no
  error). Responses list only `team_ids` still linked to the project.
- **Without the teams module** (not loaded, or off for the project) the caller has no teams
  and making a view a `team` view answers 422 (`team views need the teams module`). The
  manifest depends on `teams`.

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
config = views.load_config(view) if view else None  # ViewConfig
```

## Tests

`tests/test_saved_views.py` (shape, round trip, missing columns, visibility, defaults, API;
SQLite and PostgreSQL). `tests/test_saved_view_teams.py` (T-0074: several teams, unlink /
delete, linked-teams-only 422, teams module availability, migration `0032` with legacy
`team_id` rows and its downgrade).
