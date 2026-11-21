// Session isolation on a shared device (T-0344, audit items 4 and 5). Synthetic data only.
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { AppProviders } from "../app/AppRoot";
import { createQueryClient } from "../app/queryClient";
import { routes } from "../app/routes";
import { ServicesProvider, type AppServices } from "../app/services";
import { i18n } from "../i18n";
import { createApiClient } from "../lib/api/client";
import { returnPath } from "../pages/LoginPage";
import { FAKE_USER, fakeBackend, json, TEST_ORIGIN, tokens, type Recorded } from "../test/fakeApi";
import { AuthProvider, SIGN_IN_PATH, useAuth } from "./AuthProvider";
import { connectCrossTab, type ChannelLike } from "./crossTab";
import { memoryStorage, tokenSubject, TokenStore } from "./tokenStore";

const USER_1 = FAKE_USER;
const USER_2 = {
  id: "00000000-0000-4000-8000-000000000002",
  tenant_id: FAKE_USER.tenant_id,
  email: "second@example.test",
  display_name: "Second Inspector",
};
const USERS = [USER_1, USER_2];

function b64url(value: unknown): string {
  return btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** A JWT-shaped synthetic access token for `sub` (the signature is never checked client-side). */
function jwt(sub: string, n: number): string {
  return `${b64url({ alg: "HS256", typ: "JWT" })}.${b64url({ sub, n })}.synthetic-signature`;
}

function tokenFor(sub: string, n: number) {
  return { ...tokens(n), access_token: jwt(sub, n) };
}

/** `me` answers with the user the bearer token names. */
function meBySubject(req: Recorded): Response {
  const sub = req.auth?.startsWith("Bearer ") ? tokenSubject(req.auth.slice(7)) : null;
  const user = USERS.find((u) => u.id === sub);
  return user ? json(user) : json({ detail: "Not authenticated" }, 401);
}

/** A `BroadcastChannel` stand-in reaching every other member. */
function channelHub() {
  const members: ((event: MessageEvent) => void)[][] = [];
  return (): ChannelLike => {
    const mine: ((event: MessageEvent) => void)[] = [];
    members.push(mine);
    return {
      postMessage(message) {
        for (const other of members)
          if (other !== mine) for (const l of other) l({ data: message } as MessageEvent);
      },
      addEventListener(_type, listener) {
        mine.push(listener);
      },
      close: () => undefined,
    };
  };
}

const LIST_KEY = ["registers", "list"] as const;
const USER_1_LIST = [{ id: "row-1", title: "Synthetic row of user 1" }];

/** Only the signed-out tab's empty, disabled `me` query may be left: no data at all. */
function expectNoCachedData(queryClient: QueryClient) {
  const left = queryClient.getQueryCache().getAll();
  expect(left.filter((q) => q.state.data !== undefined)).toEqual([]);
}

let auth: ReturnType<typeof useAuth> | null = null;
function Probe() {
  const current = useAuth();
  useEffect(() => {
    auth = current;
  }, [current]);
  return <p data-testid="who">{current.user?.email ?? current.status}</p>;
}

/** One tab: auth provider over a real client, fake backend and an optional channel. */
function tab(handlers: Parameters<typeof fakeBackend>[0] = {}, channel?: ChannelLike) {
  const backend = fakeBackend({ "GET /api/v1/auth/me": meBySubject, ...handlers });
  const store = new TokenStore(memoryStorage());
  const crossTab = channel ? connectCrossTab(store, { channel }) : undefined;
  const api = createApiClient({
    baseUrl: TEST_ORIGIN,
    tokens: store,
    fetch: backend.fetch,
    ...(crossTab ? { crossTab } : {}),
  });
  const reloads: string[] = [];
  const services: AppServices = {
    api,
    tokens: store,
    preferences: memoryStorage(),
    ...(crossTab ? { crossTab } : {}),
    reloadTo: (path) => reloads.push(path),
  };
  const queryClient: QueryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  return { api, tokens: store, crossTab, services, queryClient, calls: backend.calls, reloads };
}

async function signedInAsUser1(t: ReturnType<typeof tab>) {
  t.tokens.set(tokenFor(USER_1.id, 1));
  render(
    <ServicesProvider services={t.services}>
      <QueryClientProvider client={t.queryClient}>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </QueryClientProvider>
    </ServicesProvider>,
  );
  expect(await screen.findByText(USER_1.email)).toBeInTheDocument();
  t.queryClient.setQueryData(LIST_KEY, USER_1_LIST);
}

describe("the query cache never outlives the session (audit item 4)", () => {
  it("drops user 1's list data on a sign-out broadcast from another tab", async () => {
    const join = channelHub();
    const t = tab({}, join());
    const other = connectCrossTab(new TokenStore(memoryStorage()), { channel: join() });
    await signedInAsUser1(t);

    act(() => {
      other.publishLogout();
    });

    expect(t.tokens.hasSession).toBe(false);
    expect(t.queryClient.getQueryData(LIST_KEY)).toBeUndefined();
    expectNoCachedData(t.queryClient);
    expect(await screen.findByText("anonymous")).toBeInTheDocument();
  });

  it("drops user 1's list data when the session expires (refresh refused)", async () => {
    const t = tab({
      "GET /api/v1/me/projects": () => json({ detail: "expired" }, 401),
      "POST /api/v1/auth/refresh": () => json({ detail: "Session expired" }, 401),
    });
    await signedInAsUser1(t);

    await act(async () => {
      await t.api.GET("/api/v1/me/projects");
    });

    expect(t.tokens.hasSession).toBe(false);
    expect(t.queryClient.getQueryData(LIST_KEY)).toBeUndefined();
  });

  it("drops everything on a local sign-out", async () => {
    const t = tab();
    await signedInAsUser1(t);

    await act(async () => {
      await auth?.logout();
    });

    expectNoCachedData(t.queryClient);
  });

  it("starts the session of user 2 with an empty cache and loads user 2", async () => {
    const t = tab();
    await signedInAsUser1(t);

    act(() => {
      auth?.startSession(tokenFor(USER_2.id, 7));
    });

    expect(t.queryClient.getQueryData(LIST_KEY)).toBeUndefined();
    expect(await screen.findByText(USER_2.email)).toBeInTheDocument();
    expect(t.reloads).toEqual([]);
  });

  it("adopts a cookie session (SSO) with an empty cache", async () => {
    const t = tab({ "POST /api/v1/auth/refresh": () => json(tokenFor(USER_2.id, 8)) });
    await signedInAsUser1(t);

    act(() => {
      auth?.adoptCookieSession();
    });

    expect(t.queryClient.getQueryData(LIST_KEY)).toBeUndefined();
    expect(await screen.findByText(USER_2.email)).toBeInTheDocument();
    expect(t.reloads).toEqual([]);
  });
});

describe("a token for another user is a sign-out (audit item 5)", () => {
  it("refuses user 2's broadcast token: no request with it, signed out, reloaded to sign-in", async () => {
    const join = channelHub();
    const t = tab({}, join());
    const other = connectCrossTab(new TokenStore(memoryStorage()), { channel: join() });
    await signedInAsUser1(t);
    const foreign = jwt(USER_2.id, 2);

    act(() => {
      other.publishToken({ access_token: foreign, expires_in: 900 });
    });
    await act(async () => {
      await t.api.GET("/api/v1/auth/me");
    });

    expect(t.tokens.hasSession).toBe(false);
    expect(t.tokens.access).toBeNull();
    expect(t.queryClient.getQueryData(LIST_KEY)).toBeUndefined();
    expect(t.reloads).toEqual([SIGN_IN_PATH]);
    expect(t.calls.some((c) => c.auth === `Bearer ${foreign}`)).toBe(false);
  });

  it("refuses user 2's refresh response: no retry with it, signed out, reloaded to sign-in", async () => {
    const foreign = tokenFor(USER_2.id, 2);
    const t = tab({
      "GET /api/v1/me/projects": (req) =>
        req.auth === `Bearer ${jwt(USER_1.id, 1)}` ? json({ detail: "expired" }, 401) : json([]),
      "POST /api/v1/auth/refresh": () => json(foreign),
    });
    await signedInAsUser1(t);

    let status = 0;
    await act(async () => {
      const { response } = await t.api.GET("/api/v1/me/projects");
      status = response.status;
    });

    expect(status).toBe(401);
    expect(t.tokens.hasSession).toBe(false);
    expect(t.queryClient.getQueryData(LIST_KEY)).toBeUndefined();
    expect(t.reloads).toEqual([SIGN_IN_PATH]);
    expect(t.calls.some((c) => c.auth === `Bearer ${foreign.access_token}`)).toBe(false);
  });

  it("does not post a refused token to the other tabs", async () => {
    const join = channelHub();
    const store = new TokenStore(memoryStorage());
    const backend = fakeBackend({
      "GET /api/v1/auth/me": (req) =>
        req.auth === `Bearer ${jwt(USER_1.id, 1)}` ? json({}, 401) : json(USER_2),
      "POST /api/v1/auth/refresh": () => json(tokenFor(USER_2.id, 2)),
    });
    const channel = join();
    const listener = join();
    const heard: unknown[] = [];
    listener.addEventListener("message", (event) => heard.push(event.data));
    const api = createApiClient({
      baseUrl: TEST_ORIGIN,
      tokens: store,
      fetch: backend.fetch,
      crossTab: connectCrossTab(store, { channel }),
    });
    store.set(tokenFor(USER_1.id, 1));

    await api.GET("/api/v1/auth/me");

    expect(heard).toEqual([]);
    expect(store.hasSession).toBe(false);
  });

  it("accepts the same user's broadcast and refreshed tokens as before", async () => {
    const join = channelHub();
    const t = tab(
      {
        "GET /api/v1/me/projects": (req) =>
          req.auth === `Bearer ${jwt(USER_1.id, 3)}` ? json([]) : json({ detail: "expired" }, 401),
        "POST /api/v1/auth/refresh": () => json(tokenFor(USER_1.id, 3)),
      },
      join(),
    );
    const other = connectCrossTab(new TokenStore(memoryStorage()), { channel: join() });
    await signedInAsUser1(t);

    act(() => {
      other.publishToken({ access_token: jwt(USER_1.id, 2), expires_in: 900 });
    });
    expect(t.tokens.access).toBe(jwt(USER_1.id, 2));

    let status = 0;
    await act(async () => {
      const { response } = await t.api.GET("/api/v1/me/projects");
      status = response.status;
    });

    expect(status).toBe(200);
    expect(t.tokens.access).toBe(jwt(USER_1.id, 3));
    expect(t.tokens.hasSession).toBe(true);
    expect(t.queryClient.getQueryData(LIST_KEY)).toEqual(USER_1_LIST);
    expect(t.reloads).toEqual([]);
    expect(screen.getByText(USER_1.email)).toBeInTheDocument();
  });

  it("binds a resumed session to the user `me` loaded", async () => {
    // After a reload the first token comes from the refresh; it binds the session.
    const t = tab({ "POST /api/v1/auth/refresh": () => json(tokenFor(USER_1.id, 5)) });
    t.tokens.adoptCookieSession();
    render(
      <ServicesProvider services={t.services}>
        <QueryClientProvider client={t.queryClient}>
          <AuthProvider>
            <Probe />
          </AuthProvider>
        </QueryClientProvider>
      </ServicesProvider>,
    );
    expect(await screen.findByText(USER_1.email)).toBeInTheDocument();

    expect(t.tokens.rotate({ access_token: jwt(USER_2.id, 6) })).toBe(false);
    await waitFor(() => {
      expect(t.reloads).toEqual([SIGN_IN_PATH]);
    });
  });
});

describe("TokenStore identity (T-0344)", () => {
  it("reads the subject of a JWT and nothing from anything else", () => {
    expect(tokenSubject(jwt(USER_1.id, 1))).toBe(USER_1.id);
    expect(tokenSubject("access-1")).toBeNull();
    expect(tokenSubject("a.%%%.c")).toBeNull();
    expect(tokenSubject(`a.${b64url(["sub"])}.c`)).toBeNull();
    expect(tokenSubject(`a.${b64url({ sub: 7 })}.c`)).toBeNull();
    expect(tokenSubject("a..c")).toBeNull();
  });

  it("ends the session when the loaded user is not the token's subject", () => {
    const store = new TokenStore(memoryStorage());
    let foreign = 0;
    store.onForeignSession(() => (foreign += 1));
    store.set(tokenFor(USER_1.id, 1));

    expect(store.bindUser(USER_1.id)).toBe(true);
    expect(store.hasSession).toBe(true);
    expect(store.bindUser(USER_2.id)).toBe(false);
    expect([store.hasSession, store.access, foreign]).toEqual([false, null, 1]);
  });

  it("a new sign-in may be another user; a rotation may not", () => {
    const store = new TokenStore(memoryStorage());
    store.set(tokenFor(USER_1.id, 1));
    const first = store.session;

    expect(store.rotate({ access_token: jwt(USER_1.id, 2) })).toBe(true);
    expect(store.session).toBe(first);
    store.set(tokenFor(USER_2.id, 3));
    expect(store.session).toBe(first + 1);
    expect(store.rotate({ access_token: jwt(USER_2.id, 4) })).toBe(true);
    expect(store.rotate({ access_token: jwt(USER_1.id, 5) })).toBe(false);
    expect(store.access).toBeNull();
  });
});

describe("the sign-in returns to the whole deep link (TD-521)", () => {
  it("keeps the query string and the hash", async () => {
    const t = tab();
    const router = createMemoryRouter(routes, { initialEntries: ["/execution?view=open#row-9"] });
    render(
      <AppProviders services={t.services} queryClient={t.queryClient}>
        <RouterProvider router={router} />
      </AppProviders>,
    );

    fireEvent.change(await screen.findByLabelText(i18n.t("auth.login.email")), {
      target: { value: USER_1.email },
    });
    fireEvent.change(screen.getByLabelText(i18n.t("auth.login.password")), {
      target: { value: "secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: i18n.t("auth.login.submit") }));

    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/execution");
    });
    expect(router.state.location.search).toBe("?view=open");
    expect(router.state.location.hash).toBe("#row-9");
  });

  it.each([
    [undefined, "/"],
    [42, "/"],
    ["/login", "/"],
    ["/login?next=x", "/"],
    ["/login#x", "/"],
    ["//elsewhere.example/x", "/"],
    ["https://elsewhere.example/x", "/"],
    ["/admin/users?q=a#b", "/admin/users?q=a#b"],
  ])("returns %s to %s", (from, expected) => {
    expect(returnPath(from)).toBe(expected);
  });
});
