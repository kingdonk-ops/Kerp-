import type { components } from "../api/schema";

export type TokenResponse = components["schemas"]["TokenResponse"];
export type KeyValueStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** Marks that this tab signed in, so a reload resumes the session. Holds no secret. */
export const SESSION_STORAGE_KEY = "kerp.auth.session";
/** Where builds before T-0037 kept the refresh token; removed on start. */
export const LEGACY_REFRESH_STORAGE_KEY = "kerp.auth.refresh";

type Listener = () => void;

/**
 * Holds the session's tokens. The 15-minute access token lives in memory only. The
 * rotating refresh token is an `httpOnly` cookie the server sets on the auth endpoints
 * (diff §4.2, TD-203): script never sees it, and nothing here stores it. `storage`
 * (`sessionStorage`) keeps only a marker that this tab has a session to resume.
 */
export class TokenStore {
  private accessToken: string | null = null;
  private readonly listeners = new Set<Listener>();

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

  set(tokens: Pick<TokenResponse, "access_token">): void {
    this.accessToken = tokens.access_token;
    this.storage.setItem(SESSION_STORAGE_KEY, "1");
    this.emit();
  }

  clear(): void {
    const had = this.accessToken !== null || this.hasSession;
    this.accessToken = null;
    this.storage.removeItem(SESSION_STORAGE_KEY);
    if (had) this.emit();
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
