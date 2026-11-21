# Lobby screens (T-0041)

The user side of the lobby and the access-request lifecycle (diff §4.1, §6.1). Route `/lobby`
(`src/pages/LobbyPage.tsx`): users with no project land here (T-0042); users with projects reach
it from the picker's **"Browse / join another project"**. The admin side (inbox, decisions) is
`src/admin/` (T-0051).

| File | What |
|---|---|
| `api.ts` | Typed calls on the generated client: lobby list, code lookup, `mine`, file, withdraw |
| `ActiveRequests.tsx` | **Active requests** panel (`GET /access-requests/mine`) with status chips and actions |
| `CodeLookup.tsx` | Project-code entry, for every user |
| `LobbyList.tsx` | Lobby list (internal users only) and `ProjectEntry` ('Open' / 'Request access') |
| `RequestForm.tsx` | 'Request access' with an optional message (≤ 1000 characters) |
| `messages.ts` | Refusal messages, "available from <local time>", `useHasPassed` (cooldown timer) |
| `useOpenProject.ts` | Opens a project: reloads `/me/projects`, picks it, goes to `/` |

## Behaviour

- **Lobby list.** Only when `/auth/me` says `is_internal`; external users get no list (no
  request for it is made) and a hint that projects are joined by code or invite. Each project
  shows code and name; a project the user already belongs to (`member`) offers **Open**, any
  other **Request access** with an optional message and **no** lookup ticket (lobby-visible).
- **Project code.** Every user. The trimmed code is looked up exactly
  (`GET /lobby/projects/by-code/{code}`); a hit offers **Request access**, which sends the
  lookup ticket of that lookup (T-0050; a code never grants access, TD-202), or **Open** for a
  member. Unknown code: a neutral "not found". 429: a "too many look-ups" message. A 404 on a
  request that carried a ticket (e.g. the 15-minute ticket expired) asks the user to enter the
  code again; a 404 without a ticket (the project left the lobby or was deleted) says the
  project is no longer available.
- **Active requests.** Every request from `mine` with code, name and a status chip
  (`pending`, `approved`, `rejected`, `withdrawn`). Pending → **Withdraw**; approved →
  **Open project**; rejected → the decider's reason when given and **Request again**, disabled
  with "Request again is available from <local time>" until `request_again_at`; a timer
  enables it at that moment (an unparsable time counts as passed; the server still answers
  409), and the button is described by the "available from" text (`aria-describedby`). Request again files a new request **without a ticket** (an
  earlier rejected or withdrawn request makes one unnecessary, T-0050); a 409
  `ACCESS_REQUEST_COOLDOWN` shows the same message with its `retry_after`. A withdrawn request
  has no cooldown, so it offers Request again at once. 409 "already pending" / "already a
  member" have their own messages.
- **Refresh.** The panel reloads every time the lobby opens and when the window regains
  focus: it is the requester's only outcome channel (no project notification reaches a
  non-member). When it shows an approval for a project the picker does not list yet, the
  picker's `/me/projects` is reloaded once, so the project appears without a sign-in.

## Tests

`LobbyPage.test.tsx` (mocked server; includes the external-user journey with a fake clock for
the cooldown) and `lobby.a11y.test.tsx` (axe, 48 px targets, both themes, field breakpoints).
The Playwright journey waits for the e2e harness (T-0027): TD-457.
