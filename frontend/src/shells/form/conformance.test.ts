// Shared field-type palette and form-template cases on the client engine (T-0110). The same
// files are asserted by backend/tests/test_fields_conformance.py on the server engine.
// Schema: tests/contract/README.md.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { evaluate, type JsonValue } from "../../lib/cel";
import { EVALUATOR_VERSION, FIELD_TYPES, resolveFieldType } from "../../lib/fields";
import { evaluateForm } from "./engine";
import type { FormTemplate } from "./types";

const CONTRACT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../tests/contract");

interface FormCase {
  name: string;
  template: string;
  values: Record<string, JsonValue>;
  context: Record<string, JsonValue>;
  expected: {
    visible: string[];
    values: Record<string, JsonValue>;
    required: string[];
    failed: string[];
    errors: Record<string, { kind: string; code?: string }>;
  };
}

interface FormDocument {
  evaluator_version: number;
  templates: Record<string, FormTemplate>;
  cases: FormCase[];
  invalid_contexts: Record<string, JsonValue>[];
}

/** Load a contract file; throws (never skips) if it is missing or `key` is empty. */
function loadDocument(path: string, key: string): Record<string, unknown> {
  if (!existsSync(path)) throw new Error(`contract file not found: ${path}`);
  const document = JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
  const items = document[key];
  if (!Array.isArray(items) || items.length === 0) throw new Error(`contract file has no ${key}: ${path}`);
  return document;
}

// Loaded at module scope on purpose: a missing/empty file fails the suite outright.
const FORMS = loadDocument(join(CONTRACT, "form_fixtures.json"), "cases") as unknown as FormDocument;

describe("shared field-type palette", () => {
  it("rejects a missing contract file", () => {
    expect(() => loadDocument(join(CONTRACT, "absent.json"), "cases")).toThrow("not found");
    expect(() => loadDocument(join(CONTRACT, "form_fixtures.json"), "nothing")).toThrow("no nothing");
  });

  it("equals the shared contract", () => {
    expect(FIELD_TYPES).toEqual(loadDocument(join(CONTRACT, "field_types.json"), "types").types);
    expect(resolveFieldType("number")?.key).toBe("numeric");
    expect(resolveFieldType("barcode")).toBeUndefined();
  });

  it("pins the evaluator version and has unique case names", () => {
    expect(FORMS.evaluator_version).toBe(EVALUATOR_VERSION);
    expect(new Set(FORMS.cases.map((item) => item.name)).size).toBe(FORMS.cases.length);
  });
});

describe("shared form cases (client engine)", () => {
  it.each(FORMS.cases.map((item) => [item.name, item] as const))("%s", (_name, item) => {
    const template = FORMS.templates[item.template];
    if (template === undefined) throw new Error(`unknown template ${item.template}`);
    const result = evaluateForm(template, item.values, { context: item.context });
    const errors = Object.fromEntries(
      Object.entries(result.errors).map(([key, error]) => [
        key,
        error.kind === "rule_error" ? { kind: error.kind, code: error.code } : { kind: error.kind },
      ]),
    );
    // toStrictEqual: 1 and 1.0 are the same double, true is not 1, -0 is not 0.
    expect({
      visible: [...result.visible],
      values: result.values,
      required: [...result.required],
      failed: [...result.failed],
      errors,
    }).toStrictEqual(item.expected);
    expect(result.valid).toBe(Object.keys(item.expected.errors).length === 0);
  });
});

describe("malformed contexts are rejected like on the server", () => {
  it.each(FORMS.invalid_contexts.map((context) => [JSON.stringify(context), context] as const))("%s", (_text, context) => {
    expect(() => evaluate("true", context)).toThrow(TypeError);
  });
});
