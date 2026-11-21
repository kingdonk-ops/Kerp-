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

// --- admin grants, projects, members (T-0039, T-0012, T-0014; diff §4.1) ---------------------

export type AdminGrantKind = components["schemas"]["AdminGrantKind"];
export type ProjectUpdate = components["schemas"]["ProjectUpdate"];
export type Member = components["schemas"]["MemberRead"];
export type UserSummary = components["schemas"]["UserSummary"];
/** A user with the tenant-level admin grants they hold (never personas). */
export type GrantedUser = components["schemas"]["UserRead"] & { grants: AdminGrantKind[] };

/** Every account with its admin grants (System admins only; 403 for everyone else). */
export async function fetchGrantedUsers(api: ApiClient): Promise<GrantedUser[]> {
  const [users, grants] = await Promise.all([
    api.GET("/api/v1/admin/users"),
    api.GET("/api/v1/admin/grants"),
  ]);
  if (!users.data) throw new AdminApiError(users.response.status);
  if (!grants.data) throw new AdminApiError(grants.response.status);
  const held = grants.data;
  return users.data
    .map((u) => ({ ...u, grants: held.filter((g) => g.user_id === u.id).map((g) => g.grant) }))
    .sort((a, b) => a.display_name.localeCompare(b.display_name));
}

/** Give or remove a grant; `last`: the last System admin cannot be removed (409). */
export async function setAdminGrant(
  api: ApiClient,
  userId: string,
  grant: AdminGrantKind,
  on: boolean,
): Promise<"ok" | "last"> {
  const { response } = on
    ? await api.POST("/api/v1/admin/grants", { body: { user_id: userId, grant } })
    : await api.DELETE("/api/v1/admin/grants/{user_id}/{grant}", {
        params: { path: { user_id: userId, grant } },
      });
  if (response.ok || response.status === 404) return "ok"; // 404: already removed
  if (response.status === 409) return "last";
  throw new AdminApiError(response.status);
}

/** A project as the client reads it (`GET /projects`). */
export type Project = Awaited<ReturnType<typeof fetchProjects>>[number];

export async function fetchProjects(api: ApiClient) {
  const { data, response } = await api.GET("/api/v1/projects");
  if (!data) throw new AdminApiError(response.status);
  return [...data].sort((a, b) => a.code.localeCompare(b.code));
}

/** `forbidden`: no `create_projects` grant (403); `duplicate`: the code is taken (409). */
export type SaveResult = "ok" | "forbidden" | "duplicate";

function saved(status: number, ok: boolean): SaveResult {
  if (ok) return "ok";
  if (status === 403) return "forbidden";
  if (status === 409) return "duplicate";
  throw new AdminApiError(status);
}

/** Create a project with the deployment's defaults; the creator becomes its PER-09. */
export async function createProject(api: ApiClient, code: string, name: string): Promise<SaveResult> {
  const { response } = await api.POST("/api/v1/projects", { body: { code, name } });
  return saved(response.status, response.ok);
}

export async function updateProject(
  api: ApiClient,
  id: string,
  body: ProjectUpdate,
): Promise<SaveResult> {
  const { response } = await api.PATCH("/api/v1/projects/{project_id}", {
    params: { path: { project_id: id } },
    body,
  });
  return saved(response.status, response.ok);
}

export async function setLobbyVisible(api: ApiClient, id: string, visible: boolean): Promise<void> {
  const { response } = await api.PUT("/api/v1/projects/{project_id}/lobby", {
    params: { path: { project_id: id } },
    body: { visible_in_lobby: visible },
  });
  if (!response.ok) throw new AdminApiError(response.status);
}

export async function fetchMembers(api: ApiClient, projectId: string): Promise<Member[]> {
  const { data, response } = await api.GET("/api/v1/admin/projects/{project_id}/members", {
    params: { path: { project_id: projectId } },
  });
  if (!data) throw new AdminApiError(response.status);
  return [...data].sort((a, b) => a.display_name.localeCompare(b.display_name));
}

/** An active account by exact e-mail, or `null` (404). */
export async function lookupUser(api: ApiClient, email: string): Promise<UserSummary | null> {
  const { data, response } = await api.POST("/api/v1/admin/users/lookup", {
    body: { email: email.trim() },
  });
  if (data) return data;
  if (response.status === 404) return null;
  throw new AdminApiError(response.status);
}

/** The member's individual grants (team-derived ones are read-only, ADR 0008). */
export function directGrants(member: Member | undefined): GrantIn[] {
  return (member?.grants ?? [])
    .filter((g) => g.source === "direct")
    .map(({ persona, site_node_id }) => (site_node_id ? { persona, site_node_id } : { persona }));
}

/** Set the user's complete individual persona set on a project; `sod`: confirm first (409). */
export async function assignMember(
  api: ApiClient,
  projectId: string,
  userId: string,
  grants: GrantIn[],
  confirmSod: boolean,
): Promise<"ok" | "sod"> {
  const { error, response } = await api.PUT(
    "/api/v1/admin/projects/{project_id}/members/{user_id}",
    {
      params: { path: { project_id: projectId, user_id: userId } },
      body: { grants, confirm_sod: confirmSod },
    },
  );
  if (response.ok) return "ok";
  if (response.status === 409 && conflictDetail(error).sod_warning === true) return "sod";
  throw new AdminApiError(response.status);
}

/**
 * Add `grants` to the user's individual grants on each project in turn (one action, several
 * of the admin's projects). Stops at the first project that needs the SoD confirmation and
 * returns the projects still to do, to re-run with `confirmSod`.
 */
export async function addToProjects(
  api: ApiClient,
  userId: string,
  projectIds: readonly string[],
  grants: GrantIn[],
  confirmSod: boolean,
): Promise<string[]> {
  for (const [i, projectId] of projectIds.entries()) {
    const members = await fetchMembers(api, projectId);
    const merged = new Map(
      directGrants(members.find((m) => m.user_id === userId)).map((g) => [g.persona, g]),
    );
    for (const g of grants) merged.set(g.persona, g);
    const res = await assignMember(api, projectId, userId, [...merged.values()], confirmSod);
    if (res === "sod") return projectIds.slice(i);
  }
  return [];
}

/** Remove the user's individual personas; `team`: their access is only team-derived (409). */
export async function removeMember(
  api: ApiClient,
  projectId: string,
  userId: string,
): Promise<"ok" | "team"> {
  const { response } = await api.DELETE("/api/v1/admin/projects/{project_id}/members/{user_id}", {
    params: { path: { project_id: projectId, user_id: userId } },
  });
  if (response.ok || response.status === 404) return "ok";
  if (response.status === 409) return "team";
  throw new AdminApiError(response.status);
}
