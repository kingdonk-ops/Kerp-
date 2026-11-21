import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
} from "react";
import type { ReactNode } from "react";

import type { components } from "../api/schema";
import { useServices } from "../app/services";

export type CurrentUser = components["schemas"]["MeResponse"];

export type AuthStatus = "loading" | "anonymous" | "authenticated" | "error";

/** Why a sign-in failed; maps to a translated message on the login page. */
export type LoginFailure = "invalid" | "locked" | "failed";

export class LoginError extends Error {
  constructor(readonly reason: LoginFailure) {
    super(reason);
  }
}

type TokenResponse = components["schemas"]["TokenResponse"];

/** What a sign-in that carried an `invite_token` did with it (T-0052, T-0053). */
export interface SignInResult {
  joinedProjectId: string | null;
  inviteError: string | null;
}

interface AuthContextValue {
  status: AuthStatus;
  user: CurrentUser | null;
  login: (email: string, password: string, inviteToken?: string) => Promise<SignInResult>;
  /** Starts the session of a token response another endpoint issued (accept-with-password). */
  startSession: (tokens: TokenResponse) => void;
  /** Takes over the session the SSO callback set as a cookie (T-0055); see `TokenStore`. */
  adoptCookieSession: () => void;
  logout: () => Promise<void>;
  retry: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const ME_QUERY_KEY = ["auth", "me"] as const;

/** Where a tab goes (full page load) when its session turned out to be another user's. */
export const SIGN_IN_PATH = "/login";

/**
 * Session isolation (T-0344, audit items 4 and 5): the server-state cache belongs to one
 * session. It is cleared whenever the tab goes from signed in to signed out, whatever the
 * cause (sign-out here or in another tab, a refused refresh / session expiry), and when a
 * new session starts. A refreshed or broadcast token for another user than the loaded one
 * (`TokenStore.rotate` / `bindUser`) signs the tab out and reloads it on the sign-in route.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const { api, tokens, crossTab, reloadTo } = useServices();
  const queryClient = useQueryClient();
  const subscribe = useCallback((onChange: () => void) => tokens.subscribe(onChange), [tokens]);
  const hasSession = useSyncExternalStore(subscribe, () => tokens.hasSession);
  // A new session (or the end of one) re-renders and re-keys `me`, even when the tab was
  // signed in before and after.
  const session = useSyncExternalStore(subscribe, () => tokens.session);

  useEffect(() => {
    let signedIn = tokens.hasSession;
    return tokens.subscribe(() => {
      const now = tokens.hasSession;
      if (signedIn && !now) queryClient.clear();
      signedIn = now;
    });
  }, [queryClient, tokens]);

  useEffect(
    () =>
      tokens.onForeignSession(() => {
        // The store has already cleared the session (so the cache too); drop every in-memory
        // trace of the previous user with a full load of the sign-in route.
        queryClient.clear();
        reloadTo?.(SIGN_IN_PATH);
      }),
    [queryClient, reloadTo, tokens],
  );

  const me = useQuery({
    queryKey: [...ME_QUERY_KEY, session],
    enabled: hasSession,
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/auth/me");
      if (!data) throw new Error(`me: ${String(response.status)}`);
      return data;
    },
  });

  const loadedUserId = me.data?.id;
  useEffect(() => {
    // The session belongs to the user `me` returned; a token for anyone else is refused.
    if (hasSession && loadedUserId !== undefined) tokens.bindUser(loadedUserId);
  }, [hasSession, loadedUserId, tokens]);

  const startSession = useCallback(
    (data: TokenResponse) => {
      // Nothing of an earlier session (possibly another user's) survives a new sign-in.
      queryClient.clear();
      tokens.set(data);
    },
    [queryClient, tokens],
  );

  const login = useCallback(
    async (email: string, password: string, inviteToken?: string) => {
      const { data, response } = await api.POST("/api/v1/auth/login", {
        body: inviteToken ? { email, password, invite_token: inviteToken } : { email, password },
      });
      if (!data) {
        throw new LoginError(
          response.status === 401 ? "invalid" : response.status === 429 ? "locked" : "failed",
        );
      }
      startSession(data);
      return {
        joinedProjectId: data.joined_project_id ?? null,
        inviteError: data.invite_error ?? null,
      };
    },
    [api, startSession],
  );

  const adoptCookieSession = useCallback(() => {
    // The adopted session may be another user's: nothing of the earlier one survives, and
    // `me` is loaded afresh (its key carries the new session).
    queryClient.clear();
    tokens.adoptCookieSession();
  }, [queryClient, tokens]);

  const logout = useCallback(async () => {
    try {
      await api.POST("/api/v1/auth/logout");
    } catch {
      // Signing out locally must not depend on the server being reachable.
    }
    tokens.clear();
    crossTab?.publishLogout(); // every other tab drops its access token too (T-0072)
    queryClient.clear();
  }, [api, crossTab, queryClient, tokens]);

  const value = useMemo<AuthContextValue>(() => {
    let status: AuthStatus;
    if (!hasSession) status = "anonymous";
    else if (me.data) status = "authenticated";
    else if (me.isError) status = "error";
    else status = "loading";
    return {
      status,
      user: hasSession ? (me.data ?? null) : null,
      login,
      startSession,
      adoptCookieSession,
      logout,
      retry: () => void me.refetch(),
    };
  }, [hasSession, me, login, startSession, adoptCookieSession, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth must be used inside <AuthProvider>");
  return auth;
}
