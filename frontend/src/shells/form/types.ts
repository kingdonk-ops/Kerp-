// Form template schema read by the Dynamic Form Renderer (EDGE-SCREENS §2.2). Labels, option
// labels and error messages are template content authored in the designer, not UI chrome.
import type { CelErrorCode, CelResult, JsonValue } from "../../lib/cel";

export type FieldDataType = "text" | "number" | "boolean" | "select" | "date";

export interface FieldOption {
  value: string;
  label: string;
}

export interface FieldValidation {
  /** CEL; the value is valid only when this evaluates to exactly `true`. */
  cel_expression: string;
  error_message: string;
}

export interface FormFieldDef {
  field_key: string;
  label: string;
  data_type: FieldDataType;
  options?: readonly FieldOption[];
  required?: boolean;
  /** CEL; the field is required when this evaluates to `true` (or cannot be evaluated). */
  required_if?: string;
  /** CEL; the field renders only when this evaluates to exactly `true`. */
  visible_if?: string;
  validation?: FieldValidation;
}

export interface FormTemplate {
  fields: readonly FormFieldDef[];
}

/** Form state keyed by `field_key`. Numbers are CEL doubles; empty is `null`, `""` or absent. */
export type FormValues = Readonly<Record<string, JsonValue | undefined>>;

/** Evaluator seam: the one-engine CEL runtime by default (shared registry: T-0110, TD-177). */
export type RuleEvaluator = (expression: string, context: Readonly<Record<string, JsonValue>>) => CelResult;

export type FieldError =
  | { kind: "required" }
  | { kind: "invalid"; message: string }
  /** The rule errored or returned a non-bool: fail closed. */
  | { kind: "rule_error"; code: CelErrorCode | "NON_BOOL_RESULT" };
