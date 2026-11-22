import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useServices } from "../app/services";
import {
  addressInvalid,
  COMMS_PAGE_SIZE,
  fetchMailStats,
  fetchSends,
  fetchTemplates,
  NO_SEND_FILTER,
  periodInvalid,
  SEND_STATUSES,
  type MailSend,
  type MailStats,
  type MailSendStatus,
  type Period,
  type SendFilter,
} from "./comms";
import { CountCards, formatRate, Region, useCommsGate } from "./commsUi";
import { CARD_ITEM, CommsTabs, Facts, formatTime, INPUT, Pager, PRIMARY } from "./ui";

const KEY = ["admin", "comms", "delivery"] as const;

/**
 * Admin → Communications → Delivery (diff §4.7, §6.5; T-0226, T-0229): the delivery ledger's
 * count cards for a period (and template) and the searchable send history. Recipients are
 * masked only; the exact-address lookup is hashed by the server and never shown back.
 * System admins only.
 */
export function CommsDeliveryPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const gate = useCommsGate(true);
  const [stats, setStats] = useState<{ period: Period; template: string }>({
    period: { from: "", to: "" },
    template: "",
  });
  const [filter, setFilter] = useState<SendFilter>(NO_SEND_FILTER);
  const [offset, setOffset] = useState(0);
  const templates = useQuery({
    queryKey: ["admin", "comms", "templates"],
    enabled: gate.allowed,
    queryFn: () => fetchTemplates(api),
  });
  const counts = useQuery({
    queryKey: [...KEY, "stats", stats],
    enabled: gate.allowed,
    queryFn: () => fetchMailStats(api, stats.period, stats.template),
  });
  const sends = useQuery({
    queryKey: [...KEY, "sends", filter, offset],
    enabled: gate.allowed,
    queryFn: () => fetchSends(api, filter, offset),
  });
  const forbidden = gate.forbidden([counts.error, sends.error, templates.error]);
  const keys = (templates.data ?? []).map((tpl) => tpl.key).sort();

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">{t("admin.comms.delivery.title")}</h1>
      <CommsTabs systemAdmin={gate.access.systemAdmin} />
      {forbidden ? (
        <p role="alert">{t("admin.comms.forbiddenSystem")}</p>
      ) : (
        <>
          <p>{t("admin.comms.delivery.intro")}</p>
          {(gate.access.isLoading || counts.isPending) && <p>{t("app.loading")}</p>}
          <Region title={t("admin.comms.delivery.statsTitle")}>
            <StatsFilter keys={keys} onApply={setStats} />
            {counts.isError && <p role="alert">{t("admin.comms.loadError")}</p>}
            {counts.data && (
              <>
                <CountCards counts={counts.data.totals} />
                <ByTemplate rows={counts.data.by_template} />
              </>
            )}
          </Region>
          <Region title={t("admin.comms.delivery.historyTitle")}>
            <HistoryFilter
              keys={keys}
              onApply={(f) => {
                setFilter(f);
                setOffset(0);
              }}
            />
            {sends.isError && <p role="alert">{t("admin.comms.loadError")}</p>}
            {sends.data &&
              (sends.data.length === 0 ? (
                <p>{t("admin.comms.delivery.empty")}</p>
              ) : (
                <ul className="space-y-2">
                  {sends.data.map((s) => (
                    <SendCard key={s.id} send={s} />
                  ))}
                </ul>
              ))}
            {sends.data && (
              <Pager offset={offset} count={sends.data.length} pageSize={COMMS_PAGE_SIZE} onChange={setOffset} />
            )}
          </Region>
        </>
      )}
    </section>
  );
}

function TemplateSelect({ keys, value, onChange }: { keys: string[]; value: string; onChange: (v: string) => void }) {
  const { t } = useTranslation();
  return (
    <label className="block space-y-1">
      <span>{t("admin.comms.delivery.template")}</span>
      <select className={INPUT} value={value} onChange={(e) => { onChange(e.target.value); }}>
        <option value="">{t("admin.comms.delivery.anyTemplate")}</option>
        {keys.map((k) => (
          <option key={k} value={k}>
            {k}
          </option>
        ))}
      </select>
    </label>
  );
}

function PeriodFields({ period, onChange }: { period: Period; onChange: (p: Period) => void }) {
  const { t } = useTranslation();
  const bad = periodInvalid(period);
  return (
    <>
      {(["from", "to"] as const).map((field) => (
        <label key={field} className="block space-y-1">
          <span>{t(`admin.comms.delivery.${field}`)}</span>
          <input
            type="date"
            className={INPUT}
            value={period[field]}
            aria-invalid={bad}
            onChange={(e) => { onChange({ ...period, [field]: e.target.value }); }}
          />
        </label>
      ))}
      {bad && <p className="text-sm text-danger sm:col-span-full">{t("admin.comms.delivery.periodInvalid")}</p>}
    </>
  );
}

function StatsFilter({
  keys,
  onApply,
}: {
  keys: string[];
  onApply: (s: { period: Period; template: string }) => void;
}) {
  const { t } = useTranslation();
  const [period, setPeriod] = useState<Period>({ from: "", to: "" });
  const [template, setTemplate] = useState("");
  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (!periodInvalid(period)) onApply({ period, template });
      }}
    >
      <div className="grid gap-touch-gap sm:grid-cols-3">
        <PeriodFields period={period} onChange={setPeriod} />
        <TemplateSelect keys={keys} value={template} onChange={setTemplate} />
      </div>
      <button type="submit" className={PRIMARY} disabled={periodInvalid(period)}>
        {t("admin.comms.delivery.apply")}
      </button>
    </form>
  );
}

function ByTemplate({ rows }: { rows: MailStats["by_template"] }) {
  const { t, i18n } = useTranslation();
  if (rows.length === 0) return <p>{t("admin.comms.delivery.noSends")}</p>;
  return (
    <Region level={3} title={t("admin.comms.delivery.byTemplateTitle")}>
      <ul className="space-y-2">
        {[...rows]
          .sort((a, b) => a.template_key.localeCompare(b.template_key))
          .map((row) => (
            <li key={row.template_key} className={CARD_ITEM}>
              <h4 className="font-semibold break-words">{row.template_key}</h4>
              <Facts
                items={[
                  [t("admin.comms.counts.total"), row.total],
                  [t("admin.comms.counts.sent"), row.sent],
                  [
                    t("admin.comms.counts.deliveryRate"),
                    formatRate(row.delivery_rate, i18n.language, t("admin.comms.counts.noRate")),
                  ],
                  [t("admin.comms.counts.bounced"), row.bounced],
                  [t("admin.comms.counts.complained"), row.complained],
                  [t("admin.comms.counts.failed"), row.failed],
                ]}
              />
            </li>
          ))}
      </ul>
    </Region>
  );
}

function HistoryFilter({ keys, onApply }: { keys: string[]; onApply: (f: SendFilter) => void }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<SendFilter>(NO_SEND_FILTER);
  const badAddress = addressInvalid(draft.address);
  const bad = badAddress || periodInvalid(draft);
  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (!bad) onApply(draft);
      }}
    >
      <div className="grid gap-touch-gap sm:grid-cols-3">
        <TemplateSelect keys={keys} value={draft.template} onChange={(template) => { setDraft({ ...draft, template }); }} />
        <label className="block space-y-1">
          <span>{t("admin.comms.delivery.status")}</span>
          <select
            className={INPUT}
            value={draft.status}
            onChange={(e) => { setDraft({ ...draft, status: e.target.value as MailSendStatus | "" }); }}
          >
            <option value="">{t("admin.comms.delivery.anyStatus")}</option>
            {SEND_STATUSES.map((s) => (
              <option key={s} value={s}>
                {t(`admin.comms.delivery.statuses.${s}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="block space-y-1">
          <span>{t("admin.comms.delivery.address")}</span>
          <input
            type="email"
            autoComplete="off"
            className={INPUT}
            value={draft.address}
            aria-invalid={badAddress}
            onChange={(e) => { setDraft({ ...draft, address: e.target.value }); }}
          />
          {badAddress && <span className="text-sm text-danger">{t("admin.comms.delivery.addressInvalid")}</span>}
        </label>
        <PeriodFields period={draft} onChange={(p) => { setDraft({ ...draft, ...p }); }} />
      </div>
      <button type="submit" className={PRIMARY} disabled={bad}>
        {t("admin.comms.delivery.search")}
      </button>
    </form>
  );
}

function SendCard({ send: s }: { send: MailSend }) {
  const { t, i18n } = useTranslation();
  const none = t("admin.system.none");
  return (
    <li className={CARD_ITEM}>
      <h3 className="font-semibold break-words">{s.template_key}</h3>
      <Facts
        items={[
          [t("admin.comms.delivery.recipient"), s.recipient_masked],
          [t("admin.comms.delivery.status"), t(`admin.comms.delivery.statuses.${s.status}`)],
          [t("admin.comms.delivery.attempts"), s.attempts],
          [t("admin.comms.delivery.error"), s.error_code ?? none],
          [t("admin.comms.delivery.created"), formatTime(s.created_at, i18n.language) ?? none],
          [t("admin.comms.delivery.statusAt"), formatTime(s.status_at, i18n.language) ?? none],
          ...(s.broadcast_id ? [[t("admin.comms.delivery.broadcast"), s.broadcast_id] as const] : []),
        ]}
      />
    </li>
  );
}
