// Admin → Communications → Broadcasts (T-0229, diff §4.7, §6.5). Synthetic data only.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { i18n } from "../i18n";
import type { ProjectSummary } from "../projects/directory";
import { json } from "../test/fakeApi";
import { BROADCAST, BROADCAST_ID, COUNTS } from "./commsFixtures";
import { ADMIN_PROJECT, project, renderAdmin } from "./testing";

vi.setConfig({ testTimeout: 20_000 });
const WAIT = { timeout: 5000 };

const PATH = "/admin/communications/broadcasts";
const OTHER = project({ id: "00000000-0000-4000-8000-000000000102", code: "SYN-002", name: "Synthetic Tank Farm" });
const handlers = (extra = {}) => ({
  "GET /api/v1/notifications/broadcasts": () => json([BROADCAST]),
  "GET /api/v1/projects": () => json([project(), OTHER]),
  "POST /api/v1/notifications/broadcasts/preview": () => json({ recipient_count: 12 }),
  "POST /api/v1/notifications/broadcasts": () => json({ ...BROADCAST, status: "queued", finished_at: null }, 202),
  ...extra,
});
const SYS = { projects: [], systemAdmin: true };
/** `t()` over keys built at run time (the typed `t` takes literal keys only). */
const tr = i18n.t.bind(i18n) as unknown as (key: string, vars?: Record<string, unknown>) => string;
const button = (key: string, vars?: Record<string, unknown>) => screen.getByRole("button", { name: tr(key, vars) });
const composer = () => screen.findByRole("region", { name: tr("admin.comms.broadcasts.composeTitle") }, WAIT);
const write = (subject: string, body: string) => {
  fireEvent.change(screen.getByLabelText(tr("admin.comms.broadcasts.subject")), { target: { value: subject } });
  fireEvent.change(screen.getByLabelText(tr("admin.comms.broadcasts.body")), { target: { value: body } });
};

describe("broadcast composer", () => {
  it("lets a System admin count a tenant-wide audience, confirm and send", async () => {
    const calls = renderAdmin(PATH, handlers(), SYS);
    await composer();
    write("Synthetic notice", "Line one.\nLine two.");
    const send = button("admin.comms.broadcasts.send");
    expect(send).toBeDisabled();
    expect(screen.getByText(tr("admin.comms.broadcasts.countFirst"))).toBeInTheDocument();

    fireEvent.click(button("admin.comms.broadcasts.count"));
    expect(await screen.findByText(tr("admin.comms.broadcasts.recipients", { count: 12 }), {}, WAIT)).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/broadcasts/preview"))?.body).toEqual({
      scope: "tenant",
      project_ids: [],
      category: null,
      personas: [],
    });
    expect(send).toBeEnabled();
    fireEvent.click(send);

    const dialog = within(await screen.findByRole("dialog", { name: tr("admin.comms.broadcasts.confirmTitle") }));
    expect(dialog.getByText(tr("admin.comms.broadcasts.confirmBody", { subject: "Synthetic notice", count: 12 }))).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/notifications/broadcasts")).toBe(false);
    fireEvent.click(dialog.getByRole("button", { name: tr("admin.comms.broadcasts.confirm", { count: 12 }) }));

    expect(await screen.findByText(tr("admin.comms.broadcasts.queued", { count: 12 }), {}, WAIT)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/notifications/broadcasts")?.body).toEqual({
      scope: "tenant",
      project_ids: [],
      category: null,
      personas: [],
      subject: "Synthetic notice",
      body: "Line one.\nLine two.",
    });
    // The form is cleared and the history re-read.
    expect(screen.getByLabelText(tr("admin.comms.broadcasts.subject"))).toHaveValue("");
    await waitFor(() => {
      expect(calls.filter((c) => c.method === "GET" && c.path === "/api/v1/notifications/broadcasts")).toHaveLength(2);
    }, WAIT);
  });

  it("asks for a new count when the audience changes and sends projects, category and personas", async () => {
    const calls = renderAdmin(PATH, handlers(), SYS);
    await composer();
    write("Synthetic notice", "Body.");
    fireEvent.click(screen.getByLabelText(tr("admin.comms.broadcasts.scopeProjects")));
    expect(button("admin.comms.broadcasts.count")).toBeDisabled();
    fireEvent.click(await screen.findByLabelText(tr("admin.comms.broadcasts.project", { code: "SYN-002", name: OTHER.name }), {}, WAIT));
    fireEvent.click(button("admin.comms.broadcasts.count"));
    await screen.findByText(tr("admin.comms.broadcasts.recipients", { count: 12 }), {}, WAIT);
    expect(button("admin.comms.broadcasts.send")).toBeEnabled();

    fireEvent.change(screen.getByLabelText(tr("admin.comms.broadcasts.category")), { target: { value: "client" } });
    expect(screen.queryByText(tr("admin.comms.broadcasts.recipients", { count: 12 }))).not.toBeInTheDocument();
    expect(button("admin.comms.broadcasts.send")).toBeDisabled();
    fireEvent.click(screen.getByLabelText(tr("admin.accessRequests.persona", { code: "PER-15", name: tr("personas.PER-15") })));
    fireEvent.click(button("admin.comms.broadcasts.count"));
    await screen.findByText(tr("admin.comms.broadcasts.recipients", { count: 12 }), {}, WAIT);
    expect(calls.filter((c) => c.path.endsWith("/broadcasts/preview")).at(-1)?.body).toEqual({
      scope: "projects",
      project_ids: [OTHER.id],
      category: "client",
      personas: ["PER-15"],
    });
  });

  it("offers a PER-09 only its own projects, never the whole tenant", async () => {
    const calls = renderAdmin(PATH, handlers());
    await composer();
    expect(screen.queryByLabelText(tr("admin.comms.broadcasts.scopeTenant"))).not.toBeInTheDocument();
    expect(screen.queryByText(tr("admin.comms.broadcasts.project", { code: "SYN-002", name: OTHER.name }))).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(tr("admin.comms.broadcasts.project", { code: ADMIN_PROJECT.code, name: ADMIN_PROJECT.name })));
    fireEvent.click(button("admin.comms.broadcasts.count"));
    await screen.findByText(tr("admin.comms.broadcasts.recipients", { count: 12 }), {}, WAIT);
    expect(calls.find((c) => c.path.endsWith("/broadcasts/preview"))?.body).toMatchObject({
      scope: "projects",
      project_ids: [ADMIN_PROJECT.id],
    });
    // A PER-09 never lists every project.
    expect(calls.some((c) => c.path === "/api/v1/projects")).toBe(false);
    // Only the Broadcasts tab: delivery and templates are System admin screens.
    const tabs = within(screen.getByRole("navigation", { name: tr("admin.comms.tabs") }));
    expect(tabs.getAllByRole("link").map((l) => l.textContent)).toEqual([tr("admin.comms.broadcastsTab")]);
  });

  it("never offers or sends a project where the PER-09 is only a member", async () => {
    const member: ProjectSummary = {
      id: "00000000-0000-4000-8000-000000000103",
      code: "SYN-003",
      name: "Synthetic Jetty",
      personas: ["PER-01"],
      access: "member",
    };
    const calls = renderAdmin(PATH, handlers(), { projects: [ADMIN_PROJECT, member] });
    await composer();
    const own = screen.getByLabelText(tr("admin.comms.broadcasts.project", { code: ADMIN_PROJECT.code, name: ADMIN_PROJECT.name }));
    expect(screen.getAllByRole("checkbox", { name: /SYN-/ })).toEqual([own]);
    expect(screen.queryByLabelText(tr("admin.comms.broadcasts.project", { code: member.code, name: member.name }))).not.toBeInTheDocument();

    fireEvent.click(own);
    write("Synthetic notice", "Body.");
    fireEvent.click(button("admin.comms.broadcasts.count"));
    await screen.findByText(tr("admin.comms.broadcasts.recipients", { count: 12 }), {}, WAIT);
    fireEvent.click(button("admin.comms.broadcasts.send"));
    fireEvent.click(await screen.findByRole("button", { name: tr("admin.comms.broadcasts.confirm", { count: 12 }) }, WAIT));
    await screen.findByText(tr("admin.comms.broadcasts.queued", { count: 12 }), {}, WAIT);
    const bodies = calls
      .filter((c) => c.method === "POST" && c.path.startsWith("/api/v1/notifications/broadcasts"))
      .map((c) => c.body as { project_ids: string[] });
    expect(bodies).toHaveLength(2);
    for (const body of bodies) expect(body.project_ids).toEqual([ADMIN_PROJECT.id]);
  });

  it.each([
    [{ status: 429, headers: { "Retry-After": "3600" } }, /^You have sent the maximum number of broadcasts for now\. Try again from /],
    [{ status: 403, body: { detail: "x", code: "IMPERSONATION_ACTION_FORBIDDEN" } }, tr("admin.comms.broadcasts.impersonating")],
    [{ status: 403, body: { detail: "Forbidden" } }, tr("admin.comms.broadcasts.notAllowed")],
    [{ status: 404, body: { detail: "Not found" } }, tr("admin.comms.broadcasts.outOfScope")],
    [{ status: 422, body: { detail: "The audience has no recipients" } }, tr("admin.comms.broadcasts.noRecipients")],
    [
      { status: 422, body: { detail: [{ loc: ["body", "subject"], msg: "plain text on one line", type: "value_error" }] } },
      tr("admin.comms.broadcasts.sendRefused"),
    ],
  ] as const)("explains a refused send (%o)", async (answer, message) => {
    const respond = () =>
      new Response(JSON.stringify("body" in answer ? answer.body : { detail: "x" }), {
        status: answer.status,
        headers: { "Content-Type": "application/json", ...("headers" in answer ? answer.headers : {}) },
      });
    renderAdmin(PATH, handlers({ "POST /api/v1/notifications/broadcasts": respond }), SYS);
    await composer();
    write("Synthetic notice", "Body.");
    fireEvent.click(button("admin.comms.broadcasts.count"));
    await screen.findByText(tr("admin.comms.broadcasts.recipients", { count: 12 }), {}, WAIT);
    fireEvent.click(button("admin.comms.broadcasts.send"));
    fireEvent.click(await screen.findByRole("button", { name: tr("admin.comms.broadcasts.confirm", { count: 12 }) }, WAIT));
    expect(await screen.findByText(message, {}, WAIT)).toHaveAttribute("role", "alert");
    expect(document.body.textContent).not.toContain("The audience has no recipients");
    expect(document.body.textContent).not.toContain("plain text on one line");
  });
});

describe("broadcast history", () => {
  it("lists the broadcasts and shows a broadcast's delivery counts", async () => {
    const calls = renderAdmin(
      PATH,
      handlers({ [`GET /api/v1/notifications/broadcasts/${BROADCAST_ID}`]: () => json({ ...BROADCAST, delivery: COUNTS }) }),
      SYS,
    );
    const history = within(await screen.findByRole("region", { name: tr("admin.comms.broadcasts.historyTitle") }, WAIT));
    const card = within(history.getByRole("listitem"));
    expect(card.getByRole("heading", { name: BROADCAST.subject })).toBeInTheDocument();
    expect(card.getByText(tr("admin.comms.broadcasts.statuses.sent"))).toBeInTheDocument();
    expect(await card.findByText(tr("admin.comms.broadcasts.audienceProjects", { projects: "SYN-001" }), {}, WAIT)).toBeInTheDocument();
    expect(calls.some((c) => c.path === `/api/v1/notifications/broadcasts/${BROADCAST_ID}`)).toBe(false);

    fireEvent.click(card.getByRole("button", { name: tr("admin.comms.broadcasts.showDelivery") }));
    const delivery = within(
      await screen.findByRole("region", { name: tr("admin.comms.broadcasts.deliveryTitle", { subject: BROADCAST.subject }) }, WAIT),
    );
    expect(delivery.getByText("91.7%")).toBeInTheDocument();
    expect(delivery.getByText(tr("admin.comms.counts.bounced"))).toBeInTheDocument();
    expect(delivery.getByText(tr("admin.comms.counts.complained"))).toBeInTheDocument();
    expect(delivery.getByText(tr("admin.comms.counts.failed"))).toBeInTheDocument();
  });

  it("says when there are none", async () => {
    renderAdmin(PATH, handlers({ "GET /api/v1/notifications/broadcasts": () => json([]) }), SYS);
    expect(await screen.findByText(tr("admin.comms.broadcasts.empty"), {}, WAIT)).toBeInTheDocument();
  });
});

describe("who sees Communications", () => {
  it("refuses a user who administers nothing, without calling the API", async () => {
    const calls = renderAdmin(PATH, handlers(), { projects: [{ ...ADMIN_PROJECT, personas: ["PER-14"] }] });
    expect(await screen.findByText(tr("admin.comms.forbidden"), {}, WAIT)).toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/api/v1/notifications/"))).toBe(false);
    expect(screen.queryByRole("link", { name: tr("nav.items.communications") })).not.toBeInTheDocument();
  });

  it("shows the nav item and every tab to a System admin", async () => {
    renderAdmin(PATH, handlers(), SYS);
    await composer();
    expect(screen.getByRole("link", { name: tr("nav.items.communications") })).toHaveAttribute("href", PATH);
    const tabs = within(screen.getByRole("navigation", { name: tr("admin.comms.tabs") }));
    expect(tabs.getAllByRole("link").map((l) => l.textContent)).toEqual([
      tr("admin.comms.broadcastsTab"),
      tr("admin.comms.deliveryTab"),
      tr("admin.comms.templatesTab"),
    ]);
  });

  it("explains a 403 from the server", async () => {
    renderAdmin(PATH, handlers({ "GET /api/v1/notifications/broadcasts": () => json({ detail: "Forbidden" }, 403) }), SYS);
    expect(await screen.findByText(tr("admin.comms.forbidden"), {}, WAIT)).toBeInTheDocument();
  });
});
