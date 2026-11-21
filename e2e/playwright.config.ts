import { defineConfig, devices } from "@playwright/test";

import {
  API_URL,
  BACKEND_DIR,
  BASE_URL,
  CHROMIUM_PATH,
  E2E_DIR,
  FRONTEND_DIR,
  PYTHON,
  backendEnv,
} from "./support/env";

const CI = Boolean(process.env.CI);
const api = new URL(API_URL);

/**
 * KERP end-to-end suite (T-0027): Chromium against the real stack, i.e. PostgreSQL with RLS
 * as the runtime role, Redis, the API (uvicorn, development) and the built frontend
 * (`vite preview`, `/api` proxied). See README.md.
 */
export default defineConfig({
  testDir: "./tests",
  globalSetup: "./global-setup.ts",
  // One journey shares one database and one run id: no parallel workers.
  workers: 1,
  fullyParallel: false,
  forbidOnly: CI,
  // No retries: a journey's data is per run (project code, QA user), so a retry of a serial
  // journey would meet its own earlier attempt; a flake must be fixed, not retried.
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report", open: "never" }],
  ],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(CHROMIUM_PATH ? { launchOptions: { executablePath: CHROMIUM_PATH } } : {}),
      },
    },
  ],
  webServer: [
    {
      // Playwright starts the servers before globalSetup: the API migrates first, since its
      // start-up reads the schema. Runtime role, development, the synthetic tenant.
      command:
        `${PYTHON} -m alembic upgrade head && ` +
        `${PYTHON} -m uvicorn app.main:create_app --factory --host ${api.hostname} --port ${api.port}`,
      cwd: BACKEND_DIR,
      env: backendEnv(),
      url: `${API_URL}/health`,
      reuseExistingServer: !CI,
      timeout: 120_000,
      // The API's logs (stderr) show next to a failure, in CI too.
      stderr: "pipe",
    },
    {
      // The production bundle, rebuilt for every run that starts the server.
      command: `npx vite build && npx vite preview --config "${E2E_DIR}/vite.preview.config.mjs"`,
      cwd: FRONTEND_DIR,
      env: { KERP_E2E_BASE_URL: BASE_URL, KERP_E2E_API_URL: API_URL },
      url: BASE_URL,
      reuseExistingServer: !CI,
      timeout: 180_000,
      stderr: "pipe",
    },
  ],
});
