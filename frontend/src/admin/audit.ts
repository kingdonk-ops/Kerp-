// Audit explorer (T-0222) and per-project legal hold (T-0223) APIs. `system_admin` only
// (else 403; diff §4.7); deleting a project needs its `admin` capability.
import type { components } from "../api/schema";
import type { ApiClient } from "../lib/api/client";
import { AdminApiError } from "./api";
import { actionResult, type ActionResult } from "./system";

export type AuditEntry = components["schemas"]["AuditEntryRead"];
export type AuditPage = components["schemas"]["AuditPageRead"];
export type ChainReport = components["schemas"]["ChainVerifyRead"];
export type Job = components["schemas"]["JobRead"];
export type LegalHold = components["schemas"]["LegalHoldRead"];

/** Rows per page of the audit register. */
export const AUDIT_PAGE_SIZE = 50;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const FLAG = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;

/** The register filters as typed; dates are `YYYY-MM-DD` in the user's time zone. */
export interface AuditFilter {
  actor: string;
  device: string;
  chainPrefix: string;
  event: string;
  flag: string;
  from: string;
  to: string;
}

export const NO_AUDIT_FILTER: AuditFilter = {
  actor: "",
  device: "",
  chainPrefix: "",
  event: "",
  flag: "",
  from: "",
  to: "",
};

/** The fields the server would refuse (422): ids that are not UUIDs, a malformed flag key. */
export function invalidFields(f: AuditFilter): (keyof AuditFilter)[] {
  const bad: (keyof AuditFilter)[] = [];
  if (f.actor.trim() !== "" && !UUID.test(f.actor.trim())) bad.push("actor");
  if (f.device.trim() !== "" && !UUID.test(f.device.trim())) bad.push("device");
  if (f.flag.trim() !== "" && !FLAG.test(f.flag.trim())) bad.push("flag");
  return bad;
}

/** Local midnight of `date` (plus `days`) as an aware ISO time. */
function localMidnight(date: string, days = 0): string {
  const d = new Date(`${date}T00:00:00`);
  d.setDate(d.getDate() + days);
  return d.toISOString();
}

/** The set filters as query parameters; the "to" day is included (`created_to` is exclusive). */
export function auditQuery(f: AuditFilter) {
  const set = (v: string) => (v.trim() === "" ? undefined : v.trim());
  const query = {
    actor_id: set(f.actor),
    device_id: set(f.device),
    chain_key_prefix: set(f.chainPrefix),
    event: set(f.event),
    flag: set(f.flag),
    created_from: f.from === "" ? undefined : localMidnight(f.from),
    created_to: f.to === "" ? undefined : localMidnight(f.to, 1),
  };
  return Object.fromEntries(Object.entries(query).filter(([, v]) => v !== undefined));
}

export async function fetchAudit(
  api: ApiClient,
  filter: AuditFilter,
  cursor: string | null,
): Promise<AuditPage> {
  const { data, response } = await api.GET("/api/v1/admin/system/audit", {
    params: {
      query: { ...auditQuery(filter), limit: AUDIT_PAGE_SIZE, ...(cursor ? { cursor } : {}) },
    },
  });
  if (!data) throw new AdminApiError(response.status);
  return data;
}

/** Verify one chain now; `null` when it does not exist for the caller (404). */
export async function verifyChain(
  api: ApiClient,
  chainKey: string,
  projectId: string | null,
): Promise<ChainReport | null> {
  const { data, response } = await api.POST("/api/v1/admin/system/audit/verify", {
    body: { chain_key: chainKey, project_id: projectId },
  });
  if (response.status === 404) return null;
  if (!data) throw new AdminApiError(response.status);
  return data;
}

/** Queue the job verifying every chain; its id (poll with `fetchJob`). */
export async function startVerifyAll(api: ApiClient): Promise<string> {
  const { data, response } = await api.POST("/api/v1/admin/system/audit/verify-all");
  if (!data) throw new AdminApiError(response.status);
  return data.id;
}

export async function fetchJob(api: ApiClient, jobId: string): Promise<Job> {
  const { data, response } = await api.GET("/api/v1/jobs/{job_id}", {
    params: { path: { job_id: jobId } },
  });
  if (!data) throw new AdminApiError(response.status);
  return data;
}

/** The verify-all job's stored report (T-0222 `run_verify_all`). */
export interface VerifyAllResult {
  ok: boolean;
  chains: number;
  entries: number;
  broken_chains: number;
  first_broken: { project_id: string | null; chain_key: string; seq: number } | null;
}

export async function setLegalHold(
  api: ApiClient,
  projectId: string,
  on: boolean,
  reason: string,
): Promise<LegalHold> {
  const { data, response } = await api.PUT("/api/v1/admin/system/projects/{project_id}/legal-hold", {
    params: { path: { project_id: projectId } },
    body: { on, reason: reason.trim() },
  });
  if (!data) throw new AdminApiError(response.status);
  return data;
}

/** Delete the project: 409 `LEGAL_HOLD_ACTIVE` while on hold, `team_links_present` with teams. */
export async function deleteProject(api: ApiClient, projectId: string): Promise<ActionResult> {
  const { error, response } = await api.DELETE("/api/v1/projects/{project_id}", {
    params: { path: { project_id: projectId } },
  });
  return response.ok ? { kind: "ok" } : actionResult(response.status, error);
}
