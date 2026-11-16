import type { TokenResponse, TokenStore } from "./tokenStore";

/** Web Lock every tab of the origin takes around `/auth/refresh` (T-0072). */
export const REFRESH_LOCK = "kerp-refresh";
/** Channel the tabs of the origin share for the new access token and for sign-out. */
export const AUTH_CHANNEL = "kerp-auth";

/** The part of `navigator.locks` used here (injectable for tests). */
export interface LockManagerLike {
  request<T>(name: string, callback: () => Promise<T>): Promise<T>;
}

/** The part of `BroadcastChannel` used here (injectable for tests). */
export interface ChannelLike {
  postMessage(message: unknown): void;
  addEventListener(type: "message", listener: (event: MessageEvent) => void): void;
  close(): void;
}

type AuthMessage =
  | { type: "token"; access_token: string; expires_in: number }
  | { type: "logout" };

/**
 * Keeps the tabs of one browser from signing each other out (T-0072).
 *
 * The rotating refresh token is the `httpOnly` cookie every tab shares (TD-203). Two tabs
 * refreshing at once would both present the same cookie, and the second request is refresh-token
 * reuse: the server revokes the whole session. So `/auth/refresh` runs under one Web Lock
 * for the origin, and the tab that refreshed posts the new **access** token (never the refresh
 * token) on a `BroadcastChannel`; other tabs adopt it instead of refreshing. A message that
 * arrives only after the lock is released costs at most one extra refresh, and that one is
 * a valid rotation (the cookie was already rotated), never a reuse. Sign-out is broadcast too.
 *
 * Without `navigator.locks` / `BroadcastChannel` everything runs as in a single tab.
 */
export interface CrossTabAuth {
  /** Run `refresh` while holding the cross-tab refresh lock (directly if unsupported). */
  withRefreshLock<T>(refresh: () => Promise<T>): Promise<T>;
  /** Tell the other tabs about a new access token. */
  publishToken(token: Pick<TokenResponse, "access_token" | "expires_in">): void;
  /** Tell the other tabs this session was signed out. */
  publishLogout(): void;
  close(): void;
}

/** Single-tab behaviour: no lock, nothing shared. */
export const singleTab: CrossTabAuth = {
  withRefreshLock: (refresh) => refresh(),
  publishToken: () => undefined,
  publishLogout: () => undefined,
  close: () => undefined,
};

function isAuthMessage(data: unknown): data is AuthMessage {
  if (typeof data !== "object" || data === null) return false;
  const message = data as Record<string, unknown>;
  if (message.type === "logout") return true;
  return (
    message.type === "token" &&
    typeof message.access_token === "string" &&
    typeof message.expires_in === "number"
  );
}

/**
 * Wire `tokens` to the other tabs. A token another tab posts is adopted only while this tab
 * has a session (a signed-out tab stays signed out); a sign-out another tab posts drops this
 * tab's access token and session marker. Adopted state is never re-posted (no echo loop).
 */
export function connectCrossTab(
  tokens: TokenStore,
  { locks, channel }: { locks?: LockManagerLike | null; channel?: ChannelLike | null },
): CrossTabAuth {
  channel?.addEventListener("message", (event) => {
    const data: unknown = event.data;
    if (!isAuthMessage(data)) return;
    if (data.type === "logout") tokens.clear();
    else if (tokens.hasSession) tokens.set({ access_token: data.access_token });
  });
  return {
    withRefreshLock: (refresh) => (locks ? locks.request(REFRESH_LOCK, refresh) : refresh()),
    publishToken: ({ access_token, expires_in }) => {
      const message: AuthMessage = { type: "token", access_token, expires_in };
      channel?.postMessage(message);
    },
    publishLogout: () => {
      const message: AuthMessage = { type: "logout" };
      channel?.postMessage(message);
    },
    close: () => channel?.close(),
  };
}

/** The browser's lock manager and channel, where available. */
export function browserCrossTab(tokens: TokenStore): CrossTabAuth {
  let locks: LockManagerLike | null = null;
  let channel: ChannelLike | null = null;
  try {
    if (typeof navigator !== "undefined" && "locks" in navigator) {
      const manager = navigator.locks;
      locks = {
        // The DOM typing wraps the callback's promise once more; the runtime value is the
        // callback's own result.
        request: <T>(name: string, callback: () => Promise<T>) =>
          manager.request(name, callback) as unknown as Promise<T>,
      };
    }
    if (typeof BroadcastChannel !== "undefined") channel = new BroadcastChannel(AUTH_CHANNEL);
  } catch {
    // An unsupported or blocked API: single-tab behaviour for that part.
  }
  return connectCrossTab(tokens, { locks, channel });
}
