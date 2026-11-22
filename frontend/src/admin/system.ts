// System console APIs: deployment stats + health (T-0219), queue monitor (T-0218), upload
// quarantine monitor (T-0221). Every endpoint answers `system_admin` only (else 403; diff §4.7).
import type { components } from "../api/schema";
import type { ApiClient } from "../lib/api/client";
import { AdminApiError } from "./api";

export type SystemStats = components["schemas"]["SystemStatsRead"];
export type SystemHealth = components["schemas"]["SystemHealthRead"];
export type HealthProbe = components["schemas"]["HealthProbeRead"];
export type QueueOverview = components["schemas"]["QueueOverviewRead"];
export type FailedJob = components["schemas"]["FailedJobRead"];
export type QuarantinePage = components["schemas"]["QuarantinePage"];
export type QuarantineFile = components["schemas"]["QuarantineFileRead"];
export type QuarantineCount = components["schemas"]["QuarantineCountRead"];
export type QuarantineStatus = QuarantineFile["status"];

export const QUARANTINE_STATUSES: readonly QuarantineStatus[] = ["verifying", "rejected", "expired"];
/** Rows per page of the failed-jobs and quarantine registers. */
export const PAGE_SIZE = 50;

/**
 * The outcome of an action: `conflict` is a 409 (state changed, or a run already in progress;
 * `code` from a dict `detail`), `gone` a 404 (unknown or not visible).
 */
export type ActionResult = { kind: "ok" } | { kind: "conflict"; code: string } | { kind: "gone" };

function actionResult(status: number, error: unknown): ActionResult {
  if (status === 404) return { kind: "gone" };
  if (status === 409) {
    const detail = (error as { detail?: unknown } | undefined)?.detail;
    const code =
      typeof detail === "object" && detail !== null && "code" in detail ? String(detail.code) : "";
    return { kind: "conflict", code };
  }
  throw new AdminApiError(status);
}

// --- deployment stats and health (T-0219) -----------------------------------------------------

export async function fetchStats(api: ApiClient): Promise<SystemStats> {
  const { data, response } = await api.GET("/api/v1/admin/system/stats");
  if (!data) throw new AdminApiError(response.status);
  return data;
}

export async function fetchHealth(api: ApiClient): Promise<SystemHealth> {
  const { data, response } = await api.GET("/api/v1/admin/system/health");
  if (!data) throw new AdminApiError(response.status);
  return data;
}

/** Queue the object-store totals run; 409 while one is queued or running. */
export async function refreshObjectStore(api: ApiClient): Promise<ActionResult> {
  const { response } = await api.POST("/api/v1/admin/system/stats/object-store/refresh");
  return response.ok ? { kind: "ok" } : actionResult(response.status, undefined);
}

// --- queue monitor (T-0218) -------------------------------------------------------------------

export async function fetchQueues(api: ApiClient): Promise<QueueOverview> {
  const { data, response } = await api.GET("/api/v1/admin/system/queues");
  if (!data) throw new AdminApiError(response.status);
  return data;
}

export async function fetchFailedJobs(api: ApiClient, offset: number): Promise<FailedJob[]> {
  const { data, response } = await api.GET("/api/v1/admin/system/queues/failed", {
    params: { query: { limit: PAGE_SIZE, offset } },
  });
  if (!data) throw new AdminApiError(response.status);
  return data;
}

/** Queue a `failed` job again; 409 unless it is still failed. */
export async function retryJob(api: ApiClient, jobId: string): Promise<ActionResult> {
  const { error, response } = await api.POST("/api/v1/admin/system/queues/failed/{job_id}/retry", {
    params: { path: { job_id: jobId } },
  });
  return response.ok ? { kind: "ok" } : actionResult(response.status, error);
}

/** Abort a `queued` or `running` job; 409 otherwise. */
export async function abortJob(api: ApiClient, jobId: string): Promise<ActionResult> {
  const { error, response } = await api.POST("/api/v1/admin/system/queues/{job_id}/abort", {
    params: { path: { job_id: jobId } },
  });
  return response.ok ? { kind: "ok" } : actionResult(response.status, error);
}

// --- upload quarantine (T-0221) ---------------------------------------------------------------

export interface QuarantineFilter {
  status: QuarantineStatus | "";
  reason: string;
  project: string;
}

/** The set filters only (an empty one is no filter). */
function filterQuery(filter: QuarantineFilter) {
  return {
    ...(filter.status === "" ? {} : { status: filter.status }),
    ...(filter.reason === "" ? {} : { reason: filter.reason }),
    ...(filter.project === "" ? {} : { project: filter.project }),
  };
}

export async function fetchQuarantine(
  api: ApiClient,
  filter: QuarantineFilter,
  offset: number,
): Promise<QuarantinePage> {
  const { data, response } = await api.GET("/api/v1/admin/system/uploads", {
    params: { query: { ...filterQuery(filter), limit: PAGE_SIZE, offset } },
  });
  if (!data) throw new AdminApiError(response.status);
  return data;
}

/** Counts per project, status and reason of the whole quarantine (unfiltered overview). */
export async function fetchQuarantineCounts(api: ApiClient): Promise<QuarantineCount[]> {
  const { data, response } = await api.GET("/api/v1/admin/system/uploads/counts");
  if (!data) throw new AdminApiError(response.status);
  return data;
}

/** Re-check a file stuck in `verifying`; 409 `NOT_VERIFYING` / `CHECK_RUNNING` / `OBJECT_GONE`. */
export async function recheckUpload(api: ApiClient, fileId: string): Promise<ActionResult> {
  const { error, response } = await api.POST("/api/v1/admin/system/uploads/{file_id}/recheck", {
    params: { path: { file_id: fileId } },
  });
  return response.ok ? { kind: "ok" } : actionResult(response.status, error);
}

/** Queue the project's orphan sweep now. */
export async function sweepProject(api: ApiClient, projectId: string): Promise<ActionResult> {
  const { error, response } = await api.POST("/api/v1/admin/system/uploads/sweep", {
    params: { query: { project: projectId } },
  });
  return response.ok ? { kind: "ok" } : actionResult(response.status, error);
}

export interface ProjectCounts {
  projectId: string;
  projectCode: string | null;
  byStatus: Record<QuarantineStatus, number>;
  total: number;
}

/** Counts per project and status (reasons summed), by project code. */
export function countsByProject(counts: readonly QuarantineCount[]): ProjectCounts[] {
  const rows = new Map<string, ProjectCounts>();
  for (const c of counts) {
    const row = rows.get(c.project_id) ?? {
      projectId: c.project_id,
      projectCode: c.project_code,
      byStatus: { verifying: 0, rejected: 0, expired: 0 },
      total: 0,
    };
    row.byStatus[c.status] += c.count;
    row.total += c.count;
    rows.set(c.project_id, row);
  }
  return [...rows.values()].sort((a, b) =>
    (a.projectCode ?? a.projectId).localeCompare(b.projectCode ?? b.projectId),
  );
}

/** The distinct reason codes of the counts, sorted. */
export function reasonCodes(counts: readonly QuarantineCount[]): string[] {
  return [...new Set(counts.flatMap((c) => (c.reason ? [c.reason] : [])))].sort();
}

/** A byte count in the UI language, in the largest unit that keeps it at least 1. */
export function formatBytes(bytes: number, lang: string): string {
  const units = ["byte", "kilobyte", "megabyte", "gigabyte", "terabyte", "petabyte"] as const;
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return new Intl.NumberFormat(lang, {
    style: "unit",
    unit: units[i],
    unitDisplay: "short",
    maximumFractionDigits: i === 0 ? 0 : 1,
  }).format(value);
}
