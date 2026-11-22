import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useServices } from "../app/services";
import {
  abortJob,
  fetchFailedJobs,
  fetchQueues,
  PAGE_SIZE,
  retryJob,
  type ActionResult,
  type FailedJob,
  type QueueOverview,
} from "./system";
import {
  BUTTON,
  CARD_ITEM,
  Dialog,
  Facts,
  formatTime,
  INPUT,
  Pager,
  PRIMARY,
  SystemTabs,
  useSystemAdminGate,
} from "./ui";

const QUEUES_KEY = ["admin", "system", "queues"] as const;
const FAILED_KEY = ["admin", "system", "queues", "failed"] as const;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type Pending = { kind: "retry"; job: FailedJob } | { kind: "abort"; id: string };

/** Admin → System → Queues (diff §4.7, §6.5): counts, cron runs, failed jobs (T-0218). */
export function SystemQueuesPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const queryClient = useQueryClient();
  const gate = useSystemAdminGate();
  const [offset, setOffset] = useState(0);
  const [pending, setPending] = useState<Pending | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const queues = useQuery({ queryKey: QUEUES_KEY, enabled: gate.allowed, queryFn: () => fetchQueues(api) });
  const failed = useQuery({
    queryKey: [...FAILED_KEY, offset],
    enabled: gate.allowed,
    queryFn: () => fetchFailedJobs(api, offset),
  });
  const action = useMutation({
    mutationFn: (p: Pending) => (p.kind === "retry" ? retryJob(api, p.job.id) : abortJob(api, p.id)),
    onSuccess: (result: ActionResult, p) => {
      setNotice(t(noticeKey(p.kind, result)));
    },
    onError: () => { setNotice(t("admin.actionError")); },
    onSettled: () => {
      setPending(null);
      void queryClient.invalidateQueries({ queryKey: QUEUES_KEY });
    },
  });
  const forbidden = gate.forbidden([queues.error, failed.error]);

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">{t("admin.queues.title")}</h1>
      <SystemTabs systemAdmin={gate.access.systemAdmin} />
      {forbidden ? (
        <p role="alert">{t("admin.system.forbiddenConsole")}</p>
      ) : (
        <>
          {(gate.access.isLoading || queues.isPending) && <p>{t("app.loading")}</p>}
          {(queues.isError || failed.isError) && <p role="alert">{t("admin.queues.loadError")}</p>}
          {notice && <p role="status">{notice}</p>}
          {pending && (
            <Dialog
              title={
                pending.kind === "retry"
                  ? t("admin.queues.retryTitle", { kind: pending.job.kind })
                  : t("admin.queues.abortConfirmTitle", { id: pending.id })
              }
              onClose={() => { setPending(null); }}
              actions={
                <button
                  type="button"
                  className={PRIMARY}
                  disabled={action.isPending}
                  onClick={() => { action.mutate(pending); }}
                >
                  {pending.kind === "retry" ? t("admin.queues.retry") : t("admin.queues.abort")}
                </button>
              }
            >
              <p>{pending.kind === "retry" ? t("admin.queues.retryBody") : t("admin.queues.abortBody")}</p>
            </Dialog>
          )}
          {queues.data && <Overview overview={queues.data} />}
          {failed.data && (
            <FailedJobs
              jobs={failed.data}
              offset={offset}
              onPage={setOffset}
              onRetry={(job) => {
                setNotice(null);
                setPending({ kind: "retry", job });
              }}
            />
          )}
          {queues.data && (
            <AbortForm
              onAbort={(id) => {
                setNotice(null);
                setPending({ kind: "abort", id });
              }}
            />
          )}
        </>
      )}
    </section>
  );
}

function noticeKey(kind: Pending["kind"], result: ActionResult) {
  if (result.kind === "ok") return kind === "retry" ? "admin.queues.retried" : "admin.queues.aborted";
  if (result.kind === "gone") return "admin.queues.unknownJob";
  return kind === "retry" ? "admin.queues.notFailed" : "admin.queues.notAbortable";
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

function Overview({ overview }: { overview: QueueOverview }) {
  const { t, i18n } = useTranslation();
  const time = (iso: string | null) => formatTime(iso, i18n.language) ?? t("admin.system.none");
  const seconds = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 });
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        {overview.queues.map((q) => (
          <Region key={q.name} title={t(`admin.queues.queueNames.${q.name}`)}>
            <p>{t("admin.queues.queued", { count: q.queued })}</p>
            <p>{t("admin.queues.activeCount", { count: q.active })}</p>
            <p>{t("admin.queues.scheduled", { count: q.scheduled })}</p>
            <p>{t("admin.queues.failed", { count: q.failed })}</p>
            {q.oldest_queued_age_s !== null && (
              <p>
                {t("admin.queues.oldest", {
                  age: t("admin.queues.seconds", { value: seconds.format(q.oldest_queued_age_s) }),
                })}
              </p>
            )}
          </Region>
        ))}
      </div>
      <Region title={t("admin.queues.cronTitle")}>
        {overview.cron_jobs.length === 0 ? (
          <p>{t("admin.queues.cronEmpty")}</p>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2">
            {overview.cron_jobs.map((c) => (
              <li key={c.name} aria-label={c.name} className={CARD_ITEM}>
                <h3 className="break-all font-semibold">{c.name}</h3>
                <Facts
                  items={[
                    [t("admin.queues.schedule"), c.schedule],
                    [t("admin.queues.lastRun"), time(c.last_run_at)],
                    [t("admin.queues.lastFinished"), time(c.last_finished_at)],
                    [
                      t("admin.queues.outcome"),
                      c.last_outcome ? t(`admin.queues.outcomes.${c.last_outcome}`) : t("admin.system.none"),
                    ],
                    [t("admin.queues.nextRun"), time(c.next_run_at)],
                  ]}
                />
              </li>
            ))}
          </ul>
        )}
      </Region>
    </>
  );
}

function FailedJobs({
  jobs,
  offset,
  onPage,
  onRetry,
}: {
  jobs: FailedJob[];
  offset: number;
  onPage: (offset: number) => void;
  onRetry: (job: FailedJob) => void;
}) {
  const { t, i18n } = useTranslation();
  return (
    <Region title={t("admin.queues.failedTitle")}>
      {jobs.length === 0 && offset === 0 ? (
        <p>{t("admin.queues.failedEmpty")}</p>
      ) : (
        <ul className="space-y-2">
          {jobs.map((job) => (
            <li key={job.id} aria-label={job.kind} className={CARD_ITEM}>
              <h3 className="break-all font-semibold">{job.kind}</h3>
              <Facts
                items={[
                  [t("admin.queues.queue"), t(`admin.queues.queueNames.${job.queue}`)],
                  [t("admin.queues.project"), job.project_code ?? t("admin.queues.tenantLevel")],
                  [t("admin.queues.attempts"), job.attempts],
                  [t("admin.queues.error"), job.error_class],
                  [t("admin.queues.failedAt"), formatTime(job.failed_at, i18n.language) ?? t("admin.system.none")],
                ]}
              />
              <p className="whitespace-pre-wrap break-words">{job.message}</p>
              <button type="button" className={BUTTON} onClick={() => { onRetry(job); }}>
                {t("admin.queues.retry")}
              </button>
            </li>
          ))}
        </ul>
      )}
      <Pager offset={offset} count={jobs.length} pageSize={PAGE_SIZE} onChange={onPage} />
    </Region>
  );
}

/** Abort by id: the API lists no queued or running jobs, so the admin pastes the id. */
function AbortForm({ onAbort }: { onAbort: (id: string) => void }) {
  const { t } = useTranslation();
  const inputId = useId();
  const errorId = useId();
  const [value, setValue] = useState("");
  const [invalid, setInvalid] = useState(false);
  return (
    <Region title={t("admin.queues.abortTitle")}>
      <p>{t("admin.queues.abortHint")}</p>
      <form
        className="flex flex-wrap items-end gap-touch-gap"
        onSubmit={(e) => {
          e.preventDefault();
          const id = value.trim();
          if (!UUID.test(id)) {
            setInvalid(true);
            return;
          }
          setInvalid(false);
          setValue("");
          onAbort(id.toLowerCase());
        }}
      >
        <div className="min-w-72 flex-1">
          <label htmlFor={inputId}>{t("admin.queues.jobId")}</label>
          <input
            id={inputId}
            className={INPUT}
            value={value}
            aria-invalid={invalid}
            aria-describedby={invalid ? errorId : undefined}
            onChange={(e) => { setValue(e.target.value); }}
          />
        </div>
        <button type="submit" className={BUTTON}>
          {t("admin.queues.abort")}
        </button>
      </form>
      {invalid && (
        <p id={errorId} role="alert">
          {t("admin.queues.jobIdInvalid")}
        </p>
      )}
    </Region>
  );
}
