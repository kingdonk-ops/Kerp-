// Vitest reporter of the NFR performance job (T-0045, diff §6.3). Runs in Node after the
// browser measurements: evaluates them against `budgets.json`, prints GitHub annotations,
// appends the job summary and writes `perf-results.json`. While enforcement is `warn` a breach
// never fails the run; a broken measurement (failed test) still does, through Vitest itself.
import { appendFileSync, writeFileSync } from "node:fs";

import type { Reporter, TestModule } from "vitest/node";

import { NFR_CONFIG, type NfrConfig } from "./budgets";
import { annotations, evaluateBudgets, isMeasurement, NFR_META_KEY, shouldFail, summaryMarkdown, type Measurement } from "./report";

export interface ReporterIo {
  /** One line on stdout (annotations must start a line). */
  print: (line: string) => void;
  /** Appends to `$GITHUB_STEP_SUMMARY` when set. */
  appendSummary: (markdown: string) => void;
  writeResults: (json: string) => void;
  fail: () => void;
}

export const RESULTS_FILE = "perf-results.json";

const defaultIo: ReporterIo = {
  print: (line) => {
    process.stdout.write(`${line}\n`);
  },
  appendSummary: (markdown) => {
    const file = process.env.GITHUB_STEP_SUMMARY;
    if (file !== undefined && file !== "") appendFileSync(file, markdown);
  },
  writeResults: (json) => {
    writeFileSync(RESULTS_FILE, json);
  },
  fail: () => {
    process.exitCode = 1;
  },
};

type ModuleLike = Pick<TestModule, "children">;

/** Measurements stored by the perf tests in their task meta (`NFR_META_KEY`). */
export function collectMeasurements(modules: readonly ModuleLike[]): Measurement[] {
  const out: Measurement[] = [];
  for (const module of modules) {
    for (const test of module.children.allTests()) {
      const value = (test.meta() as Record<string, unknown>)[NFR_META_KEY];
      if (value === undefined) continue;
      if (!isMeasurement(value)) throw new Error(`${test.fullName}: malformed NFR measurement`);
      out.push(value);
    }
  }
  return out;
}

export default class NfrBudgetReporter implements Reporter {
  private readonly io: ReporterIo;
  private readonly config: NfrConfig;

  /** Vitest passes the reporter options object (empty from the config); tests inject io/config. */
  constructor(options: { io?: ReporterIo; config?: NfrConfig } = {}) {
    this.io = options.io ?? defaultIo;
    this.config = options.config ?? NFR_CONFIG;
  }

  onTestRunEnd(testModules: readonly TestModule[]): void {
    this.report(testModules);
  }

  report(testModules: readonly ModuleLike[]): void {
    const results = evaluateBudgets(this.config, collectMeasurements(testModules));
    const summary = summaryMarkdown(this.config, results);
    this.io.print("");
    for (const line of summary.trimEnd().split("\n")) this.io.print(line);
    for (const line of annotations(this.config, results)) this.io.print(line);
    this.io.appendSummary(summary);
    this.io.writeResults(`${JSON.stringify({ enforcement: this.config.enforcement, results }, null, 2)}\n`);
    if (shouldFail(this.config, results)) this.io.fail();
  }
}
