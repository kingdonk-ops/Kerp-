# ADR 0008 — Tenant-level teams and team-derived project access

- **Status:** accepted (approved by the product owner on 2026-11-13, with storage keyed by
  project, user, source type and source id). One planner refinement is **not yet confirmed by
  the product owner**: persona and site are also part of the main unique constraint (see
  "Deviation" below). The coordinator is confirming it separately.
- **Date:** 2026-11-13
- **Resolves:** TD-228 (overridden), TD-229, TD-230 / TD-244 (team side), TD-251 (link roles;
  new persona PER-16 Project Viewer)
- **Affects:** T-0013, T-0073, T-0074, T-0075, T-0076, T-0014, `app.core.auth` (`load_grants`, `resolve_scope`),
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
  §5.2 matrix, the same unit as an individual membership (TD-251, product owner 2026-11-13):
  - Links may carry **PER-01 to PER-14 except PER-09, plus PER-16**. A PER-07 link names its
    site.
  - **PER-09 and PER-15 are strictly individual assignments**, for auditability and to limit
    the blast radius of a team change.
  - **PER-16 "Project Viewer"** is a new persona: Viewer tier (`V` only), single-project scope,
    read-only across modules, no hold, approval or signing authority, and no SoD implications.
    It can be linked through teams and assigned individually. It is the "Viewer" of the user's
    example; "Editor" maps to whichever working persona fits (e.g. PER-04, PER-05). Edge spec §5.2
    is amended, and T-0076 adds it to the core persona model. This widens the persona CHECK on
    the core membership table. That is part of this ADR's core-table change and is additive,
    because no existing row changes.

### Separation of duties across sources

Diff §4.1 and edge §5 (Admins) require a warning, an explicit confirmation and an audit flag
whenever **PER-09 and a hold-authority persona (PER-02, PER-06, PER-07, PER-11) meet on the same
project for the same user**. Keeping PER-09 off team links is not enough, because the two can
come from different sources. So the rule is evaluated over the user's **effective** grants on the
project, individual and team-derived together:

- **Team side (T-0013 via T-0073).** Some changes would give a hold-authority persona on P to a
  user who already holds individual PER-09 on P: creating or re-roling a link, adding a member,
  or (for a team linked to several projects) any of those per project. Such a change returns
  **409** with `sod_warning` (the affected user ids and projects) unless the request carries
  `confirm_sod: true`. Core enforces it: `sync_team_link_grants` refuses
  (`SodConfirmationRequired`) unless called with `confirm_sod=True`.
- **Individual side (T-0014, extended by T-0073).** The same applies when a PER-09 or
  `system_admin` assigns individual PER-09 to a user who already holds a team-derived
  hold-authority persona on the project. The check reads all of the user's grants on the
  project, derived rows included. The individual-only case stays as T-0014 already specifies it.
- **Audit.** A confirmed combination is written to the audit ledger with `sod_warning: true`,
  whichever side created it.
- Admin still confers no `A`/`S` (diff §4.1). The warning makes the combination visible; the
  hold capability comes only from the hold persona, as for individual memberships.

### Team-derived grants live in the membership store (T-0073)

`app.core` must not import a module, and RLS policies cannot read module tables without
crossing module boundaries. So the derived access is **materialised into the core project
membership table** (approved storage shape, product owner 2026-11-13):

- `auth_project_membership` rows are keyed by **(project, user, source_type, source_id)**. The
  project is the table's `member_project_id`, since membership rows are tenant-level.
  - Two additive columns. `source_type` is NOT NULL, default `'direct'`, CHECK in
    (`direct`, `team_link`); the set can grow later, e.g. for access requests. `source_id` is a
    UUID with no FK, because the link belongs to the teams module.
  - CHECK `(source_type = 'direct') = (source_id IS NULL)`: a direct row has no source id, and a
    `team_link` row carries the team link id. Every existing row becomes `direct`.
  - **Deviation (planner, pending product-owner confirmation).** The approved key is (project,
    user, source_type, source_id). The main unique constraint adds persona and site:
    `(tenant_id, member_project_id, user_id, source_type, source_id, persona, site_node_id)`
    with `NULLS NOT DISTINCT`, which PostgreSQL 16 supports and the existing constraint already
    uses. There are two reasons. A user may hold several personas on one project directly
    (T-0010 behaviour, e.g. PER-02 + PER-09). And the sync re-roles by soft-deleting the old
    persona row and inserting a new one, which a four-column key covering soft-deleted rows would
    reject. If the product owner rejects the deviation, direct rows need another way to hold
    several personas, and this section changes.
  - **The approved key holds exactly for team rows.** A partial unique index
    `(tenant_id, member_project_id, user_id, source_id) WHERE source_type = 'team_link' AND
    deleted_at IS NULL` means a link can never hold two live personas for one user. The sync
    soft-deletes the old row before it inserts the new one, in the same flush order.
  - SQLite treats NULLs as distinct, so the constraint alone does not stop a duplicate direct
    row there. The write paths (T-0014 assignment, T-0073 sync) look up an existing row
    (including soft-deleted ones) before inserting, which is idempotent on both engines. The
    duplicate-rejection test runs on PostgreSQL.
  - Direct and team-derived grants coexist and never overwrite each other. Removing one source
    leaves the other.
- **Effective permissions = the union of the active (non-deleted) rows for (project, user)**,
  whatever their source. `load_grants` reads all of them; nothing filters on `source_type` when
  deciding access.
- `app.core.auth` exposes a narrow API that the teams service calls (CLAUDE.md §6: the module
  calls core, it never writes the table itself):
  `sync_team_link_grants(session, link_id, project_id, persona, site_node_id, user_ids,
  confirm_sod=False)` (replaces exactly the derived rows of that link: it soft-deletes the ones
  that are gone and restores a soft-deleted row rather than inserting a duplicate),
  `revoke_team_link_grants(session, link_id)` and a full read for reconciliation,
  `team_link_grants(session) -> {link_id: {(user_id, project_id, persona, site_node_id)}}`,
  which covers **every** `source_id` of non-deleted `team_link` rows. The writes are audited on
  chain `membership:<project>` with the link id and the actor.
- `load_grants` keeps reading `auth_project_membership`, so it returns the union without any
  change to its callers. `Grant` stays a value object and duplicate grants collapse.
  `resolve_scope`, `project_access`, `allowed_project_ids` and T-0014's membership-based RLS all
  see team-derived access with no extra lookup at request time.
- Derived rows are **read-only to PER-09**: the T-0014 membership API lists them, marked with
  the team, and refuses to edit or remove them (409, "managed through team X"). PER-09 keeps
  full control of individual rows.
- The teams service is the only writer of derived rows and re-syncs on every change: member
  added or removed, link created, re-roled or removed, team deleted.
- **Deactivating a user leaves their derived rows in place.** A deactivated account cannot
  authenticate, so the rows grant nothing. Reactivating the user restores their team access with
  no re-sync, so nothing is left for someone to remember to run. For that to hold, **every sync
  passes all non-deleted members of the team, whatever their account status**. Only the teams
  *read* contract (`user_team_ids`, `is_member`, `team_member_ids`) filters to active accounts.
  Otherwise the next sync of a link (say, adding another member) would soft-delete a deactivated
  member's row.
- **Reconciliation** (`teams reconcile`, T-0075, also run in tests) recomputes the expected
  derived tuples (user, project, persona, site) of every live link from the teams tables. It
  compares them with `team_link_grants` and reports three kinds of drift: missing tuples,
  excess or mismatched tuples (wrong persona, site or project), and **orphan link ids**
  (derived rows whose link no longer exists in the teams tables). `--fix` re-syncs live links
  and revokes orphans. Excess access is the dangerous direction, and it is always detected.
  Expected members include deactivated accounts (the same input as the sync). A missing tuple
  whose re-adding would create an unconfirmed PER-09 + hold combination is reported as
  `sod_pending` and is fixed only with an explicit `--confirm-sod`. `--fix` never confirms SoD
  on its own.
- **Startup check and RLS.** The disabled-teams check (below) runs before any tenant is bound.
  It calls a `SECURITY DEFINER` function, created by the T-0073 migration with a pinned
  `search_path`, that returns only the count of live `team_link` rows across all tenants.
- **Disabling the teams module.** Teams is not toggleable per project. Derived rows must never
  outlive the module that manages them: at startup, core refuses to start when the teams module
  is not loaded and non-deleted `team_link` rows exist (fail closed). The documented way to disable
  teams is to unlink every team first (which revokes the rows) or run
  `revoke_team_link_grants` for every link.

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
4. **A single nullable `via_team_link_id` column** (this ADR's first draft). It works for teams
   only. The product owner chose the general `source_type` + `source_id` shape instead.

## Consequences

- Core-table change (`auth_project_membership`: two additive columns, two CHECKs, a replaced
  unique constraint, and the persona CHECK widened for PER-16 by T-0076), so this ADR is required
  (CLAUDE.md §6). The upgrade is additive. The **downgrade is destructive**: it must delete the
  `team_link` rows before restoring the old unique constraint, because a user may hold the same
  persona directly and through a team.
- The generalised source columns leave room for other grant sources later (e.g. an access
  request) without another reshaping. Each new `source_type` value needs its own ADR note.
- Linking a big team writes one membership row per member and project. That is fine at
  deployment scale (hundreds of users). The sync runs in the same transaction as the team
  change, so access never lags the team. Moving it to an SAQ job is a later optimisation.
- The spec records the decision in diff §4.1 ("Teams") and register-views §3.
