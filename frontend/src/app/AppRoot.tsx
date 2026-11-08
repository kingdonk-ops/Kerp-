import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { I18nextProvider } from "react-i18next";
import { createBrowserRouter, createMemoryRouter, RouterProvider } from "react-router";

import { i18n } from "../i18n";
import { createQueryClient } from "./queryClient";
import { routes } from "./routes";

interface AppProvidersProps {
  children: ReactNode;
  queryClient?: QueryClient;
}

/** Cross-cutting providers (i18n, server-state cache). Router is added by `AppRoot`. */
export function AppProviders({ children, queryClient }: AppProvidersProps) {
  const [client] = useState(() => queryClient ?? createQueryClient());
  return (
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </I18nextProvider>
  );
}

interface AppRootProps {
  /** When given, an in-memory router is used (tests); otherwise the browser history. */
  initialEntries?: string[];
  queryClient?: QueryClient;
}

export function AppRoot({ initialEntries, queryClient }: AppRootProps) {
  const [router] = useState(() =>
    initialEntries ? createMemoryRouter(routes, { initialEntries }) : createBrowserRouter(routes),
  );
  return (
    <AppProviders {...(queryClient ? { queryClient } : {})}>
      <RouterProvider router={router} />
    </AppProviders>
  );
}
