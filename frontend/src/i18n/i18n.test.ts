import { describe, expect, it } from "vitest";

import { DEFAULT_LOCALE, i18n, resources } from "./index";

function flattenKeys(obj: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(obj).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return typeof value === "object" && value !== null
      ? flattenKeys(value as Record<string, unknown>, path)
      : [path];
  });
}

describe("i18n", () => {
  it("is initialised with the default locale", () => {
    expect(i18n.isInitialized).toBe(true);
    expect(i18n.language).toBe(DEFAULT_LOCALE);
  });

  it("resolves every key in the default catalogue to a non-empty string", () => {
    const keys = flattenKeys(resources[DEFAULT_LOCALE].translation);
    expect(keys.length).toBeGreaterThan(0);
    for (const key of keys) {
      expect(i18n.exists(key), key).toBe(true);
      // Keys are discovered at runtime, so bypass the compile-time key union here.
      const value = String(i18n.t(key as never));
      expect(value, key).not.toBe(key);
      expect(value.trim(), key).not.toBe("");
    }
  });
});
