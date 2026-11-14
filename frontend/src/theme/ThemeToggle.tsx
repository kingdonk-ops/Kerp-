import { useTranslation } from "react-i18next";

import { useTheme } from "./ThemeProvider";

/** Switches the outdoor high-contrast theme on or off (a toggle button, `aria-pressed`). */
export function ThemeToggle({ className = "" }: { className?: string }) {
  const { t } = useTranslation();
  const { theme, setTheme } = useTheme();
  const on = theme === "outdoor";
  return (
    <button
      type="button"
      aria-pressed={on}
      className={`inline-flex min-h-touch min-w-touch items-center gap-2 rounded border border-line bg-panel px-3 text-ink ${className}`}
      onClick={() => {
        setTheme(on ? "standard" : "outdoor");
      }}
    >
      <span
        aria-hidden="true"
        className={`size-4 rounded-full border-2 border-ink ${on ? "bg-ink" : "bg-panel"}`}
      />
      {t("theme.outdoor")}
    </button>
  );
}
