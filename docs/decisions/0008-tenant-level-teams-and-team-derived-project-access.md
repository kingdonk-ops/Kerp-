# ADR 0008 — Tenant-level teams and team-derived project access

- **Status:** proposed (the product decisions are the user's, 2026-11-13; the storage design
  below is the planner's and is confirmed when T-0073 is reviewed)
- **Date:** 2026-11-13
- **Resolves:** TD-228 (overridden), TD-229, TD-230 / TD-244 (team side)
- **Open:** TD-251 (which roles a team link may carry)
- **Affects:** T-0013, T-0073, T-0074, T-0014, `app.core.auth` (`load_grants`, `resolve_scope`),
  the `auth_project_membership` core table, saved views (T-0020)

## Context

The spec names teams only as a Backbone nav item (diff §6.1), the register filter op
`my_teams` and saved-view visibility `team` (register-views §2, §3). The first plan (T-0013,
TD-228 default) made teams project-scoped groups of existing project members, managed by
PER-09, with no effect on access.

The user decided otherwise on 2026-11-13:

1. **Teams are tenant-level.** A `system_admin` creates teams, manages their members and
   links a team to projects. One team can be linked to several projects.
2. **Each team-project link carries a role for that project** (e.g. "Editor" on A, "Viewer" on
   B). Every member of the team gets that access on that project.
3. **Project Administrators (PER-09) keep managing their own project's individual members.**
4. Teams are about **who has access and with what permissions**. They are not planner crews,
   rosters or shift teams (TD-073, TD-083). "Project team" in the specs means everyone with
   access to the project.
5. A saved view with `visibility = team` can be shared with **several teams**. Each of them
   must be linked to the view's project, and the view is visible only in that project.

So teams now change who can open a project. **Effective access to a project = the user's
individual memberships ∪ the grants they get from teams linked to that project.** Everything
that answers "is this user on project P, and as what?" has to agree: `load_grants`,
`resolve_scope` (allowed project list, site filter), `project_access` (404 for non-members),
the membership-based RLS on `projects` and membership rows (T-0014), the PER-09 membership
admin screens, and the "active project member" checks in modules.

## Decision

### Teams (module `teams`, T-0013)

- `oe_teams_team` and `oe_teams_member` are **tenant-level** rows (`project_id IS NULL`,
  `BaseRecordMixin`, RLS). `oe_teams_project_link` (`team_id`, `linked_project_id`,
  `persona`, `site_node_id`) is tenant-level too and describes a project, like a membership.
- Only `system_admin` (T-0014's `app.is_system_admin`) writes any of them. Readers: a
  `system_admin` reads everything; another user reads a team when they are its member or the team
  is linked to a project in their scope. Cross-tenant ids are 404. Nothing exposes a tenant id.
- A link carries **one persona** per (team, project), i.e. the role is a persona of the edge spec
  §5.2 matrix, the same unit as an individual membership. "Editor" and "Viewer" are not personas.
  Until TD-251 is decided, links allow PER-01 to PER-14 except PER-09, and a PER-07 link
  names its site.

### Team-derived grants live in the membership store (T-0073)

`app.core` must not import a module, and RLS policies cannot read module tables without
crossing module boundaries. So the derived access is **materialised into the core membership
store**:

- `auth_project_membership` gets an additive nullable column `via_team_link_id` (UUID, no FK:
  the link belongs to the teams module). `NULL` = individual membership (today's rows).
  Not null = a grant derived from that team link. The uniqueness constraint includes
  `via_team_link_id`. A user can then hold the same persona individually and through a team,
  and removing one leaves the other.
- `app.core.auth` exposes a narrow API that the teams service calls (CLAUDE.md §6: the module
  calls core, it never writes the table itself):
  `sync_team_link_grants(session, link_id, project_id, persona, site_node_id, user_ids)`
  (replaces exactly the derived rows of that link, soft-deleting the ones that are gone) and
  `revoke_team_link_grants(session, link_id)`. Both are audited on chain `membership:<project>`
  with the link id and the actor.
- `load_grants` keeps reading `auth_project_membership`, so it returns the union without any
  change to its callers. `Grant` stays a value object and duplicate grants collapse.
  `resolve_scope`, `project_access`, `allowed_project_ids` and T-0014's membership-based RLS all
  see team-derived access with no extra lookup at request time.
- Derived rows are **read-only to PER-09**: the T-0014 membership API lists them, marked with
  the team, and refuses to edit or remove them (409, "managed through team X"). PER-09 keeps
  full control of individual rows.
- The teams service is the only writer of derived rows and re-syncs on every change: member
  added or removed, link created, re-roled or removed, team deleted. Deactivating a user
  (T-0014) soft-deletes all of that user's membership rows, derived ones included.
  A reconciliation check (`kerp teams reconcile`, also run in tests) recomputes the expected
  derived rows from the teams tables and reports or repairs drift.

### Consumers

- Teams service contract (inside the session's bound project): `user_team_ids` (teams of the
  user **linked to the bound project**), `is_member`, `team_member_ids`, `team_exists`,
  `project_team_ids(project_id)`.
- Saved views (T-0074): a link table of view to team. All teams must be linked to the view's
  project. A team view is visible to its owner, PER-09 and members of any of its teams.
  The legacy single `team_id` is migrated into the link table.

## Alternatives considered

1. **Grant-provider hook in core** (core `load_grants` calls a registry that the teams module
   fills). This leaves the core table alone. But every SQL-side membership check (T-0014 RLS on
   `projects`, security-definer lookups, any future policy) would have to call into module
   tables, so core RLS would depend on the teams module. Rejected.
2. **A database view** that unions memberships and team links. Same layering problem, and the
   view would read a module table from a core policy. Rejected.
3. **Teams only as visibility groups** (the first plan). Contradicts the user's decision.

## Consequences

- Core-table change (`auth_project_membership`, one additive column + a replaced unique
  constraint), so this ADR is required (CLAUDE.md §6). No destructive migration.
- Linking a big team writes one membership row per member and project. That is fine at
  deployment scale (hundreds of users). The sync runs in the same transaction as the team
  change, so access never lags the team. Moving it to an SAQ job is a later optimisation.
- The spec records the decision in diff §4.1 ("Teams") and register-views §3.
