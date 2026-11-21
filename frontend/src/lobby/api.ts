import type { components } from "../api/schema";
import type { ApiClient } from "../lib/api/client";

export type LobbyProject = components["schemas"]["LobbyProject"];
export type CodeMatch = components["schemas"]["LobbyCodeMatch"];
export type MyRequest = components["schemas"]["AccessRequestRead"];
export type RequestStatus = MyRequest["status"];

export const MINE_KEY = ["accessRequests", "mine"] as const;
export const LOBBY_KEY = ["lobby", "projects"] as const;

/** A failed lobby read or write; `status` is the HTTP status. */
export class LobbyApiError extends Error {
  constructor(readonly status: number) {
    super(`lobby API: ${String(status)}`);
    this.name = "LobbyApiError";
  }
}

/** Lobby-visible projects (T-0040), by code; internal users only (403 otherwise). */
export async function fetchLobby(api: ApiClient): Promise<LobbyProject[]> {
  const { data, response } = await api.GET("/api/v1/lobby/projects");
  if (!data) throw new LobbyApiError(response.status);
  return [...data].sort((a, b) => a.code.localeCompare(b.code));
}

export type LookupResult =
  | { kind: "found"; match: CodeMatch }
  | { kind: "notFound" }
  | { kind: "throttled" };

/** The one project with exactly `code` plus its lookup ticket (T-0040); never grants access. */
export async function lookupCode(api: ApiClient, code: string): Promise<LookupResult> {
  const { data, response } = await api.GET("/api/v1/lobby/projects/by-code/{code}", {
    params: { path: { code } },
  });
  if (data) return { kind: "found", match: data };
  if (response.status === 429) return { kind: "throttled" };
  if (response.status === 404 || response.status === 422) return { kind: "notFound" };
  throw new LobbyApiError(response.status);
}

/** The caller's "Active requests" (T-0050), newest first: the authoritative outcome channel. */
export async function fetchMine(api: ApiClient): Promise<MyRequest[]> {
  const { data, response } = await api.GET("/api/v1/access-requests/mine");
  if (!data) throw new LobbyApiError(response.status);
  return [...data].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

/**
 * Outcome of filing a request. `cooldown` carries when a new request becomes allowed;
 * `notFound` is a 404 (unknown project, or the lookup ticket expired: look the code up again).
 */
export type FileResult =
  | { kind: "ok" }
  | { kind: "cooldown"; retryAfter: string | null }
  | { kind: "pending" }
  | { kind: "member" }
  | { kind: "notFound" };

export interface FileRequest {
  projectId: string;
  message?: string;
  /** The code lookup's ticket; omitted for lobby-list requests and for "Request again". */
  lookupTicket?: string;
}

export async function fileRequest(api: ApiClient, req: FileRequest): Promise<FileResult> {
  const message = req.message?.trim();
  const { data, error, response } = await api.POST("/api/v1/access-requests", {
    body: {
      project_id: req.projectId,
      ...(message ? { message } : {}),
      ...(req.lookupTicket ? { lookup_ticket: req.lookupTicket } : {}),
    },
  });
  if (data) return { kind: "ok" };
  if (response.status === 404) return { kind: "notFound" };
  if (response.status === 409) {
    const body = error as { code?: unknown; retry_after?: unknown } | undefined;
    if (body?.code === "ACCESS_REQUEST_COOLDOWN") {
      const retryAfter = typeof body.retry_after === "string" ? body.retry_after : null;
      return { kind: "cooldown", retryAfter };
    }
    return body?.code === "ACCESS_REQUEST_ALREADY_MEMBER" ? { kind: "member" } : { kind: "pending" };
  }
  throw new LobbyApiError(response.status);
}

/** Withdraws the caller's pending request; `false` when it is no longer pending (409/404). */
export async function withdrawRequest(api: ApiClient, id: string): Promise<boolean> {
  const { data, response } = await api.POST("/api/v1/access-requests/{request_id}/withdraw", {
    params: { path: { request_id: id } },
  });
  if (data) return true;
  if (response.status === 409 || response.status === 404) return false;
  throw new LobbyApiError(response.status);
}
