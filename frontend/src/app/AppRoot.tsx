import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { I18nextProvider } from "react-i18next";
import { createBrowserRouter, createMemoryRouter, RouterProvider } from "react-router";

import { AuthProvider } from "../auth/AuthProvider";
import { i18n } from "../i18n";
import { createQueryClient } from "./queryClient";
import { routes } from "./routes";
import { createAppServices, ServicesProvider, type AppServices } from "./services";

interface AppProvidersProps {
  children: ReactNode;
  queryClient?: QueryClient;
  services?: AppServices;
}

/** Cross-cutting providers (i18n, services, server-state cache, auth). Router is added by `AppRoot`. */
export function AppProviders({ children, queryClient, services }: AppProvidersProps) {
  const [client] = useState(() => queryClient ?? createQueryClient());
  const [appServices] = useState(() => services ?? createAppServices());
  return (
    <I18nextProvider i18n={i18n}>
      <ServicesProvider services={appServices}>
        <QueryClientProvider client={client}>
          <AuthProvider>{children}</AuthProvider>
        </QueryClientProvider>
      </ServicesProvider>
    </I18nextProvider>
  );
}

interface AppRootProps {
  /** When given, an in-memory router is used (tests); otherwise the browser history. */
  initialEntries?: string[];
  queryClient?: QueryClient;
  /** Injected services (tests); defaults to the browser services. */
  services?: AppServices;
}

export function AppRoot({ initialEntries, queryClient, services }: AppRootProps) {
  const [router] = useState(() =>
    initialEntries ? createMemoryRouter(routes, { initialEntries }) : createBrowserRouter(routes),
  );
  return (
    <AppProviders
      {...(queryClient ? { queryClient } : {})}
      {...(services ? { services } : {})}
    >
      <RouterProvider router={router} />
    </AppProviders>
  );
}
