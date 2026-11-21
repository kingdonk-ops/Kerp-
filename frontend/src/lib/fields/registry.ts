// The one field-type palette (core/fields, diff §2.1), client side. Mirrors
// backend/app/core/fields/registry.py; both must equal tests/contract/field_types.json
// (registry.test.ts). Answer shapes: tests/contract/README.md.
import type { JsonValue } from "../cel";

/** Version of the form evaluation semantics; equals `evaluator_version` in the form fixtures. */
export const EVALUATOR_VERSION = 1;

export const FIELD_TYPES = [
  { key: "numeric", aliases: ["number"], value: "number", rangeable: true },
  { key: "text", aliases: [], value: "string", rangeable: false },
  { key: "boolean", aliases: [], value: "boolean", rangeable: false },
  { key: "select", aliases: [], value: "option", rangeable: false },
  { key: "date", aliases: [], value: "date", rangeable: false },
  { key: "measurement", aliases: [], value: "measurement", rangeable: true },
  { key: "photo", aliases: [], value: "attachments", rangeable: false },
  { key: "signature", aliases: [], value: "attachment", rangeable: false },
  { key: "instrument_ref", aliases: [], value: "instrument", rangeable: false },
  { key: "calculated", aliases: [], value: "computed", rangeable: true },
] as const;

export type FieldTypeDef = (typeof FIELD_TYPES)[number];
export type FieldTypeKey = FieldTypeDef["key"];

/** What the value check needs from a field definition. */
export interface TypedField {
  data_type: string;
  options?: readonly { value: string }[];
  units?: readonly string[];
}

const DATE_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/; // ASCII digits, shape only (as on the server)

/** The palette entry for a `data_type` (key or alias); `undefined` when unknown. */
export function resolveFieldType(dataType: string): FieldTypeDef | undefined {
  return FIELD_TYPES.find((type) => type.key === dataType || (type.aliases as readonly string[]).includes(dataType));
}

/** Unanswered: absent, `null`, `""` or an empty list. `0` and `false` are answers. */
export function isEmptyValue(value: JsonValue | undefined): boolean {
  return value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0);
}

export function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isRecord(value: JsonValue | undefined): value is Record<string, JsonValue> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasId(value: JsonValue | undefined, key: string): boolean {
  if (!isRecord(value) || !Object.hasOwn(value, key)) return false;
  const id = value[key];
  return typeof id === "string" && id !== "";
}

/** Whether a non-empty answer has the shape of the field's type. Unknown types never match. */
export function isValidValue(field: TypedField, value: JsonValue): boolean {
  switch (resolveFieldType(field.data_type)?.value) {
    case "number":
      return isFiniteNumber(value);
    case "string":
      return typeof value === "string";
    case "boolean":
      return typeof value === "boolean";
    case "option":
      return typeof value === "string" && (field.options ?? []).some((option) => option.value === value);
    case "date":
      return typeof value === "string" && DATE_RE.test(value);
    case "measurement":
      return (
        isRecord(value) &&
        Object.keys(value).length === 2 &&
        Object.hasOwn(value, "value") &&
        Object.hasOwn(value, "unit") &&
        isFiniteNumber(value.value) &&
        typeof value.unit === "string" &&
        (field.units ?? []).includes(value.unit)
      );
    case "attachments":
      return Array.isArray(value) && value.every((item) => hasId(item, "attachment_id"));
    case "attachment":
      return hasId(value, "attachment_id");
    case "instrument":
      return hasId(value, "instrument_id");
    default: // "computed" answers come from the expression, never from the user
      return false;
  }
}
