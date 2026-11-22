import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useServices } from "../app/services";
import { useProjectScope } from "../projects/ProjectScopeProvider";
import { fetchProjects } from "./api";
import {
  BROADCAST_CATEGORIES,
  COMMS_PAGE_SIZE,
  fetchBroadcast,
  fetchBroadcasts,
  MAX_BODY,
  MAX_SUBJECT,
  previewBroadcast,
  sendBroadcast,
  type Broadcast,
  type BroadcastAudience,
  type BroadcastCategory,
  type SendBroadcastResult,
} from "./comms";
import { CountCards, Region, retryTime, useCommsGate } from "./commsUi";
import { PERSONAS, type Persona } from "./personas";
import { PROJECTS_KEY } from "./ProjectsPage";
import { BUTTON, CARD_ITEM, CommsTabs, Dialog, Facts, formatTime, INPUT, Pager, PRIMARY } from "./ui";

const KEY = ["admin", "comms", "broadcasts"] as const;

interface ProjectOption {
  id: string;
  code: string;
  name: string;
}

/**
 * Admin → Communications → Broadcasts (diff §4.7, §6.5; T-0228, T-0229): the composer
 * (audience, recipient count, plain-text subject and body, confirm and send) and the sent
 * broadcasts with their delivery counts. A System admin may target the whole tenant or any
 * project; a PER-09 only its own projects (TD-399). The server decides every call.
 */
export function CommsBroadcastsPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const gate = useCommsGate(false);
  const { projects: myProjects } = useProjectScope();
  const systemAdmin = gate.access.systemAdmin;
  const allProjects = useQuery({
    queryKey: PROJECTS_KEY,
    enabled: gate.allowed && systemAdmin,
    queryFn: () => fetchProjects(api),
  });
  const options: ProjectOption[] = useMemo(() => {
    if (systemAdmin) return allProjects.data ?? [];
    return myProjects
      .filter((p) => p.personas.includes("PER-09"))
      .sort((a, b) => a.code.localeCompare(b.code));
  }, [systemAdmin, allProjects.data, myProjects]);
  const [offset, setOffset] = useState(0);
  const list = useQuery({
    queryKey: [...KEY, "list", offset],
    enabled: gate.allowed,
    queryFn: () => fetchBroadcasts(api, offset),
  });
  const forbidden = gate.forbidden([list.error]);

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">{t("admin.comms.broadcasts.title")}</h1>
      <CommsTabs systemAdmin={systemAdmin} />
      {forbidden ? (
        <p role="alert">{t("admin.comms.forbidden")}</p>
      ) : (
        <>
          {gate.access.isLoading && <p>{t("app.loading")}</p>}
          {gate.allowed && <Composer systemAdmin={systemAdmin} projects={options} />}
          {list.isError && <p role="alert">{t("admin.comms.loadError")}</p>}
          {list.data && (
            <Region title={t("admin.comms.broadcasts.historyTitle")}>
              {list.data.length === 0 ? (
                <p>{t("admin.comms.broadcasts.empty")}</p>
              ) : (
                <ul className="space-y-2">
                  {list.data.map((b) => (
                    <BroadcastCard key={b.id} broadcast={b} projects={options} />
                  ))}
                </ul>
              )}
              <Pager offset={offset} count={list.data.length} pageSize={COMMS_PAGE_SIZE} onChange={setOffset} />
            </Region>
          )}
        </>
      )}
    </section>
  );
}

function Composer({ systemAdmin, projects }: { systemAdmin: boolean; projects: ProjectOption[] }) {
  const { t, i18n } = useTranslation();
  const { api } = useServices();
  const queryClient = useQueryClient();
  const [scope, setScope] = useState<BroadcastAudience["scope"]>(systemAdmin ? "tenant" : "projects");
  const [projectIds, setProjectIds] = useState<ReadonlySet<string>>(new Set());
  const [category, setCategory] = useState<BroadcastCategory | "">("");
  const [personas, setPersonas] = useState<ReadonlySet<Persona>>(new Set());
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [confirming, setConfirming] = useState(false);
  const bodyCountId = useId();

  const audience: BroadcastAudience = useMemo(
    () => ({
      scope,
      project_ids: scope === "projects" ? projects.filter((p) => projectIds.has(p.id)).map((p) => p.id) : [],
      category: category === "" ? null : category,
      personas: PERSONAS.filter((p) => personas.has(p)),
    }),
    [scope, projects, projectIds, category, personas],
  );
  const audienceKey = JSON.stringify(audience);
  const audienceReady = scope === "tenant" || audience.project_ids?.length !== 0;

  const count = useMutation({ mutationFn: (a: BroadcastAudience) => previewBroadcast(api, a) });
  const countedKey = count.variables === undefined ? null : JSON.stringify(count.variables);
  const counted = count.data !== undefined && countedKey === audienceKey ? count.data : null;

  const send = useMutation({
    mutationFn: () => sendBroadcast(api, { ...audience, subject: subject.trim(), body }),
    onSuccess: async (result) => {
      setConfirming(false);
      if (result.kind === "ok") {
        setSubject("");
        setBody("");
        count.reset();
        await queryClient.invalidateQueries({ queryKey: KEY });
      }
    },
  });

  const subjectOk = subject.trim() !== "" && subject.length <= MAX_SUBJECT && !/[\r\n]/.test(subject);
  const bodyOk = body.trim() !== "" && body.length <= MAX_BODY;
  const canSend = counted !== null && counted > 0 && subjectOk && bodyOk && !send.isPending;

  const toggle = <T,>(set: ReadonlySet<T>, value: T): ReadonlySet<T> => {
    const next = new Set(set);
    if (!next.delete(value)) next.add(value);
    return next;
  };

  return (
    <Region title={t("admin.comms.broadcasts.composeTitle")}>
      <p>{t("admin.comms.broadcasts.composeNote")}</p>
      {confirming && counted !== null && (
        <Dialog
          title={t("admin.comms.broadcasts.confirmTitle")}
          onClose={() => { setConfirming(false); }}
          actions={
            <button type="button" className={PRIMARY} disabled={send.isPending} onClick={() => { send.mutate(); }}>
              {t("admin.comms.broadcasts.confirm", { count: counted })}
            </button>
          }
        >
          <p>{t("admin.comms.broadcasts.confirmBody", { subject: subject.trim(), count: counted })}</p>
        </Dialog>
      )}
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSend) setConfirming(true);
        }}
      >
        {systemAdmin && (
          <fieldset className="space-y-1">
            <legend className="font-semibold">{t("admin.comms.broadcasts.scope")}</legend>
            {(["tenant", "projects"] as const).map((value) => (
              <label key={value} className="flex min-h-touch items-center gap-touch-gap">
                <input
                  type="radio"
                  name="broadcast-scope"
                  className="size-touch shrink-0"
                  checked={scope === value}
                  onChange={() => { setScope(value); }}
                />
                {t(value === "tenant" ? "admin.comms.broadcasts.scopeTenant" : "admin.comms.broadcasts.scopeProjects")}
              </label>
            ))}
          </fieldset>
        )}
        {scope === "projects" && (
          <fieldset className="space-y-1">
            <legend className="font-semibold">{t("admin.comms.broadcasts.projects")}</legend>
            {projects.length === 0 && <p>{t("admin.comms.broadcasts.noProjects")}</p>}
            {projects.map((p) => (
              <label key={p.id} className="flex min-h-touch items-center gap-touch-gap">
                <input
                  type="checkbox"
                  className="size-touch shrink-0"
                  checked={projectIds.has(p.id)}
                  onChange={() => { setProjectIds((s) => toggle(s, p.id)); }}
                />
                {t("admin.comms.broadcasts.project", { code: p.code, name: p.name })}
              </label>
            ))}
          </fieldset>
        )}
        <label className="block space-y-1">
          <span className="font-semibold">{t("admin.comms.broadcasts.category")}</span>
          <select
            className={INPUT}
            value={category}
            onChange={(e) => { setCategory(e.target.value as BroadcastCategory | ""); }}
          >
            <option value="">{t("admin.comms.broadcasts.categoryAny")}</option>
            {BROADCAST_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {t(`admin.comms.broadcasts.categories.${c}`)}
              </option>
            ))}
          </select>
        </label>
        <details className="space-y-1">
          <summary className="flex min-h-touch cursor-pointer items-center font-semibold">
            {t("admin.comms.broadcasts.personas")}
          </summary>
          <fieldset className="space-y-1">
            <legend className="sr-only">{t("admin.comms.broadcasts.personas")}</legend>
            {PERSONAS.map((p) => (
              <label key={p} className="flex min-h-touch items-center gap-touch-gap">
                <input
                  type="checkbox"
                  className="size-touch shrink-0"
                  checked={personas.has(p)}
                  onChange={() => { setPersonas((s) => toggle(s, p)); }}
                />
                {t("admin.accessRequests.persona", { code: p, name: t(`personas.${p}`) })}
              </label>
            ))}
          </fieldset>
        </details>
        <div className="flex flex-wrap items-center gap-touch-gap">
          <button
            type="button"
            className={BUTTON}
            disabled={!audienceReady || count.isPending}
            onClick={() => { count.mutate(audience); }}
          >
            {t("admin.comms.broadcasts.count")}
          </button>
          {counted !== null && (
            <p role="status">{t("admin.comms.broadcasts.recipients", { count: counted })}</p>
          )}
          {count.isError && <p role="alert">{t("admin.actionError")}</p>}
        </div>
        <label className="block space-y-1">
          <span className="font-semibold">{t("admin.comms.broadcasts.subject")}</span>
          <input
            className={INPUT}
            value={subject}
            maxLength={MAX_SUBJECT}
            onChange={(e) => { setSubject(e.target.value.replace(/[\r\n]/g, " ")); }}
          />
        </label>
        <label className="block space-y-1">
          <span className="font-semibold">{t("admin.comms.broadcasts.body")}</span>
          <textarea
            className={`${INPUT} min-h-40 py-2`}
            value={body}
            maxLength={MAX_BODY}
            aria-describedby={bodyCountId}
            onChange={(e) => { setBody(e.target.value); }}
          />
        </label>
        <p id={bodyCountId} className="text-sm">
          {t("admin.comms.broadcasts.length", { count: body.length, max: MAX_BODY })}
        </p>
        {counted === null && <p className="text-sm">{t("admin.comms.broadcasts.countFirst")}</p>}
        <button type="submit" className={PRIMARY} disabled={!canSend}>
          {t("admin.comms.broadcasts.send")}
        </button>
        {send.data && <SendOutcome result={send.data} lang={i18n.language} />}
        {send.isError && <p role="alert">{t("admin.actionError")}</p>}
      </form>
    </Region>
  );
}

function SendOutcome({ result, lang }: { result: SendBroadcastResult; lang: string }) {
  const { t } = useTranslation();
  switch (result.kind) {
    case "ok":
      return <p role="status">{t("admin.comms.broadcasts.queued", { count: result.broadcast.recipient_count })}</p>;
    case "forbidden":
      return (
        <p role="alert">
          {t(result.impersonating ? "admin.comms.broadcasts.impersonating" : "admin.comms.broadcasts.notAllowed")}
        </p>
      );
    case "outOfScope":
      return <p role="alert">{t("admin.comms.broadcasts.outOfScope")}</p>;
    case "noRecipients":
      return <p role="alert">{t("admin.comms.broadcasts.noRecipients")}</p>;
    case "invalid":
      return <p role="alert">{t("admin.comms.broadcasts.sendRefused")}</p>;
    case "rateLimited": {
      const time = retryTime(result.retryAfter, lang);
      return (
        <p role="alert">
          {time
            ? t("admin.comms.broadcasts.rateLimited", { time })
            : t("admin.comms.broadcasts.rateLimitedLater")}
        </p>
      );
    }
  }
}

function BroadcastCard({ broadcast: b, projects }: { broadcast: Broadcast; projects: ProjectOption[] }) {
  const { t, i18n } = useTranslation();
  const { api } = useServices();
  const [open, setOpen] = useState(false);
  const detail = useQuery({
    queryKey: [...KEY, "detail", b.id],
    enabled: open,
    queryFn: () => fetchBroadcast(api, b.id),
  });
  const codes = b.project_ids.map((id) => projects.find((p) => p.id === id)?.code ?? id).join(", ");
  const none = t("admin.system.none");
  return (
    <li className={CARD_ITEM}>
      <h3 className="font-semibold break-words">{b.subject}</h3>
      <Facts
        items={[
          [t("admin.comms.broadcasts.status"), t(`admin.comms.broadcasts.statuses.${b.status}`)],
          [
            t("admin.comms.broadcasts.audience"),
            b.scope === "tenant"
              ? t("admin.comms.broadcasts.audienceTenant")
              : t("admin.comms.broadcasts.audienceProjects", { projects: codes }),
          ],
          [t("admin.comms.broadcasts.recipientCount"), b.recipient_count],
          [t("admin.comms.broadcasts.queuedCount"), b.queued_count],
          [t("admin.comms.broadcasts.skippedCount"), b.skipped_count],
          [t("admin.comms.broadcasts.created"), formatTime(b.created_at, i18n.language) ?? none],
          [
            t("admin.comms.broadcasts.finished"),
            formatTime(b.finished_at, i18n.language) ?? t("admin.comms.broadcasts.notFinished"),
          ],
        ]}
      />
      <button type="button" className={BUTTON} aria-expanded={open} onClick={() => { setOpen((o) => !o); }}>
        {t(open ? "admin.comms.broadcasts.hideDelivery" : "admin.comms.broadcasts.showDelivery")}
      </button>
      {open && detail.isPending && <p>{t("app.loading")}</p>}
      {open && detail.isError && <p role="alert">{t("admin.comms.loadError")}</p>}
      {open && detail.data?.delivery && (
        <Region level={3} title={t("admin.comms.broadcasts.deliveryTitle", { subject: b.subject })}>
          <CountCards counts={detail.data.delivery} />
        </Region>
      )}
    </li>
  );
}
