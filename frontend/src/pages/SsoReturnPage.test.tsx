// Landing after the Entra ID callback (T-0055, diff §4.1, §4.5): the server set the session
// cookie; the SPA resumes it and lands like any other sign-in. Synthetic fixtures only.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppRoot } from "../app/AppRoot";
import { createQueryClient } from "../app/queryClient";
import type { AppServices } from "../app/services";
import { memoryStorage, SESSION_STORAGE_KEY } from "../auth/tokenStore";
import { i18n } from "../i18n";
import type { ProjectSummary } from "../projects/directory";
import { scopeStorageKey } from "../projects/ProjectScopeProvider";
import { FAKE_USER, json, makeServices, tokens } from "../test/fakeApi";

// First appearances wait for several fake-server round-trips on a cold `AppRoot`; a loaded CI
// runner needs more than Testing Library's 1 s default (PR #146 review). This file only.
const LOADED = { timeout: 5000 };
vi.setConfig({ testTimeout: 20_000 });

const ALPHA: ProjectSummary = {
  id: "00000000-0000-4000-8000-00000000a001",
  code: "SYN-001",
  name: "Alpha Synthetic Unit",
  personas: ["PER-02"],
  access: "member",
};
const BETA: ProjectSummary = {
  id: "00000000-0000-4000-8000-00000000b002",
  code: "SYN-002",
  name: "Beta Synthetic Unit",
  personas: ["PER-02"],
  access: "member",
};

function land(
  query: string,
  opts: { projects?: ProjectSummary[]; refresh?: () => Response; preferences?: ReturnType<typeof memoryStorage> } = {},
) {
  const session = memoryStorage();
  const preferences = opts.preferences ?? memoryStorage();
  const base = makeServices(
    {
      "GET /api/v1/me/projects": () => json(opts.projects ?? [ALPHA]),
      ...(opts.refresh ? { "POST /api/v1/auth/refresh": opts.refresh } : {}),
    },
    session,
  );
  const services: AppServices = { api: base.api, tokens: base.tokens, preferences };
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  render(
    <AppRoot
      initialEntries={[`/login/sso-return${query}`]}
      services={services}
      queryClient={queryClient}
    />,
  );
  return { calls: base.calls, session, preferences, tokens: base.tokens };
}

const scopeIs = async (project: ProjectSummary) => {
  await waitFor(() => {
    expect(screen.getByTestId("scope-indicator")).toHaveTextContent(
      i18n.t("scope.projectOption", { code: project.code, name: project.name }),
    );
  }, LOADED);
};

describe("SSO landing", () => {
  it("resumes the cookie session and opens the user's only project", async () => {
    const { calls, session, tokens } = land("?status=signed_in");

    await scopeIs(ALPHA);
    expect(
      screen.getByText(i18n.t("auth.signedInAs", { name: FAKE_USER.display_name })),
    ).toBeInTheDocument();
    // The session comes from the httpOnly cookie through /auth/refresh, never from the URL.
    const refresh = calls.find((c) => c.path === "/api/v1/auth/refresh");
    expect(refresh).toMatchObject({ method: "POST", auth: null, body: null });
    expect(calls.filter((c) => c.path === "/api/v1/auth/refresh")).toHaveLength(1);
    // Only the "signed in" marker is stored; the access token stays in memory.
    expect(session.getItem(SESSION_STORAGE_KEY)).toBe("1");
    expect(tokens.access).toMatch(/^access-/);
  });

  it("replaces an earlier session of this tab with the one the callback set", async () => {
    let refreshed = 0;
    const session = memoryStorage();
    const base = makeServices(
      {
        "GET /api/v1/me/projects": () => json([ALPHA]),
        "POST /api/v1/auth/refresh": () => json(tokens(100 + ++refreshed)),
      },
      session,
    );
    base.tokens.set(tokens(0));
    const queryClient = createQueryClient();
    queryClient.setDefaultOptions({ queries: { retry: false } });
    render(
      <AppRoot
        initialEntries={["/login/sso-return?status=signed_in"]}
        services={{ api: base.api, tokens: base.tokens, preferences: memoryStorage() }}
        queryClient={queryClient}
      />,
    );

    await scopeIs(ALPHA);
    expect(refreshed).toBe(1);
    expect(base.tokens.access).toBe("access-101");
    // `me` was loaded again with the adopted session's token.
    const meCalls = base.calls.filter((c) => c.path === "/api/v1/auth/me");
    expect(meCalls.at(-1)?.auth).toBe("Bearer access-101");
  });

  it("opens the remembered project of several", async () => {
    const preferences = memoryStorage();
    preferences.setItem(scopeStorageKey(FAKE_USER.id), `project:${BETA.id}`);
    land("?status=signed_in", { projects: [ALPHA, BETA], preferences });

    await scopeIs(BETA);
  });

  it("opens the first project by name of several when none is remembered", async () => {
    land("?status=signed_in", { projects: [BETA, ALPHA] });

    await scopeIs(ALPHA);
  });

  it("sends a user with no project to the lobby", async () => {
    land("?status=signed_in", { projects: [] });

    expect(
      await screen.findByRole("heading", { level: 1, name: i18n.t("lobby.title") }, LOADED),
    ).toBeInTheDocument();
  });

  it("opens the project just joined through an invite", async () => {
    const preferences = memoryStorage();
    preferences.setItem(scopeStorageKey(FAKE_USER.id), `project:${ALPHA.id}`);
    land(`?status=signed_in&joined_project_id=${BETA.id}`, {
      projects: [ALPHA, BETA],
      preferences,
    });

    await scopeIs(BETA);
    expect(preferences.getItem(scopeStorageKey(FAKE_USER.id))).toBe(`project:${BETA.id}`);
  });

  it("shows the generic refusal when the session cannot be resumed", async () => {
    const { session } = land("?status=signed_in", {
      refresh: () => json({ detail: "Not authenticated" }, 401),
    });

    expect(await screen.findByRole("alert", {}, LOADED)).toHaveTextContent(
      i18n.t("auth.sso.errors.SSO_FAILED"),
    );
    expect(session.getItem(SESSION_STORAGE_KEY)).toBeNull();
  });

  it("goes to the sign-in screen without a message when the URL carries no outcome", async () => {
    land("");

    expect(
      await screen.findByRole("heading", { level: 1, name: i18n.t("auth.login.title") }, LOADED),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("lets a refusal win over a success marker", async () => {
    const { calls } = land("?status=signed_in&error=SSO_NO_ACCOUNT");

    expect(await screen.findByRole("alert", {}, LOADED)).toHaveTextContent(
      i18n.t("auth.sso.errors.SSO_NO_ACCOUNT"),
    );
    expect(calls.some((c) => c.path === "/api/v1/auth/refresh")).toBe(false);
  });
});

describe("SSO landing page document", () => {
  it("sends no referrer from the SPA, the landing included (TD-334)", () => {
    const html = readFileSync(
      resolve(dirname(fileURLToPath(import.meta.url)), "../../index.html"),
      "utf8",
    );
    expect(html).toMatch(/<meta name="referrer" content="no-referrer" \/>/);
  });
});
