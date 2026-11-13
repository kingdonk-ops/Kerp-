import createClient, { type Client } from "openapi-fetch";

import type { paths } from "../../api/schema";
import type { TokenStore } from "../../auth/tokenStore";

/** Typed client over the generated OpenAPI schema (`src/api/schema.ts`). */
export type ApiClient = Client<paths>;

export type FetchFn = (input: Request) => Promise<Response>;

export interface ApiClientOptions {
  /** Absolute backend origin, e.g. `https://kerp.example`. */
  baseUrl: string;
  tokens: TokenStore;
  fetch?: FetchFn;
}

/**
 * Builds the app's API client. Every request carries the access token; a `401` triggers
 * one refresh-token rotation (shared by concurrent requests) and one retry. A refused
 * refresh clears the session, which signs the user out.
 */
export function createApiClient({ baseUrl, tokens, fetch }: ApiClientOptions): ApiClient {
  const baseFetch: FetchFn = fetch ?? ((input) => globalThis.fetch(input));
  // Unauthenticated client for the refresh call itself (never retried).
  const raw = createClient<paths>({ baseUrl, fetch: baseFetch });
  let pending: Promise<boolean> | null = null;

  const refreshOnce = (): Promise<boolean> => {
    pending ??= (async () => {
      const refreshToken = tokens.refresh;
      if (refreshToken === null) return false;
      try {
        const { data } = await raw.POST("/api/v1/auth/refresh", {
          body: { refresh_token: refreshToken },
        });
        if (!data) {
          tokens.clear();
          return false;
        }
        tokens.set(data);
        return true;
      } catch {
        // Network failure: keep the session so a later request can retry.
        return false;
      }
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

  return createClient<paths>({ baseUrl, fetch: authFetch });
}
