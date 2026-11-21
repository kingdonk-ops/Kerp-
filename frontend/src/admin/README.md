# `src/admin` — Admin screens: access requests, devices (T-0051), admin grants, projects, members (T-0026), users (T-0331)

Admin-group screens of diff §6.1 on the finished backends of T-0050 (access requests),
T-0043 (device registry), T-0039 (admin grants), T-0012 / T-0044 (projects), T-0014 (members,
accounts) and T-0052 (invites). Everything goes through the generated client (`api.ts`,
`invites.ts`, `users.ts`); every string through `t()` (`admin.*`, `devices.*`, `personas.*`).

## Who sees what

- `useAdminAccess()` (`access.ts`): the projects where the user holds **PER-09** (from
  `GET /me/projects`) and whether they are a **System admin**. `/auth/me` has no such flag yet,
  so the client probes `GET /admin/grants?user_id=<self>`, which answers only System admins
  (TD-393). The server still checks every call; the flags only decide what is offered.
- Nav items `admin.users` (`/admin/users`), `admin.system` (`/admin/system/devices`) and
  `admin.accessRequests` (`/admin/access-requests`) carry `requires: "projectAdmin"` (`src/app/nav.ts`): shown to PER-09
  holders and System admins only.
- Profile → **My devices** (`/profile/devices`) is a top-bar link for every signed-in user.

- Backbone → **Projects** (`/backbone/projects`) is shown to every signed-in user: the client
  cannot tell who holds `create_projects` (TD-449), so "New project" is offered to all and a `403`
  is explained. Admin → System → **Admin grants** (`/admin/system/grants`) is a tab of the System
  screens (`SystemTabs`, `ui.tsx`) shown to System admins only; the API answers everyone else 403.
- Tenants never appear: no screen takes, shows or sends a tenant.

## Admin grants (`SystemGrantsPage`, diff §4.1, T-0039)

- `GET /admin/users` + `GET /admin/grants`: every account with two checkboxes, **System admin**
  (`system_admin`) and **Project Admin** (`create_projects`). Ticking `POST`s the grant, unticking
  `DELETE`s it; a `409` (the last active System admin) says so and the list is re-read.
- These are the only rights the screen offers. A note states that admin grants never give hold
  release, inspection sign-off or signing: those come only from qualified personas on a project
  (PER-02, PER-06, PER-07, PER-11; PER-15 co-signs), assigned under Members.

## Projects (`ProjectsPage`, diff §4.1, T-0040, T-0044)

- `GET /projects`, by code. **New project** asks for code (`[A-Za-z0-9][A-Za-z0-9._-]{0,31}`,
  checked before sending) and name only: status, time zone, currency and locale come from the
  deployment defaults. On success every `["projects", …]` query is refetched, so the creator's new
  PER-09 membership shows in the project picker at once. `403` (no `create_projects`) and `409`
  (code taken) are explained in the dialog.
- On projects the caller administers (PER-09 from `/me/projects`, or System admin): **Visible in
  lobby** (`PUT /projects/{id}/lobby`), **Settings** (name, status, time zone, currency, locale;
  only changed fields are `PATCH`ed; code and bounds: TD-450) and **Members**.

## Members (`MembersPage`, `/admin/projects/:projectId/members`, diff §4.1, edge spec §5)

- `GET /admin/projects/{p}/members`: each member's personas with site; team-derived grants are
  marked with their team and never edited (ADR 0008).
- **Add a member by e-mail**: `POST /admin/users/lookup` (exact e-mail, active accounts; no
  enumeration). The dialog chooses persona(s) + site (`PersonaFields`, `personas.tsx`, shared with
  the access-request approval) and, optionally, more of the caller's PER-09 projects: one action
  adds the personas to the user's existing individual grants on each project (`addToProjects`:
  read the members, merge, `PUT` the complete set, project by project). Grants merge by
  **(persona, site)** (`mergeGrants`): PER-07 may be held once per site, so no site is dropped.
  While PER-07 is chosen the other projects are disabled with a note: its site is a node of this
  project's hierarchy. A failure part-way names the project (the ones before it were saved).
- **Edit personas** sets the member's complete individual set on this project. Each PER-07 site
  the member holds is its own checkbox (kept unless unticked), and ticking PER-07 above adds
  another site, so an unrelated edit never revokes a site. **Remove** (after confirmation)
  revokes the individual personas; `409 managed_through_team` is explained.
- **Separation of duties** (TD-151): a `409 sod_warning` (PER-09 together with PER-02/06/07/11,
  counted by the server over the effective grants) stops the save and shows the warning naming the
  project; "Save anyway" sends `confirm_sod: true` for **that project only**, continues the rest
  with `false` and stops again, with a warning naming it, at the next project that needs it. Any
  change in the dialog withdraws a pending warning.
- **Open invitations** (`PendingInvites.tsx`, `invites.ts`, T-0053, diff §4.1 Re-send): below
  the members, `GET /admin/projects/{p}/invites` lists pending and expired (never accepted)
  invites with e-mail, persona(s) + site, `expires_at` and `last_sent_at`. **Resend** is disabled
  until `resend_available_at` (re-enabled by a timer) and asks first ("the previous link stops
  working"); the answer's new copyable link (`invite_link`, `invite_token` only) is shown once
  in a read-only field with **Copy** until closed, never cached or stored, and the screen says a
  new e-mail was sent. `429` → when re-sending is possible again (`Retry-After`). **Revoke**
  asks first. `409`/`404` (accepted or revoked meanwhile) is explained and the list re-read;
  accepted and revoked invites are not listed, so they offer no Resend. Hidden for a member
  without PER-09 (403).
- Creating invites is Admin → Users (below), which shows the same panel.

## Users (`UsersPage`, `/admin/users`, diff §4.1, §6.1, T-0331)

- **Accounts** (System admins only; `GET /admin/users` is not even called for anyone else):
  every account with e-mail, *Active* / *Deactivated* and *Internal staff*. **New account**
  (`POST /admin/users`, e-mail + name; `409` → "exists already") creates an account without
  credentials; the dialog says the person sets a password from an invite. **Deactivate** asks
  first (signed out at once), **Reactivate** does not; both `PATCH /admin/users/{id}` with
  `is_active` only. The caller's own row shows *(you)* and no button ("You cannot deactivate
  your own account"; the server does not refuse it yet unless it is the last System admin,
  TD-463). A `409` (the last active System admin) says so and the list is re-read.
- **Invite to a project** (PER-09 holders and System admins): the project (a PER-09 sees only
  the projects it administers, from `/me/projects`; a System admin every project of
  `GET /projects`), the e-mail and persona(s) + site (`PersonaFields`, PER-07 needs its site).
  `POST /admin/projects/{p}/invites` (`createInvite`, `invites.ts`). A `409 sod_warning`
  (TD-151) shows the warning naming the project; only **Send anyway** re-sends with
  `confirm_sod: true`, and any change in the form withdraws it. `429` → when inviting is
  possible again (`Retry-After`); `409 INVITE_CONFLICT` → try again. On success the screen says
  the e-mail was sent and shows the copyable link (`invite_link`, `invite_token` only) **once**
  in a read-only field with **Copy** until **Close**. The link is held only in component state:
  the mutation hands it to state and returns a result without it (`gcTime: 0`), so it is in no
  query or mutation cache. The form is cleared.
- Below the form, the chosen project's **Open invitations** (`PendingInvites`) with Resend and
  Revoke; it is re-read after each invite.

## Access requests (`AccessRequestsPage`, diff §4.1)

- Lists `GET /access-requests/pending` (the server scopes it: PER-09 projects, all for a System
  admin; 403 → "You do not administer any project"), oldest first: requester name and e-mail,
  project, message and age.
- **Approve** opens persona selection (PER-01…16). PER-07 (Site Inspector) needs its site: an
  asset node id field until the asset hierarchy has a picker (TD-394). A `409` with
  `sod_warning` (PER-09 + hold authority, T-0014) shows the warning; only "Approve anyway"
  re-sends with `confirm_sod: true`.
- **Reject** takes an optional reason (≤ 500 characters, counter) and says the requester sees it.
- A decided request leaves the list. A `409`/`404` (already decided or withdrawn) shows a
  neutral message and refetches the list.

## Devices (`DevicesPage`, diff §4.3)

- **Admin → System → Devices**: a System admin lists every user (`GET /admin/users`), a PER-09
  the members of its projects (`GET /admin/projects/{id}/members`); then each user's devices
  (`GET /users/{id}/devices`; one call per user until a scoped list exists, TD-392). Columns:
  user, platform + OS, app version, last seen, last sync, status.
- **Profile → My devices**: `GET /devices`, the same table without the user column.
- **Revoke** (active devices) asks for confirmation with the last-sync time and the wipe of
  unsynced work. A PER-09 revoke of a user who has other projects is refused with `409
  cross_project_warning`; the dialog then states that those projects' unsynced work is lost too
  and only "Revoke and wipe all projects" sends `confirm_cross_project_wipe: true`. The row then
  shows *revoked — wipe not yet confirmed*, and the wipe time once the device has confirmed it
  (on the next load).

## Dialogs (`ui.tsx`)

`Dialog` is a confirmation panel in the page flow, above the list it acts on (no overlay, so
nothing is hidden behind it and contrast stays measurable): labelled by its title, focus moves
in on open, Escape or Cancel closes. All controls are 48 px glove targets;
`admin.a11y.test.tsx` audits the screens with their dialogs open (members with the SoD
warning) in both themes on the field breakpoints. The component-level journey *System admin
appoints a Project Admin → they create a project → assign a user* is in `MembersPage.test.tsx`;
the Playwright journey waits for T-0027 (TD-448).
