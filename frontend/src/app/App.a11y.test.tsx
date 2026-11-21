// App shell (top bar, navigation, project switcher, theme switch) and sign-in page: glove
// targets and contrast in both themes on the field breakpoints (diff §6.3, T-0046).
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { memoryStorage } from "../auth/tokenStore";
import { i18n } from "../i18n";
import { auditA11y, FIELD_VIEWPORTS, formatFindings, setViewport } from "../test/a11y";
import { FAKE_USER, json, makeServices, tokens } from "../test/fakeApi";
import { THEMES, applyTheme, themeStorageKey, DEVICE_THEME_KEY, type ThemeId } from "../theme/theme";
import { scopeStorageKey } from "../projects/ProjectScopeProvider";
import { AppRoot } from "./AppRoot";
import { createQueryClient } from "./queryClient";
import type { AppServices } from "./services";

// A portfolio persona, so the All-Projects option is audited too (synthetic).
const PROJECTS = [
  { id: "p-alpha", code: "SYN-001", name: "Synthetic Refinery Unit", personas: ["PER-01"] },
];

// A system_admin reaching a project without membership (ADR 0016, T-0276; synthetic).
const ADMIN_PROJECTS = [
  { id: "p-jetty", code: "SYN-004", name: "Synthetic Jetty Upgrade", personas: [], access: "system_admin" },
];

// Entra ID SSO offered (T-0055): the sign-in page shows "Sign in with Microsoft" first.
const SSO_CONFIG = {
  password: { available: true },
  sso: { provider: "entra", preferred: true, start_url: "/api/v1/auth/sso/entra/start" },
};

function renderApp(
  path: string,
  theme: ThemeId,
  signedIn: boolean,
  projects: unknown[] = PROJECTS,
  config: unknown = { password: { available: true } },
  storedScope: string | null = null,
) {
  const base = makeServices({
    "GET /api/v1/me/projects": () => json(projects),
    "GET /api/v1/auth/config": () => json(config),
  });
  if (signedIn) base.tokens.set(tokens(0));
  const preferences = memoryStorage();
  // The theme comes from the stored preference, exercising the real switch-on path.
  preferences.setItem(signedIn ? themeStorageKey(FAKE_USER.id) : DEVICE_THEME_KEY, theme);
  if (storedScope !== null) preferences.setItem(scopeStorageKey(FAKE_USER.id), storedScope);
  const services: AppServices = {
    api: base.api,
    tokens: base.tokens,
    preferences,
  };
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  return render(<AppRoot initialEntries={[path]} services={services} queryClient={queryClient} />);
}

describe.each(THEMES)("app shell in the %s theme", (theme) => {
  afterEach(() => {
    applyTheme("standard");
  });

  describe.each(FIELD_VIEWPORTS)("at $name", (viewport) => {
    it("the signed-in shell meets WCAG 2.1 AA and 48 px targets", async () => {
      await setViewport(viewport);
      renderApp("/", theme, true);
      await screen.findByText(i18n.t("auth.signedInAs", { name: FAKE_USER.display_name }));
      await screen.findByRole("option", { name: "SYN-001 — Synthetic Refinery Unit" });
      expect(document.documentElement.dataset.theme ?? "standard").toBe(theme);

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    });

    it("the shell with an 'Admin access' project meets WCAG 2.1 AA and 48 px targets", async () => {
      await setViewport(viewport);
      renderApp("/", theme, true, ADMIN_PROJECTS, undefined, "project:p-jetty");
      await screen.findByTestId("scope-admin-badge");

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    });

    it("the lobby of a user with no project meets WCAG 2.1 AA and 48 px targets", async () => {
      await setViewport(viewport);
      renderApp("/", theme, true, []);
      await screen.findByRole("heading", { level: 1, name: i18n.t("lobby.title") });

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    });

    it("the sign-in page meets WCAG 2.1 AA and 48 px targets", async () => {
      await setViewport(viewport);
      renderApp("/login", theme, false);
      await screen.findByRole("heading", { level: 1, name: i18n.t("auth.login.title") });

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    });

    it("the sign-in page with SSO and a refusal meets WCAG 2.1 AA and 48 px targets", async () => {
      await setViewport(viewport);
      renderApp("/login/sso-return?error=SSO_NO_ACCOUNT", theme, false, PROJECTS, SSO_CONFIG);
      // The button needs the /auth/config round-trip: more than the 1 s default under load.
      await screen.findByRole("button", { name: i18n.t("auth.sso.start") }, { timeout: 5000 });
      await screen.findByRole("alert", {}, { timeout: 5000 });

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    }, 20_000);
  });
});
