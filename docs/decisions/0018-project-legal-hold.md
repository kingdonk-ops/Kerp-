# ADR 0018 — Per-project legal hold as columns on `oe_projects_project`

- **Status:** accepted (2026-11-21; records the core-table change that diff §4.7 *Legal hold*
  requires an ADR for; resolves TD-502)
- **Date:** 2026-11-21
- **Affects:** T-0223 (this change, migration `0037_project_legal_hold.py`), T-0233 (project
  archive, must consult the hold), T-0219 / the System console (`/api/v1/admin/system/*`); the
  `oe_projects_project` core table; diff §4.7 *Legal hold*, §4.1; TD-175 (retention rules),
  TD-401 (no hard purge). Builds on ADR 0016 (system_admin global visibility).

## Context

Diff §4.7 asks for a per-project legal hold: a flag with a reason, who set it and when, set and
cleared only by a System admin, audited in the tenant-level chain `admin-system:<tenant>`. While
the hold is on, soft-deleting the project answers **409 `LEGAL_HOLD_ACTIVE`** and the project's
archives (documents-and-mdr, T-0233) cannot be deleted. KERP has no hard purge of project data
(TD-401); retention rules stay TD-175.

The spec places the flag on the project table and calls it a core-table change. CLAUDE.md §6
requires an ADR for core-table changes. `oe_projects_project` is the root of every project RLS
policy (membership-based policies, migrations `0015` and `0035`; the `SECURITY DEFINER` lobby
functions of `0021` and `0023`), so any change to it is recorded here.

## Decision

1. **Columns on the project row.** Migration `0037` adds to `oe_projects_project`:
   - `legal_hold` `BOOLEAN NOT NULL DEFAULT false`;
   - `legal_hold_reason` `VARCHAR(500)` NULL;
   - `legal_hold_set_by` user id NULL (no FK, like `created_by`);
   - `legal_hold_set_at` `TIMESTAMPTZ` NULL;
   - CHECK `ck_oe_projects_project_legal_hold_reason`:
     `NOT legal_hold OR (legal_hold_reason IS NOT NULL AND legal_hold_set_at IS NOT NULL)`.
2. **Additive.** Only `ADD COLUMN` and `ADD CONSTRAINT` on PostgreSQL; every existing row gets
   `legal_hold = false`, which the check accepts. No row is rewritten by application logic, no
   column is dropped or retyped, and no RLS policy changes: no policy or lobby function names
   the new columns, so the revision carries no `FROZEN_RLS_DDL`. SQLite (tests) copies the table
   from a frozen 0036 definition, as `0033` does; a test pins it to the live model. Downgrade
   drops the constraint and the four columns.
3. **The columns describe the active hold only.** Reason, set-by and set-at are written when the
   hold is put on (or its reason changes while on) and nulled when it is cleared. The history
   (every set, change and clear, with actor and reason) is the audit ledger, not the row.
4. **Who and how.** Only a `system_admin` sets or clears it, through
   `PUT /api/v1/admin/system/projects/{id}/legal-hold` `{on, reason}` (admin module, calls only
   `projects.service.set_legal_hold`); a reason of 1–500 characters is required both ways. Every
   change appends `legal_hold_set` / `legal_hold_cleared` (project id, code, reason) to
   `admin-system:<tenant>` in the same transaction. A no-change call writes nothing.
5. **Contract for other modules.** `projects.service.is_on_legal_hold(session, project_id)` is
   the only way another module learns of the hold (no cross-module table access).
6. **Concurrency.** `delete_project` and `set_legal_hold` lock the project row (`FOR UPDATE`)
   before their check, so a delete cannot slip past a concurrent hold.

### Why columns on the project row are acceptable

- A project has at most one hold at a time; the state is one boolean plus three describing
  fields, 1:1 with the project and read on every delete. A child table would only ever hold
  zero or one current row.
- The check lives where the delete happens: `delete_project` already locks the project row, so
  the hold check needs no join and no second lock to be race-free.
- The project row's RLS already scopes it (tenant from the JWT, membership or ADR 0016
  visibility); a new table would need its own `BaseRecordMixin` table, RLS policy and frozen
  DDL for no gain.
- History is already covered by the tamper-evident ledger, so the row need not keep it.
- The change is additive and nullable apart from a defaulted boolean, so it is safe for running
  code and for every existing row.

## Consequences

- **Delete refused.** `DELETE /api/v1/projects/{id}` on a held project answers 409
  `{code: LEGAL_HOLD_ACTIVE}` (`LegalHoldActiveError` in the service) before touching the row or
  its memberships; nothing changes.
- **Archive.** The project archive (T-0233) and any later module that deletes project data must
  call `is_on_legal_hold` first and refuse while it is `True`. T-0233 depends on T-0223.
- **Audit.** Holds are audited on the tenant-level chain `admin-system:<tenant>`
  (`projects.service.LEGAL_HOLD_CHAIN`, pinned equal to `jobs.service.CONSOLE_CHAIN` by a test),
  not on the project's chain: a System admin need not be a member of the project.
- **Authority.** Only `system_admin`; every other caller, PER-09 included, gets 403. Only a
  System admin can set it (planner default stated in diff §4.7).
- **Exposure.** `ProjectRead` shows only the `legal_hold` flag. The reason, setter and time are
  returned only by the System console endpoint (`LegalHoldRead`), never to project members.
- **Retention.** No hard purge exists (TD-401); if one or retention rules (TD-175) are added,
  they must consult `is_on_legal_hold` as well.
- A future need for multiple concurrent holds (for example per matter or per custodian) would
  need a separate hold table and a new ADR superseding §1.

## Alternatives considered

- **Separate `oe_projects_legal_hold` table** (one row per hold, history in the table).
  Rejected: needs its own RLS policy and table for a 1:1 state, the delete check would need a
  join and a second lock, and the history duplicates the audit ledger.
- **Reuse project `status` (`ON_HOLD`).** Rejected: `ON_HOLD` is an operational status changed
  through the normal project edit (`PATCH /projects/{id}`); a legal hold is orthogonal (an active or closed project can be held)
  and must be set only by a System admin.
- **Store the hold in `custom_fields`.** Rejected: a generic JSON bag with no database
  constraint, outside the typed schema and not reliably queryable; the CHECK of §1 could not
  hold.
- **Keep the hold in the archive module (T-0233).** Rejected: the hold also blocks deleting the
  project itself, and `projects` cannot depend on `archive` (the archive depends on projects).
- **Keep clear history on the row** (`legal_hold_cleared_by`, `_cleared_at`, last reason).
  Rejected: only the last clear would be kept; the ledger already holds the full history.
