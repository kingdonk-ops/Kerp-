import { createApiClient, type ApiClient } from "../lib/api/client";
import { memoryStorage, TokenStore, type KeyValueStorage } from "../auth/tokenStore";

export const TEST_ORIGIN = "http://kerp.test";

/** Synthetic identity used across frontend tests. */
export const FAKE_USER = {
  id: "00000000-0000-4000-8000-000000000001",
  tenant_id: "00000000-0000-4000-8000-0000000000aa",
  email: "inspector@example.test",
  display_name: "Test Inspector",
};

export interface Recorded {
  method: string;
  path: string;
  auth: string | null;
  body: unknown;
}

type Handler = (req: Recorded) => Response | Promise<Response>;

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function tokens(n: number) {
  return {
    access_token: `access-${n}`,
    refresh_token: `refresh-${n}`,
    token_type: "bearer" as const,
    expires_in: 900,
    refresh_expires_in: 1_209_600,
  };
}

/**
 * Fake backend: `handlers` are keyed by `"METHOD /path"`. Unhandled calls get `404`.
 * Default handlers implement login (password "secret"), refresh rotation and `me`.
 */
export function fakeBackend(handlers: Record<string, Handler> = {}) {
  const calls: Recorded[] = [];
  let issued = 0;
  const defaults: Record<string, Handler> = {
    "POST /api/v1/auth/login": (req) => {
      const body = req.body as { password?: string };
      return body.password === "secret"
        ? json(tokens(++issued))
        : json({ detail: "Not authenticated" }, 401);
    },
    "POST /api/v1/auth/refresh": () => json(tokens(++issued)),
    "POST /api/v1/auth/logout": () => new Response(null, { status: 204 }),
    "GET /api/v1/auth/me": (req) =>
      req.auth?.startsWith("Bearer access-")
        ? json(FAKE_USER)
        : json({ detail: "Not authenticated" }, 401),
  };
  const fetch = async (request: Request): Promise<Response> => {
    const url = new URL(request.url);
    const text = await request.text();
    const rec: Recorded = {
      method: request.method,
      path: url.pathname,
      auth: request.headers.get("Authorization"),
      body: text ? (JSON.parse(text) as unknown) : null,
    };
    calls.push(rec);
    const handler = handlers[`${rec.method} ${rec.path}`] ?? defaults[`${rec.method} ${rec.path}`];
    return handler ? handler(rec) : json({ detail: "Not Found" }, 404);
  };
  return { fetch, calls };
}

export function makeServices(
  handlers: Record<string, Handler> = {},
  storage: KeyValueStorage = memoryStorage(),
): {
  api: ApiClient;
  tokens: TokenStore;
  calls: Recorded[];
} {
  const backend = fakeBackend(handlers);
  const store = new TokenStore(storage);
  const api = createApiClient({ baseUrl: TEST_ORIGIN, tokens: store, fetch: backend.fetch });
  return { api, tokens: store, calls: backend.calls };
}
