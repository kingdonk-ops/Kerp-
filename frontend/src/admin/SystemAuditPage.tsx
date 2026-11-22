import { useMutation, useQuery } from "@tanstack/react-query";
import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useServices } from "../app/services";
import {
  fetchAudit,
  fetchJob,
  invalidFields,
  NO_AUDIT_FILTER,
  startVerifyAll,
  verifyChain,
  type AuditEntry,
  type AuditFilter,
  type Job,
  type VerifyAllResult,
} from "./audit";
import { BUTTON, CARD_ITEM, Facts, formatTime, INPUT, PRIMARY, SystemTabs, useSystemAdminGate } from "./ui";

const KEY = ["admin", "system", "audit"] as const;
/** How often a running verify-all job is polled. */
const JOB_POLL_MS = 1000;
const FIELDS = [
  ["actor", "admin.audit.actor", "text"],
  ["device", "admin.audit.device", "text"],
  ["chainPrefix", "admin.audit.chainPrefix", "text"],
  ["event", "admin.audit.event", "text"],
  ["flag", "admin.audit.flag", "text"],
  ["from", "admin.audit.from", "date"],
  ["to", "admin.audit.to", "date"],
] as const;

/**
 * Admin → System → Audit log (diff §4.7, §6.5; T-0222): the ledger of the tenant and of every
 * project (ADR 0016), newest first, with filters, entry hashes and payload, verify one chain
 * (inline) and verify all (a batch job, polled). Read-only: nothing here changes the ledger.
 */
export function SystemAuditPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const gate = useSystemAdminGate();
  const [filter, setFilter] = useState<AuditFilter>(NO_AUDIT_FILTER);
  // Cursors of the pages before the current one; the last is the current page's cursor.
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors.at(-1) ?? null;
  const entries = useQuery({
    queryKey: [...KEY, "entries", filter, cursor],
    enabled: gate.allowed,
    queryFn: () => fetchAudit(api, filter, cursor),
  });
  const forbidden = gate.forbidden([entries.error]);

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">{t("admin.audit.title")}</h1>
      <SystemTabs systemAdmin={gate.access.systemAdmin} />
      {forbidden ? (
        <p role="alert">{t("admin.system.forbiddenConsole")}</p>
      ) : (
        <>
          <p>{t("admin.audit.intro")}</p>
          {gate.allowed && <VerifyAll />}
          <Filters
            onApply={(f) => {
              setFilter(f);
              setCursors([null]);
            }}
          />
          {(gate.access.isLoading || entries.isPending) && <p>{t("app.loading")}</p>}
          {entries.isError && <p role="alert">{t("admin.audit.loadError")}</p>}
          {entries.data && (
            <Region title={t("admin.audit.entriesTitle")}>
              {entries.data.items.length === 0 ? (
                <p>{t("admin.audit.empty")}</p>
              ) : (
                <ul className="space-y-2">
                  {entries.data.items.map((e) => (
                    <EntryCard key={e.id} entry={e} />
                  ))}
                </ul>
              )}
              <div className="flex flex-wrap gap-touch-gap">
                <button
                  type="button"
                  className={BUTTON}
                  disabled={cursors.length === 1}
                  onClick={() => { setCursors((c) => c.slice(0, -1)); }}
                >
                  {t("admin.system.previous")}
                </button>
                <button
                  type="button"
                  className={BUTTON}
                  disabled={entries.data.next_cursor === null}
                  onClick={() => {
                    const next = entries.data.next_cursor;
                    if (next) setCursors((c) => [...c, next]);
                  }}
                >
                  {t("admin.system.next")}
                </button>
              </div>
            </Region>
          )}
        </>
      )}
    </section>
  );
}

function Region({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="space-y-2 rounded border border-line p-3">
      <h2 id={id} className="text-xl font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Filters({ onApply }: { onApply: (f: AuditFilter) => void }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<AuditFilter>(NO_AUDIT_FILTER);
  const errorId = useId();
  const bad = invalidFields(draft);
  return (
    <Region title={t("admin.audit.filters")}>
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (bad.length === 0) onApply(draft);
        }}
      >
        <div className="grid gap-touch-gap sm:grid-cols-2 lg:grid-cols-3">
          {FIELDS.map(([field, label, type]) => (
            <label key={field} className="block space-y-1">
              <span>{t(label)}</span>
              <input
                type={type}
                className={INPUT}
                value={draft[field]}
                aria-invalid={bad.includes(field) || undefined}
                aria-describedby={bad.includes(field) ? errorId : undefined}
                onChange={(e) => { setDraft({ ...draft, [field]: e.target.value }); }}
              />
            </label>
          ))}
        </div>
        {bad.length > 0 && (
          <p id={errorId} role="alert">
            {t("admin.audit.invalid")}
          </p>
        )}
        <div className="flex flex-wrap gap-touch-gap">
          <button type="submit" className={PRIMARY} disabled={bad.length > 0}>
            {t("admin.audit.apply")}
          </button>
          <button
            type="button"
            className={BUTTON}
            onClick={() => {
              setDraft(NO_AUDIT_FILTER);
              onApply(NO_AUDIT_FILTER);
            }}
          >
            {t("admin.audit.clear")}
          </button>
        </div>
      </form>
    </Region>
  );
}

function EntryCard({ entry }: { entry: AuditEntry }) {
  const { t, i18n } = useTranslation();
  const { api } = useServices();
  const none = t("admin.system.none");
  const event = typeof entry.payload.event === "string" ? entry.payload.event : t("admin.audit.noEvent");
  const verify = useMutation({ mutationFn: () => verifyChain(api, entry.chain_key, entry.project_id) });
  const report = verify.data;
  return (
    <li aria-label={`${entry.chain_key} #${String(entry.seq)}`} className={CARD_ITEM}>
      <h3 className="break-all font-semibold">{event}</h3>
      <Facts
        items={[
          [t("admin.audit.chain"), entry.chain_key],
          [t("admin.audit.seq"), entry.seq],
          [t("admin.quarantine.project"), entry.project_id ?? t("admin.audit.tenantLevel")],
          [t("admin.audit.time"), formatTime(entry.created_at, i18n.language)],
          [t("admin.audit.actor"), entry.actor_id ?? none],
          [t("admin.audit.device"), entry.device_id ?? none],
          [t("admin.audit.source"), entry.source],
        ]}
      />
      <details>
        <summary className="flex min-h-touch cursor-pointer items-center">{t("admin.audit.details")}</summary>
        <Facts
          items={[
            [t("admin.audit.contentHash"), <code key="c" className="break-all">{entry.content_hash}</code>],
            [t("admin.audit.prevHash"), <code key="p" className="break-all">{entry.prev_hash}</code>],
            [t("admin.audit.hashVersion"), entry.hash_version],
          ]}
        />
        <p className="font-semibold">{t("admin.audit.payload")}</p>
        <pre className="whitespace-pre-wrap break-all text-sm">{JSON.stringify(entry.payload, null, 2)}</pre>
      </details>
      <button type="button" className={BUTTON} disabled={verify.isPending} onClick={() => { verify.mutate(); }}>
        {verify.isPending ? t("admin.audit.verifying") : t("admin.audit.verify")}
      </button>
      <div role="status">
        {verify.isError && <p>{t("admin.actionError")}</p>}
        {report === null && <p>{t("admin.audit.chainGone")}</p>}
        {report?.ok === true && (
          <p className="break-all">{t("admin.audit.chainOk", { length: report.length, head: report.head_hash })}</p>
        )}
        {report?.ok === false && (
          <>
            <p className="font-semibold">
              {t("admin.audit.chainBroken", { seq: report.first_broken_seq ?? none, problems: report.problem_count })}
            </p>
            <ul className="list-disc pl-5 text-sm">
              {report.problems.map((p) => (
                <li key={p} className="break-all">{p}</li>
              ))}
            </ul>
          </>
        )}
      </div>
    </li>
  );
}

const running = (job: Job | undefined) => job?.status === "queued" || job?.status === "running";

function VerifyAll() {
  const { t } = useTranslation();
  const { api } = useServices();
  const start = useMutation({ mutationFn: () => startVerifyAll(api) });
  const jobId = start.data;
  const job = useQuery({
    queryKey: [...KEY, "job", jobId],
    enabled: jobId !== undefined,
    queryFn: () => fetchJob(api, jobId ?? ""),
    refetchInterval: (q) => (q.state.data === undefined || running(q.state.data) ? JOB_POLL_MS : false),
  });
  const data = jobId === undefined ? undefined : job.data;
  return (
    <Region title={t("admin.audit.verifyAllTitle")}>
      <p>{t("admin.audit.verifyAllIntro")}</p>
      <button
        type="button"
        className={PRIMARY}
        disabled={start.isPending || running(data)}
        onClick={() => { start.mutate(); }}
      >
        {t("admin.audit.verifyAll")}
      </button>
      <div role="status">
        {(start.isError || job.isError) && <p>{t("admin.actionError")}</p>}
        {data && <JobState job={data} />}
      </div>
    </Region>
  );
}

function JobState({ job }: { job: Job }) {
  const { t } = useTranslation();
  if (job.status === "failed") {
    return <p>{t("admin.audit.jobStatus.failed", { error: job.error ?? t("admin.system.none") })}</p>;
  }
  if (job.status !== "succeeded") {
    return (
      <>
        <p>{t(`admin.audit.jobStatus.${job.status}`, { progress: job.progress })}</p>
        <progress className="w-full" aria-label={t("admin.audit.progress")} max={100} value={job.progress} />
      </>
    );
  }
  const r = job.result as VerifyAllResult | null;
  if (!r) return <p>{t("admin.audit.jobStatus.succeeded")}</p>;
  if (r.ok) return <p>{t("admin.audit.allOk", { chains: r.chains, entries: r.entries })}</p>;
  return (
    <p className="break-all">
      {t("admin.audit.allBroken", {
        broken: r.broken_chains,
        chains: r.chains,
        entries: r.entries,
        chain: r.first_broken?.chain_key ?? t("admin.system.none"),
        seq: r.first_broken?.seq ?? t("admin.system.none"),
      })}
    </p>
  );
}
