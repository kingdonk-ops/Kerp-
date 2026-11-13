import { describe, expect, it } from "vitest";

import {
  LEGACY_REFRESH_STORAGE_KEY,
  memoryStorage,
  SESSION_STORAGE_KEY,
  TokenStore,
} from "../../auth/tokenStore";
import { FAKE_USER, json, makeServices, tokens } from "../../test/fakeApi";

describe("createApiClient", () => {
  it("sends typed requests to the generated paths with the bearer token", async () => {
    const { api, tokens: store, calls } = makeServices();
    store.set(tokens(7));

    const { data } = await api.GET("/api/v1/auth/me");

    expect(data).toEqual(FAKE_USER);
    expect(calls).toEqual([
      { method: "GET", path: "/api/v1/auth/me", auth: "Bearer access-7", body: null },
    ]);
  });

  it("rotates the refresh token once on 401 and retries the request", async () => {
    let first = true;
    const { api, tokens: store, calls } = makeServices({
      "GET /api/v1/auth/me": (req) => {
        if (first) {
          first = false;
          return json({ detail: "Not authenticated" }, 401);
        }
        return req.auth === "Bearer access-1" ? json(FAKE_USER) : json({}, 401);
      },
    });
    store.set(tokens(0));

    const { data } = await api.GET("/api/v1/auth/me");

    expect(data).toEqual(FAKE_USER);
    expect(calls.map((c) => c.path)).toEqual([
      "/api/v1/auth/me",
      "/api/v1/auth/refresh",
      "/api/v1/auth/me",
    ]);
    // The refresh token is the httpOnly cookie: the browser sends it, never the script.
    expect(calls[1]?.body).toBeNull();
    expect(store.access).toBe("access-1");
  });

  it("shares one refresh between concurrent 401s", async () => {
    const { api, tokens: store, calls } = makeServices({
      "GET /api/v1/auth/me": (req) =>
        req.auth === "Bearer access-1" ? json(FAKE_USER) : json({}, 401),
    });
    store.set(tokens(0));

    await Promise.all([api.GET("/api/v1/auth/me"), api.GET("/api/v1/auth/me")]);

    expect(calls.filter((c) => c.path === "/api/v1/auth/refresh")).toHaveLength(1);
  });

  it("resumes a stored session by refreshing before the first request", async () => {
    // After a reload only the session marker survives (the access token is memory-only);
    // the refresh cookie does the rest.
    const storage = memoryStorage();
    storage.setItem(SESSION_STORAGE_KEY, "1");
    const { api, calls } = makeServices({}, storage);

    const { data } = await api.GET("/api/v1/auth/me");

    expect(data).toEqual(FAKE_USER);
    expect(calls.map((c) => `${c.path} ${c.auth ?? "-"}`)).toEqual([
      "/api/v1/auth/refresh -",
      "/api/v1/auth/me Bearer access-1",
    ]);
  });

  it("keeps the session when the refresh fails transiently (503) and refreshes later", async () => {
    let refreshUp = false;
    const { api, tokens: store, calls } = makeServices({
      "GET /api/v1/auth/me": (req) =>
        req.auth === "Bearer access-1" ? json(FAKE_USER) : json({ detail: "expired" }, 401),
      "POST /api/v1/auth/refresh": () =>
        refreshUp ? json(tokens(1)) : json({ detail: "Service Unavailable" }, 503),
    });
    store.set(tokens(0));

    const first = await api.GET("/api/v1/auth/me");

    expect(first.response.status).toBe(401);
    expect(store.hasSession).toBe(true);
    expect(store.access).toBe("access-0");

    refreshUp = true;
    const second = await api.GET("/api/v1/auth/me");

    expect(second.data).toEqual(FAKE_USER);
    expect(calls.filter((c) => c.path === "/api/v1/auth/refresh")).toHaveLength(2);
    expect(store.access).toBe("access-1");
  });

  it("retries without a second refresh when another request rotated the tokens meanwhile", async () => {
    let enteredB!: () => void;
    const bEntered = new Promise<void>((resolve) => (enteredB = resolve));
    let releaseB!: () => void;
    const bGate = new Promise<void>((resolve) => (releaseB = resolve));
    let staleCalls = 0;
    const { api, tokens: store, calls } = makeServices({
      "GET /api/v1/auth/me": async (req) => {
        if (req.auth === "Bearer access-1") return json(FAKE_USER);
        staleCalls += 1;
        if (staleCalls === 1) {
          // Request B: in flight with access-0 until A has refreshed.
          enteredB();
          await bGate;
        }
        return json({ detail: "expired" }, 401);
      },
    });
    store.set(tokens(0));

    const b = api.GET("/api/v1/auth/me");
    await bEntered;
    const a = await api.GET("/api/v1/auth/me");
    expect(a.data).toEqual(FAKE_USER);
    expect(store.access).toBe("access-1");

    releaseB();
    const bResult = await b;

    expect(bResult.data).toEqual(FAKE_USER);
    expect(calls.filter((c) => c.path === "/api/v1/auth/refresh")).toHaveLength(1);
    expect(calls.map((c) => `${c.path} ${c.auth ?? "-"}`)).toEqual([
      "/api/v1/auth/me Bearer access-0",
      "/api/v1/auth/me Bearer access-0",
      "/api/v1/auth/refresh -",
      "/api/v1/auth/me Bearer access-1",
      "/api/v1/auth/me Bearer access-1",
    ]);
  });

  it("clears the session when the refresh token is refused", async () => {
    const { api, tokens: store } = makeServices({
      "GET /api/v1/auth/me": () => json({}, 401),
      "POST /api/v1/auth/refresh": () => json({ detail: "Not authenticated" }, 401),
    });
    store.set(tokens(0));
    let signedOut = false;
    store.subscribe(() => {
      signedOut = !store.hasSession;
    });

    const { response } = await api.GET("/api/v1/auth/me");

    expect(response.status).toBe(401);
    expect(store.hasSession).toBe(false);
    expect(store.access).toBeNull();
    expect(signedOut).toBe(true);
  });

  it("does not attempt a refresh without a session", async () => {
    const { api, calls } = makeServices();

    const { response } = await api.GET("/api/v1/auth/me");

    expect(response.status).toBe(401);
    expect(calls.map((c) => c.path)).toEqual(["/api/v1/auth/me"]);
  });
});

describe("TokenStore", () => {
  it("never puts a refresh token in storage, only a session marker", () => {
    const storage = memoryStorage();
    const store = new TokenStore(storage);

    store.set({ ...tokens(3), refresh_token: "must-not-be-kept" } as ReturnType<typeof tokens>);

    expect(store.access).toBe("access-3");
    expect(storage.getItem(SESSION_STORAGE_KEY)).toBe("1");
    expect(storage.getItem(LEGACY_REFRESH_STORAGE_KEY)).toBeNull();
    store.clear();
    expect(storage.getItem(SESSION_STORAGE_KEY)).toBeNull();
    expect(store.hasSession).toBe(false);
  });

  it("drops a refresh token stored by an earlier build", () => {
    const storage = memoryStorage();
    storage.setItem(LEGACY_REFRESH_STORAGE_KEY, "old-refresh");

    const store = new TokenStore(storage);

    expect(storage.getItem(LEGACY_REFRESH_STORAGE_KEY)).toBeNull();
    expect(store.hasSession).toBe(false);
  });
});
