// Shared CEL conformance fixtures on the client runtime (T-0017, diff §3).
// The same file is asserted by backend/tests/test_cel_conformance.py on the server
// runtime. Schema and value encoding: tests/contract/README.md.
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { CEL_ERROR_CODES, evaluate, type CelResult, type JsonValue } from "./index";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const FIXTURE_FILE = join(REPO_ROOT, "tests/contract/conformance_fixtures.json");
const TODO_DIR = join(REPO_ROOT, "tracker/todo");
const RUNTIME = "js";

type Expectation = { value: JsonValue } | { error: string };

interface Fixture {
  id: string;
  category: string;
  description: string;
  expression: string;
  context: Record<string, JsonValue>;
  expect: Expectation;
  known_gap?: { runtime: "python" | "js"; todo: string; actual: Expectation };
}

interface FixtureDocument {
  error_codes: string[];
  fixtures: Fixture[];
}

/** Load and sanity-check the fixture file; throws (never skips) if missing or empty. */
function loadFixtureDocument(path: string): FixtureDocument {
  if (!existsSync(path)) {
    throw new Error(`CEL conformance fixture file not found: ${path}`);
  }
  const document = JSON.parse(readFileSync(path, "utf8")) as Partial<FixtureDocument>;
  if (!Array.isArray(document.fixtures) || document.fixtures.length === 0) {
    throw new Error(`CEL conformance fixture file has no fixtures: ${path}`);
  }
  return document as FixtureDocument;
}

// Loaded at module scope on purpose: a missing/empty file fails the suite outright.
const DOCUMENT = loadFixtureDocument(FIXTURE_FILE);

type Canonical = readonly unknown[];

/** Type-tagged form for exact comparison (true !== 1, 3 and 3.0 are the same double). */
function canonical(value: JsonValue): Canonical {
  if (value === null) return ["null"];
  if (typeof value === "boolean") return ["bool", value];
  if (typeof value === "number") return ["double", value];
  if (typeof value === "string") return ["string", value];
  if (Array.isArray(value)) return ["list", value.map(canonical)];
  return [
    "map",
    Object.keys(value)
      .sort()
      .map((key) => [key, canonical(value[key] as JsonValue)]),
  ];
}

function outcome(result: CelResult): Canonical {
  return result.ok ? ["value", canonical(result.value)] : ["error", result.code];
}

function expectedOutcome(expectation: Expectation): Canonical {
  return "error" in expectation ? ["error", expectation.error] : ["value", canonical(expectation.value)];
}

describe("CEL conformance fixtures (client runtime)", () => {
  it("rejects a missing fixture file", () => {
    expect(() => loadFixtureDocument(join(tmpdir(), "kerp-absent-cel-fixtures.json"))).toThrow(/not found/);
  });

  it("rejects an empty fixture list", () => {
    const path = join(mkdtempSync(join(tmpdir(), "kerp-cel-")), "empty.json");
    writeFileSync(path, '{"fixtures": []}');
    expect(() => loadFixtureDocument(path)).toThrow(/no fixtures/);
  });

  it("uses exactly the shared error code list", () => {
    expect([...CEL_ERROR_CODES]).toEqual(DOCUMENT.error_codes);
  });

  it("has unique fixture ids", () => {
    const ids = DOCUMENT.fixtures.map((fixture) => fixture.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it.each(DOCUMENT.fixtures.map((fixture) => [fixture.id, fixture] as const))("%s", (_id, fixture) => {
    const gap = fixture.known_gap;
    if (gap !== undefined) {
      const todo = join(TODO_DIR, `${gap.todo}.md`);
      expect(existsSync(todo)).toBe(true);
      // Guards against TODO id collisions: the cited TODO must belong to this task.
      const tasks = /^tasks:\s*\[(.*)\]\s*$/m.exec(readFileSync(todo, "utf8"))?.[1] ?? "";
      expect(tasks.split(",").map((task) => task.trim())).toContain("T-0017");
    }
    // A documented gap on this runtime is pinned to its current (divergent) result, so a
    // library change is noticed; it is never skipped. See tests/contract/README.md.
    const expectation = gap !== undefined && gap.runtime === RUNTIME ? gap.actual : fixture.expect;
    const result = evaluate(fixture.expression, fixture.context);
    expect(outcome(result), JSON.stringify(result)).toEqual(expectedOutcome(expectation));
  });
});
