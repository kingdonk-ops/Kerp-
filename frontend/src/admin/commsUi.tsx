// Shared parts of the Communications screens (T-0229).
import { useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { AdminApiError } from "./api";
import { useAdminAccess, type AdminAccess } from "./access";
import type { MailCounts } from "./comms";
import { formatTime } from "./ui";

/** A titled region: a section named by its heading. */
export function Region({ title, level = 2, children }: { title: string; level?: 2 | 3; children: ReactNode }) {
  const id = useId();
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <section aria-labelledby={id} className="space-y-2 rounded border border-line p-3">
      <Heading id={id} className={level === 2 ? "text-xl font-semibold" : "font-semibold"}>
        {title}
      </Heading>
      {children}
    </section>
  );
}

/** A delivery rate (0–1) as a percentage in the UI language; "—" when nothing was sent. */
export function formatRate(rate: number | null, lang: string, none: string): string {
  if (rate === null) return none;
  return new Intl.NumberFormat(lang, { style: "percent", maximumFractionDigits: 1 }).format(rate);
}

const CARD_KEYS = ["sent", "deliveryRate", "delivered", "bounced", "complained", "failed", "suppressed", "queued"] as const;

/** Delivery count cards: sent, delivery rate, delivered, bounces, complaints, failures, ... */
export function CountCards({ counts }: { counts: MailCounts }) {
  const { t, i18n } = useTranslation();
  const value = (key: (typeof CARD_KEYS)[number]) =>
    key === "deliveryRate"
      ? formatRate(counts.delivery_rate, i18n.language, t("admin.comms.counts.noRate"))
      : new Intl.NumberFormat(i18n.language).format(counts[key]);
  return (
    <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {CARD_KEYS.map((key) => (
        <li key={key} className="rounded border border-line bg-panel p-3">
          <p className="text-sm">{t(`admin.comms.counts.${key}`)}</p>
          <p className="text-2xl font-semibold">{value(key)}</p>
        </li>
      ))}
    </ul>
  );
}

/** "from <time>" of a `Retry-After`, or `null` when the server named none. */
export function retryTime(seconds: number | null, lang: string): string | null {
  return seconds === null ? null : formatTime(new Date(Date.now() + seconds * 1000).toISOString(), lang);
}

/**
 * The gate of a Communications screen (diff §6.5): `systemAdminOnly` screens for System admins,
 * the broadcasts for PER-09 holders too. `forbidden(errors)` once the client knows the caller
 * may not use it or the API said 403.
 */
export function useCommsGate(systemAdminOnly: boolean): {
  access: AdminAccess;
  allowed: boolean;
  forbidden: (errors: readonly unknown[]) => boolean;
} {
  const access = useAdminAccess();
  const may = systemAdminOnly ? access.systemAdmin : access.projectAdmin;
  return {
    access,
    allowed: !access.isLoading && may,
    forbidden: (errors) =>
      (!access.isLoading && !may) || errors.some((e) => e instanceof AdminApiError && e.status === 403),
  };
}
