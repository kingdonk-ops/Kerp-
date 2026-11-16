import { describe, expect, it, vi } from "vitest";

import { createApiClient } from "../lib/api/client";
import { FAKE_USER, fakeBackend, json, TEST_ORIGIN, tokens } from "../test/fakeApi";
import {
  connectCrossTab,
  REFRESH_LOCK,
  type ChannelLike,
  type CrossTabAuth,
  type LockManagerLike,
} from "./crossTab";
import { memoryStorage, TokenStore } from "./tokenStore";

/** One exclusive lock per name, granted in request order (like `navigator.locks`). */
function fakeLocks(): LockManagerLike & { names: string[] } {
  const tails = new Map<string, Promise<void>>();
  const names: string[] = [];
  return {
    names,
    request<T>(name: string, callback: () => Promise<T>): Promise<T> {
      names.push(name);
      const run = (tails.get(name) ?? Promise.resolve()).then(callback);
      tails.set(
        name,
        run.then(
          () => undefined,
          () => undefined,
        ),
      );
      return run;
    },
  };
}

/** A `BroadcastChannel` stand-in: a message reaches every *other* member of the hub. */
function fakeChannelHub() {
  const members: { listeners: ((event: MessageEvent) => void)[]; open: boolean }[] = [];
  const posted: unknown[] = [];
  const join = (): ChannelLike => {
    const me = { listeners: [] as ((event: MessageEvent) => void)[], open: true };
    members.push(me);
    return {
      postMessage(message: unknown) {
        posted.push(message);
        for (const other of members) {
          if (other === me || !other.open) continue;
          for (const listener of other.listeners) listener({ data: message } as MessageEvent);
        }
      },
      addEventListener(_type, listener) {
        me.listeners.push(listener);
      },
      close() {
        me.open = false;
      },
    };
  };
  return { join, posted };
}

interface Tab {
  api: ReturnType<typeof createApiClient>;
  tokens: TokenStore;
  crossTab: CrossTabAuth;
}

function twoTabs(meHandler?: Parameters<typeof fakeBackend>[0]) {
  const backend = fakeBackend(meHandler);
  const locks = fakeLocks();
  const hub = fakeChannelHub();
  const tab = (): Tab => {
    const store = new TokenStore(memoryStorage());
    const crossTab = connectCrossTab(store, { locks, channel: hub.join() });
    const api = createApiClient({
      baseUrl: TEST_ORIGIN,
      tokens: store,
      fetch: backend.fetch,
      crossTab,
    });
    return { api, tokens: store, crossTab };
  };
  return { a: tab(), b: tab(), calls: backend.calls, locks, hub };
}

const onlyFreshTokens = {
  "GET /api/v1/auth/me": (req: { auth: string | null }) =>
    req.auth === "Bearer access-1" ? json(FAKE_USER) : json({ detail: "Not authenticated" }, 401),
};

describe("cross-tab refresh (T-0072)", () => {
  it("lets concurrent 401s in two tabs share exactly one refresh", async () => {
    const { a, b, calls, locks } = twoTabs(onlyFreshTokens);
    a.tokens.set(tokens(0));
    b.tokens.set(tokens(0));

    const [fromA, fromB] = await Promise.all([
      a.api.GET("/api/v1/auth/me"),
      b.api.GET("/api/v1/auth/me"),
    ]);

    expect(fromA.data).toEqual(FAKE_USER);
    expect(fromB.data).toEqual(FAKE_USER);
    expect(calls.filter((c) => c.path === "/api/v1/auth/refresh")).toHaveLength(1);
    expect(locks.names.every((name) => name === REFRESH_LOCK)).toBe(true);
    // Both tabs retried with the one new access token; no refresh token was ever in a body.
    const retries = calls.filter((c) => c.path === "/api/v1/auth/me").slice(2);
    expect(retries.map((c) => c.auth)).toEqual(["Bearer access-1", "Bearer access-1"]);
    expect(calls.every((c) => c.body === null)).toBe(true);
    expect([a.tokens.access, b.tokens.access]).toEqual(["access-1", "access-1"]);
  });

  it("posts only the access token on the channel", async () => {
    const { a, b, hub } = twoTabs(onlyFreshTokens);
    a.tokens.set(tokens(0));
    b.tokens.set(tokens(0));

    await a.api.GET("/api/v1/auth/me");

    expect(hub.posted).toEqual([{ type: "token", access_token: "access-1", expires_in: 900 }]);
    expect(b.tokens.access).toBe("access-1");
  });

  it("uses a token another tab posted while it waited for the lock", async () => {
    const { a, b, calls, locks } = twoTabs(onlyFreshTokens);
    a.tokens.set(tokens(0));
    b.tokens.set(tokens(0));
    // Tab A holds the lock (refreshing); tab B gets its 401 and queues for the lock.
    let release: () => void = () => undefined;
    const holding = new Promise<void>((started) => {
      void locks.request(REFRESH_LOCK, () => {
        started();
        return new Promise<void>((r) => (release = r));
      });
    });
    await holding;
    const waiting = b.api.GET("/api/v1/auth/me");
    await vi.waitFor(() => {
      expect(locks.names).toHaveLength(2);
    });
    // A finishes its refresh: posts the new access token, then releases the lock.
    a.tokens.set({ access_token: "access-1" });
    a.crossTab.publishToken({ access_token: "access-1", expires_in: 900 });
    release();

    expect((await waiting).data).toEqual(FAKE_USER);
    expect(calls.filter((c) => c.path === "/api/v1/auth/refresh")).toHaveLength(0);
  });

  it("signs every tab out when one signs out", () => {
    const { a, b } = twoTabs();
    a.tokens.set(tokens(1));
    b.tokens.set(tokens(1));

    a.tokens.clear();
    a.crossTab.publishLogout();

    expect([b.tokens.access, b.tokens.hasSession]).toEqual([null, false]);
  });

  it("keeps a signed-out tab signed out when another tab refreshes", () => {
    const { a, b } = twoTabs();
    a.tokens.set(tokens(1));

    a.crossTab.publishToken({ access_token: "access-2", expires_in: 900 });

    expect([b.tokens.access, b.tokens.hasSession]).toEqual([null, false]);
  });

  it("ignores messages that are not its own", () => {
    const { b, hub } = twoTabs();
    b.tokens.set(tokens(1));
    const stranger = hub.join();

    stranger.postMessage({ type: "token", access_token: 42 });
    stranger.postMessage("logout");
    stranger.postMessage(null);

    expect([b.tokens.access, b.tokens.hasSession]).toEqual(["access-1", true]);
  });

  it("falls back to single-tab behaviour without locks or a channel", async () => {
    const backend = fakeBackend(onlyFreshTokens);
    const store = new TokenStore(memoryStorage());
    const crossTab = connectCrossTab(store, {});
    const api = createApiClient({ baseUrl: TEST_ORIGIN, tokens: store, fetch: backend.fetch, crossTab });
    store.set(tokens(0));

    const { data } = await api.GET("/api/v1/auth/me");

    expect(data).toEqual(FAKE_USER);
    expect(backend.calls.filter((c) => c.path === "/api/v1/auth/refresh")).toHaveLength(1);
    expect(() => {
      crossTab.publishLogout();
      crossTab.close();
    }).not.toThrow();
  });
});
