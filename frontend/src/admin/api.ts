import type { components } from "../api/schema";
import type { ApiClient } from "../lib/api/client";

export type PendingRequest = components["schemas"]["PendingAccessRequest"];
export type GrantIn = components["schemas"]["GrantIn"];
export type Device = components["schemas"]["DeviceRead"];
/** A device in the admin list, with its user (diff §4.3). */
export type AdminDevice = Device & { user?: { email: string; display_name: string } };

/** A failed read; `status` decides the message (403: administers nothing). */
export class AdminApiError extends Error {
  constructor(readonly status: number) {
    super(`admin API: ${String(status)}`);
    this.name = "AdminApiError";
  }
}

/** The `detail` object of a 409 (`HTTPException` with a dict detail), if any. */
function conflictDetail(error: unknown): Record<string, unknown> {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  return typeof detail === "object" && detail !== null ? (detail as Record<string, unknown>) : {};
}

// --- access requests (T-0050, diff §4.1) ----------------------------------------------------

/** Pending requests of the projects the caller administers (all, for a System admin). */
export async function fetchPending(api: ApiClient): Promise<PendingRequest[]> {
  const { data, response } = await api.GET("/api/v1/access-requests/pending");
  if (!data) throw new AdminApiError(response.status);
  return [...data].sort((a, b) => a.created_at.localeCompare(b.created_at));
}

/** `gone`: 409/404, already decided or withdrawn; `sod`: PER-09 + hold authority to confirm. */
export type DecisionResult = "ok" | "gone" | "sod";

export async function approveRequest(
  api: ApiClient,
  id: string,
  grants: GrantIn[],
  confirmSod: boolean,
): Promise<DecisionResult> {
  const { error, response } = await api.POST("/api/v1/access-requests/{request_id}/approve", {
    params: { path: { request_id: id } },
    body: { grants, confirm_sod: confirmSod },
  });
  if (response.ok) return "ok";
  if (response.status === 409 && conflictDetail(error).sod_warning === true) return "sod";
  if (response.status === 409 || response.status === 404) return "gone";
  throw new AdminApiError(response.status);
}

export async function rejectRequest(
  api: ApiClient,
  id: string,
  reason: string,
): Promise<DecisionResult> {
  const { response } = await api.POST("/api/v1/access-requests/{request_id}/reject", {
    params: { path: { request_id: id } },
    body: { reason: reason.trim() === "" ? null : reason.trim() },
  });
  if (response.ok) return "ok";
  if (response.status === 409 || response.status === 404) return "gone";
  throw new AdminApiError(response.status);
}

// --- devices (T-0043, diff §4.3) ------------------------------------------------------------

export async function fetchMyDevices(api: ApiClient): Promise<AdminDevice[]> {
  const { data, response } = await api.GET("/api/v1/devices");
  if (!data) throw new AdminApiError(response.status);
  return data;
}

interface Person {
  id: string;
  email: string;
  display_name: string;
}

/** Users in the caller's device scope: every user (System admin) or the PER-09 projects' members. */
async function usersInScope(
  api: ApiClient,
  systemAdmin: boolean,
  adminProjectIds: readonly string[],
): Promise<Person[]> {
  if (systemAdmin) {
    const { data, response } = await api.GET("/api/v1/admin/users");
    if (!data) throw new AdminApiError(response.status);
    return data;
  }
  const people = new Map<string, Person>();
  for (const projectId of adminProjectIds) {
    const { data, response } = await api.GET("/api/v1/admin/projects/{project_id}/members", {
      params: { path: { project_id: projectId } },
    });
    if (!data) throw new AdminApiError(response.status);
    for (const m of data) people.set(m.user_id, { id: m.user_id, ...m });
  }
  return [...people.values()];
}

/**
 * Devices the caller may manage, per user (`GET /users/{id}/devices`); one call per user
 * until the backend has a scoped list (TD-392).
 */
export async function fetchAdminDevices(
  api: ApiClient,
  systemAdmin: boolean,
  adminProjectIds: readonly string[],
): Promise<AdminDevice[]> {
  const people = await usersInScope(api, systemAdmin, adminProjectIds);
  const lists = await Promise.all(
    people.map(async (person) => {
      const { data, response } = await api.GET("/api/v1/users/{user_id}/devices", {
        params: { path: { user_id: person.id } },
      });
      if (!data) throw new AdminApiError(response.status);
      return data.map((d) => ({ ...d, user: person }));
    }),
  );
  return lists
    .flat()
    .sort(
      (a, b) =>
        a.user.display_name.localeCompare(b.user.display_name) ||
        b.last_seen_at.localeCompare(a.last_seen_at),
    );
}

export type RevokeResult = { kind: "ok"; device: Device } | { kind: "crossProject" };

/** Revoke; a PER-09 revoke that also wipes other projects' work answers `crossProject` first. */
export async function revokeDevice(
  api: ApiClient,
  deviceId: string,
  confirmCrossProject: boolean,
): Promise<RevokeResult> {
  const { data, error, response } = await api.POST("/api/v1/devices/{device_id}/revoke", {
    params: { path: { device_id: deviceId } },
    body: { confirm_cross_project_wipe: confirmCrossProject },
  });
  if (data) return { kind: "ok", device: data };
  if (response.status === 409 && conflictDetail(error).cross_project_warning === true) {
    return { kind: "crossProject" };
  }
  throw new AdminApiError(response.status);
}

/** Whether the caller is a System admin: `GET /admin/grants` answers only them (TD-393). */
export async function fetchIsSystemAdmin(api: ApiClient, userId: string): Promise<boolean> {
  const { response } = await api.GET("/api/v1/admin/grants", {
    params: { query: { user_id: userId } },
  });
  return response.ok;
}
