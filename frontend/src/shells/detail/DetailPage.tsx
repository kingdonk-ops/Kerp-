import { useQuery, type QueryKey } from "@tanstack/react-query";
import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";

import { GateBanner, type GateStatus } from "./GateBanner";

export interface DetailTab {
  id: string;
  /** Already translated by the caller. */
  label: string;
  render: () => ReactNode;
}

export interface AuditEntry {
  id: string;
  /** ISO 8601 timestamp. */
  at: string;
  actor: string;
  /** Action code as recorded by the server (e.g. `status_changed`). */
  action: string;
  summary?: string;
  /** `ai` marks AI-initiated mutations (CLAUDE.md §6). */
  source: "user" | "ai" | "system";
}

/** Audit data seam: no audit endpoint exists yet (TD-181); pages pass a loader. */
export interface AuditSource {
  key: QueryKey;
  load: () => Promise<readonly AuditEntry[]>;
}

export interface DetailPageProps {
  title: string;
  subtitle?: string;
  gate?: GateStatus | null;
  tabs: readonly DetailTab[];
  /** Adds an "Audit log" tab, loaded when first opened. */
  audit?: AuditSource;
  actions?: ReactNode;
}

export const TAB_PARAM = "tab";
export const AUDIT_TAB_ID = "audit";

/** Shared record screen: gate-status banner, tabs routed via `?tab=`, audit log. */
export function DetailPage({ title, subtitle, gate, tabs, audit, actions }: DetailPageProps) {
  const { t } = useTranslation();
  const baseId = useId();
  const [params, setParams] = useSearchParams();
  const tabRefs = useRef(new Map<string, HTMLButtonElement>());

  const reservedClash = tabs.some((tab) => tab.id === AUDIT_TAB_ID);
  useEffect(() => {
    if (reservedClash && import.meta.env.DEV) {
      console.error(
        `DetailPage: tab id "${AUDIT_TAB_ID}" is reserved for the shell's audit log; rename the module tab.`,
      );
    }
  }, [reservedClash]);

  const allTabs: readonly DetailTab[] =
    audit === undefined
      ? tabs
      : [
          // The shell owns the reserved id: a clashing module tab is dropped (no duplicate keys/ids).
          ...tabs.filter((tab) => tab.id !== AUDIT_TAB_ID),
          { id: AUDIT_TAB_ID, label: t("shells.detail.audit.tab"), render: () => <AuditLog source={audit} /> },
        ];
  const requested = params.get(TAB_PARAM);
  const active = allTabs.find((tab) => tab.id === requested) ?? allTabs[0];
  const panelId = `${baseId}-panel`;

  const select = (id: string) => {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.set(TAB_PARAM, id);
        return next;
      },
      { replace: true },
    );
  };

  /** WAI-ARIA APG tabs: Left/Right wrap, Home/End jump; selection follows focus. */
  const targetIndex = (key: string, index: number): number | null => {
    const count = allTabs.length;
    switch (key) {
      case "ArrowRight":
        return (index + 1) % count;
      case "ArrowLeft":
        return (index - 1 + count) % count;
      case "Home":
        return 0;
      case "End":
        return count - 1;
      default:
        return null;
    }
  };

  const onKeyDown = (event: KeyboardEvent, index: number) => {
    const next = targetIndex(event.key, index);
    if (next === null) return;
    event.preventDefault();
    const target = allTabs[next];
    if (target === undefined) return;
    select(target.id);
    tabRefs.current.get(target.id)?.focus();
  };

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{title}</h1>
          {subtitle !== undefined && <p className="text-muted">{subtitle}</p>}
        </div>
        {actions !== undefined && <div className="flex flex-wrap gap-touch-gap">{actions}</div>}
      </header>

      {gate != null && <GateBanner gate={gate} />}

      <div role="tablist" aria-label={t("shells.detail.tabs")} className="flex gap-touch-gap overflow-x-auto border-b border-line">
        {allTabs.map((tab, index) => {
          const selected = tab.id === active?.id;
          return (
            <button
              key={tab.id}
              ref={(node) => {
                if (node) tabRefs.current.set(tab.id, node);
                else tabRefs.current.delete(tab.id);
              }}
              type="button"
              role="tab"
              id={`${baseId}-tab-${tab.id}`}
              aria-selected={selected}
              // Only the selected tab's panel is rendered, so only it may point at one.
              aria-controls={selected ? panelId : undefined}
              tabIndex={selected ? 0 : -1}
              className={`min-h-touch min-w-touch shrink-0 px-3 ${selected ? "border-b-2 border-brand font-semibold" : "text-muted"}`}
              onClick={() => { select(tab.id); }}
              onKeyDown={(event) => { onKeyDown(event, index); }}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {active !== undefined && (
        <div role="tabpanel" id={panelId} aria-labelledby={`${baseId}-tab-${active.id}`} tabIndex={0}>
          {active.render()}
        </div>
      )}
    </section>
  );
}

function AuditLog({ source }: { source: AuditSource }) {
  const { t, i18n } = useTranslation();
  const query = useQuery({ queryKey: source.key, queryFn: source.load });
  const format = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" });

  if (query.isPending) return <p className="text-muted">{t("app.loading")}</p>;
  if (query.isError) {
    return (
      <div className="space-y-2">
        <p className="text-danger">{t("shells.detail.audit.loadError")}</p>
        <button type="button" className="min-h-touch min-w-touch rounded border border-line px-3" onClick={() => void query.refetch()}>
          {t("app.retry")}
        </button>
      </div>
    );
  }
  if (query.data.length === 0) return <p className="text-muted">{t("shells.detail.audit.empty")}</p>;

  return (
    <ol aria-label={t("shells.detail.audit.tab")} className="space-y-2">
      {query.data.map((entry) => (
        <li key={entry.id} className="rounded border border-line bg-panel px-3 py-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <time dateTime={entry.at} className="text-muted">
              {formatTimestamp(format, entry.at)}
            </time>
            <span className="font-medium">{entry.actor}</span>
            <code>{entry.action}</code>
            {entry.source === "ai" && (
              <span className="rounded bg-pillar px-1 text-xs">{t("shells.detail.audit.source.ai")}</span>
            )}
          </div>
          {entry.summary !== undefined && <p>{entry.summary}</p>}
        </li>
      ))}
    </ol>
  );
}

/** Server timestamps are data: an unparsable one is shown as-is instead of throwing. */
function formatTimestamp(format: Intl.DateTimeFormat, at: string): string {
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? at : format.format(date);
}
