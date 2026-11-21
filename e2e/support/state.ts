/**
 * What `globalSetup` hands to the tests (through the environment, which Playwright passes on
 * to its workers): this run's id and what first-run and the seed reported.
 */
export const STATE_ENV = "KERP_E2E_STATE";

/** `e2e/seed/seed.py`'s JSON output. */
export interface SeedState {
  tenant_id: string;
  admin_user_id: string;
  qa_user_id: string;
  qa_email: string;
  qa_name: string;
  demo_project_id: string | null;
  rule_set_key: string;
}

export interface RunState {
  /** This run's id: [a-z0-9], unique per run (project code, QA user). */
  run: string;
  /** Output of the first `first_run` of this setup, and of the second (idempotent) one. */
  firstRun: string;
  firstRunAgain: string;
  seed: SeedState;
}

export function runState(): RunState {
  const raw = process.env[STATE_ENV];
  if (!raw) throw new Error(`${STATE_ENV} is not set: globalSetup did not run`);
  return JSON.parse(raw) as RunState;
}
