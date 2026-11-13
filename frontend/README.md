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
| Tests | Vitest 5 + jsdom + Testing Library; `fake-indexeddb` for Dexie |
| Lint | ESLint 10 flat config (`eslint.config.js`): typescript-eslint `strictTypeChecked` + `stylisticTypeChecked`, react-hooks; `src/api/` ignored |

Requires Node `>=22.12`.

## Scripts

```bash
npm ci
npm run dev        # local dev server
npm run lint       # ESLint (type-aware typescript-eslint strict + react-hooks), zero warnings
npm run typecheck  # tsc --noEmit (strict)
npm run test       # vitest run (all suites)
npm run build      # typecheck + production build to dist/
npm run gen:api    # regenerate src/api/ from the backend OpenAPI schema (backend venv active)
```

## Layout

```
src/
  api/        generated API types (openapi.json, schema.ts) — never hand-edit (see src/api/README.md)
  app/        providers (AppRoot/AppProviders), services, router, shell layout, nav groups, query client
  auth/       token store, AuthProvider (login/logout/me), RequireAuth route guard
  db/         Dexie database (KerpDb) + offline outbox helpers
  i18n/       i18next instance + message catalogues (locales/en.ts)
  lib/api/    API client (bearer token, single-flight refresh rotation + one retry on 401)
  lib/cel/    offline CEL rule evaluation (@marcbachmann/cel-js), parity with the server
  pages/      route components
  projects/   project scope: ProjectDirectory seam, ProjectScopeProvider, top-bar ProjectSwitcher
  shells/     RegisterPage, DetailPage (gate banner, tabs, audit), Dynamic Form Renderer (README.md)
  test/       Vitest setup, fake backend (`fakeApi.ts`)
```

## Shell (T-0024)

- **Auth.** `/login` posts to `/api/v1/auth/login`. The access token is memory-only; the
  rotating refresh token is an `httpOnly` cookie the browser alone holds (T-0037, TD-203);
  `sessionStorage` keeps only a "signed in" marker, so a reload resumes the session via
  `/api/v1/auth/refresh` (same origin). A session ends 12 hours after sign-in. Every other route sits behind `RequireAuth`. Sign-out calls
  `/api/v1/auth/logout` and clears the session even when the server is unreachable.
- **Navigation (diff §6).** 8 groups in `src/app/nav.ts`: Overview, the 4 pillars
  (Logistics, Execution, Defense & Claims, Controls & Insights), Backbone, Documents,
  Admin. Module screens register under a group (mapping: TD-148).
- **Project scope.** The top-bar switcher selects one project or the **All-Projects**
  portfolio scope; the choice is stored per user in `localStorage`. Projects come from the
  `ProjectDirectory` seam, empty until the projects API exists (TD-145); sending the scope
  with requests is TD-146. The scope is a UI selection — the server binds the allowed
  projects from memberships (TD-053).
- **Design tokens.** `src/index.css` `@theme` defines semantic colours (`brand`, `surface`,
  `panel`, `ink`, `muted`, `line`, `pillar`, `success`, `warning`, `danger`); components use
  those utilities, not raw palette colours.
- **Services.** `AppServices` (`src/app/services.tsx`: api, tokens, projects, preferences)
  is injected through `AppRoot` so tests use the fake backend in `src/test/fakeApi.ts`.

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
