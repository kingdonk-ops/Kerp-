// Project invites of the members screen (T-0052 API; panel T-0053, diff §4.1).
import type { components } from "../api/schema";
import type { ApiClient } from "../lib/api/client";
import { AdminApiError } from "./api";

export type OpenInvite = components["schemas"]["InviteRead"];
export type SentInvite = components["schemas"]["InviteSent"];

/** The project's open invites: pending, and expired never accepted. */
export async function fetchInvites(api: ApiClient, projectId: string): Promise<OpenInvite[]> {
  const { data, response } = await api.GET("/api/v1/admin/projects/{project_id}/invites", {
    params: { path: { project_id: projectId } },
  });
  if (!data) throw new AdminApiError(response.status);
  return data;
}

/**
 * Re-send: a new link (shown once) and a new e-mail. `throttled`: the send cap of this project
 * + e-mail, with when it allows the next send (`Retry-After`); `gone`: accepted or revoked.
 */
export type ResendResult =
  | { kind: "sent"; invite: SentInvite }
  | { kind: "throttled"; retryAt: Date | null }
  | { kind: "gone" };

export async function resendInvite(
  api: ApiClient,
  projectId: string,
  inviteId: string,
): Promise<ResendResult> {
  const { data, response } = await api.POST(
    "/api/v1/admin/projects/{project_id}/invites/{invite_id}/resend",
    { params: { path: { project_id: projectId, invite_id: inviteId } } },
  );
  if (data) return { kind: "sent", invite: data };
  if (response.status === 429) {
    const seconds = Number(response.headers.get("Retry-After"));
    const known = Number.isFinite(seconds) && seconds > 0;
    return { kind: "throttled", retryAt: known ? new Date(Date.now() + seconds * 1000) : null };
  }
  if (response.status === 409 || response.status === 404) return { kind: "gone" };
  throw new AdminApiError(response.status);
}

/** Revoke an open invite; `gone`: already accepted or revoked. */
export async function revokeInvite(
  api: ApiClient,
  projectId: string,
  inviteId: string,
): Promise<"ok" | "gone"> {
  const { response } = await api.DELETE("/api/v1/admin/projects/{project_id}/invites/{invite_id}", {
    params: { path: { project_id: projectId, invite_id: inviteId } },
  });
  if (response.ok) return "ok";
  if (response.status === 409 || response.status === 404) return "gone";
  throw new AdminApiError(response.status);
}
