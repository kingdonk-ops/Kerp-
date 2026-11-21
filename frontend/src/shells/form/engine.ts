// Pure evaluation of a form template against the current values: visibility (visible_if),
// sanitisation of hidden fields, calculated fields, required / required_if, answer shapes,
// CEL validation and ranges. Fail closed: a rule passes only on `ok && value === true`
// (src/lib/cel/README.md). The server runs the same semantics in
// backend/app/core/fields/evaluator.py; tests/contract/form_fixtures.json holds both to it.
import { evaluate, type CelResult, type JsonValue } from "../../lib/cel";
import { isEmptyValue, isFiniteNumber, isValidValue, resolveFieldType } from "../../lib/fields";
import type { FieldError, FormFieldDef, FormTemplate, FormValues, RuleEvaluator } from "./types";

export { isEmptyValue };

export interface FormEvaluation {
  /** Keys of the fields to render, in template order. */
  visible: ReadonlySet<string>;
  /**
   * Visible, non-empty values plus the calculated ones: what is submitted and what `fields`
   * holds in CEL. A value sent for a calculated field is ignored.
   */
  values: Record<string, JsonValue>;
  errors: Readonly<Record<string, FieldError>>;
  /** Keys of the visible fields that are required now: `required`, or an active `required_if`. */
  required: ReadonlySet<string>;
  /** Keys whose answer is outside its `range`: flagged, not blocking (`valid` ignores them). */
  failed: ReadonlySet<string>;
  valid: boolean;
}

export interface EvaluateFormOptions {
  /** Extra CEL variables (e.g. `asset`, `device`); `fields` is always the form values. */
  context?: Readonly<Record<string, JsonValue>>;
  evaluator?: RuleEvaluator;
}

type RuleFailure = Extract<FieldError, { kind: "rule_error" }>["code"];

function passes(result: CelResult): boolean {
  return result.ok && result.value === true;
}

function isCalculated(field: FormFieldDef): boolean {
  return resolveFieldType(field.data_type)?.value === "computed";
}

/**
 * Evaluate without throwing: `src/lib/cel` throws on a malformed context (e.g. a bad `$int`
 * in a value or in server-provided `context`). That becomes a rule failure, so it fails closed.
 */
function safeRunner(run: RuleEvaluator): RuleEvaluator {
  return (expression, context) => {
    try {
      return run(expression, context);
    } catch (error) {
      return { ok: false, code: "INVALID_ARGUMENT", message: error instanceof Error ? error.message : String(error) };
    }
  };
}

function sameSet(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  return a.size === b.size && [...a].every((key) => b.has(key));
}

export function evaluateForm(
  template: FormTemplate,
  values: FormValues,
  options: EvaluateFormOptions = {},
): FormEvaluation {
  const run = safeRunner(options.evaluator ?? evaluate);
  const extra = options.context ?? {};
  // A Map, so keys such as `constructor` or `__proto__` never touch `Object.prototype`.
  const contextFor = (fields: ReadonlyMap<string, JsonValue>) => ({ ...extra, fields: Object.fromEntries(fields) });

  /**
   * `fields` for a visible set: own, non-empty answers of the visible input fields, then each
   * visible calculated field in template order (it sees the calculated fields before it).
   */
  const materialise = (visible: ReadonlySet<string>) => {
    const fields = new Map<string, JsonValue>();
    const failures = new Map<string, RuleFailure>();
    for (const field of template.fields) {
      const key = field.field_key;
      if (!visible.has(key) || isCalculated(field) || !Object.hasOwn(values, key)) continue;
      const value = values[key];
      if (value !== undefined && !isEmptyValue(value)) fields.set(key, value);
    }
    for (const field of template.fields) {
      if (!visible.has(field.field_key) || !isCalculated(field)) continue;
      const result = run(field.expression ?? "", contextFor(fields));
      if (!result.ok) failures.set(field.field_key, result.code);
      else if (!isEmptyValue(result.value)) fields.set(field.field_key, result.value);
    }
    return { fields, failures };
  };

  // Visibility to a fixpoint: a field hidden by its condition is removed from `fields`, which
  // can hide fields that depend on it. Conditions that never settle hide the field.
  const allKeys = template.fields.map((field) => field.field_key);
  let visible: ReadonlySet<string> = new Set(allKeys);
  let previous: ReadonlySet<string> | null = null;
  for (let pass = 0; pass <= template.fields.length; pass += 1) {
    const context = contextFor(materialise(visible).fields);
    const next = new Set(
      template.fields
        .filter((field) => field.visible_if === undefined || passes(run(field.visible_if, context)))
        .map((field) => field.field_key),
    );
    previous = visible;
    visible = next;
    if (sameSet(previous, visible)) break;
  }
  if (previous !== null && !sameSet(previous, visible)) {
    const settled = previous;
    visible = new Set(allKeys.filter((key) => visible.has(key) && settled.has(key)));
  }

  const { fields: sanitized, failures } = materialise(visible);
  const context = contextFor(sanitized);
  const errors: [string, FieldError][] = [];
  const requiredKeys = new Set<string>();
  const failed = new Set<string>();

  /** A range bound: a number, or CEL that must give a finite double. */
  const bound = (spec: number | string | undefined): number | RuleFailure | undefined => {
    if (spec === undefined) return undefined;
    if (typeof spec !== "string") return isFiniteNumber(spec) ? spec : "NON_NUMERIC_RESULT";
    const result = run(spec, context);
    if (!result.ok) return result.code;
    return isFiniteNumber(result.value) ? result.value : "NON_NUMERIC_RESULT";
  };

  for (const field of template.fields) {
    const key = field.field_key;
    if (!visible.has(key)) continue;
    const required =
      field.required === true || (field.required_if !== undefined && !isFalse(run(field.required_if, context)));
    if (required) requiredKeys.add(key);
    const failure = failures.get(key);
    if (failure !== undefined) {
      errors.push([key, { kind: "rule_error", code: failure }]);
      continue;
    }
    const value = sanitized.get(key);
    if (value === undefined) {
      if (required) errors.push([key, { kind: "required" }]);
      continue;
    }
    if (!isCalculated(field) && !isValidValue(field, value)) {
      errors.push([key, { kind: "type_mismatch" }]);
      continue;
    }
    if (field.validation !== undefined) {
      const result = run(field.validation.cel_expression, context);
      let error: FieldError | undefined;
      if (!result.ok) error = { kind: "rule_error", code: result.code };
      else if (typeof result.value !== "boolean") error = { kind: "rule_error", code: "NON_BOOL_RESULT" };
      else if (!result.value) error = { kind: "invalid", message: field.validation.error_message };
      if (error !== undefined) {
        errors.push([key, error]);
        continue;
      }
    }
    if (field.range === undefined) continue;
    // The measured number: the answer itself, or `value` of a measurement.
    const reading = typeof value === "object" && value !== null && !Array.isArray(value) ? value.value : value;
    const min = bound(field.range.min);
    const max = bound(field.range.max);
    const broken = [min, max].find((item) => typeof item === "string") ?? (isFiniteNumber(reading) ? undefined : "NON_NUMERIC_RESULT");
    if (typeof broken === "string") errors.push([key, { kind: "rule_error", code: broken }]);
    else if (isFiniteNumber(reading) && ((typeof min === "number" && reading < min) || (typeof max === "number" && reading > max))) {
      failed.add(key);
    }
  }

  return {
    visible,
    values: Object.fromEntries(sanitized),
    errors: Object.fromEntries(errors),
    required: requiredKeys,
    failed,
    valid: errors.length === 0,
  };
}

/** required_if fails closed: only an explicit `false` makes the field optional. */
function isFalse(result: CelResult): boolean {
  return result.ok && result.value === false;
}
