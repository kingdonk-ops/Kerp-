// Localised template strings (EDGE-SCREENS §2.2 amendment, TD-183): template content is a plain
// string or a per-locale map keyed by BCP 47 tag. UI chrome stays on i18n `t()`.
import type { LocalisedString } from "./types";

/** Default when a template does not declare `default_language`. */
export const DEFAULT_TEMPLATE_LANGUAGE = "en";

/** Primary language subtag of a BCP 47 tag (`de-AT` -> `de`), lower-cased. */
function languageOf(tag: string): string {
  return tag.split("-", 1)[0]?.toLowerCase() ?? "";
}

/**
 * Resolves a template string for display: a plain string as is; a map by the exact locale, then
 * its language (`de-AT` -> `de`), then `defaultLanguage`, then the first entry. Tags compare
 * case-insensitively; only own entries are read. An empty map (rejected by the template lint)
 * resolves to `""`.
 */
export function localise(value: LocalisedString, locale: string, defaultLanguage: string): string {
  if (typeof value === "string") return value;
  const entries = Object.entries(value);
  const find = (tag: string): string | undefined => {
    const wanted = tag.toLowerCase();
    return entries.find(([key]) => key.toLowerCase() === wanted)?.[1];
  };
  return find(locale) ?? find(languageOf(locale)) ?? find(defaultLanguage) ?? entries[0]?.[1] ?? "";
}
