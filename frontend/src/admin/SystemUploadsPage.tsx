import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useServices } from "../app/services";
import {
  countsByProject,
  fetchQuarantine,
  fetchQuarantineCounts,
  formatBytes,
  PAGE_SIZE,
  QUARANTINE_STATUSES,
  reasonCodes,
  recheckUpload,
  sweepProject,
  type ActionResult,
  type ProjectCounts,
  type QuarantineFile,
  type QuarantineFilter,
  type QuarantineStatus,
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

const KEY = ["admin", "system", "uploads"] as const;
const NO_FILTER: QuarantineFilter = { status: "", reason: "", project: "" };

type Pending = { kind: "recheck"; file: QuarantineFile } | { kind: "sweep"; project: ProjectCounts };

/** A reason code's label; a code without one (e.g. later media-probe codes) shows the code. */
function useReasonLabel() {
  const { t } = useTranslation();
  const labels = t("admin.quarantine.reasons", { returnObjects: true }) as Record<string, string>;
  return (reason: string | null) => (reason === null ? t("admin.quarantine.noReason") : (labels[reason] ?? reason));
}

/**
 * Admin → System → Upload quarantine (diff §4.7, §6.5; T-0221): uploads still being checked,
 * rejected or expired, of every project (ADR 0016), with counts and re-check / sweep now.
 */
export function SystemUploadsPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const queryClient = useQueryClient();
  const gate = useSystemAdminGate();
  const [filter, setFilter] = useState<QuarantineFilter>(NO_FILTER);
  const [offset, setOffset] = useState(0);
  const [pending, setPending] = useState<Pending | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const files = useQuery({
    queryKey: [...KEY, "files", filter, offset],
    enabled: gate.allowed,
    queryFn: () => fetchQuarantine(api, filter, offset),
  });
  const counts = useQuery({
    queryKey: [...KEY, "counts"],
    enabled: gate.allowed,
    queryFn: () => fetchQuarantineCounts(api),
  });
  const action = useMutation({
    mutationFn: (p: Pending) =>
      p.kind === "recheck" ? recheckUpload(api, p.file.id) : sweepProject(api, p.project.projectId),
    onSuccess: (result: ActionResult, p) => { setNotice(noticeText(result, p)); },
    onError: () => { setNotice(t("admin.actionError")); },
    onSettled: () => {
      setPending(null);
      void queryClient.invalidateQueries({ queryKey: KEY });
    },
  });
  const forbidden = gate.forbidden([files.error, counts.error]);

  function noticeText(result: ActionResult, p: Pending): string {
    if (result.kind === "ok") return t(p.kind === "recheck" ? "admin.quarantine.rechecked" : "admin.quarantine.swept");
    if (result.kind === "gone") return t("admin.quarantine.gone");
    const conflicts = t("admin.quarantine.conflicts", { returnObjects: true }) as Record<string, string>;
    return conflicts[result.code] ?? t("admin.actionError");
  }

  const change = (patch: Partial<QuarantineFilter>) => {
    setFilter((f) => ({ ...f, ...patch }));
    setOffset(0);
  };
  const ask = (p: Pending) => {
    setNotice(null);
    setPending(p);
  };

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">{t("admin.quarantine.title")}</h1>
      <SystemTabs systemAdmin={gate.access.systemAdmin} />
      {forbidden ? (
        <p role="alert">{t("admin.system.forbiddenConsole")}</p>
      ) : (
        <>
          <p>{t("admin.quarantine.intro")}</p>
          {(gate.access.isLoading || files.isPending) && <p>{t("app.loading")}</p>}
          {(files.isError || counts.isError) && <p role="alert">{t("admin.quarantine.loadError")}</p>}
          {notice && <p role="status">{notice}</p>}
          {pending && (
            <Dialog
              title={
                pending.kind === "recheck"
                  ? t("admin.quarantine.recheckTitle", { file: pending.file.filename })
                  : t("admin.quarantine.sweepTitle", {
                      project: pending.project.projectCode ?? pending.project.projectId,
                    })
              }
              onClose={() => { setPending(null); }}
              actions={
                <button
                  type="button"
                  className={PRIMARY}
                  disabled={action.isPending}
                  onClick={() => { action.mutate(pending); }}
                >
                  {pending.kind === "recheck" ? t("admin.quarantine.recheck") : t("admin.quarantine.sweep")}
                </button>
              }
            >
              <p>
                {pending.kind === "recheck" ? t("admin.quarantine.recheckBody") : t("admin.quarantine.sweepBody")}
              </p>
            </Dialog>
          )}
          {counts.data && (
            <>
              <Filters filter={filter} counts={counts.data} onChange={change} />
              <CountsList rows={countsByProject(counts.data)} onSweep={(project) => { ask({ kind: "sweep", project }); }} />
            </>
          )}
          {files.data && (
            <Region title={t("admin.quarantine.filesTitle")}>
              {files.data.items.length === 0 ? (
                <p>{t("admin.quarantine.empty")}</p>
              ) : (
                <FilesList files={files.data.items} onRecheck={(file) => { ask({ kind: "recheck", file }); }} />
              )}
              <Pager
                offset={offset}
                count={files.data.items.length}
                pageSize={PAGE_SIZE}
                total={files.data.total}
                onChange={setOffset}
              />
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

function Filters({
  filter,
  counts,
  onChange,
}: {
  filter: QuarantineFilter;
  counts: Parameters<typeof countsByProject>[0];
  onChange: (patch: Partial<QuarantineFilter>) => void;
}) {
  const { t } = useTranslation();
  const reasonLabel = useReasonLabel();
  const ids = { status: useId(), reason: useId(), project: useId() };
  const reasons = reasonCodes(counts);
  if (filter.reason !== "" && !reasons.includes(filter.reason)) reasons.push(filter.reason);
  const projects = countsByProject(counts);
  return (
    <Region title={t("admin.quarantine.filters")}>
      <div className="grid gap-touch-gap sm:grid-cols-3">
        <div>
          <label htmlFor={ids.status}>{t("admin.quarantine.status")}</label>
          <select
            id={ids.status}
            className={INPUT}
            value={filter.status}
            onChange={(e) => { onChange({ status: e.target.value as QuarantineStatus | "" }); }}
          >
            <option value="">{t("admin.quarantine.all")}</option>
            {QUARANTINE_STATUSES.map((s) => (
              <option key={s} value={s}>
                {t(`admin.quarantine.statuses.${s}`)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={ids.reason}>{t("admin.quarantine.reason")}</label>
          <select
            id={ids.reason}
            className={INPUT}
            value={filter.reason}
            onChange={(e) => { onChange({ reason: e.target.value }); }}
          >
            <option value="">{t("admin.quarantine.all")}</option>
            {reasons.map((r) => (
              <option key={r} value={r}>
                {reasonLabel(r)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={ids.project}>{t("admin.quarantine.project")}</label>
          <select
            id={ids.project}
            className={INPUT}
            value={filter.project}
            onChange={(e) => { onChange({ project: e.target.value }); }}
          >
            <option value="">{t("admin.quarantine.all")}</option>
            {projects.map((p) => (
              <option key={p.projectId} value={p.projectId}>
                {p.projectCode ?? p.projectId}
              </option>
            ))}
          </select>
        </div>
      </div>
    </Region>
  );
}

function CountsList({ rows, onSweep }: { rows: ProjectCounts[]; onSweep: (p: ProjectCounts) => void }) {
  const { t } = useTranslation();
  return (
    <Region title={t("admin.quarantine.countsTitle")}>
      {rows.length === 0 ? (
        <p>{t("admin.quarantine.empty")}</p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {rows.map((row) => {
            const code = row.projectCode ?? row.projectId;
            return (
              <li key={row.projectId} aria-label={code} className={CARD_ITEM}>
                <h3 className="break-all font-semibold">{code}</h3>
                <Facts
                  items={[
                    ...QUARANTINE_STATUSES.map((st) => [t(`admin.quarantine.statuses.${st}`), row.byStatus[st]] as const),
                    [t("admin.quarantine.total"), row.total],
                  ]}
                />
                <button type="button" className={BUTTON} onClick={() => { onSweep(row); }}>
                  {t("admin.quarantine.sweep")}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Region>
  );
}

function FilesList({ files, onRecheck }: { files: QuarantineFile[]; onRecheck: (f: QuarantineFile) => void }) {
  const { t, i18n } = useTranslation();
  const reasonLabel = useReasonLabel();
  return (
    <ul className="space-y-2">
      {files.map((f) => (
        <li key={f.id} aria-label={f.filename} className={CARD_ITEM}>
          <h3 className="break-all font-semibold">{f.filename}</h3>
          {f.object_deleted && <p className="font-semibold">{t("admin.quarantine.objectDeleted")}</p>}
          <Facts
            items={[
              [t("admin.quarantine.project"), f.project_code ?? f.project_id],
              [t("admin.quarantine.status"), t(`admin.quarantine.statuses.${f.status}`)],
              [t("admin.quarantine.reason"), reasonLabel(f.reason)],
              [t("admin.quarantine.module"), f.module],
              [t("admin.quarantine.size"), `${formatBytes(f.size_bytes, i18n.language)} (${f.content_type})`],
              [t("admin.quarantine.changed"), formatTime(f.updated_at, i18n.language)],
            ]}
          />
          {f.scan_signature && (
            <p className="break-all">{t("admin.quarantine.signature", { signature: f.scan_signature })}</p>
          )}
          {f.status === "verifying" && !f.object_deleted && (
            <button type="button" className={BUTTON} onClick={() => { onRecheck(f); }}>
              {t("admin.quarantine.recheck")}
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}
