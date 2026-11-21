// Shared harness of the invite and password screen tests (T-0053). Synthetic data only.
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";

import { AppProviders } from "../app/AppRoot";
import { createQueryClient } from "../app/queryClient";
import { routes } from "../app/routes";
import type { AppServices } from "../app/services";
import { memoryStorage } from "../auth/tokenStore";
import { FAKE_USER, json, makeServices, tokens } from "../test/fakeApi";
import { DEVICE_THEME_KEY, themeStorageKey, type ThemeId } from "../theme/theme";

/** A synthetic invite and setup token (`token_urlsafe` alphabet). */
export const INVITE_TOKEN = "syntheticInviteToken_0123456789-abcdefABCDEF";
export const SETUP_TOKEN = "syntheticSetupToken_9876543210-fedcbaFEDCBA";
export const JOINED = "00000000-0000-4000-8000-000000000102";
export const OTHER = "00000000-0000-4000-8000-000000000101";
/** The joined project sorts after another, so only the remembered scope opens it. */
export const MY_PROJECTS = [
  { id: OTHER, code: "SYN-001", name: "Alpha Synthetic Unit", personas: ["PER-14"] },
  { id: JOINED, code: "SYN-002", name: "Beta Synthetic Tank Farm", personas: ["PER-14"] },
];
export const JOINED_LABEL = "SYN-002 — Beta Synthetic Tank Farm";

/** The whole app at `path` with an inspectable memory router and storages. */
export function renderAt(
  path: string,
  handlers: Parameters<typeof makeServices>[0] = {},
  { signedIn = false, theme = "standard" }: { signedIn?: boolean; theme?: ThemeId } = {},
) {
  const base = makeServices({ "GET /api/v1/me/projects": () => json(MY_PROJECTS), ...handlers });
  if (signedIn) base.tokens.set(tokens(0));
  const preferences = memoryStorage();
  preferences.setItem(DEVICE_THEME_KEY, theme);
  preferences.setItem(themeStorageKey(FAKE_USER.id), theme);
  const session = memoryStorage();
  const services: AppServices = { api: base.api, tokens: base.tokens, preferences, session };
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <AppProviders services={services} queryClient={queryClient}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return { calls: base.calls, router, preferences, session };
}
