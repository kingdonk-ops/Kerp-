// NFR budgets of diff §6.3 (TD-174, decision 18a), loaded from the one config file
// `budgets.json` that the CI performance job (T-0045, T-0145) and the Pilot-0 gate (T-0146) share.
import raw from "./budgets.json";

/** `warn`: a breach is a CI warning (now). `fail`: a breach fails the job (T-0146). */
export type Enforcement = "warn" | "fail";

export const BUDGET_IDS = ["formOpen", "deltaSync"] as const;
export type BudgetId = (typeof BUDGET_IDS)[number];

export interface Budget {
  label: string;
  /** Workload size, e.g. 300 questions or 1,000 records. */
  size: number;
  unit: string;
  /** Measured runs per CI job; the statistic is taken over them. */
  runs: number;
  statistic: "median";
  /** Strict upper bound: the statistic must be below it. */
  maxMs: number;
  /** Task that measures it. */
  task: string;
}

export interface NetworkProfile {
  name: string;
  downloadKbps: number;
  uploadKbps: number;
  rttMs: number;
  todo: string;
}

export interface NfrConfig {
  enforcement: Enforcement;
  referenceDevice: { name: string; confirmed: boolean; todo: string };
  /** Chrome DevTools `Emulation.setCPUThrottlingRate` rate (4 = 4x slower). */
  cpuThrottleRate: number;
  network: NetworkProfile;
  budgets: Readonly<Record<BudgetId, Budget>>;
}

class ConfigError extends Error {
  constructor(path: string, expected: string) {
    super(`frontend/src/perf/budgets.json: ${path} must be ${expected}`);
  }
}

type Obj = Readonly<Record<string, unknown>>;

const isObj = (value: unknown): value is Obj => typeof value === "object" && value !== null && !Array.isArray(value);

function obj(value: unknown, path: string): Obj {
  if (!isObj(value)) throw new ConfigError(path, "an object");
  return value;
}

function str(o: Obj, key: string, path: string): string {
  const value = o[key];
  if (typeof value !== "string" || value === "") throw new ConfigError(`${path}.${key}`, "a non-empty string");
  return value;
}

function num(o: Obj, key: string, path: string, { min = 0, integer = false } = {}): number {
  const value = o[key];
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0 || value < min || (integer && !Number.isInteger(value))) {
    throw new ConfigError(`${path}.${key}`, `a positive ${integer ? "integer" : "number"}${min > 0 ? ` >= ${String(min)}` : ""}`);
  }
  return value;
}

function parseBudget(value: unknown, path: string): Budget {
  const o = obj(value, path);
  if (o.statistic !== "median") throw new ConfigError(`${path}.statistic`, '"median"');
  return {
    label: str(o, "label", path),
    size: num(o, "size", path, { integer: true }),
    unit: str(o, "unit", path),
    runs: num(o, "runs", path, { integer: true }),
    statistic: "median",
    maxMs: num(o, "maxMs", path),
    task: str(o, "task", path),
  };
}

/** Validates the config; throws naming the offending path. */
export function parseNfrConfig(value: unknown): NfrConfig {
  const o = obj(value, "<root>");
  if (o.enforcement !== "warn" && o.enforcement !== "fail") throw new ConfigError("enforcement", '"warn" or "fail"');
  const device = obj(o.referenceDevice, "referenceDevice");
  if (typeof device.confirmed !== "boolean") throw new ConfigError("referenceDevice.confirmed", "a boolean");
  const network = obj(o.network, "network");
  const budgets = obj(o.budgets, "budgets");
  return {
    enforcement: o.enforcement,
    referenceDevice: {
      name: str(device, "name", "referenceDevice"),
      confirmed: device.confirmed,
      todo: str(device, "todo", "referenceDevice"),
    },
    cpuThrottleRate: num(o, "cpuThrottleRate", "<root>", { min: 1 }),
    network: {
      name: str(network, "name", "network"),
      downloadKbps: num(network, "downloadKbps", "network"),
      uploadKbps: num(network, "uploadKbps", "network"),
      rttMs: num(network, "rttMs", "network"),
      todo: str(network, "todo", "network"),
    },
    budgets: {
      formOpen: parseBudget(budgets.formOpen, "budgets.formOpen"),
      deltaSync: parseBudget(budgets.deltaSync, "budgets.deltaSync"),
    },
  };
}

export const NFR_CONFIG: NfrConfig = parseNfrConfig(raw);
