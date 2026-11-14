// App shell (top bar, navigation, project switcher, theme switch) and sign-in page: glove
// targets and contrast in both themes on the field breakpoints (diff §6.3, T-0046).
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { memoryStorage } from "../auth/tokenStore";
import { i18n } from "../i18n";
import { staticProjectDirectory } from "../projects/directory";
import { auditA11y, FIELD_VIEWPORTS, formatFindings, setViewport } from "../test/a11y";
import { FAKE_USER, makeServices, tokens } from "../test/fakeApi";
import { THEMES, applyTheme, themeStorageKey, DEVICE_THEME_KEY, type ThemeId } from "../theme/theme";
import { AppRoot } from "./AppRoot";
import { createQueryClient } from "./queryClient";
import type { AppServices } from "./services";

function renderApp(path: string, theme: ThemeId, signedIn: boolean) {
  const base = makeServices();
  if (signedIn) base.tokens.set(tokens(0));
  const preferences = memoryStorage();
  // The theme comes from the stored preference, exercising the real switch-on path.
  preferences.setItem(signedIn ? themeStorageKey(FAKE_USER.id) : DEVICE_THEME_KEY, theme);
  const services: AppServices = {
    api: base.api,
    tokens: base.tokens,
    projects: staticProjectDirectory([{ id: "p-alpha", code: "SYN-001", name: "Synthetic Refinery Unit" }]),
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

    it("the sign-in page meets WCAG 2.1 AA and 48 px targets", async () => {
      await setViewport(viewport);
      renderApp("/login", theme, false);
      await screen.findByRole("heading", { level: 1, name: i18n.t("auth.login.title") });

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    });
  });
});
