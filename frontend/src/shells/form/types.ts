// Form template schema read by the Dynamic Form Renderer (EDGE-SCREENS §2.2). Labels, option
// labels, error messages and help text are template content authored in the designer, not UI
// chrome: each is a `LocalisedString` resolved by `localise` (§2.2 amendment, TD-183).
import type { CelErrorCode, CelResult, JsonValue } from "../../lib/cel";

/**
 * A user-facing template string: plain (used as is) or a per-locale map keyed by BCP 47 tag,
 * e.g. `{ en: "Remarks", de: "Bemerkungen" }`. Keys, option values and CEL are never localised.
 */
export type LocalisedString = string | Readonly<Record<string, string>>;

export type FieldDataType = "text" | "number" | "boolean" | "select" | "date";

export interface FieldOption {
  value: string;
  label: LocalisedString;
}

export interface FieldValidation {
  /** CEL; the value is valid only when this evaluates to exactly `true`. */
  cel_expression: string;
  error_message: LocalisedString;
}

export interface FormFieldDef {
  field_key: string;
  label: LocalisedString;
  /** Help text shown under the label. */
  help?: LocalisedString;
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
  /** BCP 47 tag used when a localised string has no entry for the user's locale (default `en`). */
  default_language?: string;
  fields: readonly FormFieldDef[];
}

/** Form state keyed by `field_key`. Numbers are CEL doubles; empty is `null`, `""` or absent. */
export type FormValues = Readonly<Record<string, JsonValue | undefined>>;

/** Evaluator seam: the one-engine CEL runtime by default (shared registry: T-0110, TD-177). */
export type RuleEvaluator = (expression: string, context: Readonly<Record<string, JsonValue>>) => CelResult;

export type FieldError =
  | { kind: "required" }
  /** `message` is the template `error_message`, unresolved: the renderer localises it. */
  | { kind: "invalid"; message: LocalisedString }
  /** The rule errored or returned a non-bool: fail closed. */
  | { kind: "rule_error"; code: CelErrorCode | "NON_BOOL_RESULT" };
