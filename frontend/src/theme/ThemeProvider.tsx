import { createContext, useContext, useLayoutEffect, useState, type ReactNode } from "react";

import { useServices } from "../app/services";
import { useAuth } from "../auth/AuthProvider";
import { applyTheme, decodeTheme, DEFAULT_THEME, DEVICE_THEME_KEY, themeStorageKey, type ThemeId } from "./theme";

interface ThemeValue {
  theme: ThemeId;
  setTheme: (theme: ThemeId) => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);

/**
 * Applies the display theme and remembers it per user in the preferences storage
 * (`kerp.theme.<userId>`; server-side preferences: see the frontend README). Before sign-in
 * the device's last choice applies, so the sign-in page is readable outdoors too.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const { preferences } = useServices();
  const { user } = useAuth();
  const key = user ? themeStorageKey(user.id) : DEVICE_THEME_KEY;
  const [chosen, setChosen] = useState<{ key: string; theme: ThemeId } | null>(null);

  // A choice made in this session wins for its key; otherwise read the stored preference
  // (another user signing in reads their own).
  const theme =
    chosen?.key === key ? chosen.theme : (decodeTheme(preferences.getItem(key)) ?? DEFAULT_THEME);

  useLayoutEffect(() => {
    applyTheme(theme);
    return () => {
      applyTheme(DEFAULT_THEME);
    };
  }, [theme]);

  const setTheme = (next: ThemeId) => {
    preferences.setItem(key, next);
    // The sign-in page follows the last choice on this device.
    if (key !== DEVICE_THEME_KEY) preferences.setItem(DEVICE_THEME_KEY, next);
    setChosen({ key, theme: next });
  };

  const value: ThemeValue = { theme, setTheme };
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside <ThemeProvider>");
  return value;
}
