import { createContext, useContext, type ReactNode } from "react";

import { memoryStorage, TokenStore, type KeyValueStorage } from "../auth/tokenStore";
import { createApiClient, type ApiClient } from "../lib/api/client";
import { pendingProjectDirectory, type ProjectDirectory } from "../projects/directory";

/** App-wide service singletons, injectable so tests can supply fakes. */
export interface AppServices {
  api: ApiClient;
  tokens: TokenStore;
  projects: ProjectDirectory;
  /** Persists UI preferences such as the selected project scope. */
  preferences: KeyValueStorage;
}

function browserStorage(kind: "localStorage" | "sessionStorage"): KeyValueStorage {
  try {
    return window[kind];
  } catch {
    return memoryStorage();
  }
}

export function createAppServices(): AppServices {
  const tokens = new TokenStore(browserStorage("sessionStorage"));
  const env = import.meta.env as Record<string, string | undefined>;
  const baseUrl = env["VITE_API_BASE_URL"] ?? window.location.origin;
  return {
    api: createApiClient({ baseUrl, tokens }),
    tokens,
    projects: pendingProjectDirectory,
    preferences: browserStorage("localStorage"),
  };
}

const ServicesContext = createContext<AppServices | null>(null);

export function ServicesProvider({
  services,
  children,
}: {
  services: AppServices;
  children: ReactNode;
}) {
  return <ServicesContext.Provider value={services}>{children}</ServicesContext.Provider>;
}

export function useServices(): AppServices {
  const services = useContext(ServicesContext);
  if (!services) throw new Error("useServices must be used inside <ServicesProvider>");
  return services;
}
