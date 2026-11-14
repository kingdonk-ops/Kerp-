import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppRoot } from "../app/AppRoot";
import { createQueryClient } from "../app/queryClient";
import type { AppServices } from "../app/services";
import { memoryStorage, type KeyValueStorage } from "../auth/tokenStore";
import { i18n } from "../i18n";
import { staticProjectDirectory } from "../projects/directory";
import { FAKE_USER, json, makeServices, tokens } from "../test/fakeApi";
import { DEVICE_THEME_KEY, THEME_ATTRIBUTE, applyTheme, decodeTheme, themeStorageKey } from "./theme";

const OTHER_USER = { ...FAKE_USER, id: "00000000-0000-4000-8000-000000000002", display_name: "Other Inspector" };

function renderApp(
  path: string,
  { signedIn = true, preferences = memoryStorage(), user = FAKE_USER }: {
    signedIn?: boolean;
    preferences?: KeyValueStorage;
    user?: typeof FAKE_USER;
  } = {},
) {
  const base = makeServices({ "GET /api/v1/auth/me": () => json(user) });
  if (signedIn) base.tokens.set(tokens(0));
  const services: AppServices = {
    api: base.api,
    tokens: base.tokens,
    projects: staticProjectDirectory([]),
    preferences,
  };
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  return render(<AppRoot initialEntries={[path]} services={services} queryClient={queryClient} />);
}

const html = () => document.documentElement;

async function outdoorToggle() {
  return screen.findByRole("button", { name: i18n.t("theme.outdoor") });
}

describe("theme helpers", () => {
  it("keys the preference per user", () => {
    expect(themeStorageKey("u-1")).toBe("kerp.theme.u-1");
    expect(themeStorageKey("u-1")).not.toBe(themeStorageKey("u-2"));
  });

  it("decodes only known themes", () => {
    expect(decodeTheme("outdoor")).toBe("outdoor");
    expect(decodeTheme("standard")).toBe("standard");
    expect(decodeTheme("neon")).toBeNull();
    expect(decodeTheme(null)).toBeNull();
  });

  it("applies the outdoor theme as a root attribute and removes it for the standard theme", () => {
    applyTheme("outdoor");
    expect(html().getAttribute(THEME_ATTRIBUTE)).toBe("outdoor");
    applyTheme("standard");
    expect(html().hasAttribute(THEME_ATTRIBUTE)).toBe(false);
  });
});

describe("outdoor high-contrast theme", () => {
  it("starts in the standard theme with the toggle off", async () => {
    renderApp("/");
    const toggle = await outdoorToggle();
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(html().hasAttribute(THEME_ATTRIBUTE)).toBe(false);
  });

  it("switches on from the top bar and remembers the choice for the user", async () => {
    const preferences = memoryStorage();
    const { unmount } = renderApp("/", { preferences });

    fireEvent.click(await outdoorToggle());

    expect(await outdoorToggle()).toHaveAttribute("aria-pressed", "true");
    expect(html().getAttribute(THEME_ATTRIBUTE)).toBe("outdoor");
    expect(preferences.getItem(themeStorageKey(FAKE_USER.id))).toBe("outdoor");

    unmount();
    expect(html().hasAttribute(THEME_ATTRIBUTE)).toBe(false);

    // Next visit (reload) by the same user restores it.
    renderApp("/", { preferences });
    expect(await outdoorToggle()).toHaveAttribute("aria-pressed", "true");
    expect(html().getAttribute(THEME_ATTRIBUTE)).toBe("outdoor");
  });

  it("switches back off", async () => {
    const preferences = memoryStorage();
    preferences.setItem(themeStorageKey(FAKE_USER.id), "outdoor");
    renderApp("/", { preferences });

    fireEvent.click(await outdoorToggle());

    await waitFor(() => {
      expect(html().hasAttribute(THEME_ATTRIBUTE)).toBe(false);
    });
    expect(preferences.getItem(themeStorageKey(FAKE_USER.id))).toBe("standard");
  });

  it("keeps each user's choice separate", async () => {
    const preferences = memoryStorage();
    preferences.setItem(themeStorageKey(FAKE_USER.id), "outdoor");
    renderApp("/", { preferences, user: OTHER_USER });

    expect(await outdoorToggle()).toHaveAttribute("aria-pressed", "false");
    expect(html().hasAttribute(THEME_ATTRIBUTE)).toBe(false);
  });

  it("ignores an unknown stored value", async () => {
    const preferences = memoryStorage();
    preferences.setItem(themeStorageKey(FAKE_USER.id), "neon");
    renderApp("/", { preferences });

    expect(await outdoorToggle()).toHaveAttribute("aria-pressed", "false");
  });

  it("is available on the sign-in page and remembered for this device", async () => {
    const preferences = memoryStorage();
    renderApp("/", { signedIn: false, preferences });

    await screen.findByRole("heading", { level: 1, name: i18n.t("auth.login.title") });
    fireEvent.click(await outdoorToggle());

    expect(html().getAttribute(THEME_ATTRIBUTE)).toBe("outdoor");
    expect(preferences.getItem(DEVICE_THEME_KEY)).toBe("outdoor");
  });

  it("uses the device's last theme on the sign-in page after a user chose one", async () => {
    const preferences = memoryStorage();
    preferences.setItem(themeStorageKey(FAKE_USER.id), "standard");
    const { unmount } = renderApp("/", { preferences });
    fireEvent.click(await outdoorToggle());
    expect(preferences.getItem(DEVICE_THEME_KEY)).toBe("outdoor");
    unmount();

    renderApp("/", { signedIn: false, preferences });
    await screen.findByRole("heading", { level: 1, name: i18n.t("auth.login.title") });
    expect(html().getAttribute(THEME_ATTRIBUTE)).toBe("outdoor");
  });
});
