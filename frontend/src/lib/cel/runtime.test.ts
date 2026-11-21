// Client CEL wrapper behaviour not covered by the shared fixtures (T-0017).
import { Environment } from "@marcbachmann/cel-js";
import { describe, expect, it } from "vitest";

import { CEL_ERROR_CODES, evaluate } from "./index";
import { classifyCelError, compiledProgramCount } from "./evaluate";
import { checkLimits, LimitExceededError, MAX_MACRO_NESTING, MAX_NESTING_DEPTH, measure } from "./limits";
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

  it("refuses nested loop macros over the cap before compiling them (audit item 1)", () => {
    const four = "l.all(a, l.all(b, l.all(c, l.all(d, a + b + c + d >= 0.0))))";
    const before = compiledProgramCount();
    expect(evaluate(four, { l: [1, 2] })).toEqual({
      ok: false,
      code: "PARSE_ERROR",
      message: `more than ${String(MAX_MACRO_NESTING)} nested loop macros`,
    });
    expect(compiledProgramCount()).toBe(before); // refused programs are not cached
    expect(evaluate("l.all(a, l.all(b, l.all(c, a + b + c >= 0.0)))", { l: [1, 2] })).toEqual({ ok: true, value: true });
  });

  it("refuses 1500 nested parentheses as PARSE_ERROR (audit item 11)", () => {
    const deep = `${"(".repeat(1500)}1${")".repeat(1500)}`;
    expect(evaluate(deep, {})).toMatchObject({ ok: false, code: "PARSE_ERROR" });
    expect(evaluate(`${"(".repeat(MAX_NESTING_DEPTH - 1)}1${")".repeat(MAX_NESTING_DEPTH - 1)}`, {})).toEqual({
      ok: true,
      value: { $int: "1" },
    });
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

describe("CEL cost limits (T-0341)", () => {
  const ast = (expression: string) => new Environment({ unlistedVariablesAreDyn: true }).parse(expression).ast;

  // Same expressions and values as backend/tests/test_cel_limits.py.
  it.each([
    ["l.all(x, x > 0.0)", 1],
    ["l.map(x, l.filter(y, y > x))", 2],
    ["l.exists(a, l.exists_one(b, l.map(c, c).size() > 0))", 3],
    ["l.all(a, true) && l.all(b, true) && l.all(c, true) && l.all(d, true)", 1],
    ["l.filter(a, true).map(b, b).filter(c, true).all(d, true)", 1],
    ["l.map(a, l.map(b, b)).all(x, x.all(y, y > 0.0))", 2],
    ["l.all(a, has(a.b) && size(l) > 0 && 'x'.contains('x'))", 1],
  ] as const)("counts macro nesting along one path: %s", (expression, macros) => {
    expect(measure(ast(expression)).macroNesting).toBe(macros);
  });

  it.each([
    ["1", 1],
    ["(1)", 1],
    ["1 + 2 + 3", 3],
    ["-1", 2],
    ["-x", 2],
    ["a.b.c", 3],
    ["a.b(1)", 2],
    ["f(1)", 2],
    ["[[1]]", 3],
    ["{'k': 1}", 2],
    ["true ? 1 : 2", 2],
    ["l[0][1]", 3],
  ] as const)("measures the syntax-tree depth of %s", (expression, depth) => {
    expect(measure(ast(expression)).treeDepth).toBe(depth);
  });

  it("measures without recursion and throws LimitExceededError over a cap", () => {
    const chain = Array.from({ length: 2000 }, () => "1").join("+");
    expect(measure(ast(chain)).treeDepth).toBe(2000);
    expect(() => {
      checkLimits(ast(chain));
    }).toThrow(LimitExceededError);
  });
});
