import { useEffect, useId, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { NavLink } from "react-router";

export const BUTTON = "min-h-touch min-w-touch rounded border border-line bg-panel px-3";
export const PRIMARY = "min-h-touch min-w-touch rounded bg-brand px-3 text-on-brand";
export const INPUT = "block min-h-touch w-full rounded border border-line bg-panel px-2";

/** Links between the System screens; Admin grants only for System admins (T-0026). */
export function SystemTabs({ systemAdmin }: { systemAdmin: boolean }) {
  const { t } = useTranslation();
  const tabs = [
    ...(systemAdmin ? [{ to: "/admin/system/grants", label: t("admin.system.grantsTab") }] : []),
    { to: "/admin/system/devices", label: t("admin.system.devicesTab") },
  ];
  return (
    <nav aria-label={t("admin.system.tabs")} className="flex flex-wrap gap-touch-gap">
      {tabs.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          className={({ isActive }) =>
            `flex min-h-touch items-center rounded px-3 ${isActive ? "bg-brand text-on-brand" : "border border-line"}`
          }
        >
          {tab.label}
        </NavLink>
      ))}
    </nav>
  );
}

/** Date and time in the UI language; `null` when the time is absent. */
export function formatTime(iso: string | null | undefined, lang: string): string | null {
  if (!iso) return null;
  return new Intl.DateTimeFormat(lang, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(iso),
  );
}

/** "3 days ago" in the UI language (largest whole unit). */
export function formatAge(iso: string, lang: string, now: number = Date.now()): string {
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  const fmt = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return fmt.format(Math.trunc(seconds / size), unit);
  }
  return fmt.format(0, "minute");
}


/**
 * Confirmation dialog, shown in the page flow above the list it acts on (no overlay, so
 * nothing on the page is hidden behind it and contrast stays measurable): labelled by its
 * title, focus moves in on open, Escape or Cancel closes. `actions` sit next to Cancel, a
 * glove gap apart.
 */
export function Dialog({
  title,
  onClose,
  children,
  actions,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  actions: ReactNode;
}) {
  const { t } = useTranslation();
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return (
    <div
      ref={ref}
      role="dialog"
      aria-labelledby={titleId}
      tabIndex={-1}
      className="max-w-lg space-y-3 rounded border-2 border-brand bg-panel p-4 text-ink"
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <h2 id={titleId} className="text-lg font-semibold">
        {title}
      </h2>
      {children}
      <div className="flex flex-wrap gap-touch-gap">
        {actions}
        <button type="button" className={BUTTON} onClick={onClose}>
          {t("admin.cancel")}
        </button>
      </div>
    </div>
  );
}
