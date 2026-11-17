// Synthetic inspection-form template for the form-open budget (diff §6.3, T-0045).
// Synthetic content only. Mixes every data type and carries the CEL rules a real check sheet
// has (conditional visibility, conditional requirement, range validation, help text), so the
// measured open includes rule evaluation, not just DOM creation.
import type { FieldDataType, FormFieldDef, FormTemplate } from "../shells/form/types";

const TYPES: readonly FieldDataType[] = ["text", "number", "boolean", "select", "date"];

const OPTIONS = [
  { value: "ok", label: { en: "Acceptable", de: "Annehmbar" } },
  { value: "minor", label: { en: "Minor defect", de: "Kleiner Mangel" } },
  { value: "major", label: { en: "Major defect", de: "Großer Mangel" } },
] as const;

const key = (i: number): string => `q${String(i + 1).padStart(3, "0")}`;

function question(i: number): FormFieldDef {
  const type = TYPES[i % TYPES.length] ?? "text";
  const field: FormFieldDef = {
    field_key: key(i),
    label: { en: `Synthetic check ${String(i + 1)}`, de: `Synthetische Prüfung ${String(i + 1)}` },
    data_type: type,
    ...(type === "select" && { options: OPTIONS }),
    ...(i % 7 === 0 && { required: true }),
    ...(i % 4 === 0 && { help: `Synthetic guidance for check ${String(i + 1)}.` }),
  };
  // Conditional visibility on the previous boolean (i - 4: data type index 2): shown unless that
  // answer is "no", so every question is visible when the form opens.
  if (i % 5 === 1 && i > 5) {
    const gate = key(i - 4);
    field.visible_if = `!has(fields.${gate}) || fields.${gate} == true`;
  }
  // Conditional requirement on the previous select (i - 2: data type index 3).
  if (i % 5 === 0 && i > 5) {
    const gate = key(i - 2);
    field.required_if = `has(fields.${gate}) && fields.${gate} == "major"`;
  }
  // Range validation on numbers.
  if (type === "number") {
    field.validation = {
      cel_expression: `fields.${key(i)} >= 0.0 && fields.${key(i)} <= 1000.0`,
      error_message: { en: "Enter a value from 0 to 1000.", de: "Einen Wert von 0 bis 1000 eingeben." },
    };
  }
  return field;
}

/** A deterministic template of `questions` fields. */
export function syntheticTemplate(questions: number): FormTemplate {
  return { default_language: "en", fields: Array.from({ length: questions }, (_, i) => question(i)) };
}
