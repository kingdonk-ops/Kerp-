import { useId, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import type { JsonValue } from "../../lib/cel";
import { evaluateForm } from "./engine";
import type { FieldError, FormFieldDef, FormTemplate, FormValues, RuleEvaluator } from "./types";

export interface DynamicFormProps {
  template: FormTemplate;
  initialValues?: FormValues;
  /** Extra CEL variables next to `fields` (e.g. `asset`, `device`). */
  context?: Readonly<Record<string, JsonValue>>;
  evaluator?: RuleEvaluator;
  /** Receives only visible, non-empty values (hidden fields are sanitised). */
  onSubmit: (values: Record<string, JsonValue>) => void;
  submitLabel?: string;
  busy?: boolean;
}

const INPUT = "w-full rounded border border-line bg-panel px-2 py-1";

/** Renders a form template; visibility and validation are CEL rules evaluated live. */
export function DynamicForm({
  template,
  initialValues = {},
  context,
  evaluator,
  onSubmit,
  submitLabel,
  busy = false,
}: DynamicFormProps) {
  const { t } = useTranslation();
  const formId = useId();
  const [values, setValues] = useState<FormValues>(initialValues);
  const [touched, setTouched] = useState<ReadonlySet<string>>(new Set());
  const [submitted, setSubmitted] = useState(false);

  const evaluation = useMemo(
    () => evaluateForm(template, values, { ...(context && { context }), ...(evaluator && { evaluator }) }),
    [template, values, context, evaluator],
  );

  const setValue = (key: string, value: JsonValue) => {
    setValues((current) => ({ ...current, [key]: value }));
    setTouched((current) => new Set(current).add(key));
  };

  const errorText = (error: FieldError): string => {
    if (error.kind === "required") return t("shells.form.errors.required");
    if (error.kind === "invalid") return error.message;
    return t("shells.form.errors.ruleError");
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    if (evaluation.valid) onSubmit(evaluation.values);
  };

  return (
    <form className="space-y-4" noValidate onSubmit={handleSubmit}>
      {template.fields
        .filter((field) => evaluation.visible.has(field.field_key))
        .map((field) => {
          const id = `${formId}-${field.field_key}`;
          // Own-property reads: field keys such as `constructor` must not hit Object.prototype.
          const error = Object.hasOwn(evaluation.errors, field.field_key) ? evaluation.errors[field.field_key] : undefined;
          const shownError = error !== undefined && (submitted || touched.has(field.field_key)) ? error : undefined;
          const errorId = `${id}-error`;
          // Static `required` or an active `required_if` (fail closed), computed by the engine.
          const required = evaluation.required.has(field.field_key);
          return (
            <div key={field.field_key} className="space-y-1">
              <label htmlFor={id} className="block text-sm font-medium">
                {field.label}
                {required && (
                  <span className="ml-1 text-danger" aria-hidden="true" title={t("shells.form.requiredMark")}>
                    *
                  </span>
                )}
              </label>
              <FieldControl
                field={field}
                id={id}
                value={Object.hasOwn(values, field.field_key) ? values[field.field_key] : undefined}
                required={required}
                invalid={shownError !== undefined}
                describedBy={shownError !== undefined ? errorId : undefined}
                onChange={(value) => { setValue(field.field_key, value); }}
              />
              {shownError !== undefined && (
                <p id={errorId} className="text-sm text-danger">
                  {errorText(shownError)}
                </p>
              )}
            </div>
          );
        })}
      <button
        type="submit"
        disabled={busy}
        className="rounded bg-brand px-4 py-2 text-on-brand disabled:opacity-50"
      >
        {submitLabel ?? t("shells.form.submit")}
      </button>
    </form>
  );
}

interface FieldControlProps {
  field: FormFieldDef;
  id: string;
  value: JsonValue | undefined;
  required: boolean;
  invalid: boolean;
  describedBy: string | undefined;
  onChange: (value: JsonValue) => void;
}

function FieldControl({ field, id, value, required, invalid, describedBy, onChange }: FieldControlProps) {
  const common = {
    id,
    name: field.field_key,
    "aria-invalid": invalid || undefined,
    "aria-describedby": describedBy,
    "aria-required": required || undefined,
  };
  const text = typeof value === "string" || typeof value === "number" ? String(value) : "";

  switch (field.data_type) {
    case "boolean":
      return (
        <input {...common} type="checkbox" checked={value === true} onChange={(e) => { onChange(e.target.checked); }} />
      );
    case "select":
      return (
        <select {...common} className={INPUT} value={text} onChange={(e) => { onChange(e.target.value); }}>
          <option value="" />
          {(field.options ?? []).map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      );
    case "number":
      return (
        <input
          {...common}
          type="number"
          step="any"
          className={INPUT}
          value={text}
          // Plain JSON numbers are CEL doubles (src/lib/cel/README.md); empty clears the value.
          onChange={(e) => { onChange(e.target.value === "" ? null : Number(e.target.value)); }}
        />
      );
    case "date":
    case "text":
      return (
        <input
          {...common}
          type={field.data_type}
          className={INPUT}
          value={text}
          onChange={(e) => { onChange(e.target.value); }}
        />
      );
  }
}
