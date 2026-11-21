import type { components } from "../api/schema";
import type { ApiClient } from "../lib/api/client";
import { passwordRefusal, PasswordRefusedError } from "../password/policy";

export type InvitePreview = components["schemas"]["InvitePreview"];
export type InviteSetupPreview = components["schemas"]["InviteSetupPreview"];
type TokenResponse = components["schemas"]["TokenResponse"];

/**
 * Why an invite cannot be used, as one neutral message each (diff §4.1): the signed-in user is
 * not the invited one; the invite is expired, revoked or used (`INVITE_CONFLICT`: accepted in
 * parallel, i.e. used); the account must sign in instead; anything else.
 */
export type InviteProblem = "wrongUser" | "gone" | "signInRequired" | "unavailable";

export class InviteError extends Error {
  constructor(readonly problem: InviteProblem) {
    super(problem);
    this.name = "InviteError";
  }
}

/** A refusal `code` (response body or `invite_error` of a sign-in) as a problem; never shown raw. */
export function inviteProblem(code: unknown): InviteProblem {
  if (code === "INVITE_WRONG_USER") return "wrongUser";
  if (code === "INVITE_GONE" || code === "INVITE_CONFLICT") return "gone";
  if (code === "INVITE_SIGN_IN_REQUIRED") return "signInRequired";
  return "unavailable";
}

function refusal(error: unknown): InviteError {
  return new InviteError(inviteProblem((error as { code?: unknown } | undefined)?.code));
}

/** "Join project" confirmation of a signed-in user (`POST /admin/invites/preview`, token in the body). */
export async function previewInvite(api: ApiClient, inviteToken: string): Promise<InvitePreview> {
  const { data, error } = await api.POST("/api/v1/admin/invites/preview", {
    body: { invite_token: inviteToken },
  });
  if (data) return data;
  throw refusal(error);
}

/** Joins the project; returns its id. */
export async function acceptInvite(api: ApiClient, inviteToken: string): Promise<string> {
  const { data, error } = await api.POST("/api/v1/admin/invites/accept", {
    body: { invite_token: inviteToken },
  });
  if (data) return data.joined_project_id;
  throw refusal(error);
}

/** What the e-mailed link offers before sign-in (both tokens; no session). */
export async function setupPreview(
  api: ApiClient,
  inviteToken: string,
  setupToken: string,
): Promise<InviteSetupPreview> {
  const { data, error } = await api.POST("/api/v1/admin/invites/setup-preview", {
    body: { invite_token: inviteToken, setup_token: setupToken },
  });
  if (data) return data;
  throw refusal(error);
}

/**
 * First password of a brand-new invitee + join + sign-in in one request (T-0059). A policy
 * refusal is a `PasswordRefusedError`; invite problems an `InviteError`.
 */
export async function acceptWithPassword(
  api: ApiClient,
  inviteToken: string,
  setupToken: string,
  password: string,
): Promise<TokenResponse> {
  const { data, error, response } = await api.POST("/api/v1/admin/invites/accept-with-password", {
    body: { invite_token: inviteToken, setup_token: setupToken, new_password: password },
  });
  if (data) return data;
  const policy = response.status === 422 ? passwordRefusal(error) : null;
  if (policy) throw new PasswordRefusedError(policy);
  throw refusal(error);
}
