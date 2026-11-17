import { describe, expect, it } from "vitest";

import { NFR_CONFIG, parseNfrConfig } from "./budgets";
import { NFR_META_KEY } from "./report";
import NfrBudgetReporter, { collectMeasurements, type ReporterIo } from "./reporter";

interface FakeTest {
  fullName: string;
  meta: () => Record<string, unknown>;
}

const moduleWith = (...metas: Record<string, unknown>[]) => ({
  children: {
    *allTests() {
      for (const [i, meta] of metas.entries()) yield { fullName: `t${String(i)}`, meta: () => meta } satisfies FakeTest;
    },
  },
});

type Module = Parameters<typeof collectMeasurements>[0][number];
const asModules = (...modules: ReturnType<typeof moduleWith>[]): Module[] => modules as unknown as Module[];

function recordingIo() {
  const io = { lines: [] as string[], summary: "", results: "", failed: false };
  const sink: ReporterIo = {
    print: (line) => io.lines.push(line),
    appendSummary: (md) => {
      io.summary += md;
    },
    writeResults: (json) => {
      io.results = json;
    },
    fail: () => {
      io.failed = true;
    },
  };
  return { io, sink };
}

const breach = { [NFR_META_KEY]: { budget: "formOpen", samplesMs: [2000, 2000, 2000, 2000, 2000] } };

describe("collectMeasurements", () => {
  it("reads the measurement from each test meta and ignores other tests", () => {
    expect(collectMeasurements(asModules(moduleWith({}, breach)))).toEqual([breach[NFR_META_KEY]]);
  });

  it("rejects a malformed measurement", () => {
    expect(() => collectMeasurements(asModules(moduleWith({ [NFR_META_KEY]: { budget: "formOpen" } })))).toThrow(
      /t0: malformed/,
    );
  });
});

describe("NfrBudgetReporter", () => {
  it("builds from the empty options object Vitest passes", () => {
    expect(() => new NfrBudgetReporter({})).not.toThrow();
  });

  it("warns on a breach without failing, and writes summary and results", () => {
    const { io, sink } = recordingIo();
    new NfrBudgetReporter({ io: sink }).report(asModules(moduleWith(breach)));
    expect(io.lines.some((l) => l.startsWith("::warning "))).toBe(true);
    expect(io.summary).toContain("## NFR performance budgets");
    expect(JSON.parse(io.results)).toMatchObject({
      enforcement: "warn",
      results: [{ id: "formOpen", status: "breach" }, { id: "deltaSync", status: "not_measured" }],
    });
    expect(io.failed).toBe(false);
  });

  it("fails on a breach once enforcement is fail (T-0146)", () => {
    const { io, sink } = recordingIo();
    const config = parseNfrConfig({ ...JSON.parse(JSON.stringify(NFR_CONFIG)), enforcement: "fail" });
    new NfrBudgetReporter({ io: sink, config }).report(asModules(moduleWith(breach)));
    expect(io.lines.some((l) => l.startsWith("::error "))).toBe(true);
    expect(io.failed).toBe(true);
  });

  it("prints no annotation when every budget passes", () => {
    const { io, sink } = recordingIo();
    new NfrBudgetReporter({ io: sink }).report(
      asModules(moduleWith({ [NFR_META_KEY]: { budget: "formOpen", samplesMs: [10, 20, 30, 40, 50] } })),
    );
    expect(io.lines.some((l) => l.startsWith("::"))).toBe(false);
    expect(io.failed).toBe(false);
  });
});
