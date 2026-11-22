import { useEffect, useId, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { NavLink } from "react-router";

import { useAdminAccess, type AdminAccess } from "./access";
import { AdminApiError } from "./api";

export const BUTTON = "min-h-touch min-w-touch rounded border border-line bg-panel px-3";
export const PRIMARY = "min-h-touch min-w-touch rounded bg-brand px-3 text-on-brand";
export const INPUT = "block min-h-touch w-full rounded border border-line bg-panel px-2";
/** A card of a register list (cards, not tables: no sideways scrolling on field phones). */
export const CARD_ITEM = "space-y-1 rounded border border-line bg-panel p-3";

/**
 * The System screens, in tab order (diff §6.5). `systemAdminOnly` tabs are hidden from
 * everyone else (the API answers them 403 anyway). A new System screen adds its row here and
 * its route in `src/app/routes.tsx`.
 */
export const SYSTEM_TABS = [
  { to: "/admin/system/deployment", label: "admin.system.deploymentTab", systemAdminOnly: true },
  { to: "/admin/system/queues", label: "admin.system.queuesTab", systemAdminOnly: true },
  { to: "/admin/system/uploads", label: "admin.system.uploadsTab", systemAdminOnly: true },
  { to: "/admin/system/settings", label: "admin.system.settingsTab", systemAdminOnly: true },
  { to: "/admin/system/audit", label: "admin.system.auditTab", systemAdminOnly: true },
  { to: "/admin/system/grants", label: "admin.system.grantsTab", systemAdminOnly: true },
  { to: "/admin/system/devices", label: "admin.system.devicesTab", systemAdminOnly: false },
] as const;

/**
 * The gate of a `system_admin`-only System screen (diff §6.5): `allowed` enables its queries;
 * `forbidden(errors)` once the client knows the caller is no System admin or the API said 403.
 */
export function useSystemAdminGate(): {
  access: AdminAccess;
  allowed: boolean;
  forbidden: (errors: readonly unknown[]) => boolean;
} {
  const access = useAdminAccess();
  return {
    access,
    allowed: !access.isLoading && access.systemAdmin,
    forbidden: (errors) =>
      (!access.isLoading && !access.systemAdmin) ||
      errors.some((e) => e instanceof AdminApiError && e.status === 403),
  };
}

/** Label / value pairs of a card. */
export function Facts({ items }: { items: readonly (readonly [string, ReactNode])[] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 text-sm">
      {items.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="font-semibold">{label}</dt>
          <dd className="break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Previous / next buttons of a paged register with "1–50 of 120" (no total: "1–50"). */
export function Pager({
  offset,
  count,
  pageSize,
  total,
  onChange,
}: {
  offset: number;
  count: number;
  pageSize: number;
  total?: number;
  onChange: (offset: number) => void;
}) {
  const { t } = useTranslation();
  const hasNext = total === undefined ? count === pageSize : offset + count < total;
  if (offset === 0 && !hasNext) return null;
  return (
    <div className="flex flex-wrap items-center gap-touch-gap">
      <button
        type="button"
        className={BUTTON}
        disabled={offset === 0}
        onClick={() => { onChange(Math.max(0, offset - pageSize)); }}
      >
        {t("admin.system.previous")}
      </button>
      {total !== undefined && count > 0 && (
        <span>
          {t("admin.system.range", { from: offset + 1, to: offset + count, total })}
        </span>
      )}
      <button
        type="button"
        className={BUTTON}
        disabled={!hasNext}
        onClick={() => { onChange(offset + pageSize); }}
      >
        {t("admin.system.next")}
      </button>
    </div>
  );
}

/** Links between the System screens; System-admin-only ones for System admins (T-0026). */
export function SystemTabs({ systemAdmin }: { systemAdmin: boolean }) {
  const { t } = useTranslation();
  const tabs = SYSTEM_TABS.filter((tab) => systemAdmin || !tab.systemAdminOnly).map((tab) => ({
    to: tab.to,
    label: t(tab.label),
  }));
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
