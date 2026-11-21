/**
 * Prepares the database for this run, idempotently (T-0027): migrations, first-run with the
 * demo project, then the dev-only seed for this run's id. Every step runs the backend's own
 * entry points as the runtime role; a second run against the same database passes.
 *
 * Playwright starts the `webServer`s before this file, so the API command migrates the
 * database itself before it starts (playwright.config.ts); `alembic upgrade head` here is
 * then a no-op, kept so the setup is complete on its own.
 */
import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import path from "node:path";

import {
  ADMIN_EMAIL,
  ADMIN_NAME,
  ADMIN_PASSWORD,
  BACKEND_DIR,
  E2E_DIR,
  PYTHON,
  QA_PASSWORD,
  TENANT_ID,
  backendEnv,
} from "./support/env";
import { STATE_ENV, type RunState, type SeedState } from "./support/state";

function python(args: string[], extraEnv: Record<string, string> = {}): string {
  const result = spawnSync(PYTHON, args, {
    cwd: BACKEND_DIR,
    env: { ...process.env, ...backendEnv(), ...extraEnv },
    encoding: "utf8",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(
      `${PYTHON} ${args.join(" ")} exited ${String(result.status)}\n${result.stdout}\n${result.stderr}`,
    );
  }
  return result.stdout;
}

/** The connection must be the runtime role, so row-level security applies to the journey. */
const ROLE_CHECK = `
from sqlalchemy import create_engine, text
from app.core.config import get_settings
engine = create_engine(get_settings().database_url)
with engine.connect() as c:
    print(c.execute(text(
        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )).scalar_one())
engine.dispose()
`;

function newRunId(): string {
  return `${Date.now().toString(36)}${randomBytes(2).toString("hex")}`;
}

export default function globalSetup(): void {
  const run = process.env.KERP_E2E_RUN_ID ?? newRunId();
  if (!/^[a-z0-9]{1,24}$/.test(run)) throw new Error("KERP_E2E_RUN_ID must be 1-24 of [a-z0-9]");

  if (python(["-c", ROLE_CHECK]).trim() !== "False") {
    throw new Error("KERP_E2E_DATABASE_URL must use the NOSUPERUSER NOBYPASSRLS runtime role");
  }
  python(["-m", "alembic", "upgrade", "head"]);
  const firstRunArgs = [
    "-m",
    "app.modules.onboarding.first_run",
    "--demo",
    "--admin-email",
    ADMIN_EMAIL,
    "--admin-name",
    ADMIN_NAME,
  ];
  const firstRun = python(firstRunArgs);
  if (!firstRun.includes(`KERP_DEFAULT_TENANT_ID=${TENANT_ID}\n`)) {
    throw new Error(`first_run did not report the tenant ${TENANT_ID}:\n${firstRun}`);
  }
  // Idempotent: a second run exits 0 (checked by python()) and finds everything present.
  const firstRunAgain = python(firstRunArgs);
  for (const line of ["tenant: present", "system admin account: present", "demo project DEMO: present"]) {
    if (!firstRunAgain.includes(line)) {
      throw new Error(`second first_run is not idempotent (no "${line}"):\n${firstRunAgain}`);
    }
  }
  const seedOut = python([path.join(E2E_DIR, "seed", "seed.py"), "--run", run, "--admin-email", ADMIN_EMAIL], {
    KERP_E2E_ADMIN_PASSWORD: ADMIN_PASSWORD,
    KERP_E2E_QA_PASSWORD: QA_PASSWORD,
  });
  const seed = JSON.parse(seedOut) as SeedState;
  const state: RunState = { run, firstRun, firstRunAgain, seed };
  process.env.KERP_E2E_RUN_ID = run;
  process.env[STATE_ENV] = JSON.stringify(state);
}
