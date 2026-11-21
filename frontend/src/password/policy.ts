import type { ApiClient } from "../lib/api/client";

/** Password policy refusals of every password set (diff §4.2 *Local passwords*, T-0077). */
export const POLICY_CODES = ["PASSWORD_TOO_SHORT", "PASSWORD_TOO_LONG", "PASSWORD_TOO_COMMON"] as const;
export type PolicyCode = (typeof POLICY_CODES)[number];

export interface PolicyRefusal {
  code: PolicyCode;
  minLength: number | null;
  maxLength: number | null;
}

export class PasswordRefusedError extends Error {
  constructor(readonly refusal: PolicyRefusal) {
    super(refusal.code);
    this.name = "PasswordRefusedError";
  }
}

const KNOWN: ReadonlySet<string> = new Set(POLICY_CODES);
const size = (v: unknown) => (typeof v === "number" && Number.isInteger(v) && v > 0 ? v : null);

/** A `422` body as a known policy refusal; `null` for anything else (never shown raw). */
export function passwordRefusal(body: unknown): PolicyRefusal | null {
  if (typeof body !== "object" || body === null) return null;
  const { code, min_length, max_length } = body as Record<string, unknown>;
  if (typeof code !== "string" || !KNOWN.has(code)) return null;
  return { code: code as PolicyCode, minLength: size(min_length), maxLength: size(max_length) };
}

/** `throttled`: 429; `failed`: any other unexpected answer. */
export class PasswordRequestError extends Error {
  constructor(readonly reason: "throttled" | "failed" | "linkInvalid") {
    super(reason);
    this.name = "PasswordRequestError";
  }
}

/** Ask for a reset / set-password link; the answer is the same for every address. */
export async function requestPasswordEmail(api: ApiClient, email: string): Promise<void> {
  const { response } = await api.POST("/api/v1/auth/password-reset/request", { body: { email } });
  if (response.status === 202) return;
  throw new PasswordRequestError(response.status === 429 ? "throttled" : "failed");
}

/** Set a new password with a reset link's token. */
export async function confirmPasswordReset(
  api: ApiClient,
  token: string,
  password: string,
): Promise<void> {
  const { data, error, response } = await api.POST("/api/v1/auth/password-reset/confirm", {
    body: { token, new_password: password },
  });
  if (data) return;
  const policy = response.status === 422 ? passwordRefusal(error) : null;
  if (policy) throw new PasswordRefusedError(policy);
  if (response.status === 410) throw new PasswordRequestError("linkInvalid");
  throw new PasswordRequestError(response.status === 429 ? "throttled" : "failed");
}
