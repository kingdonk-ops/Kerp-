// Top-bar project picker and post-sign-in landing (T-0042, diff §4.1, §6.1). Synthetic data.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppRoot } from "../app/AppRoot";
import { createQueryClient } from "../app/queryClient";
import type { AppServices } from "../app/services";
import { memoryStorage, type KeyValueStorage } from "../auth/tokenStore";
import { i18n } from "../i18n";
import { FAKE_USER, json, makeServices, tokens, type Recorded } from "../test/fakeApi";
import {
  byName,
  hasPortfolioPersona,
  hasSystemAdminAccess,
  offersPortfolio,
  type ProjectSummary,
} from "./directory";
import { resolveScope, scopeStorageKey } from "./ProjectScopeProvider";

const TANK: ProjectSummary = {
  id: "p-tank",
  code: "SYN-002",
  name: "Synthetic Tank Farm",
  personas: ["PER-02"],
  access: "member",
};
const REFINERY: ProjectSummary = {
  id: "p-refinery",
  code: "SYN-001",
  name: "Synthetic Refinery Unit",
  personas: ["PER-07"],
  access: "member",
};
const PIPELINE: ProjectSummary = {
  id: "p-pipeline",
  code: "SYN-003",
  name: "Synthetic Pipeline Spread",
  personas: ["PER-05"],
  access: "member",
};

// Projects a system_admin reaches through its grant only (ADR 0016, T-0275): no personas.
const JETTY: ProjectSummary = {
  id: "p-jetty",
  code: "SYN-004",
  name: "Synthetic Jetty Upgrade",
  personas: [],
  access: "system_admin",
};
const BERTH: ProjectSummary = {
  id: "p-berth",
  code: "SYN-005",
  name: "Synthetic Berth Works",
  personas: [],
  access: "system_admin",
};

const KEY = scopeStorageKey(FAKE_USER.id);

function setup(
  projects: ProjectSummary[] | (() => Response),
  { signedIn = true, preferences = memoryStorage() }: {
    signedIn?: boolean;
    preferences?: KeyValueStorage;
  } = {},
) {
  const base = makeServices({
    "GET /api/v1/me/projects": typeof projects === "function" ? projects : () => json(projects),
  });
  if (signedIn) base.tokens.set(tokens(0));
  const services: AppServices = { api: base.api, tokens: base.tokens, preferences };
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  return { services, queryClient, calls: base.calls, preferences };
}

function renderApp(path: string, ctx: ReturnType<typeof setup>) {
  return render(
    <AppRoot initialEntries={[path]} services={ctx.services} queryClient={ctx.queryClient} />,
  );
}

const option = (p: ProjectSummary) => i18n.t("scope.projectOption", { code: p.code, name: p.name });
const picker = () => screen.findByRole("combobox", { name: i18n.t("scope.label") });
const indicator = () => screen.getByTestId("scope-indicator");

async function expectScope(label: string) {
  await waitFor(() => {
    expect(indicator()).toHaveTextContent(i18n.t("scope.current", { scope: label }));
  });
}

async function signIn() {
  fireEvent.change(await screen.findByLabelText(i18n.t("auth.login.email")), {
    target: { value: FAKE_USER.email },
  });
  fireEvent.change(screen.getByLabelText(i18n.t("auth.login.password")), {
    target: { value: "secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: i18n.t("auth.login.submit") }));
}

describe("project helpers", () => {
  it("offers the portfolio scope only for PER-01/10/11/12/13", () => {
    expect(hasPortfolioPersona([TANK, REFINERY])).toBe(false);
    for (const persona of ["PER-01", "PER-10", "PER-11", "PER-12", "PER-13"] as const) {
      expect(hasPortfolioPersona([TANK, { ...REFINERY, personas: [persona] }]), persona).toBe(
        true,
      );
    }
  });

  it("orders projects by name, then code", () => {
    const twin = { ...PIPELINE, id: "p-twin", code: "SYN-000" };
    expect(byName([TANK, REFINERY, PIPELINE, twin]).map((p) => p.id)).toEqual([
      "p-twin",
      "p-pipeline",
      "p-refinery",
      "p-tank",
    ]);
  });

  it("resolves the landing scope: remembered while allowed, else first, else none", () => {
    const list = byName([TANK, REFINERY]);
    expect(resolveScope(null, [])).toEqual({ kind: "none" });
    expect(resolveScope(null, list)).toEqual({ kind: "project", projectId: REFINERY.id });
    expect(resolveScope({ kind: "project", projectId: TANK.id }, list)).toEqual({
      kind: "project",
      projectId: TANK.id,
    });
    expect(resolveScope({ kind: "project", projectId: "p-gone" }, list)).toEqual({
      kind: "project",
      projectId: REFINERY.id,
    });
    expect(resolveScope({ kind: "all" }, list)).toEqual({ kind: "project", projectId: REFINERY.id });
    const portfolio = [{ ...REFINERY, personas: ["PER-10" as const] }];
    expect(resolveScope({ kind: "all" }, portfolio)).toEqual({ kind: "all" });
  });
});

describe("project picker", () => {
  it("lists the projects from GET /me/projects by name with code, without All-Projects", async () => {
    const ctx = setup([TANK, REFINERY]);
    renderApp("/", ctx);

    const select = await picker();
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      option(REFINERY),
      option(TANK),
    ]);
    expect(
      ctx.calls.some((c: Recorded) => c.method === "GET" && c.path === "/api/v1/me/projects"),
    ).toBe(true);
    expect(ctx.calls.find((c) => c.path === "/api/v1/me/projects")?.auth).toBe("Bearer access-0");
  });

  it("offers All-Projects to a portfolio persona and remembers the choice", async () => {
    const ctx = setup([TANK, { ...REFINERY, personas: ["PER-11"], access: "member" }]);
    renderApp("/", ctx);

    const select = await picker();
    expect(within(select).getAllByRole("option")[0]).toHaveTextContent(
      i18n.t("scope.allProjects"),
    );
    fireEvent.change(select, { target: { value: "__all__" } });
    await expectScope(i18n.t("scope.allProjects"));
    expect(ctx.preferences.getItem(KEY)).toBe("all");
  });

  it("remembers the picked project per user", async () => {
    const ctx = setup([TANK, REFINERY]);
    renderApp("/", ctx);

    fireEvent.change(await picker(), { target: { value: TANK.id } });
    await expectScope(option(TANK));
    expect(ctx.preferences.getItem(KEY)).toBe(`project:${TANK.id}`);
    expect(ctx.preferences.getItem(scopeStorageKey("someone-else"))).toBeNull();
  });

  it("has a 'Browse / join another project' entry that opens the lobby", async () => {
    renderApp("/execution", setup([TANK, REFINERY]));

    await picker();
    fireEvent.click(screen.getByRole("link", { name: i18n.t("scope.browseLobby") }));
    expect(
      await screen.findByRole("heading", { level: 1, name: i18n.t("lobby.title") }),
    ).toBeInTheDocument();
    // The user keeps their projects in the picker while in the lobby.
    expect(within(await picker()).getAllByRole("option")).toHaveLength(2);
    expect(screen.queryByText(i18n.t("lobby.noProjects"))).not.toBeInTheDocument();
  });

  it("shows a translated error and no lobby redirect when the projects cannot be loaded", async () => {
    renderApp("/", setup(() => json({ detail: "down" }, 503)));

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("scope.loadError"));
    expect(
      screen.getByRole("heading", { level: 1, name: i18n.t("nav.groups.overview.title") }),
    ).toBeInTheDocument();
    expect(indicator()).toHaveTextContent(i18n.t("scope.none"));
  });

  it("never shows the tenant in the shell", async () => {
    const { container } = renderApp("/", setup([TANK, REFINERY]));

    await picker();
    expect(container.textContent).not.toContain(FAKE_USER.tenant_id);
    expect(container.innerHTML).not.toContain(FAKE_USER.tenant_id);
  });
});

describe("landing after sign-in (diff §4.1)", () => {
  it("opens the only project", async () => {
    renderApp("/login", setup([TANK], { signedIn: false }));
    await signIn();

    await screen.findByRole("heading", { level: 1, name: i18n.t("nav.groups.overview.title") });
    await expectScope(option(TANK));
  });

  it("opens the last used project of several", async () => {
    const preferences = memoryStorage();
    preferences.setItem(KEY, `project:${TANK.id}`);
    renderApp("/login", setup([TANK, REFINERY, PIPELINE], { signedIn: false, preferences }));
    await signIn();

    await screen.findByRole("heading", { level: 1, name: i18n.t("nav.groups.overview.title") });
    await expectScope(option(TANK));
    expect(await picker()).toHaveValue(TANK.id);
  });

  it("falls back to the first by name and drops a remembered project no longer allowed", async () => {
    const preferences = memoryStorage();
    preferences.setItem(KEY, "project:p-gone");
    renderApp("/login", setup([TANK, REFINERY, PIPELINE], { signedIn: false, preferences }));
    await signIn();

    await screen.findByRole("heading", { level: 1, name: i18n.t("nav.groups.overview.title") });
    await expectScope(option(PIPELINE));
    await waitFor(() => {
      expect(preferences.getItem(KEY)).toBeNull();
    });
  });

  it("drops a remembered All-Projects scope once the user has no portfolio persona", async () => {
    const preferences = memoryStorage();
    preferences.setItem(KEY, "all");
    renderApp("/", setup([TANK, REFINERY], { preferences }));

    await expectScope(option(REFINERY));
    expect(within(await picker()).queryByText(i18n.t("scope.allProjects"))).toBeNull();
    await waitFor(() => {
      expect(preferences.getItem(KEY)).toBeNull();
    });
  });

  it("sends a user with no project to the lobby", async () => {
    renderApp("/login", setup([], { signedIn: false }));
    await signIn();

    expect(
      await screen.findByRole("heading", { level: 1, name: i18n.t("lobby.title") }),
    ).toBeInTheDocument();
    expect(screen.getByText(i18n.t("lobby.noProjects"))).toBeInTheDocument();
    expect(screen.getByText(i18n.t("scope.noProjects"))).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: i18n.t("scope.label") })).toBeNull();
    expect(screen.getByRole("link", { name: i18n.t("scope.browseLobby") })).toHaveAttribute(
      "href",
      "/lobby",
    );
  });

  it("keeps a deep link for a user with no project", async () => {
    renderApp("/documents", setup([]));

    expect(
      await screen.findByRole("heading", { level: 1, name: i18n.t("nav.groups.documents.title") }),
    ).toBeInTheDocument();
  });
});

describe("system_admin picker and All-Projects (ADR 0016, T-0276)", () => {
  const adminOption = (p: ProjectSummary) =>
    i18n.t("scope.projectOptionAdmin", {
      code: p.code,
      name: p.name,
      badge: i18n.t("scope.adminAccess"),
    });

  it("detects admin access and offers the portfolio scope for it", () => {
    expect(hasSystemAdminAccess([TANK, REFINERY])).toBe(false);
    expect(hasSystemAdminAccess([TANK, JETTY])).toBe(true);
    expect(offersPortfolio([TANK, REFINERY])).toBe(false);
    expect(offersPortfolio([TANK, JETTY])).toBe(true);
    expect(offersPortfolio([{ ...TANK, personas: ["PER-13"] }])).toBe(true);
  });

  it("lands an admin without membership on the remembered project, else on none", () => {
    const list = byName([JETTY, BERTH]);
    expect(resolveScope(null, list)).toEqual({ kind: "none" });
    expect(resolveScope({ kind: "project", projectId: "p-gone" }, list)).toEqual({ kind: "none" });
    expect(resolveScope({ kind: "project", projectId: JETTY.id }, list)).toEqual({
      kind: "project",
      projectId: JETTY.id,
    });
    expect(resolveScope({ kind: "all" }, list)).toEqual({ kind: "all" });
    // An admin who is also a member falls back to the first project it is a member of.
    expect(resolveScope(null, byName([JETTY, BERTH, TANK]))).toEqual({
      kind: "project",
      projectId: TANK.id,
    });
  });

  it("lists every project with an 'Admin access' badge on those reached without membership", async () => {
    const ctx = setup([TANK, JETTY, BERTH]);
    renderApp("/", ctx);

    const select = await picker();
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      i18n.t("scope.allProjects"),
      adminOption(BERTH),
      adminOption(JETTY),
      option(TANK),
    ]);
    // The badge is text in the option itself, so type-ahead finds it like any other project.
    expect(adminOption(JETTY)).toContain(i18n.t("scope.adminAccess"));
    // A member project carries no badge.
    expect(screen.queryByTestId("scope-admin-badge")).toBeNull();
  });

  it("opens project B without a membership and marks it accessibly", async () => {
    const ctx = setup([TANK, JETTY]);
    renderApp("/", ctx);

    const select = await picker();
    fireEvent.change(select, { target: { value: JETTY.id } });
    await expectScope(option(JETTY));
    expect(ctx.preferences.getItem(KEY)).toBe(`project:${JETTY.id}`);

    const badge = screen.getByTestId("scope-admin-badge");
    expect(badge).toHaveTextContent(i18n.t("scope.adminAccess"));
    expect(badge).toHaveAttribute("title", i18n.t("scope.adminAccessHint"));
    // The picker is described by the badge, so assistive technology hears it with the value.
    expect(select).toHaveAccessibleDescription(i18n.t("scope.adminAccess"));
  });

  it("offers All-Projects to an admin without a portfolio persona", async () => {
    const ctx = setup([TANK, JETTY]);
    renderApp("/", ctx);

    fireEvent.change(await picker(), { target: { value: "__all__" } });
    await expectScope(i18n.t("scope.allProjects"));
    expect(ctx.preferences.getItem(KEY)).toBe("all");
  });

  it("lands an admin with no membership on the picker, not the lobby", async () => {
    renderApp("/login", setup([JETTY, BERTH], { signedIn: false }));
    await signIn();

    await screen.findByRole("heading", { level: 1, name: i18n.t("nav.groups.overview.title") });
    expect(screen.queryByRole("heading", { level: 1, name: i18n.t("lobby.title") })).toBeNull();
    await expectScope(i18n.t("scope.none"));
    const select = await picker();
    expect(select).toHaveValue("");
    expect(within(select).getAllByRole("option")[0]).toHaveTextContent(i18n.t("scope.choose"));
    expect(screen.getByText(i18n.t("scope.choosePrompt"))).toBeInTheDocument();
  });

  it("lands an admin with no membership on the last used project", async () => {
    const preferences = memoryStorage();
    preferences.setItem(KEY, `project:${JETTY.id}`);
    renderApp("/login", setup([JETTY, BERTH], { signedIn: false, preferences }));
    await signIn();

    await screen.findByRole("heading", { level: 1, name: i18n.t("nav.groups.overview.title") });
    await expectScope(option(JETTY));
    expect(await picker()).toHaveValue(JETTY.id);
  });

  it("keeps a non-admin to its own projects, with no badge and no All-Projects", async () => {
    renderApp("/", setup([TANK, REFINERY]));

    const select = await picker();
    const labels = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(labels).toEqual([option(REFINERY), option(TANK)]);
    expect(labels.some((l) => l.includes(i18n.t("scope.adminAccess")))).toBe(false);
    expect(screen.queryByTestId("scope-admin-badge")).toBeNull();
  });
});
