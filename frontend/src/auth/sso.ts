import type { components } from "../api/schema";

/** What the sign-in screen offers (`GET /api/v1/auth/config`, T-0054). */
export type AuthConfig = components["schemas"]["AuthConfigResponse"];

/** The SPA route the Entra ID callback redirects the browser to (T-0054 `WEB_RETURN_PATH`). */
export const SSO_RETURN_PATH = "/login/sso-return";

/** The callback's success marker (`status=signed_in`, T-0054 `SIGNED_IN`). */
export const SSO_SIGNED_IN = "signed_in";

/**
 * The `error` codes the Entra ID SSO callback redirects with (T-0054,
 * `backend/app/core/auth/entra.py` `CLIENT_CODES` + `DEVICE_REVOKED`). Each has a message
 * under `auth.sso.errors.<code>`.
 */
export const SSO_ERROR_CODES = [
  "SSO_GUEST",
  "SSO_NO_ACCOUNT",
  "SSO_ACCOUNT_DEACTIVATED",
  "SSO_EMAIL_UNVERIFIED",
  "SSO_ACCOUNT_LINKED_ELSEWHERE",
  "SSO_FAILED",
  "DEVICE_REVOKED",
] as const;

export type SsoErrorCode = (typeof SSO_ERROR_CODES)[number];

const KNOWN: ReadonlySet<string> = new Set(SSO_ERROR_CODES);

function isSsoErrorCode(raw: string): raw is SsoErrorCode {
  return KNOWN.has(raw);
}

/**
 * A refusal code from the callback's query string, as a known message key. Anything else
 * (unknown, malformed, or content that is not a code at all) is the generic `SSO_FAILED`:
 * the raw value is never shown or logged (diff §4.5).
 */
export function ssoErrorCode(raw: unknown): SsoErrorCode {
  return typeof raw === "string" && isSsoErrorCode(raw) ? raw : "SSO_FAILED";
}

/**
 * The server-reported start URL, accepted only as a same-origin API path: the pre-auth
 * cookie of T-0054 and the session cookie are bound to this origin. Anything else (an
 * absolute or protocol-relative URL) is not offered.
 */
export function safeStartUrl(config: AuthConfig | undefined): string | null {
  const url = config?.sso?.start_url;
  if (typeof url !== "string") return null;
  return url.startsWith("/api/") && !url.includes("//") && !url.includes("\\") ? url : null;
}
