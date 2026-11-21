// Sign-in screen with Entra ID SSO (T-0055, diff §4.5): SSO promoted when the server offers
// it, password kept for everyone, refusals translated. Synthetic fixtures only.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../app/queryClient";
import { AppRoot } from "../app/AppRoot";
import type { AppServices } from "../app/services";
import { SSO_ERROR_CODES } from "../auth/sso";
import { memoryStorage } from "../auth/tokenStore";
import { i18n } from "../i18n";
import { json, makeServices } from "../test/fakeApi";

const START_URL = "/api/v1/auth/sso/entra/start";
const SSO_ON = { password: { available: true }, sso: { provider: "entra", preferred: true, start_url: START_URL } };
const SSO_OFF = { password: { available: true }, sso: null };
const PROJECTS = [
  { id: "p-alpha", code: "SYN-001", name: "Synthetic Refinery Unit", personas: ["PER-02"] },
];

function renderLogin(config: unknown, path = "/login", status = 200) {
  const base = makeServices({
    "GET /api/v1/auth/config": () => json(config, status),
    "GET /api/v1/me/projects": () => json(PROJECTS),
  });
  const services: AppServices = { api: base.api, tokens: base.tokens, preferences: memoryStorage() };
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  render(<AppRoot initialEntries={[path]} services={services} queryClient={queryClient} />);
  return { calls: base.calls };
}

const ssoButton = () => screen.findByRole("button", { name: i18n.t("auth.sso.start") });
const passwordSubmit = () => screen.getByRole("button", { name: i18n.t("auth.login.submit") });

afterEach(() => {
  vi.restoreAllMocks();
});

describe("sign-in screen with SSO enabled", () => {
  it("offers Sign in with Microsoft first, as the primary, focused action with a staff hint", async () => {
    const { calls } = renderLogin(SSO_ON);
    const button = await ssoButton();

    await waitFor(() => {
      expect(button).toHaveFocus();
    });
    expect(screen.getByText(i18n.t("auth.sso.hint"))).toBeInTheDocument();
    // First in the document, ahead of the password form, and the visually primary one.
    const buttons = screen.getAllByRole("button");
    expect(buttons.indexOf(button)).toBeLessThan(buttons.indexOf(passwordSubmit()));
    expect(button).toHaveAttribute("data-variant", "primary");
    expect(passwordSubmit()).toHaveAttribute("data-variant", "secondary");
    expect(calls.some((c) => c.method === "GET" && c.path === "/api/v1/auth/config")).toBe(true);
  });

  it("starts the SSO flow by a top-level navigation to the server's start URL", async () => {
    renderLogin(SSO_ON);
    const button = await ssoButton();
    const form = button.closest("form");
    expect(form).not.toBeNull();
    expect(form).toHaveAttribute("action", START_URL);
    expect(form).toHaveAttribute("method", "get");
    // No invite pending (T-0053 wires one): nothing but the navigation is sent.
    expect(form?.querySelectorAll("input")).toHaveLength(0);

    const submitted = vi.fn((event: Event) => {
      event.preventDefault(); // jsdom cannot navigate
    });
    form?.addEventListener("submit", submitted);
    fireEvent.click(button);
    expect(submitted).toHaveBeenCalledTimes(1);
  });

  it("keeps password sign-in for an internal-domain e-mail (no hiding by domain)", async () => {
    const { calls } = renderLogin(SSO_ON);
    await ssoButton();

    fireEvent.change(screen.getByLabelText(i18n.t("auth.login.email")), {
      target: { value: "site.engineer@company.example" },
    });
    fireEvent.change(screen.getByLabelText(i18n.t("auth.login.password")), {
      target: { value: "secret" },
    });
    expect(passwordSubmit()).toBeEnabled();
    fireEvent.click(passwordSubmit());

    await screen.findByTestId("scope-indicator");
    expect(calls).toContainEqual(
      expect.objectContaining({
        method: "POST",
        path: "/api/v1/auth/login",
        body: { email: "site.engineer@company.example", password: "secret" },
      }),
    );
  });

  it("does not move the focus away from a field the user already uses", async () => {
    let answer: (r: Response) => void = () => undefined;
    const base = makeServices({
      "GET /api/v1/auth/config": () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    });
    const queryClient = createQueryClient();
    queryClient.setDefaultOptions({ queries: { retry: false } });
    render(
      <AppRoot
        initialEntries={["/login"]}
        services={{ api: base.api, tokens: base.tokens, preferences: memoryStorage() }}
        queryClient={queryClient}
      />,
    );
    const email = await screen.findByLabelText(i18n.t("auth.login.email"));
    email.focus();
    await waitFor(() => {
      expect(base.calls.some((c) => c.path === "/api/v1/auth/config")).toBe(true);
    });
    answer(json(SSO_ON));

    await ssoButton();
    expect(email).toHaveFocus();
  });

  it("does not offer a start URL outside this origin's API", async () => {
    renderLogin({ sso: { provider: "entra", preferred: true, start_url: "https://evil.example/start" } });
    await waitFor(() => {
      expect(passwordSubmit()).toHaveAttribute("data-variant", "primary");
    });
    expect(screen.queryByRole("button", { name: i18n.t("auth.sso.start") })).toBeNull();
  });
});

describe("sign-in screen with SSO disabled", () => {
  it.each([
    ["the server reports no SSO", SSO_OFF, 200],
    ["the configuration cannot be loaded", { detail: "Not Found" }, 404],
  ])("shows password sign-in only when %s", async (_label, config, status) => {
    const { calls } = renderLogin(config, "/login", status);
    await waitFor(() => {
      expect(calls.some((c) => c.path === "/api/v1/auth/config")).toBe(true);
    });
    await waitFor(() => {
      expect(passwordSubmit()).toHaveAttribute("data-variant", "primary");
    });
    expect(screen.queryByRole("button", { name: i18n.t("auth.sso.start") })).toBeNull();
    expect(screen.queryByText(i18n.t("auth.sso.hint"))).toBeNull();
    expect(screen.queryByText(i18n.t("auth.sso.passwordDivider"))).toBeNull();
  });
});

describe("SSO refusals", () => {
  it.each(SSO_ERROR_CODES)("shows the translated message for %s on the sign-in screen", async (code) => {
    renderLogin(SSO_ON, `/login/sso-return?error=${code}`);

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t(`auth.sso.errors.${code}`));
    expect(await screen.findByRole("heading", { level: 1, name: i18n.t("auth.login.title") })).toBeInTheDocument();
    // Password stays available after a refusal.
    expect(passwordSubmit()).toBeInTheDocument();
  });

  it.each(["SSO_NO_ACCOUNT", "SSO_ACCOUNT_DEACTIVATED", "SSO_FAILED"] as const)(
    "%s tells the user to ask the administrator",
    (code) => {
      expect(i18n.t(`auth.sso.errors.${code}`)).toMatch(/ask your administrator/);
    },
  );

  it("never shows or logs what the URL carries beyond a known code", async () => {
    const leak = "eyJhbGciOiJSUzI1NiJ9.eyJvaWQiOiJzeW50aGV0aWMifQ.c2ln";
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((level) =>
      vi.spyOn(console, level),
    );
    renderLogin(SSO_ON, `/login/sso-return?error=${leak}&id_token=${leak}`);

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("auth.sso.errors.SSO_FAILED"));
    expect(document.body.textContent).not.toContain(leak);
    for (const spy of spies) {
      for (const args of spy.mock.calls) expect(JSON.stringify(args)).not.toContain(leak);
    }
  });
});
