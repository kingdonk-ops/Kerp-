import { describe, expect, it } from "vitest";

import { evaluateForm } from "../shells/form/engine";
import { syntheticTemplate } from "./syntheticTemplate";

describe("syntheticTemplate", () => {
  const template = syntheticTemplate(300);

  it("has the requested number of uniquely keyed questions", () => {
    expect(template.fields).toHaveLength(300);
    expect(new Set(template.fields.map((f) => f.field_key)).size).toBe(300);
  });

  it("mixes every data type and carries CEL rules like a real inspection form", () => {
    expect(new Set(template.fields.map((f) => f.data_type))).toEqual(
      new Set(["text", "number", "boolean", "select", "date"]),
    );
    expect(template.fields.filter((f) => f.visible_if !== undefined).length).toBeGreaterThanOrEqual(30);
    expect(template.fields.filter((f) => f.required_if !== undefined).length).toBeGreaterThanOrEqual(30);
    expect(template.fields.filter((f) => f.validation !== undefined).length).toBeGreaterThanOrEqual(30);
    expect(template.fields.filter((f) => f.help !== undefined).length).toBeGreaterThanOrEqual(30);
  });

  it("shows every question on open and every rule evaluates without error", () => {
    const evaluation = evaluateForm(template, {});
    expect(evaluation.visible.size).toBe(300);
    const ruleErrors = Object.values(evaluation.errors).filter((e) => e.kind === "rule_error");
    expect(ruleErrors).toEqual([]);
  });

  it("validation rules evaluate once filled in", () => {
    const values = Object.fromEntries(
      template.fields.filter((f) => f.data_type === "number").map((f) => [f.field_key, 5]),
    );
    const evaluation = evaluateForm(template, values);
    const numberErrors = template.fields
      .filter((f) => f.data_type === "number")
      .map((f) => evaluation.errors[f.field_key])
      .filter((e) => e !== undefined);
    expect(numberErrors).toEqual([]);
  });

  it("is deterministic", () => {
    expect(syntheticTemplate(300)).toEqual(template);
  });
});
