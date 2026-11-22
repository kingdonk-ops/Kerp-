# `src/admin` — Admin screens: access requests, devices (T-0051), admin grants, projects, members (T-0026), users (T-0331), runtime settings (T-0232), System console (T-0224), audit log and legal hold (T-0225), Communications (T-0229)

Admin-group screens of diff §6.1 on the finished backends of T-0050 (access requests),
T-0043 (device registry), T-0039 (admin grants), T-0012 / T-0044 (projects), T-0014 (members,
accounts), T-0052 (invites), T-0230 (settings registry), T-0218 / T-0219 / T-0221 / T-0222 / T-0223 (System console, audit explorer, legal hold)
and T-0226 / T-0227 / T-0228 (delivery ledger, template overrides, broadcast e-mail).
Everything goes through the generated client (`api.ts`, `invites.ts`, `users.ts`, `settings.ts`,
`system.ts`, `audit.ts`, `comms.ts`); every string through `t()` (`admin.*`, `devices.*`, `personas.*`, `unsubscribe.*`).

## Who sees what

- `useAdminAccess()` (`access.ts`): the projects where the user holds **PER-09** (from
  `GET /me/projects`) and whether they are a **System admin**. `/auth/me` has no such flag yet,
  so the client probes `GET /admin/grants?user_id=<self>`, which answers only System admins
  (TD-393). The server still checks every call; the flags only decide what is offered.
- Nav items `admin.users` (`/admin/users`), `admin.system` (`/admin/system/devices`),
  `admin.accessRequests` (`/admin/access-requests`) and `admin.communications`
  (`/admin/communications/broadcasts`) carry `requires: "projectAdmin"` (`src/app/nav.ts`): shown to PER-09
  holders and System admins only.
- Profile → **My devices** (`/profile/devices`) is a top-bar link for every signed-in user.

- Backbone → **Projects** (`/backbone/projects`) is shown to every signed-in user: the client
  cannot tell who holds `create_projects` (TD-449), so "New project" is offered to all and a `403`
  is explained. Admin → System → **Deployment**, **Queues**, **Upload quarantine** (T-0224),
  **Settings** (`/admin/system/settings`), **Audit log** (`/admin/system/audit`, T-0225) and **Admin grants** (`/admin/system/grants`) are tabs of the System screens (`SystemTabs`, `ui.tsx`) shown to System
  admins only; the API answers everyone else 403. The tabs are one list (`SYSTEM_TABS`, with
  `systemAdminOnly`): a new System screen (T-0224, T-0225, …) adds its row there and its route in
  `src/app/routes.tsx`. The Admin nav item `admin.system` still opens Devices, the one System
  screen a PER-09 may use.
- Tenants never appear: no screen takes, shows or sends a tenant.

## Admin grants (`SystemGrantsPage`, diff §4.1, T-0039)

- `GET /admin/users` + `GET /admin/grants`: every account with two checkboxes, **System admin**
  (`system_admin`) and **Project Admin** (`create_projects`). Ticking `POST`s the grant, unticking
  `DELETE`s it; a `409` (the last active System admin) says so and the list is re-read.
- These are the only rights the screen offers. A note states that admin grants never give hold
  release, inspection sign-off or signing: those come only from qualified personas on a project
  (PER-02, PER-06, PER-07, PER-11; PER-15 co-signs), assigned under Members.

## Runtime settings (`SystemSettingsPage`, `/admin/system/settings`, diff §4.7, §6.5, T-0232)

- System admins only: for anyone else the screen says so and calls nothing (a `403` from the
  server says the same).
- **Runtime settings**: `GET /settings/registry`, grouped by category (headings
  `admin.settings.categories.*`; a category without a label shows its key). Each entry gets an
  input for its type: `int` / `float` a number field with `min` / `max`, `bool` a checkbox,
  `enum` a select of its choices, `string` a text field, `int_list` comma-separated whole
  numbers. Under it: the bounds, the deployment cap with its environment variable (the effective
  maximum is the lower of the two), the default (or "Default value" while none is stored) and
  whether field devices receive it.
- Labels come from `admin.settings.entries.<key>`. An entry declared by a later task without a
  label shows its key and the server's description until the task adds its label (TD-532).
- **Bounds are checked client-side** (`checkSettingValue`, `settings.ts`: type, minimum, maximum
  lowered by the cap, length, items) and an invalid value cannot be saved. Each entry saves on
  its own (`PATCH /settings/registry/{key}` with the value in its JSON type); the server's
  `422` (checks the client cannot make: IANA zone, ISO 4217, BCP 47, `offline_grace_hours` ≤
  24 × `offline_window_days`) is shown inline at the entry, never clamped. A save replaces the
  entry in the cached list.
- **Deployment configuration** (TD-046, TD-398): `GET /settings/deployment-config`, a read-only
  list with no inputs: refresh-token lifetime, PIN idle lock, PIN lockout schedule, region,
  object storage region, mail transport and sender, malware scanner, upload limits (the caps of
  the registry) and the AI provider (`none`). Nothing here is ever a secret (the API's
  allow-list); it changes only with a redeploy.

## System console (`/admin/system/deployment`, `/queues`, `/uploads`; diff §4.7, §6.5, T-0224)

System admins only, like Settings: `useSystemAdminGate()` (`ui.tsx`) enables the queries only for
a System admin; anyone else gets `admin.system.forbiddenConsole` and nothing is called (a `403`
says the same). Registers are card lists (`CARD_ITEM`, `Facts`), not tables, so nothing scrolls
sideways on a field phone (the `RegisterPage` shell has no row actions or server paging yet:
TD-537); actions confirm in a `Dialog` first. Fixtures: `systemFixtures.ts`.

- **Deployment** (`SystemDeploymentPage`): `GET /admin/system/stats` as cards (projects, users,
  devices; object storage per storage class with objects and size, its **as of** time or "not
  counted yet", and **Refresh totals**: `POST .../stats/object-store/refresh`, `409` = a count is
  already running; disabled while `refreshing`), and the module counts (labels
  `admin.deployment.modules.*` / `moduleCounts.<module>.<key>`, else the key).
  `GET /admin/system/health`: overall status, checked time and a tile per probe with status and
  latency (no answer when `null`); **Check again** re-reads it.
- **Queues** (`SystemQueuesPage`): `GET /admin/system/queues` (per queue queued, running,
  scheduled, failed, oldest waiting; the cron tasks with last run, outcome and next run) and
  `GET .../queues/failed` (50 per page; kind, queue, project code or "Deployment", attempts,
  error class, message; never a payload). **Retry** (`POST .../failed/{id}/retry`; `409` = no
  longer failed). **Abort** takes a pasted job id (UUID checked first): the API lists no queued
  or running jobs (TD-536); `409` = not queued or running, `404` = unknown.
- **Upload quarantine** (`SystemUploadsPage`): `GET /admin/system/uploads` with status, reason
  and project filters (server-side, 50 per page with the total) — rows of every project
  (ADR 0016). Each row: file, project, status, reason (`admin.quarantine.reasons.*`, else the
  code), module, size and type, signature for `infected`, **Object deleted** when the bytes are
  gone. **Re-check** only for `verifying` rows with their bytes (`409` `NOT_VERIFYING` /
  `CHECK_RUNNING` / `OBJECT_GONE` explained). `GET .../uploads/counts` (unfiltered) gives the
  counts per project and status, the reason and project filter options, and **Sweep now** per
  project (`POST .../uploads/sweep?project=`).

- **Audit log** (`SystemAuditPage`, `/admin/system/audit`, T-0222, T-0225; `audit.ts`):
  `GET /admin/system/audit`, newest first, 50 per page with the keyset cursor (Previous / Next
  keep the cursors of the pages seen). Filters, applied on **Apply filters**: actor id, device id,
  chain-key prefix, `event`, a boolean payload `flag` (e.g. `sod_warning`) and a date range in
  the user's time zone (from local midnight; the "to" day is included: `created_to` is the next
  local midnight, the API bound is exclusive). Ids that are not UUIDs and malformed flag keys are
  marked and not sent (the server would answer `422`). Each entry is a card (event, chain, seq,
  project or *Tenant level*, time, actor, device, source) with **Hashes and payload**
  (content hash, previous hash, hash version, the payload as JSON) and **Verify chain**
  (`POST .../audit/verify` with the entry's `chain_key` and `project_id`; inline: intact with
  length and head hash, or broken at a seq with the problem count and the first 100 problems;
  `404` = the chain no longer exists for the caller). **Verify all** (`POST .../audit/verify-all`,
  `202`) polls `GET /jobs/{id}` every second while queued or running (status, progress bar) and
  then shows the job's result (chains, entries, broken chains, first broken chain and seq) or
  its error. Nothing here writes to the ledger except the verifications the server records.

## Communications (`/admin/communications/*`, diff §4.7, §6.5, T-0229; `comms.ts`, `commsUi.tsx`)

Tabs (`COMMS_TABS`, `CommsTabs` in `ui.tsx`): **Broadcasts** for PER-09 holders and System admins;
**Delivery** and **E-mail templates** for System admins only (hidden from everyone else; the API
answers them 403). `useCommsGate(systemAdminOnly)` enables the queries only for a caller who may
use the screen; anyone else gets `admin.comms.forbidden` / `forbiddenSystem` and nothing is
called. Fixtures: `commsFixtures.ts`. The three screens and `/unsubscribe` are lazy routes
(`lazy` in `src/app/routes.tsx`): their code is a separate chunk loaded on first visit, which keeps
the main bundle under Vite's 500 kB warning.

- **Broadcasts** (`CommsBroadcastsPage`, T-0228, TD-399): the composer. A System admin chooses
  *All active users* (`scope: tenant`) or projects of `GET /projects`; a PER-09 gets no tenant
  option and only its own PER-09 projects (from `/me/projects`; `GET /projects` is not called).
  Optional audience category (`internal` / `external` / `client`) and personas (PER-01…16).
  **Count recipients** posts the audience to `POST /notifications/broadcasts/preview` and shows
  the count (never an address); any change to the audience withdraws the count, and **Send** is
  enabled only with a fresh count above 0, a one-line subject (≤ 200) and a plain-text body
  (≤ 10 000, line breaks kept). Send asks first ("goes to N people; cannot be recalled"), then
  `POST /notifications/broadcasts` (202). Refusals are explained, never the server text: `403`
  (`IMPERSONATION_ACTION_FORBIDDEN` while logged in as another user, else not allowed), `404`
  (a project not the caller's), `422` (nobody in the audience; any other `422` says the broadcast
could not be sent: TD-610 for stable codes), `429` (5 per 24 h, with the time
  from `Retry-After`). Below: **Sent broadcasts** (`GET /notifications/broadcasts`, 50 per page),
  each a card with status, audience (project codes), recipients, queued, skipped, created and
  finished; **Delivery** opens `GET /notifications/broadcasts/{id}` and shows its count cards
  (the only delivery stats a PER-09 sees, diff §4.7).
- **Delivery** (`CommsDeliveryPage`, T-0226): count cards from `GET /mail/stats` (sent, delivery
  rate as a percentage or "—" when nothing was sent, delivered, bounced, complaints, failed,
  suppressed, queued) and the counts per template, for a period (local days; the "to" day is
  included) and template applied on **Apply**. **History** (`GET /mail/sends`, 50 per page):
  template, status, period and an exact-address lookup (checked as an address before sending;
  hashed by the server, never shown back; it travels in the query string, TD-574). Each send is
  a card with the masked recipient only (never the hash), status, attempts, error code, queued
  and last-change times, and whether it came from a broadcast.
- **E-mail templates** (`CommsTemplatesPage`, T-0227, diff §4.7 'Editor details'): a
  **mail-health banner** when the deployment cannot send, from `GET /settings/deployment-config`
  (`mailProblems`: the `log` transport delivers nothing; no sender address). It never shows the
  configuration's values. Sender verification and bounce wiring have no signal yet (TD-609).
  The templates of `GET /mail/templates` are listed grouped by audience (internal staff, client,
  witness / external party, deployment-wide), marked *Required* and *Reworded*. **Edit** opens
  the editor for one template and language: subject, opening and closing text and (templates
  with a link only) the button label, prefilled with the current override or the shipped text.
  The declared placeholders sit beside the editor as buttons that insert `{name}` at the cursor
  of the field edited last. The client refuses what it can check (empty subject or button
  label, empty texts of a required template, lengths 200 / 2000 / 2000 / 80); the server's
  `422` is shown per field from its `loc` (empty, too long, line break, HTML, link, placeholder),
  never its text. `action_label` is sent as `null`, never `""`, when empty or the template has
  no link. **Save** (`PUT`, a new revision; `409` reloads the texts and says so), **Preview**
  (`POST .../preview` with the draft: subject, plain text, and the HTML **only inside an
  `<iframe sandbox="">`**: no scripts, no same-origin, never in the page itself), **Send a test
  to me** (`POST .../test-send`, the caller's own address; `429` with the retry time), **Reset to
  default** (asks first; `DELETE`, revisions kept) and **Revisions** (`GET .../revisions`, newest
  first, the one in use marked; **Load** copies a revision into the editor).
- **Unsubscribe** (`src/pages/UnsubscribePage.tsx`, `/unsubscribe#<token>`, TD-601): public,
  outside the auth guard. The token is read from the fragment once, kept in memory and removed
  from the address bar. Nothing is posted on load (link scanners prefetch): **Unsubscribe**
  posts `{token}` in the body to `POST /notifications/broadcasts/unsubscribe` (never in a URL),
  and the page shows the same neutral confirmation for every answer the server gives (it gives
  `200` for every token); a missing or malformed token says the link is incomplete; a server
  failure offers a retry.

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

- **Project section** (`ProjectAdminPanel.tsx`, T-0223, T-0225) at the top of the page: the
  legal-hold state (`legal_hold` of `GET /projects`; the reason is only in the ledger). System
  admins get **Put on legal hold** / **Lift legal hold**: a dialog with a mandatory reason
  (trimmed, ≤ 500 characters) sends `PUT /admin/system/projects/{p}/legal-hold` `{on, reason}`
  and the project list is re-read. **Delete project** (PER-09 of the project or a System admin only) asks first and sends `DELETE /projects/{p}`;
  `409 LEGAL_HOLD_ACTIVE` shows `admin.projectAdmin.conflicts.LEGAL_HOLD_ACTIVE` (never the
  server text), `409 team_links_present`, `403` and `404` are explained; on success the screen
  returns to Backbone → Projects.
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
  your own account"). The server refuses it anyway (T-0085): a `409` with
  `code: CANNOT_DEACTIVATE_SELF` (stale list, second tab) shows `admin.users.cannotDeactivateSelf`;
  any other `409` (the last active System admin) says so. Either way the list is re-read.
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
