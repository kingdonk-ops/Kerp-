// Shared harness of the Admin screen tests (T-0051). Synthetic data only.
import { render } from "@testing-library/react";

import { AppRoot } from "../app/AppRoot";
import { createQueryClient } from "../app/queryClient";
import type { AppServices } from "../app/services";
import { memoryStorage } from "../auth/tokenStore";
import type { ProjectSummary } from "../projects/directory";
import { FAKE_USER, json, makeServices, tokens } from "../test/fakeApi";
import { themeStorageKey, type ThemeId } from "../theme/theme";

export const ADMIN_PROJECT: ProjectSummary = {
  id: "00000000-0000-4000-8000-000000000101",
  code: "SYN-001",
  name: "Synthetic Refinery Unit",
  personas: ["PER-09"],
};

/** Signed-in app at `path`; a PER-09 on one project unless told otherwise. */
export function renderAdmin(
  path: string,
  handlers: Parameters<typeof makeServices>[0] = {},
  {
    projects = [ADMIN_PROJECT],
    systemAdmin = false,
    theme = "standard",
  }: { projects?: ProjectSummary[]; systemAdmin?: boolean; theme?: ThemeId } = {},
) {
  const base = makeServices({
    "GET /api/v1/me/projects": () => json(projects),
    // `GET /admin/grants` answers only System admins (the client's probe, TD-393).
    "GET /api/v1/admin/grants": () =>
      systemAdmin ? json([]) : json({ detail: "Forbidden" }, 403),
    ...handlers,
  });
  base.tokens.set(tokens(0));
  const preferences = memoryStorage();
  preferences.setItem(themeStorageKey(FAKE_USER.id), theme);
  const services: AppServices = { api: base.api, tokens: base.tokens, preferences };
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  render(<AppRoot initialEntries={[path]} services={services} queryClient={queryClient} />);
  return base.calls;
}
