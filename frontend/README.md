# KERP frontend

Single-page app for the KERP Field Operations Platform (`docs/spec/`).

## Stack

| Concern | Choice |
|---|---|
| Build / dev server | Vite 8 |
| UI | React 18 (pinned `^18.3`), TypeScript 5.9 `strict` |
| Styling | Tailwind CSS v4 via `@tailwindcss/vite` (`src/index.css` → `@import "tailwindcss"`) |
| Routing | React Router v7 (library / data-router mode, `src/app/routes.tsx`) |
| Server state | TanStack Query v5 (`src/app/queryClient.ts`) |
| API client | `openapi-fetch` typed by the generated `src/api/schema.ts` (`src/lib/api/client.ts`) |
| Offline outbox | Dexie 4 on IndexedDB (`src/db/`) |
| Rule expressions | CEL via `@marcbachmann/cel-js` (`src/lib/cel/`) |
| i18n | i18next + react-i18next (`src/i18n/`) |
| Tests | Vitest 5 + jsdom + Testing Library; `fake-indexeddb` for Dexie. Accessibility suites (`*.a11y.test.tsx`): Vitest browser mode in headless Google Chrome (Playwright) + axe-core |
| Lint | ESLint 10 flat config (`eslint.config.js`): typescript-eslint `strictTypeChecked` + `stylisticTypeChecked`, react-hooks; `src/api/` ignored |

Requires Node `>=22.12` and, for `npm run test`, an installed Google Chrome (`KERP_CHROME_PATH`
points at another Chrome/Chromium binary; TD-260).

## Scripts

```bash
npm ci
npm run dev        # local dev server
npm run lint       # ESLint (type-aware typescript-eslint strict + react-hooks), zero warnings
npm run typecheck  # tsc --noEmit (strict)
npm run test       # vitest run: `unit` (jsdom) + `a11y` (headless Chrome) projects
npx vitest run --project unit   # jsdom suites only
npm run build      # typecheck + production build to dist/
npm run perf       # NFR performance budgets (headless Chrome, 4x CPU throttle; warn-only, see below)
npm run gen:api    # regenerate src/api/ from the backend OpenAPI schema (backend venv active)
```

## Layout

```
src/
  admin/      Admin screens: access-request inbox, devices (admin + My devices), revoke (README.md)
  api/        generated API types (openapi.json, schema.ts) — never hand-edit (see src/api/README.md)
  app/        providers (AppRoot/AppProviders), services, router, shell layout, nav groups, query client
  auth/       token store, AuthProvider (login/logout/me), RequireAuth route guard, Entra ID SSO button + codes
  db/         Dexie database (KerpDb) + offline outbox helpers
  i18n/       i18next instance + message catalogues (locales/en.ts)
  invite/     `/invite#<token>`: join, invite carried through sign-in, first password (README.md)
  password/   forgot password (`/forgot-password`), `/reset-password#<token>`, new-password form
  lib/api/    API client (bearer token, single-flight refresh rotation + one retry on 401)
  lib/cel/    offline CEL rule evaluation (@marcbachmann/cel-js), parity with the server
  lib/fields/ the one field-type palette and answer shapes (mirrors backend app/core/fields)
  lobby/      lobby screens: lobby list, project-code entry, Active requests panel (README.md)
  perf/       NFR performance budgets: budgets.json (single config), measurements, CI reporter
  pages/      route components (incl. the lobby route `/lobby`, built from `src/lobby/`)
  projects/   project scope: `/me/projects` fetch + portfolio personas, ProjectScopeProvider, top-bar ProjectSwitcher, capability gate (A/S)
  shells/     RegisterPage, DetailPage (gate banner, tabs, audit), Dynamic Form Renderer (README.md)
  test/       Vitest setup, fake backend (`fakeApi.ts`), axe harness (`a11y.ts`), timing budget (`timeouts.ts`: Testing Library waits up to 5 s project-wide, unit tests 20 s — do not add per-test 1 s waits)
  theme/      display themes: ThemeProvider (per-user preference), ThemeToggle
```

## Shell (T-0024)

- **Auth.** `/login` posts to `/api/v1/auth/login`. The access token is memory-only; the
  rotating refresh token is an `httpOnly` cookie the browser alone holds (T-0037, TD-203);
  `sessionStorage` keeps only a "signed in" marker, so a reload resumes the session via
  `/api/v1/auth/refresh` (same origin). A session ends 12 hours after sign-in. Every other route sits behind `RequireAuth`. Sign-out calls
  `/api/v1/auth/logout` and clears the session even when the server is unreachable.
- **Sign in with Microsoft (T-0055, diff §4.5).** `/login` asks `GET /api/v1/auth/config`
  (T-0054). When it reports `sso`, **"Sign in with Microsoft"** is the primary, default action:
  first on the screen, visually primary, focused when it appears (unless the user is already in
  a field), with a short hint for company staff. Password sign-in stays below it for everyone,
  internal users included (never hidden by e-mail domain). Without `sso`, or when the
  configuration cannot be read, the screen is password only, as before. (The magic-link
  request is offered only on the invite sign-in, T-0053; the general one is TD-147.) The button is a plain HTML
  form to the server's `start_url` (accepted only as a same-origin `/api/...` path, since the
  pre-auth and session cookies are same-origin): a `GET` top-level navigation, or a `POST`
  with the `invite_token` in the body when a pending invite is passed (T-0053).
- **Invites and passwords (T-0053, diff §4.1, §4.2).** `/invite#<token>` (join, sign-in
  carry-through, "Create your password"), "Forgot password?" → `/forgot-password` and
  `/reset-password#<token>` live outside the auth guard; tokens come only from the URL fragment,
  which is removed at once: `src/invite/README.md`.
  The callback redirects to **`/login/sso-return`** (`src/pages/SsoReturnPage.tsx`):
  `?status=signed_in` → the tab adopts the session the server set as the `httpOnly` refresh
  cookie (`TokenStore.adoptCookieSession`: only the "signed in" marker is stored; the access
  token comes from `/auth/refresh`, memory only) and lands on `/` like any sign-in (diff
  §4.1); `&joined_project_id=<id>` is remembered as the scope first, so that project opens.
  `?error=<code>` → the sign-in screen with the translated refusal (`auth.sso.errors.*`, codes
  in `src/auth/sso.ts`; unknown values show the generic `SSO_FAILED`). Nothing else from the
  URL is shown or logged. `index.html` sets `<meta name="referrer" content="no-referrer">`
  (TD-334). The e2e journey against the fake OIDC provider waits for the Playwright harness
  (T-0027): TD-446.
- **Navigation (diff §6, §6.1).** 8 groups in `src/app/nav.ts`: Overview, the 4 pillars
  (Logistics, Execution, Defense & Claims, Controls & Insights), Backbone, Documents,
  Admin. `NAV_ITEMS` holds the item mapping of diff §6.1 (e.g. Execution: inspections / QMS,
  ITPs, field time, progress, look-ahead); an item shows under its group only once its
  screen exists — the module task that ships the screen gives the item its `path`. An item
  with `requires: "projectAdmin"` shows only to PER-09 holders and System admins. Built so
  far: Admin → Users (T-0331), Admin → System (Devices) and Admin → Access requests (T-0051,
  `src/admin/README.md`).
  The top bar links Profile → **My devices** (`/profile/devices`).
- **Project picker and landing (T-0042, diff §4.1, §6.1).** The top-bar picker lists the
  caller's projects from `GET /api/v1/me/projects` (memberships, T-0014) as "code — name",
  ordered by name, then code. **All-Projects** is offered only to users holding a portfolio
  persona (PER-01/10/11/12/13) on any project. The picked scope is remembered per user on this
  client (`localStorage` key `kerp.scope.<user id>`) and removed once it is no longer allowed
  (project gone from `/me/projects`, or All-Projects without a portfolio persona). Landing on
  `/` after sign-in: one project → it opens; several → the remembered one, else the first by
  name; none → the **lobby** (`/lobby`). Deep links are kept.
  **System admin (ADR 0016, T-0276).** For a `system_admin`, `/me/projects` returns every live
  project of the tenant with `access: member | system_admin`. A project reached only through
  the grant is listed as "code — name (Admin access)" (the badge is option text, so type-ahead
  finds it) and, when picked, a text badge "Admin access" next to the picker describes it
  (`aria-describedby`, tooltip with the reason). All-Projects is offered to a `system_admin`
  too and covers every project. Landing: a `system_admin` with no membership opens the
  remembered project, else stays on the home page with "Choose a project…" in the picker (never
  the lobby); with memberships, the fallback is the first project it is a member of.
  **A/S actions** go through `RequireCapability` (`src/projects/capabilities.tsx`), which
  derives the caller's capabilities per project from its personas (mirror of
  `app.core.auth.access.PERSONAS`; the API does not send them yet, TD-500): `system_admin`
  access gives `V`/`C`/`E`/`admin`, never `A` or `S`. In the All-Projects scope every row
  passes its own project. The picker always shows
  **"Browse / join another project"**, a link to the lobby. The lobby screens (T-0041:
  lobby list, project-code entry, Active requests) are described in `src/lobby/README.md`. Sending the scope with requests is TD-146; the
  scope is a UI selection — the server binds the allowed projects from memberships (TD-053).
  No tenant is shown anywhere in the shell.
- **Design tokens.** `src/index.css` `@theme` defines semantic colours (`brand`, `surface`,
  `panel`, `ink`, `muted`, `line`, `pillar`, `focus`, `success`, `warning`, `danger`) and the touch
  spacing (`touch`, `touch-gap`; see *Field ergonomics*); components use
  those utilities, not raw palette colours.
- **Services.** `AppServices` (`src/app/services.tsx`: api, tokens, preferences, crossTab)
  is injected through `AppRoot` so tests use the fake backend in `src/test/fakeApi.ts`.

## Field ergonomics (T-0046, diff §6.3)

- **Glove targets.** `--spacing-touch: 48px` (48 CSS px = 48 dp) gives `min-h-touch`,
  `min-w-touch`, `size-touch`, `h-touch`; `--spacing-touch-gap: 8px` gives `gap-touch-gap`
  between neighbouring targets. Every control of the shell, sign-in page, `RegisterPage`,
  `DetailPage` and `DynamicForm` is at least 48 × 48 px (checkboxes are `size-touch`). On phones
  (< `md`) the navigation wraps into rows above the content; from `md` it is a side bar.
- **Outdoor high-contrast theme.** `<html data-theme="outdoor">` swaps the semantic colour
  tokens in `src/index.css` for a sunlight set (text ≥ 7:1, WCAG AAA); the standard theme
  meets WCAG 2.1 AA. Focus rings use `--color-focus` (3 px outdoors). Components keep using
  the semantic utilities, so they follow the theme. The top bar and the sign-in page carry the
  toggle (`ThemeToggle`, `aria-pressed`).
- **Remembered per user.** `ThemeProvider` (`src/theme/`) stores the choice in the preferences
  storage under `kerp.theme.<userId>`; another user on the same device gets their own. The
  sign-in page (no user yet) follows the device's last choice, `kerp.theme.device`.
  Client-side only, like the project scope; a server-side preferences store is TD-258. An OS
  "more contrast" default is TD-259.
- **Checks.** `*.a11y.test.tsx` run axe-core (`src/test/a11y.ts`) in headless Chrome on the
  field breakpoints (360 and 412 px phones, 800 × 1280 and 1280 × 800 tablets), in both themes,
  over the app shell, sign-in page, `RegisterPage`, `DetailPage` (all gate states, tabs, audit
  log) and `DynamicForm` (with errors). They fail on any WCAG 2.1 A/AA violation, on axe
  `target-size` configured to 48 px, on a contrast result axe cannot decide, on enhanced
  contrast (7:1) in the outdoor theme, and on any rendered control under 48 × 48 px
  (`glove-target`: axe accepts a small target with free space around it; gloves do not). New
  field screens add a case there.

## Performance budgets (T-0045, diff §6.3)

- **One config.** `src/perf/budgets.json` holds the budgets (form open: 300 questions,
  median of 5 runs, < 1500 ms; delta sync: 1,000 records, < 30 s), the CPU throttle (4x), the
  simulated 4G profile (9000/3000 kbit/s, 85 ms RTT) and `enforcement` (`warn` now; T-0146 sets
  `fail`). `src/perf/budgets.ts` validates it. The delta-sync budget is measured by T-0145 with
  the same config. The reference device is not named yet and the profile is a planner default:
  TD-205.
- **Measurement.** `npm run perf` runs `src/**/*.perf.{ts,tsx}` (config `vitest.perf.config.ts`,
  not part of `npm run test`) in headless Chrome; `cdp()` applies the CPU throttle.
  `formOpen.perf.tsx` renders a synthetic 300-question template (`syntheticTemplate.ts`: all
  data types, `visible_if` / `required_if` / validation CEL rules, help text) in `DynamicForm`
  and times from the render call to the next painted frame with the first control focused
  ("first interactive render"), 5 fresh mounts. The test fails only if the measurement is
  broken (not all questions rendered, form not interactive).
- **Reporting.** The Vitest reporter `src/perf/reporter.ts` takes the samples from the test
  meta, computes the median (`report.ts`), prints the table, appends it to
  `$GITHUB_STEP_SUMMARY`, writes `perf-results.json` and prints a `::warning` annotation per
  breach. With `enforcement: "fail"` it prints `::error` and fails the run instead.
- **CI.** Job `perf` in `.github/workflows/ci.yml` runs on every PR and push when the frontend
  exists, uploads `perf-results.json`, and is part of `ci-gate` (a breach warns; a broken
  harness fails).

## Shells (T-0025)

Module pages are built on the reusable shells in `src/shells/` (consolidation §6):
`RegisterPage`, `DetailPage` and `DynamicForm`. Details and usage: `src/shells/README.md`.

## Rules

- **Every user-visible string goes through `t()`.** Add keys to
  `src/i18n/locales/en.ts`; keys are type-checked (`src/i18n/i18next.d.ts`) and a
  test asserts every key resolves.
- **Offline writes go through the Dexie outbox**, table `outbox_mutations`, primary
  key `client_op_id` (client-generated UUID). `enqueueMutation` is idempotent on
  that key. Drain/replay, vector clocks and the offline indicator are T-0126/T-0127.
- Tests render the app through `AppRoot` with `initialEntries` (memory router) so
  all providers are exercised.
- Synthetic fixtures only.
