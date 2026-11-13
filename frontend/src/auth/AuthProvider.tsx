import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useSyncExternalStore } from "react";
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

interface AuthContextValue {
  status: AuthStatus;
  user: CurrentUser | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  retry: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const ME_QUERY_KEY = ["auth", "me"] as const;

export function AuthProvider({ children }: { children: ReactNode }) {
  const { api, tokens } = useServices();
  const queryClient = useQueryClient();
  const hasSession = useSyncExternalStore(
    (onChange) => tokens.subscribe(onChange),
    () => tokens.hasSession,
  );

  const me = useQuery({
    queryKey: ME_QUERY_KEY,
    enabled: hasSession,
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/auth/me");
      if (!data) throw new Error(`me: ${String(response.status)}`);
      return data;
    },
  });

  const login = useCallback(
    async (email: string, password: string) => {
      const { data, response } = await api.POST("/api/v1/auth/login", {
        body: { email, password },
      });
      if (!data) {
        throw new LoginError(
          response.status === 401 ? "invalid" : response.status === 429 ? "locked" : "failed",
        );
      }
      queryClient.removeQueries({ queryKey: ME_QUERY_KEY });
      tokens.set(data);
    },
    [api, queryClient, tokens],
  );

  const logout = useCallback(async () => {
    try {
      await api.POST("/api/v1/auth/logout");
    } catch {
      // Signing out locally must not depend on the server being reachable.
    }
    tokens.clear();
    queryClient.clear();
  }, [api, queryClient, tokens]);

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
      logout,
      retry: () => void me.refetch(),
    };
  }, [hasSession, me, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth must be used inside <AuthProvider>");
  return auth;
}
