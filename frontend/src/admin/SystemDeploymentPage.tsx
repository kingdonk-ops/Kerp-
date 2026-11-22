import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useServices } from "../app/services";
import {
  fetchHealth,
  fetchStats,
  formatBytes,
  refreshObjectStore,
  type SystemHealth,
  type SystemStats,
} from "./system";
import { BUTTON, formatTime, SystemTabs, useSystemAdminGate } from "./ui";

const STATS_KEY = ["admin", "system", "stats"] as const;
const HEALTH_KEY = ["admin", "system", "health"] as const;
const CARD = "space-y-1 rounded border border-line p-3";

/** Admin → System → Deployment (diff §4.7, §6.5): count cards and health tiles (T-0219). */
export function SystemDeploymentPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const gate = useSystemAdminGate();
  const stats = useQuery({ queryKey: STATS_KEY, enabled: gate.allowed, queryFn: () => fetchStats(api) });
  const health = useQuery({ queryKey: HEALTH_KEY, enabled: gate.allowed, queryFn: () => fetchHealth(api) });
  const forbidden = gate.forbidden([stats.error, health.error]);

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">{t("admin.deployment.title")}</h1>
      <SystemTabs systemAdmin={gate.access.systemAdmin} />
      {forbidden ? (
        <p role="alert">{t("admin.system.forbiddenConsole")}</p>
      ) : (
        <>
          {(gate.access.isLoading || stats.isPending) && <p>{t("app.loading")}</p>}
          {(stats.isError || health.isError) && <p role="alert">{t("admin.deployment.loadError")}</p>}
          {stats.data && <Stats stats={stats.data} />}
          {health.data && <Health health={health.data} onCheck={() => void health.refetch()} />}
        </>
      )}
    </section>
  );
}

/** A titled card or panel: a region named by its heading. */
function Panel({ title, level = 3, children }: { title: string; level?: 2 | 3; children: ReactNode }) {
  const id = useId();
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <section aria-labelledby={id} className={CARD}>
      <Heading id={id} className={level === 2 ? "text-xl font-semibold" : "font-semibold"}>
        {title}
      </Heading>
      {children}
    </section>
  );
}

/** A label from a catalogue object, or `undefined` when the catalogue has none. */
function useCatalogue(key: "admin.deployment.modules" | "admin.deployment.moduleCounts") {
  const { t } = useTranslation();
  return t(key, { returnObjects: true }) as Record<string, string | Record<string, string> | undefined>;
}

function Stats({ stats }: { stats: SystemStats }) {
  const { t } = useTranslation();
  const modules = useCatalogue("admin.deployment.modules");
  const moduleCounts = useCatalogue("admin.deployment.moduleCounts");
  const label = (module: string, key: string) => {
    const group = moduleCounts[module];
    return (typeof group === "object" ? group[key] : undefined) ?? key;
  };
  return (
    <div className="space-y-3">
      <h2 className="text-xl font-semibold">{t("admin.deployment.statsTitle")}</h2>
      <div className="grid gap-3 sm:grid-cols-3">
        <Panel title={t("admin.deployment.projects")}>
          <p>{t("admin.deployment.active", { count: stats.projects.active })}</p>
          <p>{t("admin.deployment.softDeleted", { count: stats.projects.soft_deleted })}</p>
        </Panel>
        <Panel title={t("admin.deployment.users")}>
          <p>{t("admin.deployment.active", { count: stats.users.active })}</p>
          <p>{t("admin.deployment.deactivated", { count: stats.users.deactivated })}</p>
        </Panel>
        <Panel title={t("admin.deployment.devices")}>
          <p>{t("admin.deployment.active", { count: stats.devices.active })}</p>
          <p>{t("admin.deployment.revoked", { count: stats.devices.revoked })}</p>
        </Panel>
      </div>
      <ObjectStore store={stats.object_store} />
      <Panel title={t("admin.deployment.modulesTitle")}>
        <div className="grid gap-3 sm:grid-cols-3">
          {Object.entries(stats.modules)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([module, counts]) => (
              <div key={module}>
                <h4 className="font-semibold">{(modules[module] as string | undefined) ?? module}</h4>
                <dl className="grid grid-cols-[1fr_auto] gap-x-3">
                  {Object.entries(counts).map(([key, value]) => (
                    <div key={key} className="contents">
                      <dt>{label(module, key)}</dt>
                      <dd className="text-right">{value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            ))}
        </div>
      </Panel>
    </div>
  );
}

function ObjectStore({ store }: { store: SystemStats["object_store"] }) {
  const { t, i18n } = useTranslation();
  const { api } = useServices();
  const queryClient = useQueryClient();
  const refresh = useMutation({
    mutationFn: () => refreshObjectStore(api),
    onSettled: () => queryClient.invalidateQueries({ queryKey: STATS_KEY }),
  });
  const asOf = formatTime(store.as_of, i18n.language);
  return (
    <Panel title={t("admin.deployment.objectStore")}>
      <p>{asOf ? t("admin.deployment.asOf", { time: asOf }) : t("admin.deployment.never")}</p>
      {store.refreshing && <p>{t("admin.deployment.refreshing")}</p>}
      <ul className="space-y-1">
        {store.storage_classes.map((c) => (
          <li key={c.storage_class} className="flex flex-wrap justify-between gap-x-3">
            <span>{t(`admin.deployment.storageClasses.${c.storage_class}`)}</span>
            <span>
              {t("admin.deployment.objects", {
                count: c.objects,
                size: formatBytes(c.bytes, i18n.language),
              })}
            </span>
          </li>
        ))}
      </ul>
      <button
        type="button"
        className={BUTTON}
        disabled={store.refreshing || refresh.isPending}
        onClick={() => { refresh.mutate(); }}
      >
        {t("admin.deployment.refresh")}
      </button>
      {refresh.data && (
        <p role="status">
          {refresh.data.kind === "ok"
            ? t("admin.deployment.refreshQueued")
            : t("admin.deployment.refreshRunning")}
        </p>
      )}
      {refresh.isError && <p role="alert">{t("admin.actionError")}</p>}
    </Panel>
  );
}

const TONE: Record<SystemHealth["status"], string> = {
  ok: "border-line",
  degraded: "border-brand border-2",
  down: "border-ink border-4",
};

function Health({ health, onCheck }: { health: SystemHealth; onCheck: () => void }) {
  const { t, i18n } = useTranslation();
  const latency = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 });
  return (
    <Panel level={2} title={t("admin.deployment.healthTitle")}>
      <p>
        {t("admin.deployment.overall", { status: t(`admin.system.status.${health.status}`) })}
      </p>
      <p className="text-sm">
        {t("admin.deployment.checkedAt", { time: formatTime(health.checked_at, i18n.language) ?? "" })}
      </p>
      <div className="grid gap-3 sm:grid-cols-4">
        {health.probes.map((probe) => (
          <ProbeTile key={probe.name} title={t(`admin.deployment.probes.${probe.name}`)} tone={TONE[probe.status]}>
            <p className="font-semibold">{t(`admin.system.status.${probe.status}`)}</p>
            <p>
              {probe.latency_ms === null
                ? t("admin.deployment.noLatency")
                : t("admin.deployment.latency", { value: latency.format(probe.latency_ms) })}
            </p>
          </ProbeTile>
        ))}
      </div>
      <button type="button" className={BUTTON} onClick={onCheck}>
        {t("admin.deployment.checkAgain")}
      </button>
    </Panel>
  );
}

function ProbeTile({ title, tone, children }: { title: string; tone: string; children: ReactNode }) {
  const id = useId();
  return (
    <div role="group" aria-labelledby={id} className={`rounded border p-3 ${tone}`}>
      <h3 id={id} className="font-semibold">
        {title}
      </h3>
      {children}
    </div>
  );
}
