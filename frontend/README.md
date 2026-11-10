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
| Offline outbox | Dexie 4 on IndexedDB (`src/db/`) |
| Rule expressions | CEL via `@marcbachmann/cel-js` (`src/lib/cel/`) |
| i18n | i18next + react-i18next (`src/i18n/`) |
| Tests | Vitest 5 + jsdom + Testing Library; `fake-indexeddb` for Dexie |

Requires Node `>=22.12`.

## Scripts

```bash
npm ci
npm run dev        # local dev server
npm run typecheck  # tsc --noEmit (strict)
npm run test       # vitest run (all suites)
npm run build      # typecheck + production build to dist/
npm run gen:api    # regenerate src/api/ from the backend OpenAPI schema (backend venv active)
```

## Layout

```
src/
  api/        generated API types (openapi.json, schema.ts) — never hand-edit (see src/api/README.md)
  app/        providers (AppRoot/AppProviders), router, layout, query client
  db/         Dexie database (KerpDb) + offline outbox helpers
  i18n/       i18next instance + message catalogues (locales/en.ts)
  lib/cel/    offline CEL rule evaluation (@marcbachmann/cel-js), parity with the server
  pages/      route components
  test/       Vitest setup
```

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
