# `src/invite` and `src/password` — invite link, first password, forgot / reset password (T-0053)

Screens of diff §4.1 (*Invites*, *External invitees create their own password*) and §4.2
(*Self-service password reset*) on the merged backends of T-0052, T-0059 and T-0077. All
requests go through the generated client; every string through `t()` (`invite.*`,
`password.*`).

## Tokens are secrets in a URL

- Invite links are `/invite#<invite_token>` (copied by the inviter) or
  `/invite#<invite_token>.<setup_token>` (the e-mailed link); reset links are
  `/reset-password#<token>`. Only the URL **fragment** carries a token, so it never reaches a
  server or a `Referer`. `index.html` sets `<meta name="referrer" content="no-referrer">` for
  every SPA page; the HTTP header for the SPA host is a deployment concern (TD-334).
- The page reads the fragment once (`parseInviteFragment`: `token_urlsafe` characters only,
  anything else is treated as an unusable link and nothing is sent), then **replaces the URL**
  without it before anything else happens. Tokens are sent only in POST bodies.
- The invite is kept for the sign-in in this tab's **`sessionStorage`** only
  (`kerp.invite.pending`, `AppServices.session`): never `localStorage`, never a query key or a
  log. It is cleared when the invite is used or refused, when the sign-in screen is opened
  outside the invite flow, and on the SSO return. The reset token stays in component memory.
- Server refusals are mapped to a few translated, neutral messages by `code`; a server
  `detail` is never rendered.

## `/invite` (`InvitePage.tsx`)

- **Signed in:** `POST /admin/invites/preview` → "Join project" (project, inviter) →
  `POST /admin/invites/accept` → the project is remembered as the scope (T-0042) with a fresh
  `/me/projects`, and `/` opens it (the lobby is skipped).
- **Not signed in, e-mailed link:** `POST /admin/invites/setup-preview`. `can_set_password`
  → **Create your password** (password + confirmation, show/hide, paste allowed, live length
  hint; no Microsoft button) → `POST /admin/invites/accept-with-password` signs in and opens
  `joined_project_id`. An account that has credentials (`can_set_password: false` or `409
  INVITE_SIGN_IN_REQUIRED`) goes to the sign-in screen with the invite.
- **Not signed in, copied link:** the normal sign-in screen (router state `{ invite: true }`):
  password login, the "Sign in with Microsoft" form (T-0055) and the magic-link request all
  carry `invite_token` in their bodies; the screen also offers **Send me an e-mail to set my
  password** (the reset request, for an account without credentials) and says to use the
  password (or Microsoft, company staff only) when no magic link arrives (only PER-14 invites
  get one, decided by the server). The sign-in's `joined_project_id` opens the project; its
  `invite_error` (also from the SSO return, `?invite_error=`) shows the neutral message.
- **Refusals:** wrong user (with **Sign out**, back into the invite sign-in), expired /
  revoked / used (`INVITE_GONE`, `INVITE_CONFLICT`) and anything else, each with a link to the
  lobby.

## Password screens (`src/password/`)

- **Forgot password?** on the sign-in screen → `/forgot-password` (`PasswordEmailPage`): one
  e-mail field, `POST /auth/password-reset/request`, the same neutral confirmation for every
  address (429 → "wait"). The invitee variant ("Set your password") is the same request.
- **`/reset-password#<token>`** (`ResetPasswordPage`): `NewPasswordForm` →
  `POST /auth/password-reset/confirm`; success ends this tab's session too (the server revoked
  them all) and routes to sign-in with a translated notice; `410` → "request a new link";
  policy `422` → `password.policy.<code>` with the server's `min_length` / `max_length`.
- `NewPasswordForm`: the length hint counts code points; the minimum shows once the server has
  stated it in a refusal, since no public endpoint exposes the policy yet (TD-365).

## Open invitations panel (`src/admin/PendingInvites.tsx`)

On the project members screen: see `src/admin/README.md`.

## Not here yet

- Android App Links for `/invite` and `/reset-password` (Capacitor build, T-0143) and the
  Playwright journeys (T-0027): see the T-0053 notes.
- The magic link's landing in the SPA is TD-147.
