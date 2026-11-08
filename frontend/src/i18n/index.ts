import i18next from "i18next";
import { initReactI18next } from "react-i18next";

import { en } from "./locales/en";

export const DEFAULT_LOCALE = "en";

export const resources = {
  en: { translation: en },
} as const;

export const i18n = i18next.createInstance();

void i18n.use(initReactI18next).init({
  resources,
  lng: DEFAULT_LOCALE,
  fallbackLng: DEFAULT_LOCALE,
  // Resources are bundled, so init completes synchronously.
  initAsync: false,
  interpolation: {
    // React already escapes rendered output.
    escapeValue: false,
  },
  returnNull: false,
});
