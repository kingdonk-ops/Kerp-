# teams — tenant-level teams and team-derived project access (T-0013)

A team is a **tenant-level** named group of user accounts (diff §4.1 Teams, ADR 0008). A
`system_admin` creates teams, manages their members and links each team to projects with
**one role (persona) per project**. Every member of the team then has that persona on that
project: effective access = individual memberships ∪ team-derived grants. Teams are for
access only; rosters, shifts, crews and people without a login belong to the planner resource
pool (TD-073, TD-083). "Project team" in handover §2/§8 and planner §12 means everyone with
access to the project.

| File | What |
|---|---|
| `models.py` | `oe_teams_team`, `oe_teams_member`, `oe_teams_project_link` (`BaseRecordMixin`, tenant-level, `team` RLS policy; migration `0028`) |
| `schemas.py` | `TeamCreate`, `TeamUpdate`, `MemberPut`, `LinkPut`, `TeamRead`, `TeamDetail`, `TeamLinkRead`, `ProjectTeamRead`, `SodWarning` |
| `service.py` | writes (`create_team`, `update_team`, `delete_team`, `add_member`, `remove_member`, `set_link`, `unlink`), reads, and the **public read contract** (below) |
| `router.py` | `/api/v1/teams/...` |
| `reconcile.py` | the command line: `python -m app.modules.teams.reconcile [--fix [--confirm-sod]]` (T-0075) |

## Not toggleable per project

`MANIFEST.toggleable = False`. A team-project link *grants project access*: switching the
module off for one project would either silently keep the derived grants (access nobody can
manage from that project any more) or silently drop them. Neither is acceptable, so the
module is platform, like `admin`.

**Disabling teams tenant-wide** (removing the module) requires **unlinking every team first**
(`DELETE /api/v1/teams/{id}/projects/{p}` or `DELETE /api/v1/teams/{id}`), which revokes the
derived grants. Core's startup check (T-0073, `app/core/auth/README.md`, "Team-derived
grants") refuses to start while live `team_link` membership rows exist and the module is not
loaded (fail closed). The `admin` module also lists `teams` in `depends_on` (it names the team
of each team-derived grant in the project member list), so that dependency goes with it.

## Tables

| Table | Columns | Rules |
|---|---|---|
| `oe_teams_team` | `name`, `name_key`, `description` | `name` unique per tenant among live teams, case-insensitively (`name_key` = whitespace-collapsed, case-folded name; partial unique index; 409) |
| `oe_teams_member` | `team_id`, `user_id` | one live row per (team, user); kept while the account is deactivated |
| `oe_teams_project_link` | `team_id`, `linked_project_id`, `persona`, `site_node_id` | one live row per (team, project); persona CHECK: PER-01..PER-14 except PER-09, plus PER-16 (TD-251; PER-09 / PER-15 are individual only, 422); `site_node_id` set exactly for PER-07 (CHECK) |

All three are tenant-level (`project_id IS NULL`, CHECK). Removing anything soft-deletes it.
`linked_project_id` has no foreign key (the project row belongs to `projects`).

**RLS** (`team` policy, `app/core/tenancy/README.md`): a `system_admin` session reads and
writes every row. Any other session reads a team, its members and its links only when the
team has a live link to a project the session describes (bound project,
`app.allowed_project_ids` or `app.member_project_ids`, as for memberships; T-0014 pattern) or
the caller (`app.current_user_id`) is a live member. Nobody else inserts, updates or deletes.
The ORM scope mirrors it on SQLite.

## API (`/api/v1/teams`)

| Method + path | Who | What |
|---|---|---|
| `GET /projects/{p}/teams` | anyone with access to `p` (individual or team-derived); else 404 | teams linked to `p`: `team_id`, `name`, `link_id`, `persona`, `site_node_id`, `member_count` (active accounts) |
| `GET /projects/{p}/teams/mine` | same | the caller's teams on `p` (the `my_teams` UI) |
| `GET ""` | any signed-in user | the teams the caller can see, with `member_count`, `project_count` |
| `GET /{team}` | a caller who can see the team (else 404) | team with members (`is_active` shown) and links |
| `POST ""` | `system_admin` (else 403) | create; duplicate name 409 |
| `PATCH /{team}` | `system_admin` | rename / describe; duplicate name 409 |
| `DELETE /{team}` | `system_admin` | soft delete: ends memberships and links, revokes their derived grants |
| `PUT /{team}/members/{user}` | `system_admin` | add (idempotent, 204); body `{confirm_sod?}` optional. Unknown, deactivated or other-tenant user: 422, always the same body |
| `DELETE /{team}/members/{user}` | `system_admin` | remove (204; not a member: no-op) |
| `PUT /{team}/projects/{p}` | `system_admin` | `{persona, site_node_id?, confirm_sod?}`: link or re-role (idempotent when unchanged); deleted / other-tenant project 404 |
| `DELETE /{team}/projects/{p}` | `system_admin` | unlink (204; not linked: no-op). A live link is removable even after its project was deleted; with no link, a deleted / other-tenant project is 404 |

A non-admin gets 403 on a write to a team they can see, 404 on one they cannot. The project
routes bind the path project like `uploads` (`projects.bind_project`, until TD-122). No
endpoint takes or returns a tenant id.

**Separation of duties** (ADR 0008). A link create / re-role, or a member add, that would give
a hold-authority persona (PER-02, PER-06, PER-07, PER-11) on a linked project to a user who
holds **individual** PER-09 there answers 409
`{"code": "sod_confirmation_required", "sod_warning": {"user_ids": [...], "project_ids": [...]}}`
and writes nothing, unless the body carries `confirm_sod: true`. A member add checks every
project the team is linked to. A confirmed change is audited with `sod_warning: true`.

## Derived access (T-0073)

The module never writes `auth_project_membership`. Every member add/remove, link
create/re-role/remove and team delete calls `app.core.auth.team_grants.sync_team_link_grants`
/ `revoke_team_link_grants` for each affected link, in the same transaction. The rows are
`source_type = 'team_link'`, `source_id` = the link id.

**The sync gets every live member of the team, whatever their account status.** A
deactivated member keeps their derived rows (a deactivated account cannot sign in, so they
grant nothing) and gets the access back on reactivation with no re-sync. Only the read
contract below filters to active accounts. An individual membership is never touched.

**Links to a deleted project confer nothing.** Deleting a project is refused while team-derived
rows exist on it (`memberships.revoke_project`), but an *empty* team's link does not stop it,
so a link can outlive its project. Every sync checks each link's project through
`projects.list_projects` (writes are `system_admin` sessions, which see every live project of
the tenant): a link to a deleted project is **never synced**; any rows it still holds are
revoked instead, and it never triggers the SoD check. The link itself stays until a system
admin unlinks it (`DELETE /{team}/projects/{p}` still answers 204 and audits `link_remove`)
or the team is deleted. Until then `GET /{team}` lists it and `project_count` counts it: the
reads cannot tell a deleted project from one the caller may not see (a member sees every link
of their team), so they do not filter by project. Removing such links at project delete is
TD-358.

## Reconcile (T-0075)

The in-transaction sync keeps the derived rows in step; `teams reconcile` is the safety net
that **proves** it (ADR 0008, "Reconciliation"). It compares full tuples, so it also catches a
wrong persona, site or project, not just a wrong user list.

```bash
python -m app.modules.teams.reconcile                      # report only
python -m app.modules.teams.reconcile --fix                # repair (never confirms SoD)
python -m app.modules.teams.reconcile --fix --confirm-sod  # also re-add sod_pending grants
```

The command runs as the runtime role (`KERP_DATABASE_URL`) in a tenant-level `system_admin`
session of `--tenant-id` (default `KERP_DEFAULT_TENANT_ID`). Exit codes: `0` no drift left,
`1` drift found and not (fully) fixed, `2` bad usage or configuration. In code:
`teams.reconcile(session, *, fix=False, confirm_sod=False) -> ReconcileReport` (a tenant-level
`system_admin` session only: `NotSystemAdminError` otherwise).

- **Expected**: for every live link of a live team, `(user_id, linked_project_id, persona,
  site_node_id)` over the team's live members, **deactivated accounts included** (the same
  input the sync gets, so a deactivated member's row is never excess). A link to a deleted
  project expects nothing (it confers nothing; see "Links to a deleted project").
- **Actual**: core's `team_grants.team_link_grants(session)` (every link id with live derived
  rows). The teams tables are read only here, the core store only through the T-0073 API.
- **Report** (`ReconcileReport.links`, one `LinkDrift` per drifting link id): `missing`
  (expected, not stored), `excess` (stored, not expected: a removed member, or a stale persona,
  site or project, which is excess + missing), `orphan` (live rows whose link is unknown or
  deleted in the teams tables, or whose team is deleted) and `sod_pending`.
- **`--fix`**: a drifting live link is re-synced to the expected state; an orphan (or a link
  that should hold nothing) is revoked. Core audits each on `membership:<project>` with
  `reason: "reconcile"`. `added` / `removed` say what was repaired.
- **SoD**: a missing tuple whose re-adding would combine a hold-authority persona with an
  individual PER-09 on that project is `sod_pending`: the individual PER-09 may have been
  granted while the derived row was missing, so nobody confirmed the combination. `--fix`
  repairs the rest of the link (the sync gets the expected users minus the pending ones; their
  stale rows of that link, if any, are excess and go) and exits `1`. `--fix --confirm-sod`
  re-adds them, audited `sod_warning: true`.
- **Idempotent**: running it twice gives the same report, and a second `--fix` finds nothing.

**When to run it**: after a restore or a manual database repair, after an incident in which a
team change may have committed without its sync, before disabling the teams module, and
whenever access looks wrong. A report-only run writes nothing. Scheduling it (a nightly batch
job) is TD-383.

## Audit

Chain `team:<id>`, with the actor: `create`, `update` (`fields`), `delete`
(`unlinked_project_ids`, `removed_user_ids`), `member_add` (`user_id`, `sod_warning`,
`sod_project_ids`), `member_remove`, `link_create` / `link_rerole` (`link_id`, `project_id`,
`persona`, `site_node_id`, previous role, `sod_warning`, `sod_user_ids`), `link_remove`. An
idempotent no-op writes nothing. Core audits the derived-row changes on `membership:<project>`.

## Service contract (public API)

Other modules call only these (CLAUDE.md §6: nobody else reads the teams tables). Soft-deleted
teams, members and links are ignored, and **a member counts only while their account is
active** (this is the read contract, never the sync input). Each answers within what the
session may see.

```python
from app.modules.teams import service as teams

teams.project_team_ids(session, project_id)    # frozenset[UUID]: teams linked to the project
teams.user_team_ids(session, user_id)          # frozenset[UUID]: the user's teams linked to the
                                               #   session's bound project (empty without one)
teams.is_member(session, team_id, user_id)     # bool: False for an unknown or deleted team
teams.team_member_ids(session, team_id)        # frozenset[UUID]
teams.team_exists(session, team_id)            # bool
teams.link_team_names(session, link_ids)       # {link_id: team name} (T-0014 member list)
```

`user_team_ids` is what the `my_teams` filter op and saved-view visibility `team` build on
(register-views §2, §3; T-0074).

## Tests

`tests/test_teams.py` (SQLite and PostgreSQL: contract, API role matrix, derived access,
deactivation, SoD, audit, read contract with a test-only consumer model),
`tests/test_rls_teams.py` (PostgreSQL as `kerp_app`: raw-SQL policy checks),
`tests/test_teams_reconcile.py` (reconcile: drift kinds, fix, SoD pending, deactivated
members, idempotence, the command).
