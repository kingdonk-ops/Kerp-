// Runtime settings registry and read-only deployment configuration (T-0230 API; diff §4.7).
import type { components } from "../api/schema";
import type { ApiClient } from "../lib/api/client";
import { AdminApiError } from "./api";

export type SettingEntry = components["schemas"]["SettingEntryRead"];
export type SettingValue = SettingEntry["value"];
export type DeploymentConfig = components["schemas"]["DeploymentConfigRead"];

/** Every registry entry with its effective value and bounds (`system_admin` only, else 403). */
export async function fetchRegistry(api: ApiClient): Promise<SettingEntry[]> {
  const { data, response } = await api.GET("/api/v1/settings/registry");
  if (!data) throw new AdminApiError(response.status);
  return data;
}

/** The deployment configuration: set by the environment, shown read-only. */
export async function fetchDeploymentConfig(api: ApiClient): Promise<DeploymentConfig> {
  const { data, response } = await api.GET("/api/v1/settings/deployment-config");
  if (!data) throw new AdminApiError(response.status);
  return data;
}

export type UpdateResult = { kind: "ok"; entry: SettingEntry } | { kind: "invalid"; detail: string };

/** The message of a 422: a string `detail` (registry check) or the first validation error. */
function invalidDetail(error: unknown): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: unknown } | undefined;
    if (typeof first?.msg === "string") return first.msg;
  }
  return "";
}

/** `PATCH /registry/{key}`; out of bounds, over the cap or against a rule is 422 (never clamped). */
export async function updateSetting(
  api: ApiClient,
  key: string,
  value: SettingValue,
): Promise<UpdateResult> {
  const { data, error, response } = await api.PATCH("/api/v1/settings/registry/{key}", {
    params: { path: { key } },
    body: { value },
  });
  if (data) return { kind: "ok", entry: data };
  if (response.status === 422) return { kind: "invalid", detail: invalidDetail(error) };
  throw new AdminApiError(response.status);
}

// --- client-side checks ---------------------------------------------------------------------

/** A draft as the input holds it: text for every type but `bool`. */
export type SettingDraft = string | boolean;

export type SettingError =
  | { key: "required" | "integer" | "number" | "choice" }
  | { key: "min"; min: number }
  | { key: "max"; max: number }
  | { key: "maxLength" | "maxItems"; max: number };

export type SettingCheck = { ok: true; value: SettingValue } | { ok: false; error: SettingError };

/** The highest value the entry accepts: its maximum, lowered by the deployment cap. */
export function effectiveMaximum(entry: SettingEntry): number | null {
  const { maximum, cap } = entry;
  if (maximum === null) return cap;
  return cap === null ? maximum : Math.min(maximum, cap);
}

/** The input's text for a value. */
export function draftOf(entry: SettingEntry, value: SettingValue = entry.value): SettingDraft {
  if (entry.type === "bool") return value === true;
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

const INTEGER = /^-?\d+$/;
const NUMBER = /^-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

function checkNumber(entry: SettingEntry, text: string, integer: boolean): SettingCheck {
  const trimmed = text.trim();
  if (trimmed === "") return { ok: false, error: { key: "required" } };
  if (integer && !INTEGER.test(trimmed)) return { ok: false, error: { key: "integer" } };
  if (!integer && !NUMBER.test(trimmed)) return { ok: false, error: { key: "number" } };
  const value = Number(trimmed);
  const max = effectiveMaximum(entry);
  if (entry.minimum !== null && value < entry.minimum) {
    return { ok: false, error: { key: "min", min: entry.minimum } };
  }
  if (max !== null && value > max) return { ok: false, error: { key: "max", max } };
  return { ok: true, value };
}

/**
 * The registry's own checks of type, bounds, cap and length, before sending (diff §4.7). The
 * server repeats them and adds the checks the client cannot make (IANA zone, ISO 4217, rules
 * across keys); its 422 is shown at the entry.
 */
export function checkSettingValue(entry: SettingEntry, draft: SettingDraft): SettingCheck {
  if (entry.type === "bool") return { ok: true, value: draft === true };
  const text = typeof draft === "string" ? draft : String(draft);
  switch (entry.type) {
    case "int":
      return checkNumber(entry, text, true);
    case "float":
      return checkNumber(entry, text, false);
    case "enum":
      return entry.choices.includes(text)
        ? { ok: true, value: text }
        : { ok: false, error: { key: "choice" } };
    case "string":
      if (entry.max_length !== null && text.length > entry.max_length) {
        return { ok: false, error: { key: "maxLength", max: entry.max_length } };
      }
      return { ok: true, value: text };
    case "int_list": {
      const parts = text.trim() === "" ? [] : text.split(",");
      if (entry.max_length !== null && parts.length > entry.max_length) {
        return { ok: false, error: { key: "maxItems", max: entry.max_length } };
      }
      const values: number[] = [];
      for (const part of parts) {
        const item = checkNumber(entry, part, true);
        if (!item.ok) return item.error.key === "required" ? { ok: false, error: { key: "integer" } } : item;
        values.push(item.value as number);
      }
      return { ok: true, value: values };
    }
  }
}

/** Entries grouped by category, in the API's order (by category, then key). */
export function byCategory(entries: readonly SettingEntry[]): [string, SettingEntry[]][] {
  const groups = new Map<string, SettingEntry[]>();
  for (const entry of entries) {
    const group = groups.get(entry.category) ?? [];
    group.push(entry);
    groups.set(entry.category, group);
  }
  return [...groups.entries()];
}
