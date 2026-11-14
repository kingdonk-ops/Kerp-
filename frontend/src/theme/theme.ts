// Display themes (diff §6.3 "Outdoor"): the standard theme and the outdoor high-contrast theme.
// A theme is a set of semantic colour tokens in `src/index.css`, switched by one attribute on
// the root element, so every component using the semantic utilities follows.

export const THEMES = ["standard", "outdoor"] as const;
export type ThemeId = (typeof THEMES)[number];

export const DEFAULT_THEME: ThemeId = "standard";

/** Root attribute selecting the theme (`<html data-theme="outdoor">`); absent = standard. */
export const THEME_ATTRIBUTE = "data-theme";

/** Per-user preference key in the preferences storage. */
export function themeStorageKey(userId: string): string {
  return `kerp.theme.${userId}`;
}

/** Theme last chosen on this device: used on the sign-in page, before a user is known. */
export const DEVICE_THEME_KEY = "kerp.theme.device";

export function decodeTheme(raw: string | null): ThemeId | null {
  return THEMES.find((theme) => theme === raw) ?? null;
}

export function applyTheme(theme: ThemeId, root: HTMLElement = document.documentElement): void {
  if (theme === DEFAULT_THEME) root.removeAttribute(THEME_ATTRIBUTE);
  else root.setAttribute(THEME_ATTRIBUTE, theme);
}
