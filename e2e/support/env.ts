/**
 * The e2e suite's configuration, one place (T-0027). Every value can be overridden from the
 * environment; the defaults are dev-only, synthetic, and match `make up` (docker-compose.yml)
 * and CI's e2e job. No real secret lives here: the passwords are throwaway values for a
 * development database, never used anywhere else.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";

const env = process.env;

/** `e2e/`, `backend/` and `frontend/` on disk. */
export const E2E_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const BACKEND_DIR = path.resolve(E2E_DIR, "..", "backend");
export const FRONTEND_DIR = path.resolve(E2E_DIR, "..", "frontend");

/** The built frontend (`vite preview`, `/api` proxied to the API). */
export const BASE_URL = env.KERP_E2E_BASE_URL ?? "http://127.0.0.1:4127";
/** The API (uvicorn). Ports differ from the dev defaults, so a running dev server is not hit. */
export const API_URL = env.KERP_E2E_API_URL ?? "http://127.0.0.1:8027";
export const PYTHON = env.KERP_E2E_PYTHON ?? "python";

/** The runtime role (NOSUPERUSER NOBYPASSRLS, infra/postgres/initdb/20-kerp-app.sql). */
export const DATABASE_URL =
  env.KERP_E2E_DATABASE_URL ?? "postgresql+psycopg://kerp_app:kerp_app@localhost:5432/kerp_test";
export const REDIS_URL = env.KERP_E2E_REDIS_URL ?? "redis://localhost:6379/0";
/** The deployment's one hidden tenant: a synthetic id, created by the API and first-run. */
export const TENANT_ID = env.KERP_E2E_TENANT_ID ?? "00000000-0000-4000-8000-0000000e2e27";

export const ADMIN_EMAIL = "e2e-admin@example.test";
export const ADMIN_NAME = "Avery Example (e2e admin)";
export const ADMIN_PASSWORD = env.KERP_E2E_ADMIN_PASSWORD ?? "dev-only-admin-Quartz-41";
export const QA_PASSWORD = env.KERP_E2E_QA_PASSWORD ?? "dev-only-qa-Lantern-73";

/** Chromium binary to use instead of Playwright's own download (an image with a browser). */
export const CHROMIUM_PATH = env.KERP_E2E_CHROMIUM_PATH;

/** Environment of every backend process the suite starts (API, migrations, first-run, seed). */
export function backendEnv(): Record<string, string> {
  return {
    KERP_ENVIRONMENT: "development",
    KERP_DATABASE_URL: DATABASE_URL,
    KERP_DEFAULT_TENANT_ID: TENANT_ID,
    KERP_REDIS_URL: REDIS_URL,
    KERP_WEB_ORIGIN: new URL(BASE_URL).origin,
    PYTHONPATH: BACKEND_DIR,
  };
}
