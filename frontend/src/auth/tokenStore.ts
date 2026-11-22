import type { components } from "../api/schema";

export type TokenResponse = components["schemas"]["TokenResponse"];
export type KeyValueStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** Marks that this tab signed in, so a reload resumes the session. Holds no secret. */
export const SESSION_STORAGE_KEY = "kerp.auth.session";
/** Where builds before T-0037 kept the refresh token; removed on start. */
export const LEGACY_REFRESH_STORAGE_KEY = "kerp.auth.refresh";

type Listener = () => void;

/**
 * The `sub` claim (user id) of an access token, read without verifying it: the server
 * verifies tokens, the tab only compares identities (T-0344). `null` when unreadable.
 */
export function tokenSubject(token: string): string | null {
  const payload = token.split(".")[1];
  if (payload === undefined || payload === "") return null;
  try {
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    const claims: unknown = JSON.parse(atob(padded));
    if (typeof claims !== "object" || claims === null) return null;
    const sub = (claims as Record<string, unknown>).sub;
    return typeof sub === "string" && sub !== "" ? sub : null;
  } catch {
    return null;
  }
}

/**
 * Holds the session's tokens. The 15-minute access token lives in memory only. The
 * rotating refresh token is an `httpOnly` cookie the server sets on the auth endpoints
 * (diff §4.2, TD-203): script never sees it, and nothing here stores it. `storage`
 * (`sessionStorage`) keeps only a marker that this tab has a session to resume.
 */
export class TokenStore {
  private accessToken: string | null = null;
  /** The user this tab's session belongs to (token `sub`, then `me.id`); T-0344. */
  private subject: string | null = null;
  /** Bumped whenever a new session starts or the session ends (not on a rotation). */
  private generation = 0;
  private readonly listeners = new Set<Listener>();
  private readonly foreignListeners = new Set<Listener>();

  constructor(private readonly storage: KeyValueStorage) {
    storage.removeItem(LEGACY_REFRESH_STORAGE_KEY);
  }

  get access(): string | null {
    return this.accessToken;
  }

  /** True while the tab is signed in, i.e. the session can be resumed with the cookie. */
  get hasSession(): boolean {
    return this.storage.getItem(SESSION_STORAGE_KEY) !== null;
  }

  /** Identifies the current session; changes on a new session or a sign-out, not on a rotation. */
  get session(): number {
    return this.generation;
  }

  /** Starts a new session (sign-in): the session may belong to a different user than before. */
  set(tokens: Pick<TokenResponse, "access_token">): void {
    this.accessToken = tokens.access_token;
    this.subject = tokenSubject(tokens.access_token);
    this.generation += 1;
    this.storage.setItem(SESSION_STORAGE_KEY, "1");
    this.emit();
  }

  /**
   * Takes a rotated access token of the **current** session (a refresh, or one another tab
   * posted). A token for a different user than this session's (`sub` vs the bound user) is
   * refused and ends the session as a foreign one (T-0344): it is never stored, so no request
   * carries it. A token without a readable `sub` cannot be compared and is accepted (the
   * server always sets `sub`). Returns whether the token was taken.
   */
  rotate(tokens: Pick<TokenResponse, "access_token">): boolean {
    const sub = tokenSubject(tokens.access_token);
    if (this.subject !== null && sub !== null && sub !== this.subject) {
      this.endForeignSession();
      return false;
    }
    this.subject ??= sub;
    this.accessToken = tokens.access_token;
    this.storage.setItem(SESSION_STORAGE_KEY, "1");
    this.emit();
    return true;
  }

  /**
   * Binds the session to the loaded user (`me.id`). A session already bound to a different
   * user (its token's `sub`) is ended as a foreign one. Returns whether the user matches.
   */
  bindUser(userId: string): boolean {
    if (this.subject !== null && this.subject !== userId) {
      this.endForeignSession();
      return false;
    }
    this.subject = userId;
    return true;
  }

  /** Called after a session was ended because a token belonged to a different user. */
  onForeignSession(listener: Listener): () => void {
    this.foreignListeners.add(listener);
    return () => {
      this.foreignListeners.delete(listener);
    };
  }

  /**
   * Adopts a session the server started with a redirect (Entra ID SSO callback, T-0055): the
   * `httpOnly` refresh cookie is set, but script has no access token yet. Marks the tab as
   * signed in and drops any access token of an earlier session, so the next request resumes
   * through `/auth/refresh` (single-flight, shared with the other tabs).
   */
  adoptCookieSession(): void {
    this.accessToken = null;
    this.subject = null;
    this.generation += 1;
    this.storage.setItem(SESSION_STORAGE_KEY, "1");
    this.emit();
  }

  clear(): void {
    const had = this.accessToken !== null || this.hasSession;
    this.accessToken = null;
    this.subject = null;
    this.storage.removeItem(SESSION_STORAGE_KEY);
    if (had) {
      this.generation += 1;
      this.emit();
    }
  }

  /** Signs this tab out (only this tab: the other user's session stays) and says why. */
  private endForeignSession(): void {
    this.clear();
    for (const listener of this.foreignListeners) listener();
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private emit(): void {
    for (const listener of this.listeners) listener();
  }
}

/** In-memory storage (tests, or where `sessionStorage` is unavailable). */
export function memoryStorage(): KeyValueStorage {
  const map = new Map<string, string>();
  return {
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => {
      map.set(key, value);
    },
    removeItem: (key) => {
      map.delete(key);
    },
  };
}
