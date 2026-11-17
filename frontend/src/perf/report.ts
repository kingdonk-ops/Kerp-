// Pure budget evaluation and GitHub reporting for the NFR performance job (T-0045, diff §6.3).
// Runs in Node (the Vitest reporter) and is imported by the browser measurements for the types.
import { BUDGET_IDS, type BudgetId, type NfrConfig } from "./budgets";

/** Key under which a perf test stores its `Measurement` in the Vitest task meta. */
export const NFR_META_KEY = "kerpNfr";

export interface Measurement {
  budget: BudgetId;
  /** Wall-clock duration of each run, in milliseconds. */
  samplesMs: number[];
}

export type BudgetResult =
  | { id: BudgetId; label: string; maxMs: number; task: string; status: "not_measured" }
  | {
      id: BudgetId;
      label: string;
      maxMs: number;
      task: string;
      status: "pass" | "breach";
      medianMs: number;
      samplesMs: number[];
    };

export function isMeasurement(value: unknown): value is Measurement {
  if (typeof value !== "object" || value === null) return false;
  const { budget, samplesMs } = value as { budget?: unknown; samplesMs?: unknown };
  return (
    typeof budget === "string" &&
    (BUDGET_IDS as readonly string[]).includes(budget) &&
    Array.isArray(samplesMs) &&
    samplesMs.every((s) => typeof s === "number" && Number.isFinite(s))
  );
}

export function median(samples: readonly number[]): number {
  if (samples.length === 0) throw new Error("median of no samples");
  const sorted = [...samples].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const upper = sorted[mid] ?? Number.NaN;
  return sorted.length % 2 === 1 ? upper : ((sorted[mid - 1] ?? Number.NaN) + upper) / 2;
}

/** One result per configured budget, in config order; unmeasured budgets are reported as such. */
export function evaluateBudgets(config: NfrConfig, measurements: readonly Measurement[]): BudgetResult[] {
  const byId = new Map<BudgetId, Measurement>();
  for (const m of measurements) {
    if (byId.has(m.budget)) throw new Error(`NFR budget ${m.budget} measured twice`);
    byId.set(m.budget, m);
  }
  return BUDGET_IDS.map((id): BudgetResult => {
    const budget = config.budgets[id];
    const base = { id, label: budget.label, maxMs: budget.maxMs, task: budget.task };
    const m = byId.get(id);
    if (m === undefined) return { ...base, status: "not_measured" };
    if (m.samplesMs.length !== budget.runs) {
      throw new Error(`NFR budget ${id} needs ${String(budget.runs)} runs, got ${String(m.samplesMs.length)}`);
    }
    const medianMs = median(m.samplesMs);
    return { ...base, status: medianMs < budget.maxMs ? "pass" : "breach", medianMs, samplesMs: [...m.samplesMs] };
  });
}

const ms = (value: number): string => `${String(Math.round(value))} ms`;

// GitHub workflow-command escaping (data and property values).
const escapeData = (s: string): string => s.replace(/%/g, "%25").replace(/\r/g, "%0D").replace(/\n/g, "%0A");
const escapeProperty = (s: string): string => escapeData(s).replace(/:/g, "%3A").replace(/,/g, "%2C");

/** `::warning` (enforcement warn) or `::error` (enforcement fail) lines, one per breach. */
export function annotations(config: NfrConfig, results: readonly BudgetResult[]): string[] {
  const level = config.enforcement === "fail" ? "error" : "warning";
  return results.flatMap((r) =>
    r.status === "breach"
      ? [
          `::${level} title=${escapeProperty(`NFR budget breach (${r.id})`)}::` +
            escapeData(
              `${r.label}: median ${ms(r.medianMs)} of ${String(r.samplesMs.length)} runs, budget < ${ms(r.maxMs)} ` +
                `(diff §6.3; enforcement ${config.enforcement}, gate flips in T-0146)`,
            ),
        ]
      : [],
  );
}

export function shouldFail(config: NfrConfig, results: readonly BudgetResult[]): boolean {
  return config.enforcement === "fail" && results.some((r) => r.status === "breach");
}

/** Markdown for `$GITHUB_STEP_SUMMARY`. */
export function summaryMarkdown(config: NfrConfig, results: readonly BudgetResult[]): string {
  const breachWord = config.enforcement === "fail" ? "**fail**" : "warning";
  const rows = results.map((r) =>
    r.status === "not_measured"
      ? `| ${r.label} | – | < ${ms(r.maxMs)} | – | not measured yet (${r.task}) |`
      : `| ${r.label} | ${ms(r.medianMs)} | < ${ms(r.maxMs)} | ${r.samplesMs.map((s) => String(Math.round(s))).join(", ")} | ${
          r.status === "pass" ? "pass" : `${breachWord}: over budget`
        } |`,
  );
  const n = config.network;
  return [
    "## NFR performance budgets (diff §6.3)",
    "",
    "| Budget | Median | Limit | Runs (ms) | Result |",
    "|---|---|---|---|---|",
    ...rows,
    "",
    `- Enforcement: **${config.enforcement}** (a breach ${config.enforcement === "fail" ? "fails the job" : "is a warning until T-0146"}).`,
    `- Profile: headless Chrome, ${String(config.cpuThrottleRate)}x CPU throttling; network ${n.name}: ` +
      `${String(n.downloadKbps)}/${String(n.uploadKbps)} kbit/s, ${String(n.rttMs)} ms RTT (${n.todo}).`,
    `- Reference device: ${config.referenceDevice.name}${
      config.referenceDevice.confirmed ? "" : ` — not confirmed (${config.referenceDevice.todo})`
    }.`,
    "- Config: `frontend/src/perf/budgets.json`.",
    "",
  ].join("\n");
}
