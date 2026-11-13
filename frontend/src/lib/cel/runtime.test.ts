// Client CEL wrapper behaviour not covered by the shared fixtures (T-0017).
import { describe, expect, it } from "vitest";

import { CEL_ERROR_CODES, evaluate } from "./index";
import { classifyCelError, compiledProgramCount } from "./evaluate";
import { encodeValue, IntOverflowError, UnencodableValueError } from "./values";

describe("CEL client wrapper", () => {
  it("exposes the taxonomy", () => {
    expect(CEL_ERROR_CODES).toContain("PARSE_ERROR");
  });

  it("caches compiled programs per expression", () => {
    const before = compiledProgramCount();
    for (const x of [1, 2, 3]) {
      expect(evaluate("fields.cache_probe_x > 1.5", { fields: { cache_probe_x: x } })).toEqual({ ok: true, value: x > 1.5 });
    }
    expect(compiledProgramCount()).toBe(before + 1);
  });

  it("caps expressions at 4096 UTF-8 bytes before compiling", () => {
    const atLimit = `'${"é".repeat(2047)}'`; // 2 + 2047 * 2 = 4096 bytes
    expect(evaluate(atLimit, {}).ok).toBe(true);
    const before = compiledProgramCount();
    expect(evaluate(`'${"é".repeat(2047)}x'`, {})).toMatchObject({ ok: false, code: "INVALID_ARGUMENT" });
    expect(compiledProgramCount()).toBe(before);
  });

  it("reports parse errors with a message instead of throwing", () => {
    const result = evaluate("1 +", {});
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe("PARSE_ERROR");
      expect(result.message).not.toBe("");
    }
  });

  // An int-keyed map result is a pinned parity gap (fixture gap.int_keyed_map_result).
  it.each(["b'abc'", "type(1)"])("reports the unencodable result %s as EVAL_ERROR", (expression) => {
    expect(evaluate(expression, {})).toMatchObject({ ok: false, code: "EVAL_ERROR" });
  });

  it.each([
    ["unknown tag", { $unknown: "1" }],
    ["int not integer", { $int: "12.5" }],
    ["int not string", { $int: 12 }],
    ["int out of range", { $int: "9223372036854775808" }],
    ["uint input", { $uint: "1" }],
    ["duration input", { $duration: "1s" }],
    ["double bad name", { $double: "nan" }],
    ["timestamp date only", { $timestamp: "2024-01-01" }],
    ["timestamp without offset", { $timestamp: "2024-01-01T00:00:00" }],
    ["timestamp lowercase t", { $timestamp: "2024-01-01t00:00:00Z" }],
    ["timestamp lowercase z", { $timestamp: "2024-01-01T00:00:00z" }],
    ["non-JSON value", () => 1],
  ])("throws on an invalid context (%s)", (_name, value) => {
    expect(() => evaluate("true", { fields: value as never })).toThrow();
  });

  it("decodes context tags", () => {
    const context = {
      fields: {
        nan: { $double: "NaN" },
        neg_inf: { $double: "-Infinity" },
        ts: { $timestamp: "2024-06-01T10:30:00.123456+02:00" },
      },
    };
    expect(evaluate("fields.nan != fields.nan", context)).toEqual({ ok: true, value: true });
    expect(evaluate("fields.neg_inf", context)).toEqual({ ok: true, value: { $double: "-Infinity" } });
    // Offsets are normalised to UTC and sub-millisecond digits dropped (server does the same).
    expect(evaluate("fields.ts", context)).toEqual({ ok: true, value: { $timestamp: "2024-06-01T08:30:00.123Z" } });
  });

  it("encodes plain values", () => {
    expect(encodeValue(3n)).toEqual({ $int: "3" });
    expect(encodeValue(Number.NaN)).toEqual({ $double: "NaN" });
    expect(encodeValue([1.5, null])).toEqual([1.5, null]);
    expect(encodeValue(new Map([["a", 1.5]]))).toEqual({ a: 1.5 });
    expect(() => encodeValue(2n ** 63n)).toThrow(IntOverflowError);
    expect(() => encodeValue(new Map([[1n, "a"]]))).toThrow(UnencodableValueError);
    expect(() => encodeValue(new Date(Number.NaN))).toThrow(UnencodableValueError);
    expect(() => encodeValue(undefined)).toThrow(UnencodableValueError);
  });

  it("encodes negative and fractional durations like the server", () => {
    expect(evaluate("duration('-90s')", {})).toEqual({ ok: true, value: { $duration: "-90s" } });
    expect(evaluate("duration('-1.5s')", {})).toEqual({ ok: true, value: { $duration: "-1.5s" } });
    expect(evaluate("duration('250us')", {})).toEqual({ ok: true, value: { $duration: "0.00025s" } });
  });

  it("classifies unknown errors as EVAL_ERROR", () => {
    expect(classifyCelError(new Error("boom")).code).toBe("EVAL_ERROR");
    expect(classifyCelError("not an error").code).toBe("EVAL_ERROR");
  });
});
