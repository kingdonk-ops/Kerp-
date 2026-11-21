/**
 * API calls of the journey, as a signed-in user: the bearer token is the one the browser got
 * from its own sign-in (captured from the login response), so an API check runs in that
 * user's session. Requests go through the frontend origin (`/api` is proxied), like the app's.
 */
import { expect, type APIRequestContext, type APIResponse, type Page } from "@playwright/test";

export interface Session {
  request: APIRequestContext;
  token: string;
}

type Method = "GET" | "PUT" | "POST";

export async function call(
  session: Session,
  method: Method,
  url: string,
  data?: unknown,
): Promise<APIResponse> {
  return session.request.fetch(url, {
    method,
    headers: { Authorization: `Bearer ${session.token}` },
    ...(data === undefined ? {} : { data }),
  });
}

/** GET/PUT/POST that must answer `status`; its JSON body. */
export async function json<T>(
  session: Session,
  method: Method,
  url: string,
  status: number,
  data?: unknown,
): Promise<T> {
  const response = await call(session, method, url, data);
  expect(response.status(), `${method} ${url}: ${await response.text()}`).toBe(status);
  return (await response.json()) as T;
}

/** Runs `signIn` (the UI submit) and returns the access token the browser received. */
export async function captureLogin(page: Page, signIn: () => Promise<void>): Promise<string> {
  const login = page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/v1/auth/login" && r.request().method() === "POST",
  );
  await signIn();
  const response = await login;
  expect(response.status()).toBe(200);
  const body = (await response.json()) as { access_token: string };
  return body.access_token;
}
