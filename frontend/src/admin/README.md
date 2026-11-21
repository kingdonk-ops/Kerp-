# `src/admin` — Admin screens: access requests and devices (T-0051)

Admin-group screens of diff §6.1 on the finished backends of T-0050 (access requests) and
T-0043 (device registry). Everything goes through the generated client (`api.ts`); every
string through `t()` (`admin.*`, `devices.*`, `personas.*`).

## Who sees what

- `useAdminAccess()` (`access.ts`): the projects where the user holds **PER-09** (from
  `GET /me/projects`) and whether they are a **System admin**. `/auth/me` has no such flag yet,
  so the client probes `GET /admin/grants?user_id=<self>`, which answers only System admins
  (TD-393). The server still checks every call; the flags only decide what is offered.
- Nav items `admin.system` (`/admin/system/devices`) and `admin.accessRequests`
  (`/admin/access-requests`) carry `requires: "projectAdmin"` (`src/app/nav.ts`): shown to PER-09
  holders and System admins only.
- Profile → **My devices** (`/profile/devices`) is a top-bar link for every signed-in user.

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
`admin.a11y.test.tsx` audits both screens with their dialogs open in both themes on the field
breakpoints.
