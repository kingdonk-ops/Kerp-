import type { components } from "../api/schema";

export type TokenResponse = components["schemas"]["TokenResponse"];
export type KeyValueStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export const REFRESH_STORAGE_KEY = "kerp.auth.refresh";

type Listener = () => void;

/**
 * Holds the session's tokens. The 15-minute access token lives in memory only; the
 * rotating refresh token is kept in `sessionStorage` (per tab, gone when the tab closes)
 * so a reload resumes the session. Refresh-token transport (body vs. httpOnly cookie) is
 * open in TD-121.
 */
export class TokenStore {
  private accessToken: string | null = null;
  private readonly listeners = new Set<Listener>();

  constructor(private readonly storage: KeyValueStorage) {}

  get access(): string | null {
    return this.accessToken;
  }

  get refresh(): string | null {
    return this.storage.getItem(REFRESH_STORAGE_KEY);
  }

  /** True while a refresh token is held, i.e. the session can be resumed. */
  get hasSession(): boolean {
    return this.refresh !== null;
  }

  set(tokens: Pick<TokenResponse, "access_token" | "refresh_token">): void {
    this.accessToken = tokens.access_token;
    this.storage.setItem(REFRESH_STORAGE_KEY, tokens.refresh_token);
    this.emit();
  }

  clear(): void {
    const had = this.accessToken !== null || this.hasSession;
    this.accessToken = null;
    this.storage.removeItem(REFRESH_STORAGE_KEY);
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
