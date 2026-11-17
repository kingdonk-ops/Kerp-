import { describe, expect, it } from "vitest";

import { parseNfrConfig, NFR_CONFIG, type NfrConfig } from "./budgets";
import { annotations, evaluateBudgets, isMeasurement, median, shouldFail, summaryMarkdown } from "./report";

const failing = (): NfrConfig => parseNfrConfig({ ...JSON.parse(JSON.stringify(NFR_CONFIG)), enforcement: "fail" });

describe("median", () => {
  it("takes the middle sample of an odd count, unsorted input", () => {
    expect(median([900, 100, 500, 300, 700])).toBe(500);
  });

  it("averages the two middle samples of an even count", () => {
    expect(median([4, 1, 3, 2])).toBe(2.5);
  });

  it("rejects no samples", () => {
    expect(() => median([])).toThrow();
  });
});

describe("evaluateBudgets", () => {
  it("passes under the budget and reports every sample", () => {
    const [form, sync] = evaluateBudgets(NFR_CONFIG, [{ budget: "formOpen", samplesMs: [700, 800, 650, 900, 1600] }]);
    expect(form).toMatchObject({ id: "formOpen", status: "pass", medianMs: 800, maxMs: 1500 });
    expect(form).toHaveProperty("samplesMs", [700, 800, 650, 900, 1600]);
    expect(sync).toMatchObject({ id: "deltaSync", status: "not_measured" });
  });

  it("breaches at or above the budget (the budget is strict: < 1.5 s)", () => {
    const [form] = evaluateBudgets(NFR_CONFIG, [{ budget: "formOpen", samplesMs: [1500, 1500, 1500, 1500, 1500] }]);
    expect(form?.status).toBe("breach");
  });

  it("rejects a budget measured twice or with the wrong number of runs", () => {
    const one = { budget: "formOpen" as const, samplesMs: [1, 2, 3, 4, 5] };
    expect(() => evaluateBudgets(NFR_CONFIG, [one, one])).toThrow(/twice/);
    expect(() => evaluateBudgets(NFR_CONFIG, [{ budget: "formOpen", samplesMs: [1, 2] }])).toThrow(/5 runs/);
  });
});

describe("reporting", () => {
  const breach = evaluateBudgets(NFR_CONFIG, [{ budget: "formOpen", samplesMs: [2000, 2100, 1900, 2200, 2050] }]);
  const pass = evaluateBudgets(NFR_CONFIG, [{ budget: "formOpen", samplesMs: [700, 800, 650, 900, 750] }]);

  it("emits a GitHub warning annotation per breach while enforcement is warn", () => {
    const lines = annotations(NFR_CONFIG, breach);
    expect(lines).toHaveLength(1);
    expect(lines[0]).toMatch(/^::warning title=NFR budget breach \(formOpen\)::/);
    expect(lines[0]).toContain("2050 ms");
    expect(lines[0]).toContain("1500 ms");
    expect(annotations(NFR_CONFIG, pass)).toEqual([]);
  });

  it("emits an error annotation once enforcement is fail (T-0146)", () => {
    expect(annotations(failing(), breach)[0]).toMatch(/^::error /);
  });

  it("escapes workflow-command data", () => {
    const config = parseNfrConfig(JSON.parse(JSON.stringify(NFR_CONFIG)));
    const odd = { ...config, budgets: { ...config.budgets, formOpen: { ...config.budgets.formOpen, label: "a%b\nc" } } };
    const [line] = annotations(odd, evaluateBudgets(odd, [{ budget: "formOpen", samplesMs: [9e3, 9e3, 9e3, 9e3, 9e3] }]));
    expect(line).toContain("a%25b%0Ac");
  });

  it("fails the run only when enforcement is fail and a budget breached", () => {
    expect(shouldFail(NFR_CONFIG, breach)).toBe(false);
    expect(shouldFail(failing(), breach)).toBe(true);
    expect(shouldFail(failing(), pass)).toBe(false);
  });

  it("writes a job summary table with the median, the runs, the profile and TD-205", () => {
    const md = summaryMarkdown(NFR_CONFIG, breach);
    expect(md).toContain("## NFR performance budgets");
    expect(md).toContain("| Form open: 300-question template, first interactive render | 2050 ms | < 1500 ms |");
    expect(md).toContain("2000, 2100, 1900, 2200, 2050");
    expect(md).toContain("warning");
    expect(md).toContain("not measured yet (T-0145)");
    expect(md).toContain("4x CPU throttling");
    expect(md).toContain("9000/3000 kbit/s, 85 ms RTT");
    expect(md).toContain("TD-205");
    expect(md).toMatch(/Enforcement: \*\*warn\*\*/);
  });

  it("marks passing budgets", () => {
    expect(summaryMarkdown(NFR_CONFIG, pass)).toContain("| 750 ms | < 1500 ms | 700, 800, 650, 900, 750 | pass |");
  });
});

describe("isMeasurement", () => {
  it("accepts a known budget with finite samples only", () => {
    expect(isMeasurement({ budget: "formOpen", samplesMs: [1, 2] })).toBe(true);
    expect(isMeasurement({ budget: "nope", samplesMs: [1] })).toBe(false);
    expect(isMeasurement({ budget: "formOpen", samplesMs: [Number.NaN] })).toBe(false);
    expect(isMeasurement({ budget: "formOpen", samplesMs: "1" })).toBe(false);
    expect(isMeasurement(undefined)).toBe(false);
  });
});
