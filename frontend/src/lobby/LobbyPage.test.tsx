// Lobby screens (T-0041, diff §4.1, §6.1): lobby list, project-code entry, Active requests.
// Synthetic data only.
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderAdmin } from "../admin/testing";
import { formatTime } from "../admin/ui";
import { i18n } from "../i18n";
import type { ProjectSummary } from "../projects/directory";
import { FAKE_USER, json, type Recorded } from "../test/fakeApi";
import type { CodeMatch, LobbyProject, MyRequest } from "./api";

const HOUR = 3_600_000;
const T0 = new Date("2026-11-20T08:00:00Z").getTime();
const BY_CODE = "/api/v1/lobby/projects/by-code";

const TANK: LobbyProject = {
  id: "00000000-0000-4000-8000-000000000711",
  code: "SYN-TANK",
  name: "Synthetic Tank Farm",
  member: false,
};
const JETTY: LobbyProject = {
  id: "00000000-0000-4000-8000-000000000712",
  code: "SYN-JETTY",
  name: "Synthetic Jetty",
  member: true,
};
const MATCH: CodeMatch = {
  ...TANK,
  lookup_ticket: "synthetic-ticket-1",
  lookup_ticket_expires_at: "2026-11-20T08:15:00Z",
};
const TANK_MEMBERSHIP: ProjectSummary = { id: TANK.id, code: TANK.code, name: TANK.name, personas: ["PER-14"] };

function req(extra: Partial<MyRequest> = {}): MyRequest {
  return {
    id: "00000000-0000-4000-8000-000000000801",
    project_id: TANK.id,
    project_code: TANK.code,
    project_name: TANK.name,
    status: "pending",
    message: null,
    created_at: "2026-11-19T08:00:00Z",
    decided_at: null,
    withdrawn_at: null,
    rejection_reason: null,
    request_again_at: null,
    ...extra,
  };
}

const me = (internal: boolean) => () => json({ ...FAKE_USER, is_internal: internal });
const label = (p: { code: string; name: string }) => i18n.t("lobby.project", { code: p.code, name: p.name });
const availableFrom = (iso: string) =>
  i18n.t("lobby.requests.availableFrom", { time: formatTime(iso, "en") ?? "" });
const button = (name: string, scope: HTMLElement = document.body) => within(scope).getByRole("button", { name });
const requestsPanel = () => screen.findByRole("region", { name: i18n.t("lobby.requests.title") });
const count = (calls: Recorded[], method: string, path: string) =>
  calls.filter((c) => c.method === method && c.path === path).length;

async function lookUp(code: string) {
  fireEvent.change(await screen.findByLabelText(i18n.t("lobby.code.label")), { target: { value: code } });
  fireEvent.click(button(i18n.t("lobby.code.find")));
}

afterEach(() => {
  vi.useRealTimers();
});

describe("lobby list and code entry", () => {
  it("shows external users no list (no request for it), a hint, and a neutral 'not found'", async () => {
    const calls = renderAdmin(
      "/lobby",
      { "GET /api/v1/auth/me": me(false), "GET /api/v1/access-requests/mine": () => json([]) },
      { projects: [] },
    );
    expect(await screen.findByText(i18n.t("lobby.list.externalHint"))).toBeInTheDocument();
    expect(await screen.findByText(i18n.t("lobby.noProjects"))).toBeInTheDocument();
    await lookUp("NOPE-1");
    expect(await screen.findByText(i18n.t("lobby.code.notFound"))).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/api/v1/lobby/projects")).toBe(false);
    expect(screen.queryByRole("region", { name: i18n.t("lobby.list.title") })).not.toBeInTheDocument();
  });

  it("lists lobby projects to internal users; 'Request access' sends the message and no ticket", async () => {
    const calls = renderAdmin("/lobby", {
      "GET /api/v1/auth/me": me(true),
      "GET /api/v1/lobby/projects": () => json([TANK, JETTY]),
      "GET /api/v1/access-requests/mine": () => json([]),
      "POST /api/v1/access-requests": () => json(req(), 201),
    });
    const tank = await screen.findByRole("listitem", { name: label(TANK) });
    const jetty = screen.getByRole("listitem", { name: label(JETTY) });
    expect(button(i18n.t("lobby.open"), jetty)).toBeInTheDocument();
    expect(screen.queryByText(i18n.t("lobby.list.externalHint"))).not.toBeInTheDocument();

    fireEvent.click(button(i18n.t("lobby.requestAccess"), tank));
    fireEvent.change(within(tank).getByLabelText(i18n.t("lobby.message")), { target: { value: "Shutdown crew." } });
    fireEvent.click(button(i18n.t("lobby.send"), tank));

    expect(await screen.findByText(i18n.t("lobby.sent"))).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST" && c.path === "/api/v1/access-requests");
    expect(post?.body).toEqual({ project_id: TANK.id, message: "Shutdown crew." });
  });

  it("finds a project by exact code and sends its lookup ticket with the request", async () => {
    let mine: MyRequest[] = [];
    const calls = renderAdmin(
      "/lobby",
      {
        "GET /api/v1/auth/me": me(false),
        [`GET ${BY_CODE}/SYN-TANK`]: () => json(MATCH),
        "GET /api/v1/access-requests/mine": () => json(mine),
        "POST /api/v1/access-requests": () => {
          mine = [req()];
          return json(mine[0], 201);
        },
      },
      { projects: [] },
    );
    await lookUp("  SYN-TANK ");
    const found = await screen.findByRole("listitem", { name: label(TANK) });
    fireEvent.click(button(i18n.t("lobby.requestAccess"), found));
    fireEvent.click(button(i18n.t("lobby.send"), found));

    const card = await within(await requestsPanel()).findByRole("listitem", { name: label(TANK) });
    expect(card).toHaveTextContent(i18n.t("lobby.requests.status.pending"));
    const post = calls.find((c) => c.method === "POST" && c.path === "/api/v1/access-requests");
    expect(post?.body).toEqual({ project_id: TANK.id, lookup_ticket: MATCH.lookup_ticket });
  });

  it("offers 'Open' for a code of a project the user belongs to, which opens it", async () => {
    renderAdmin(
      "/lobby",
      {
        "GET /api/v1/auth/me": me(false),
        [`GET ${BY_CODE}/SYN-TANK`]: () => json({ ...MATCH, member: true }),
        "GET /api/v1/access-requests/mine": () => json([]),
      },
      { projects: [TANK_MEMBERSHIP] },
    );
    await lookUp("SYN-TANK");
    const found = await screen.findByRole("listitem", { name: label(TANK) });
    expect(within(found).queryByRole("button", { name: i18n.t("lobby.requestAccess") })).not.toBeInTheDocument();
    fireEvent.click(button(i18n.t("lobby.open"), found));
    expect(await screen.findByTestId("scope-indicator")).toHaveTextContent(
      i18n.t("scope.projectOption", { code: TANK.code, name: TANK.name }),
    );
  });

  it("shows the throttling message on 429 and an already-pending refusal on 409", async () => {
    let lookups = 0;
    renderAdmin(
      "/lobby",
      {
        "GET /api/v1/auth/me": me(false),
        [`GET ${BY_CODE}/SYN-TANK`]: () => (++lookups === 1 ? json({ detail: "slow" }, 429) : json(MATCH)),
        "GET /api/v1/access-requests/mine": () => json([]),
        "POST /api/v1/access-requests": () =>
          json({ detail: "pending", code: "ACCESS_REQUEST_PENDING" }, 409),
      },
      { projects: [] },
    );
    await lookUp("SYN-TANK");
    expect(await screen.findByText(i18n.t("lobby.code.throttled"))).toBeInTheDocument();
    fireEvent.click(button(i18n.t("lobby.code.find")));
    const found = await screen.findByRole("listitem", { name: label(TANK) });
    fireEvent.click(button(i18n.t("lobby.requestAccess"), found));
    fireEvent.click(button(i18n.t("lobby.send"), found));
    expect(await screen.findByText(i18n.t("lobby.requests.alreadyPending"))).toBeInTheDocument();
  });
});

describe("Active requests panel", () => {
  it("shows a chip per status; pending → Withdraw; approved → Open project", async () => {
    const OTHER = { project_id: JETTY.id, project_code: JETTY.code, project_name: JETTY.name };
    let mine = [
      req(),
      req({ ...OTHER, id: "00000000-0000-4000-8000-000000000802", status: "approved" }),
    ];
    let projects: ProjectSummary[] = [];
    const calls = renderAdmin(
      "/lobby",
      {
        "GET /api/v1/auth/me": me(false),
        "GET /api/v1/me/projects": () => json(projects),
        "GET /api/v1/access-requests/mine": () => json(mine),
        [`POST /api/v1/access-requests/${req().id}/withdraw`]: () => {
          mine = [req({ status: "withdrawn" }), mine[1] ?? req()];
          return json(mine[0]);
        },
      },
      { projects: [] },
    );
    const pending = await within(await requestsPanel()).findByRole("listitem", { name: label(TANK) });
    expect(pending).toHaveTextContent(i18n.t("lobby.requests.status.pending"));
    const approved = within(await requestsPanel()).getByRole("listitem", { name: label(JETTY) });
    expect(approved).toHaveTextContent(i18n.t("lobby.requests.status.approved"));

    fireEvent.click(button(i18n.t("lobby.requests.withdraw"), pending));
    expect(await screen.findByText(i18n.t("lobby.requests.withdrawn"))).toBeInTheDocument();
    await waitFor(() => {
      expect(pending).toHaveTextContent(i18n.t("lobby.requests.status.withdrawn"));
    });
    // A withdrawn request has no cooldown: Request again is offered at once.
    expect(button(i18n.t("lobby.requests.requestAgain"), pending)).toBeEnabled();
    expect(count(calls, "POST", `/api/v1/access-requests/${req().id}/withdraw`)).toBe(1);

    projects = [{ id: JETTY.id, code: JETTY.code, name: JETTY.name, personas: ["PER-14"] }];
    fireEvent.click(button(i18n.t("lobby.requests.openProject"), approved));
    expect(await screen.findByTestId("scope-indicator")).toHaveTextContent(
      i18n.t("scope.projectOption", { code: JETTY.code, name: JETTY.name }),
    );
  });

  it("refreshes when the lobby opens and when the window regains focus", async () => {
    const calls = renderAdmin(
      "/lobby",
      { "GET /api/v1/auth/me": me(false), "GET /api/v1/access-requests/mine": () => json([]) },
      { projects: [] },
    );
    expect(await screen.findByText(i18n.t("lobby.requests.empty"))).toBeInTheDocument();
    const before = count(calls, "GET", "/api/v1/access-requests/mine");
    expect(before).toBeGreaterThanOrEqual(1);
    fireEvent.focus(window);
    await waitFor(() => {
      expect(count(calls, "GET", "/api/v1/access-requests/mine")).toBe(before + 1);
    });
  });

  it("shows a 409 cooldown on Request again with the same 'available from' message", async () => {
    const until = "2026-11-21T09:30:00Z";
    renderAdmin(
      "/lobby",
      {
        "GET /api/v1/auth/me": me(false),
        "GET /api/v1/access-requests/mine": () => json([req({ status: "withdrawn" })]),
        "POST /api/v1/access-requests": () =>
          json({ detail: "cooldown", code: "ACCESS_REQUEST_COOLDOWN", retry_after: until }, 409),
      },
      { projects: [] },
    );
    const card = await within(await requestsPanel()).findByRole("listitem", { name: label(TANK) });
    fireEvent.click(button(i18n.t("lobby.requests.requestAgain"), card));
    expect(
      await within(card).findByText(
        availableFrom(until),
      ),
    ).toBeInTheDocument();
  });

  it("journey: external user, code → request with ticket → rejected with reason → cooldown → Request again without ticket → approved → project in the picker", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, now: T0 });
    const COOLDOWN = 2 * HOUR;
    let mine: MyRequest[] = [];
    let projects: ProjectSummary[] = [];
    const calls = renderAdmin(
      "/lobby",
      {
        "GET /api/v1/auth/me": me(false),
        "GET /api/v1/me/projects": () => json(projects),
        [`GET ${BY_CODE}/SYN-TANK`]: () => json(MATCH),
        "GET /api/v1/access-requests/mine": () => json(mine),
        "POST /api/v1/access-requests": () => {
          const n = String(mine.length + 1).padStart(3, "0");
          mine = [req({ id: `00000000-0000-4000-8000-000000000${n}`, created_at: new Date().toISOString() })];
          return json(mine[0], 201);
        },
      },
      { projects: [] },
    );

    // 1. Code lookup, request with the ticket.
    await lookUp("SYN-TANK");
    const found = await screen.findByRole("listitem", { name: label(TANK) });
    fireEvent.click(button(i18n.t("lobby.requestAccess"), found));
    fireEvent.click(button(i18n.t("lobby.send"), found));
    let card = await within(await requestsPanel()).findByRole("listitem", { name: label(TANK) });

    // 2. A PER-09 rejects with a reason (the server side of T-0051); the panel learns on focus.
    const decidedAt = new Date(Date.now()).toISOString();
    const again = new Date(Date.now() + COOLDOWN).toISOString();
    mine = [req({ ...mine[0], status: "rejected", decided_at: decidedAt, rejection_reason: "Crew is full.", request_again_at: again })];
    fireEvent.focus(window);
    await within(card).findByText(i18n.t("lobby.requests.reason", { reason: "Crew is full." }));
    expect(card).toHaveTextContent(i18n.t("lobby.requests.status.rejected"));
    const requestAgain = button(i18n.t("lobby.requests.requestAgain"), card);
    expect(requestAgain).toBeDisabled();
    expect(card).toHaveTextContent(availableFrom(again));

    // 3. The cooldown passes (fake clock): Request again is enabled and sends no ticket.
    act(() => {
      vi.advanceTimersByTime(COOLDOWN - 60_000);
    });
    expect(requestAgain).toBeDisabled();
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    await waitFor(() => {
      expect(requestAgain).toBeEnabled();
    });
    fireEvent.click(requestAgain);
    await screen.findByText(i18n.t("lobby.sent"));
    const posts = calls.filter((c) => c.method === "POST" && c.path === "/api/v1/access-requests");
    expect(posts.map((p) => p.body)).toEqual([
      { project_id: TANK.id, lookup_ticket: MATCH.lookup_ticket },
      { project_id: TANK.id },
    ]);
    card = await within(await requestsPanel()).findByRole("listitem", { name: label(TANK) });
    await waitFor(() => {
      expect(card).toHaveTextContent(i18n.t("lobby.requests.status.pending"));
    });

    // 4. Approved (T-0051): the panel shows it and the picker gains the project.
    projects = [TANK_MEMBERSHIP];
    mine = [req({ ...mine[0], status: "approved", decided_at: new Date().toISOString() })];
    fireEvent.focus(window);
    await within(card).findByText(i18n.t("lobby.requests.status.approved"));
    const option = await screen.findByRole("option", {
      name: i18n.t("scope.projectOption", { code: TANK.code, name: TANK.name }),
    });
    expect(option).toBeInTheDocument();
  });
});
