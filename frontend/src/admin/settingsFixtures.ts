// Synthetic registry and deployment configuration for the Settings screen tests (T-0232).
import type { DeploymentConfig, SettingEntry } from "./settings";

const base = {
  description: "",
  is_default: true,
  minimum: null,
  maximum: null,
  choices: [],
  max_length: null,
  client_visible: false,
  cap: null,
  cap_source: null,
} satisfies Partial<SettingEntry>;

const int = (key: string, category: string, value: number, minimum: number, maximum: number): SettingEntry => ({
  ...base,
  key,
  category,
  type: "int",
  value,
  default: value,
  minimum,
  maximum,
});

/** In the API's order: by category, then key. */
export const REGISTRY: SettingEntry[] = [
  int("max_checkout_clock_skew_s", "offline", 5, 1, 300),
  int("offline_grace_hours", "offline", 24, 1, 336),
  int("offline_window_days", "offline", 7, 1, 14),
  { ...base, key: "default_currency_code", category: "project_defaults", type: "string", value: "AUD", default: "AUD", max_length: 255 },
  { ...base, key: "default_locale", category: "project_defaults", type: "string", value: "en-AU", default: "en-AU", max_length: 255 },
  { ...base, key: "default_site_timezone", category: "project_defaults", type: "string", value: "Australia/Perth", default: "Australia/Perth", max_length: 255 },
  { ...base, key: "default_status", category: "project_defaults", type: "enum", value: "ACTIVE", default: "ACTIVE", choices: ["ACTIVE", "ON_HOLD", "CLOSED"] },
  { ...base, key: "sample_flag", category: "sample_category", type: "bool", value: true, default: true },
  { ...base, key: "sample_list", category: "sample_category", type: "int_list", value: [1, 2, 3], default: [1, 2, 3], minimum: 1, maximum: 9, max_length: 5 },
  { ...int("max_upload_size_mb", "uploads_media", 50, 1, 5120), cap: 50, cap_source: "KERP_UPLOAD_MAX_BYTES", client_visible: true },
  { ...int("presigned_url_ttl_minutes", "uploads_media", 15, 1, 60), cap: 15, cap_source: "KERP_UPLOAD_URL_SECONDS" },
];

export function settingEntry(key: string): SettingEntry {
  const found = REGISTRY.find((e) => e.key === key);
  if (!found) throw new Error(`no synthetic entry ${key}`);
  return found;
}

export const DEPLOYMENT_CONFIG: DeploymentConfig = {
  refresh_token_hours: 12,
  pin_idle_minutes: 30,
  pin_lockout: { free_attempts: 3, base_delay_seconds: 30, max_delay_seconds: 900, max_attempts: 10 },
  region: "ap-southeast-2",
  storage_region: "ap-southeast-2",
  mail_transport: "ses",
  mail_from_address: "noreply@example.test",
  upload_scanner: "clamd",
  upload_max_bytes: 52_428_800,
  upload_url_seconds: 900,
  ai_provider: "none",
};
