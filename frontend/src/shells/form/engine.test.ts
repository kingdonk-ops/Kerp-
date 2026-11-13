import { describe, expect, it, vi } from "vitest";

import type { CelResult } from "../../lib/cel";
import { evaluateForm } from "./engine";
import type { FormTemplate } from "./types";

// Synthetic coating inspection template (EDGE-SCREENS §2.2 shape).
const COATING: FormTemplate = {
  fields: [
    {
      field_key: "substrate_type",
      label: "Substrate",
      data_type: "select",
      required: true,
      options: [
        { value: "STRUCTURAL_STEEL", label: "Structural steel" },
        { value: "CONCRETE", label: "Concrete" },
      ],
    },
    { field_key: "coating_system", label: "Coating system", data_type: "text" },
    {
      field_key: "coating_adhesion_psi",
      label: "Pull-off adhesion (PSI)",
      data_type: "number",
      required: true,
      visible_if: "fields.substrate_type == 'STRUCTURAL_STEEL' && fields.coating_system == 'SYSTEM_5_EPOXY'",
      validation: {
        cel_expression: "fields.coating_adhesion_psi >= 1450.0",
        error_message: "Minimum allowable threshold is 1450 PSI.",
      },
    },
  ],
};

const STEEL_EPOXY = { substrate_type: "STRUCTURAL_STEEL", coating_system: "SYSTEM_5_EPOXY" };

describe("evaluateForm — visible_if", () => {
  it("shows a field only when its CEL condition is true", () => {
    const shown = evaluateForm(COATING, { ...STEEL_EPOXY });
    expect(shown.visible.has("coating_adhesion_psi")).toBe(true);

    const hidden = evaluateForm(COATING, { substrate_type: "CONCRETE", coating_system: "SYSTEM_5_EPOXY" });
    expect(hidden.visible.has("coating_adhesion_psi")).toBe(false);
  });

  it("sanitizes values of hidden fields and does not validate them", () => {
    const result = evaluateForm(COATING, { substrate_type: "CONCRETE", coating_adhesion_psi: 10 });
    expect(result.values).toEqual({ substrate_type: "CONCRETE" });
    expect(result.errors).toEqual({});
    expect(result.valid).toBe(true);
  });

  it("fails closed: an evaluation error or a non-bool result hides the field", () => {
    const template: FormTemplate = {
      fields: [
        { field_key: "a", label: "A", data_type: "text", visible_if: "fields.missing == 'x'" },
        { field_key: "b", label: "B", data_type: "text", visible_if: "'truthy'" },
        { field_key: "c", label: "C", data_type: "text", visible_if: "1 == 1" },
      ],
    };
    const result = evaluateForm(template, {});
    expect([...result.visible]).toEqual(["c"]);
  });

  it("hides fields whose condition depends on a hidden field (cascade to a fixpoint)", () => {
    const template: FormTemplate = {
      fields: [
        { field_key: "kind", label: "Kind", data_type: "text" },
        { field_key: "detail", label: "Detail", data_type: "text", visible_if: "fields.kind == 'weld'" },
        { field_key: "deeper", label: "Deeper", data_type: "text", visible_if: "fields.detail == 'reject'" },
      ],
    };
    const result = evaluateForm(template, { kind: "bolt", detail: "reject", deeper: "x" });
    expect([...result.visible]).toEqual(["kind"]);
    expect(result.values).toEqual({ kind: "bolt" });
  });

  it("hides every field whose visibility does not settle (fail closed on oscillation)", () => {
    const template: FormTemplate = {
      fields: [
        { field_key: "stable", label: "Stable", data_type: "text" },
        { field_key: "flip", label: "Flip", data_type: "text", visible_if: "!has(fields.flip)" },
      ],
    };
    const result = evaluateForm(template, { stable: "s", flip: "f" });
    expect([...result.visible]).toEqual(["stable"]);
    expect(result.values).toEqual({ stable: "s" });
  });

  it("hides a field that oscillates even when the last pass would show it", () => {
    // Three fields -> four passes, ending on the "visible" phase of `flip`.
    const template: FormTemplate = {
      fields: [
        { field_key: "stable", label: "Stable", data_type: "text" },
        { field_key: "other", label: "Other", data_type: "text" },
        { field_key: "flip", label: "Flip", data_type: "text", visible_if: "!has(fields.flip)" },
      ],
    };
    const result = evaluateForm(template, { stable: "s", other: "o", flip: "f" });
    expect([...result.visible]).toEqual(["stable", "other"]);
    expect(result.values).toEqual({ stable: "s", other: "o" });
  });

  it("exposes extra context variables next to `fields`", () => {
    const template: FormTemplate = {
      fields: [
        {
          field_key: "thickness",
          label: "Thickness",
          data_type: "number",
          validation: {
            cel_expression: "fields.thickness >= asset.attributes.min_wall_thickness",
            error_message: "Below minimum wall thickness.",
          },
        },
      ],
    };
    const context = { asset: { attributes: { min_wall_thickness: 6.0 } } };
    expect(evaluateForm(template, { thickness: 5.5 }, { context }).valid).toBe(false);
    expect(evaluateForm(template, { thickness: 6.5 }, { context }).valid).toBe(true);
  });
});

describe("evaluateForm — validation", () => {
  it("reports the template error message when the CEL validation is false", () => {
    const result = evaluateForm(COATING, { ...STEEL_EPOXY, coating_adhesion_psi: 1200 });
    expect(result.errors).toEqual({
      coating_adhesion_psi: { kind: "invalid", message: "Minimum allowable threshold is 1450 PSI." },
    });
    expect(result.valid).toBe(false);
  });

  it("passes when the CEL validation is true", () => {
    const result = evaluateForm(COATING, { ...STEEL_EPOXY, coating_adhesion_psi: 1500 });
    expect(result.errors).toEqual({});
    expect(result.valid).toBe(true);
    expect(result.values).toEqual({ ...STEEL_EPOXY, coating_adhesion_psi: 1500 });
  });

  it("fails closed: a validation that errors or is non-bool blocks with a rule error", () => {
    const template: FormTemplate = {
      fields: [
        {
          field_key: "steel_temp",
          label: "Steel temperature",
          data_type: "number",
          validation: {
            cel_expression: "fields.steel_temp >= fields.dew_point + 3.0",
            error_message: "Too close to dew point.",
          },
        },
        {
          field_key: "note",
          label: "Note",
          data_type: "text",
          validation: { cel_expression: "fields.note", error_message: "unused" },
        },
      ],
    };
    const result = evaluateForm(template, { steel_temp: 20, note: "ok" });
    expect(result.errors).toEqual({
      steel_temp: { kind: "rule_error", code: "NO_SUCH_KEY" },
      note: { kind: "rule_error", code: "NON_BOOL_RESULT" },
    });
    expect(result.valid).toBe(false);
  });

  it("enforces required and required_if on visible fields only", () => {
    const template: FormTemplate = {
      fields: [
        { field_key: "result", label: "Result", data_type: "text", required: true },
        { field_key: "reason", label: "Reason", data_type: "text", required_if: "fields.result == 'REJECT'" },
        { field_key: "flag", label: "Flag", data_type: "boolean", required: true },
      ],
    };
    // `result` is empty, so `fields.result` is NO_SUCH_KEY and required_if fails closed.
    expect(evaluateForm(template, { result: "", flag: false }).errors).toEqual({
      result: { kind: "required" },
      reason: { kind: "required" },
    });
    expect(evaluateForm(template, { result: "REJECT", flag: true }).errors).toEqual({ reason: { kind: "required" } });
    expect(evaluateForm(template, { result: "ACCEPT", flag: true }).errors).toEqual({});
  });

  it("fails closed on required_if: an erroring condition makes the field required", () => {
    const template: FormTemplate = {
      fields: [{ field_key: "x", label: "X", data_type: "text", required_if: "fields.nope == 1" }],
    };
    expect(evaluateForm(template, {}).errors).toEqual({ x: { kind: "required" } });
  });

  it("lets templates guard optional references with has()", () => {
    const template: FormTemplate = {
      fields: [
        { field_key: "result", label: "Result", data_type: "text" },
        {
          field_key: "reason",
          label: "Reason",
          data_type: "text",
          required_if: "has(fields.result) && fields.result == 'REJECT'",
        },
      ],
    };
    expect(evaluateForm(template, {}).errors).toEqual({});
  });

  it("skips the CEL validation of an empty optional field", () => {
    const template: FormTemplate = {
      fields: [
        {
          field_key: "reading",
          label: "Reading",
          data_type: "number",
          validation: { cel_expression: "fields.reading > 0.0", error_message: "Must be positive." },
        },
      ],
    };
    expect(evaluateForm(template, { reading: null }).valid).toBe(true);
    expect(evaluateForm(template, { reading: null }).values).toEqual({});
  });

  it("evaluates through an injected evaluator seam", () => {
    const evaluator = vi.fn((): CelResult => ({ ok: true, value: false }));
    const template: FormTemplate = {
      fields: [{ field_key: "x", label: "X", data_type: "text", visible_if: "anything" }],
    };
    const result = evaluateForm(template, { x: "1" }, { evaluator });
    expect(result.visible.size).toBe(0);
    expect(evaluator).toHaveBeenCalledWith("anything", { fields: { x: "1" } });
  });

  it("does not let extra context override the `fields` variable", () => {
    const template: FormTemplate = {
      fields: [{ field_key: "x", label: "X", data_type: "text", visible_if: "fields.x == 'real'" }],
    };
    const result = evaluateForm(template, { x: "real" }, { context: { fields: { x: "spoofed" } } });
    expect(result.visible.has("x")).toBe(true);
  });
});

describe("evaluateForm — hostile keys and malformed data", () => {
  it.each(["constructor", "toString", "__proto__", "hasOwnProperty"])(
    "treats a required `%s` field without a value as missing",
    (key) => {
      const template: FormTemplate = {
        fields: [
          { field_key: key, label: key, data_type: "text", required: true },
          { field_key: "other", label: "Other", data_type: "text", visible_if: "true" },
        ],
      };
      const result = evaluateForm(template, {});
      expect(result.valid).toBe(false);
      expect(Object.hasOwn(result.errors, key)).toBe(true);
      expect(result.errors[key]).toEqual({ kind: "required" });
      expect(Object.hasOwn(result.values, key)).toBe(false);
      expect(Object.keys(result.values)).toEqual([]);
    },
  );

  it("keeps a `__proto__` field value as an own property", () => {
    const template: FormTemplate = { fields: [{ field_key: "__proto__", label: "P", data_type: "text" }] };
    const values = Object.fromEntries([["__proto__", "x"]]);
    const result = evaluateForm(template, values);
    expect(Object.keys(result.values)).toEqual(["__proto__"]);
    expect(Object.getPrototypeOf(result.values)).toBe(Object.prototype);
  });

  const RULES: FormTemplate = {
    fields: [
      { field_key: "shown", label: "Shown", data_type: "text", visible_if: "true" },
      { field_key: "cond", label: "Cond", data_type: "text", required_if: "false" },
      {
        field_key: "checked",
        label: "Checked",
        data_type: "text",
        validation: { cel_expression: "true", error_message: "unused" },
      },
    ],
  };

  it("fails closed instead of throwing on a malformed value", () => {
    const result = evaluateForm(RULES, { checked: { $int: "abc" } });
    expect(result.visible.has("shown")).toBe(false);
    expect(result.errors).toEqual({
      cond: { kind: "required" },
      checked: { kind: "rule_error", code: "INVALID_ARGUMENT" },
    });
  });

  it("fails closed instead of throwing on a malformed extra context", () => {
    const context = { asset: { attributes: { $int: "abc" } } };
    const result = evaluateForm(RULES, { checked: "ok" }, { context });
    expect(result.visible.has("shown")).toBe(false);
    expect(result.errors).toEqual({
      cond: { kind: "required" },
      checked: { kind: "rule_error", code: "INVALID_ARGUMENT" },
    });
  });
});
