import createClient, { type Client } from "openapi-fetch";

import type { paths } from "../../api/schema";
import { singleTab, type CrossTabAuth } from "../../auth/crossTab";
import type { TokenStore } from "../../auth/tokenStore";

/** Typed client over the generated OpenAPI schema (`src/api/schema.ts`). */
export type ApiClient = Client<paths>;

/** Refresh responses meaning the refresh token itself is invalid (terminal). */
const REFRESH_REJECTED: ReadonlySet<number> = new Set([400, 401, 422]);

export type FetchFn = (input: Request) => Promise<Response>;

export interface ApiClientOptions {
  /** Absolute backend origin, e.g. `https://kerp.example`. */
  baseUrl: string;
  tokens: TokenStore;
  fetch?: FetchFn;
  /** Shares the refresh with the other tabs (T-0072); single-tab behaviour when left out. */
  crossTab?: CrossTabAuth;
}

/**
 * Builds the app's API client. Every request carries the access token; a `401` triggers
 * one refresh-token rotation (shared by concurrent requests, and across tabs with
 * `crossTab`, T-0072) and one retry. The refresh
 * token is the `httpOnly` cookie the browser sends to the auth endpoints itself. A refresh
 * the server rejects (400/401/422, e.g. 12 hours after sign-in) clears the session, which
 * signs the user out; transient failures (network, 429, 5xx) keep it. A refreshed token for
 * a different user than this tab's is refused and signs this tab out (T-0344).
 */
export function createApiClient({
  baseUrl,
  tokens,
  fetch,
  crossTab = singleTab,
}: ApiClientOptions): ApiClient {
  const baseFetch: FetchFn = fetch ?? ((input) => globalThis.fetch(input));
  // The refresh cookie is same-origin only (SameSite=Strict + the server's Origin check).
  const credentials: RequestCredentials = "same-origin";
  // Unauthenticated client for the refresh call itself (never retried).
  const raw = createClient<paths>({ baseUrl, fetch: baseFetch, credentials });
  let pending: Promise<boolean> | null = null;

  const refresh = async (): Promise<boolean> => {
    try {
      const { data, response } = await raw.POST("/api/v1/auth/refresh");
      if (data) {
        // The shared cookie may now belong to another user (signed in in another tab): that
        // token is refused, this tab is signed out and nothing is retried with it (T-0344).
        if (!tokens.rotate(data)) return false;
        crossTab.publishToken(data);
        return true;
      }
      // Only a rejected token ends the session; 429/5xx (e.g. 503 when the revocation
      // store is down) are transient, so the refresh token is kept for a later attempt.
      if (REFRESH_REJECTED.has(response.status)) tokens.clear();
      return false;
    } catch {
      // Network failure: keep the session so a later request can retry.
      return false;
    }
  };

  const refreshOnce = (): Promise<boolean> => {
    pending ??= (async () => {
      if (!tokens.hasSession) return false;
      const stale = tokens.access;
      // One refresh at a time across the browser's tabs (T-0072): they share the cookie.
      return crossTab.withRefreshLock(async () => {
        // Another tab refreshed while this one waited and posted its access token: use it.
        if (tokens.access !== null && tokens.access !== stale) return true;
        if (!tokens.hasSession) return false;
        return refresh();
      });
    })().finally(() => {
      pending = null;
    });
    return pending;
  };

  const withAuth = (request: Request): Request => {
    const access = tokens.access;
    if (access === null) return request;
    const authed = new Request(request);
    authed.headers.set("Authorization", `Bearer ${access}`);
    return authed;
  };

  const authFetch: FetchFn = async (request) => {
    const retry = request.clone();
    if (tokens.access === null && tokens.hasSession) await refreshOnce();
    const used = tokens.access;
    const response = await baseFetch(withAuth(request));
    if (response.status !== 401 || !tokens.hasSession) return response;
    // Another request may already have rotated the tokens while this one was in flight.
    const rotated = tokens.access !== null && tokens.access !== used;
    if (!rotated && !(await refreshOnce())) return response;
    return baseFetch(withAuth(retry));
  };

  return createClient<paths>({ baseUrl, fetch: authFetch, credentials });
}
