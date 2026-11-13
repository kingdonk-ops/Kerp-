import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { memoryStorage, type KeyValueStorage } from "../auth/tokenStore";
import { i18n } from "../i18n";
import { staticProjectDirectory, type ProjectSummary } from "../projects/directory";
import { scopeStorageKey } from "../projects/ProjectScopeProvider";
import { FAKE_USER, json, makeServices, tokens } from "../test/fakeApi";
import { AppRoot } from "./AppRoot";
import { NAV_GROUPS } from "./nav";
import { createQueryClient } from "./queryClient";
import type { AppServices } from "./services";

const PROJECTS: [ProjectSummary, ProjectSummary] = [
  { id: "p-alpha", code: "SYN-001", name: "Synthetic Refinery Unit" },
  { id: "p-beta", code: "SYN-002", name: "Synthetic Tank Farm" },
];

function setup(
  opts: { signedIn?: boolean; projects?: ProjectSummary[]; preferences?: KeyValueStorage } = {},
  handlers: Parameters<typeof makeServices>[0] = {},
) {
  const base = makeServices(handlers);
  if (opts.signedIn ?? true) base.tokens.set(tokens(0));
  const services: AppServices = {
    api: base.api,
    tokens: base.tokens,
    projects: staticProjectDirectory(opts.projects ?? PROJECTS),
    preferences: opts.preferences ?? memoryStorage(),
  };
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  return { services, calls: base.calls, queryClient };
}

function renderApp(path: string, ctx: ReturnType<typeof setup>) {
  return render(
    <AppRoot initialEntries={[path]} services={ctx.services} queryClient={ctx.queryClient} />,
  );
}

describe("app shell", () => {
  it("redirects an anonymous user to the login page", async () => {
    renderApp("/logistics", setup({ signedIn: false }));

    expect(
      await screen.findByRole("heading", { level: 1, name: i18n.t("auth.login.title") }),
    ).toBeInTheDocument();
  });

  it("signs in through the generated client and returns to the requested page", async () => {
    const ctx = setup({ signedIn: false });
    renderApp("/execution", ctx);

    fireEvent.change(await screen.findByLabelText(i18n.t("auth.login.email")), {
      target: { value: FAKE_USER.email },
    });
    fireEvent.change(screen.getByLabelText(i18n.t("auth.login.password")), {
      target: { value: "secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: i18n.t("auth.login.submit") }));

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: i18n.t("nav.groups.execution.title"),
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t("auth.signedInAs", { name: FAKE_USER.display_name })),
    ).toBeInTheDocument();
    expect(ctx.calls[0]).toMatchObject({
      method: "POST",
      path: "/api/v1/auth/login",
      body: { email: FAKE_USER.email, password: "secret" },
    });
  });

  it("shows a translated error for wrong credentials and for lockout", async () => {
    let locked = false;
    renderApp(
      "/login",
      setup(
        { signedIn: false },
        {
          "POST /api/v1/auth/login": () =>
            locked ? json({ detail: "locked" }, 429) : json({ detail: "Not authenticated" }, 401),
        },
      ),
    );
    fireEvent.change(await screen.findByLabelText(i18n.t("auth.login.email")), {
      target: { value: FAKE_USER.email },
    });
    fireEvent.change(screen.getByLabelText(i18n.t("auth.login.password")), {
      target: { value: "wrong" },
    });
    const submit = screen.getByRole("button", { name: i18n.t("auth.login.submit") });

    fireEvent.click(submit);
    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("auth.login.invalid"));

    locked = true;
    fireEvent.click(submit);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(i18n.t("auth.login.locked")),
    );
  });

  it("renders the 8 navigation groups: 4 pillars + Backbone, Overview, Documents, Admin", async () => {
    renderApp("/", setup());

    const nav = await screen.findByRole("navigation", { name: i18n.t("nav.label") });
    const links = within(nav).getAllByRole("link");
    expect(links).toHaveLength(8);
    expect(NAV_GROUPS).toHaveLength(8);
    expect(NAV_GROUPS.filter((g) => g.pillar).map((g) => g.id)).toEqual([
      "logistics",
      "execution",
      "defenseClaims",
      "controlsInsights",
    ]);
    expect(NAV_GROUPS.filter((g) => !g.pillar).map((g) => g.id)).toEqual([
      "overview",
      "backbone",
      "documents",
      "admin",
    ]);
    for (const [i, group] of NAV_GROUPS.entries()) {
      expect(links[i]).toHaveAttribute("href", group.path);
      expect(links[i]).toHaveTextContent(i18n.t(`nav.groups.${group.id}.title`));
    }
    expect(within(nav).getAllByText(i18n.t("nav.pillar"))).toHaveLength(4);
  });

  it("navigates between groups", async () => {
    renderApp("/", setup());

    const nav = await screen.findByRole("navigation", { name: i18n.t("nav.label") });
    fireEvent.click(within(nav).getByRole("link", { name: /Defense & Claims/ }));

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: i18n.t("nav.groups.defenseClaims.title"),
      }),
    ).toBeInTheDocument();
  });

  it("switches between a project and the All-Projects portfolio scope and remembers it", async () => {
    const preferences = memoryStorage();
    renderApp("/", setup({ preferences }));

    const switcher = await screen.findByRole("combobox", { name: i18n.t("scope.label") });
    const project = (p: ProjectSummary) =>
      i18n.t("scope.projectOption", { code: p.code, name: p.name });
    const indicator = () => screen.getByTestId("scope-indicator");
    await waitFor(() =>
      expect(indicator()).toHaveTextContent(
        i18n.t("scope.current", { scope: project(PROJECTS[0]) }),
      ),
    );
    expect(within(switcher).getAllByRole("option").map((o) => o.textContent)).toEqual([
      i18n.t("scope.allProjects"),
      project(PROJECTS[0]),
      project(PROJECTS[1]),
    ]);

    fireEvent.change(switcher, { target: { value: "p-beta" } });
    expect(indicator()).toHaveTextContent(project(PROJECTS[1]));

    fireEvent.change(switcher, { target: { value: "__all__" } });
    expect(indicator()).toHaveTextContent(
      i18n.t("scope.current", { scope: i18n.t("scope.allProjects") }),
    );
    expect(preferences.getItem(scopeStorageKey(FAKE_USER.id))).toBe("all");
  });

  it("restores the stored scope and ignores a project the user no longer has", async () => {
    const preferences = memoryStorage();
    preferences.setItem(scopeStorageKey(FAKE_USER.id), "project:p-beta");
    const { unmount } = renderApp("/", setup({ preferences }));
    expect(
      await screen.findByText(new RegExp(PROJECTS[1].name), { selector: "[data-testid]" }),
    ).toBeInTheDocument();
    unmount();

    preferences.setItem(scopeStorageKey(FAKE_USER.id), "project:p-gone");
    renderApp("/", setup({ preferences }));
    expect(
      await screen.findByText(new RegExp(PROJECTS[0].name), { selector: "[data-testid]" }),
    ).toBeInTheDocument();
  });

  it("falls back to the All-Projects scope while no projects are available", async () => {
    renderApp("/", setup({ projects: [] }));

    expect(await screen.findByText(i18n.t("scope.noProjects"))).toBeInTheDocument();
    expect(screen.getByTestId("scope-indicator")).toHaveTextContent(i18n.t("scope.allProjects"));
  });

  it("signs out: revokes the session server-side and returns to login", async () => {
    const ctx = setup();
    renderApp("/admin", ctx);

    fireEvent.click(await screen.findByRole("button", { name: i18n.t("auth.logout") }));

    expect(
      await screen.findByRole("heading", { level: 1, name: i18n.t("auth.login.title") }),
    ).toBeInTheDocument();
    expect(ctx.calls.some((c) => c.path === "/api/v1/auth/logout")).toBe(true);
    expect(ctx.services.tokens.hasSession).toBe(false);
  });

  it("offers a retry when the session cannot be loaded", async () => {
    let fail = true;
    renderApp(
      "/",
      setup({}, {
        "GET /api/v1/auth/me": () => (fail ? json({ detail: "down" }, 503) : json(FAKE_USER)),
      }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("app.loadError"));
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: i18n.t("app.retry") }));
    expect(
      await screen.findByRole("heading", { level: 1, name: i18n.t("nav.groups.overview.title") }),
    ).toBeInTheDocument();
  });

  it("renders the not-found route for unknown paths and sets the title", async () => {
    renderApp("/does-not-exist", setup());

    expect(
      await screen.findByRole("heading", { level: 1, name: i18n.t("notFound.title") }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: i18n.t("notFound.backHome") })).toHaveAttribute(
      "href",
      "/",
    );
    expect(document.title).toBe(i18n.t("app.name"));
  });
});
