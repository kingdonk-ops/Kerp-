import { describe, expect, it } from "vitest";

import raw from "./budgets.json";
import { BUDGET_IDS, NFR_CONFIG, parseNfrConfig } from "./budgets";

const clone = (): Record<string, unknown> => JSON.parse(JSON.stringify(raw)) as Record<string, unknown>;
const formOpen = (v: Record<string, unknown>): Record<string, unknown> =>
  (v.budgets as { formOpen: Record<string, unknown> }).formOpen;

describe("NFR budgets config (diff §6.3)", () => {
  it("holds the §6.3 budgets, the CPU throttle and the 4G profile", () => {
    expect(NFR_CONFIG.enforcement).toBe("warn");
    expect(NFR_CONFIG.cpuThrottleRate).toBe(4);
    expect(NFR_CONFIG.network).toMatchObject({ downloadKbps: 9000, uploadKbps: 3000, rttMs: 85 });
    expect(NFR_CONFIG.budgets.formOpen).toMatchObject({ size: 300, runs: 5, statistic: "median", maxMs: 1500 });
    expect(NFR_CONFIG.budgets.deltaSync).toMatchObject({ size: 1000, maxMs: 30000 });
  });

  it("marks the reference device as not confirmed until TD-205 closes", () => {
    expect(NFR_CONFIG.referenceDevice.confirmed).toBe(false);
    expect(NFR_CONFIG.referenceDevice.todo).toBe("TD-205");
    expect(NFR_CONFIG.network.todo).toBe("TD-205");
  });

  it("lists every budget id", () => {
    expect([...BUDGET_IDS].sort()).toEqual(Object.keys(NFR_CONFIG.budgets).sort());
  });

  it("accepts enforcement fail (T-0146)", () => {
    const value = clone();
    value.enforcement = "fail";
    expect(parseNfrConfig(value).enforcement).toBe("fail");
  });

  it.each([
    ["unknown enforcement", (v: Record<string, unknown>) => { v.enforcement = "ignore"; }],
    ["throttle below 1", (v: Record<string, unknown>) => { v.cpuThrottleRate = 0.5; }],
    ["missing network", (v: Record<string, unknown>) => { delete v.network; }],
    ["missing budget", (v: Record<string, unknown>) => { delete (v.budgets as Record<string, unknown>).formOpen; }],
    ["non-positive limit", (v: Record<string, unknown>) => { formOpen(v).maxMs = 0; }],
    ["fractional runs", (v: Record<string, unknown>) => { formOpen(v).runs = 2.5; }],
    ["unknown statistic", (v: Record<string, unknown>) => { formOpen(v).statistic = "p95"; }],
  ])("rejects a config with %s", (_name, mutate) => {
    const value = clone();
    mutate(value);
    expect(() => parseNfrConfig(value)).toThrow(/budgets\.json/);
  });

  it("rejects a non-object", () => {
    expect(() => parseNfrConfig(null)).toThrow(/budgets\.json/);
  });
});
