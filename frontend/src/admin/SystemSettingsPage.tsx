import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { useServices } from "../app/services";
import { useAdminAccess } from "./access";
import { AdminApiError } from "./api";
import {
  byCategory,
  checkSettingValue,
  draftOf,
  effectiveMaximum,
  fetchDeploymentConfig,
  fetchRegistry,
  updateSetting,
  type DeploymentConfig,
  type SettingDraft,
  type SettingEntry,
  type SettingError,
} from "./settings";
import { INPUT, PRIMARY, SystemTabs } from "./ui";

const REGISTRY_KEY = ["settings", "registry"] as const;
const CONFIG_KEY = ["settings", "deploymentConfig"] as const;

/** Admin → System → Settings (diff §4.7, §6.5): the editable registry and the deployment config. */
export function SystemSettingsPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const access = useAdminAccess();
  const allowed = !access.isLoading && access.systemAdmin;
  const registry = useQuery({
    queryKey: REGISTRY_KEY,
    enabled: allowed,
    queryFn: () => fetchRegistry(api),
  });
  const config = useQuery({
    queryKey: CONFIG_KEY,
    enabled: allowed,
    queryFn: () => fetchDeploymentConfig(api),
  });
  const forbidden =
    (!access.isLoading && !access.systemAdmin) ||
    [registry.error, config.error].some((e) => e instanceof AdminApiError && e.status === 403);

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">{t("admin.settings.title")}</h1>
      <SystemTabs systemAdmin={access.systemAdmin} />
      {forbidden ? (
        <p role="alert">{t("admin.settings.forbidden")}</p>
      ) : (
        <>
          <p>{t("admin.settings.intro")}</p>
          {(access.isLoading || registry.isPending) && <p>{t("app.loading")}</p>}
          {(registry.isError || config.isError) && (
            <p role="alert">{t("admin.settings.loadError")}</p>
          )}
          {registry.data && <Registry entries={registry.data} />}
          {config.data && <DeploymentConfiguration config={config.data} />}
        </>
      )}
    </section>
  );
}

/** A catalogue label by key, or `undefined` when the catalogue has none (later tasks' entries). */
function useLabels() {
  const { t } = useTranslation();
  const entries = t("admin.settings.entries", { returnObjects: true }) as Record<string, string>;
  const categories = t("admin.settings.categories", { returnObjects: true }) as Record<
    string,
    string
  >;
  return { entry: (key: string) => entries[key], category: (key: string) => categories[key] };
}

function Registry({ entries }: { entries: SettingEntry[] }) {
  const { t } = useTranslation();
  const labels = useLabels();
  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">{t("admin.settings.registryTitle")}</h2>
      {byCategory(entries).map(([category, group]) => (
        <CategorySection
          key={category}
          title={labels.category(category) ?? category}
          entries={group}
        />
      ))}
    </div>
  );
}

function CategorySection({ title, entries }: { title: string; entries: SettingEntry[] }) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="space-y-3 rounded border border-line p-3">
      <h3 id={headingId} className="text-lg font-semibold">
        {title}
      </h3>
      {entries.map((entry) => (
        <EntryRow key={entry.key} entry={entry} />
      ))}
    </section>
  );
}

const show = (n: number) => String(n);

/** The bounds of an entry as the hint under its input. */
function useBoundsHint(entry: SettingEntry): string | null {
  const { t } = useTranslation();
  const max = effectiveMaximum(entry);
  const min = entry.minimum;
  switch (entry.type) {
    case "int":
    case "float":
      if (min !== null && max !== null) {
        return t("admin.settings.range", { min: show(min), max: show(max) });
      }
      if (min !== null) return t("admin.settings.atLeast", { min: show(min) });
      if (max !== null) return t("admin.settings.atMost", { max: show(max) });
      return null;
    case "int_list": {
      const parts: string[] = [t("admin.settings.listHint")];
      if (min !== null && max !== null) {
        parts.push(t("admin.settings.itemRange", { min: show(min), max: show(max) }));
      }
      if (entry.max_length !== null) {
        parts.push(t("admin.settings.maxItems", { max: show(entry.max_length) }));
      }
      return parts.join(". ");
    }
    case "string":
      return entry.max_length === null
        ? null
        : t("admin.settings.maxLength", { max: show(entry.max_length) });
    default:
      return null;
  }
}

function useErrorText() {
  const { t } = useTranslation();
  return (error: SettingError): string => {
    switch (error.key) {
      case "min":
        return t("admin.settings.errors.min", { min: show(error.min) });
      case "max":
        return t("admin.settings.errors.max", { max: show(error.max) });
      case "maxLength":
        return t("admin.settings.errors.maxLength", { max: show(error.max) });
      case "maxItems":
        return t("admin.settings.errors.maxItems", { max: show(error.max) });
      default:
        return t(`admin.settings.errors.${error.key}`);
    }
  };
}

/** One entry: its input, bounds and cap, a Save of its own, and its errors inline. */
function EntryRow({ entry }: { entry: SettingEntry }) {
  const { t } = useTranslation();
  const { api } = useServices();
  const queryClient = useQueryClient();
  const labels = useLabels();
  const errorText = useErrorText();
  const bounds = useBoundsHint(entry);
  const [draft, setDraft] = useState<SettingDraft>(() => draftOf(entry));
  const [serverError, setServerError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const id = useId();
  const labelId = `${id}-label`;
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;

  const check = checkSettingValue(entry, draft);
  const dirty = !check.ok || JSON.stringify(check.value) !== JSON.stringify(entry.value);
  const save = useMutation({
    mutationFn: (value: SettingEntry["value"]) => updateSetting(api, entry.key, value),
    onSuccess: (result) => {
      if (result.kind === "invalid") {
        setServerError(
          result.detail === ""
            ? t("admin.settings.errors.serverUnknown")
            : t("admin.settings.errors.server", { detail: result.detail }),
        );
        return;
      }
      queryClient.setQueryData<SettingEntry[]>(REGISTRY_KEY, (old) =>
        old?.map((e) => (e.key === result.entry.key ? result.entry : e)),
      );
      setDraft(draftOf(result.entry));
      setNotice(t("admin.settings.saved"));
    },
    onError: () => {
      setNotice(t("admin.settings.saveError"));
    },
  });

  const change = (next: SettingDraft) => {
    setDraft(next);
    setServerError(null);
    setNotice(null);
  };
  const error = !check.ok ? errorText(check.error) : serverError;
  const label = labels.entry(entry.key);
  const max = effectiveMaximum(entry);
  const inputProps = {
    id,
    "aria-labelledby": labelId,
    "aria-describedby": `${hintId}${error ? ` ${errorId}` : ""}`,
    "aria-invalid": error ? true : undefined,
    disabled: save.isPending,
  };

  return (
    <div role="group" aria-labelledby={labelId} className="space-y-1 border-t border-line pt-3">
      <label id={labelId} htmlFor={id} className="block font-medium">
        {label ?? entry.key}
      </label>
      {entry.type === "bool" ? (
        <input
          {...inputProps}
          type="checkbox"
          className="size-touch"
          checked={draft === true}
          onChange={(e) => {
            change(e.target.checked);
          }}
        />
      ) : entry.type === "enum" ? (
        <select
          {...inputProps}
          className={INPUT}
          value={String(draft)}
          onChange={(e) => {
            change(e.target.value);
          }}
        >
          {entry.choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      ) : (
        <input
          {...inputProps}
          type={entry.type === "int" || entry.type === "float" ? "number" : "text"}
          inputMode={entry.type === "int_list" ? "numeric" : undefined}
          min={entry.minimum ?? undefined}
          max={(entry.type === "int" || entry.type === "float") && max !== null ? max : undefined}
          step={entry.type === "float" ? "any" : entry.type === "int" ? 1 : undefined}
          className={INPUT}
          value={String(draft)}
          onChange={(e) => {
            change(e.target.value);
          }}
        />
      )}
      <div id={hintId} className="text-sm text-muted">
        {/* Entries without a catalogue label (later tasks) show the server's description. */}
        {label === undefined && entry.description !== "" && <p>{entry.description}</p>}
        {bounds && <p>{bounds}</p>}
        {entry.cap !== null && entry.cap_source !== null && (
          <p>{t("admin.settings.capped", { cap: show(entry.cap), source: entry.cap_source })}</p>
        )}
        <p>
          {entry.is_default
            ? t("admin.settings.isDefault")
            : t("admin.settings.defaultValue", { value: formatValue(entry, entry.default, t) })}
          {entry.client_visible && ` · ${t("admin.settings.clientVisible")}`}
        </p>
      </div>
      {error && (
        <p id={errorId} role="alert" className="font-medium">
          {error}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-touch-gap">
        <button
          type="button"
          className={PRIMARY}
          disabled={!dirty || !check.ok || save.isPending}
          onClick={() => {
            if (check.ok) save.mutate(check.value);
          }}
        >
          {save.isPending ? t("admin.settings.saving") : t("admin.settings.save")}
        </button>
        <p role="status">{notice}</p>
      </div>
    </div>
  );
}

type T = ReturnType<typeof useTranslation>["t"];

function formatValue(entry: SettingEntry, value: SettingEntry["value"], t: T): string {
  if (entry.type === "bool") return value === true ? t("admin.settings.on") : t("admin.settings.off");
  return String(draftOf(entry, value));
}

/** Read-only deployment configuration (TD-046, TD-398): shown, never edited at runtime. */
function DeploymentConfiguration({ config }: { config: DeploymentConfig }) {
  const { t, i18n } = useTranslation();
  const headingId = useId();
  const number = (n: number) => new Intl.NumberFormat(i18n.language).format(n);
  const lockout = config.pin_lockout;
  const lockoutArgs = {
    free: number(lockout.free_attempts),
    base: number(lockout.base_delay_seconds),
    max: number(lockout.max_delay_seconds),
  };
  const rows: [string, string][] = [
    [
      t("admin.settings.deployment.refreshToken"),
      t("admin.settings.deployment.hours", { value: number(config.refresh_token_hours) }),
    ],
    [
      t("admin.settings.deployment.pinIdle"),
      t("admin.settings.deployment.minutes", { value: number(config.pin_idle_minutes) }),
    ],
    [
      t("admin.settings.deployment.pinLockout"),
      lockout.max_attempts === null
        ? t("admin.settings.deployment.pinLockoutNoLimit", lockoutArgs)
        : t("admin.settings.deployment.pinLockoutValue", {
            ...lockoutArgs,
            attempts: number(lockout.max_attempts),
          }),
    ],
    [t("admin.settings.deployment.region"), config.region],
    [t("admin.settings.deployment.storageRegion"), config.storage_region],
    [
      t("admin.settings.deployment.mailTransport"),
      t(`admin.settings.deployment.transports.${config.mail_transport}`),
    ],
    [
      t("admin.settings.deployment.mailFrom"),
      config.mail_from_address ?? t("admin.settings.deployment.notSet"),
    ],
    [
      t("admin.settings.deployment.scanner"),
      t(`admin.settings.deployment.scanners.${config.upload_scanner}`),
    ],
    [
      t("admin.settings.deployment.uploadMax"),
      t("admin.settings.deployment.bytes", { value: number(config.upload_max_bytes) }),
    ],
    [
      t("admin.settings.deployment.uploadUrl"),
      t("admin.settings.deployment.seconds", { value: number(config.upload_url_seconds) }),
    ],
    [t("admin.settings.deployment.aiProvider"), t("admin.settings.deployment.aiNone")],
  ];
  return (
    <section aria-labelledby={headingId} className="space-y-2 rounded border border-line p-3">
      <h2 id={headingId} className="text-xl font-semibold">
        {t("admin.settings.deployment.title")}
      </h2>
      <p>{t("admin.settings.deployment.readOnly")}</p>
      <dl className="grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-[max-content_1fr]">
        {rows.map(([term, value]) => (
          <div key={term} className="contents">
            <dt className="font-medium">{term}</dt>
            <dd className="break-words">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
