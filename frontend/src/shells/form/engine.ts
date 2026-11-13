// Pure evaluation of a form template against the current values: visibility (visible_if),
// sanitisation of hidden fields, required / required_if and CEL validation. Fail closed: a
// rule passes only on `ok && value === true` (src/lib/cel/README.md).
import { evaluate, type CelResult, type JsonValue } from "../../lib/cel";
import type { FieldError, FormTemplate, FormValues, RuleEvaluator } from "./types";

export interface FormEvaluation {
  /** Keys of the fields to render, in template order. */
  visible: ReadonlySet<string>;
  /** Visible, non-empty values only: what is submitted and what `fields` holds in CEL. */
  values: Record<string, JsonValue>;
  errors: Readonly<Record<string, FieldError>>;
  valid: boolean;
}

export interface EvaluateFormOptions {
  /** Extra CEL variables (e.g. `asset`, `device`); `fields` is always the form values. */
  context?: Readonly<Record<string, JsonValue>>;
  evaluator?: RuleEvaluator;
}

export function isEmptyValue(value: JsonValue | undefined): boolean {
  return value === undefined || value === null || value === "";
}

function passes(result: CelResult): boolean {
  return result.ok && result.value === true;
}

function pick(values: FormValues, keys: ReadonlySet<string>): Record<string, JsonValue> {
  const picked: Record<string, JsonValue> = {};
  for (const key of keys) {
    const value = values[key];
    if (value !== undefined && !isEmptyValue(value)) picked[key] = value;
  }
  return picked;
}

function sameSet(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  return a.size === b.size && [...a].every((key) => b.has(key));
}

export function evaluateForm(
  template: FormTemplate,
  values: FormValues,
  options: EvaluateFormOptions = {},
): FormEvaluation {
  const run = options.evaluator ?? evaluate;
  const extra = options.context ?? {};
  const contextFor = (fields: Record<string, JsonValue>) => ({ ...extra, fields });

  // Visibility to a fixpoint: a field hidden by its condition is removed from `fields`, which
  // can hide fields that depend on it. Conditions that never settle hide the field.
  const allKeys = template.fields.map((field) => field.field_key);
  let visible: ReadonlySet<string> = new Set(allKeys);
  let previous: ReadonlySet<string> | null = null;
  for (let pass = 0; pass <= template.fields.length; pass += 1) {
    const context = contextFor(pick(values, visible));
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

  const sanitized = pick(values, visible);
  const context = contextFor(sanitized);
  const errors: Record<string, FieldError> = {};
  for (const field of template.fields) {
    const key = field.field_key;
    if (!visible.has(key)) continue;
    const required =
      field.required === true || (field.required_if !== undefined && !isFalse(run(field.required_if, context)));
    if (!(key in sanitized)) {
      if (required) errors[key] = { kind: "required" };
      continue;
    }
    if (field.validation === undefined) continue;
    const result = run(field.validation.cel_expression, context);
    if (!result.ok) errors[key] = { kind: "rule_error", code: result.code };
    else if (typeof result.value !== "boolean") errors[key] = { kind: "rule_error", code: "NON_BOOL_RESULT" };
    else if (!result.value) errors[key] = { kind: "invalid", message: field.validation.error_message };
  }

  return { visible, values: sanitized, errors, valid: Object.keys(errors).length === 0 };
}

/** required_if fails closed: only an explicit `false` makes the field optional. */
function isFalse(result: CelResult): boolean {
  return result.ok && result.value === false;
}
