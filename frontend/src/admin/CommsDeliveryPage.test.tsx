// Admin → Communications → Delivery (T-0229, diff §4.7, §6.5). Synthetic data only.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { i18n } from "../i18n";
import { json } from "../test/fakeApi";
import { periodQuery } from "./comms";
import { SEND, STATS, TEMPLATES } from "./commsFixtures";
import { renderAdmin } from "./testing";

vi.setConfig({ testTimeout: 20_000 });
const WAIT = { timeout: 5000 };

const PATH = "/admin/communications/delivery";
const handlers = (extra = {}) => ({
  "GET /api/v1/mail/stats": () => json(STATS),
  "GET /api/v1/mail/sends": () => json([SEND]),
  "GET /api/v1/mail/templates": () => json(TEMPLATES),
  ...extra,
});
const SYS = { projects: [], systemAdmin: true };
/** `t()` over keys built at run time (the typed `t` takes literal keys only). */
const tr = i18n.t.bind(i18n) as unknown as (key: string, vars?: Record<string, unknown>) => string;
const region = async (key: string) => within(await screen.findByRole("region", { name: tr(key) }, WAIT));

describe("delivery stats", () => {
  it("shows the count cards and the counts per template", async () => {
    renderAdmin(PATH, handlers(), SYS);
    const stats = await region("admin.comms.delivery.statsTitle");
    const cards = await stats.findAllByRole("listitem", {}, WAIT);
    const text = cards.map((c) => c.textContent);
    expect(text).toContain(`${tr("admin.comms.counts.sent")}36`);
    expect(text).toContain(`${tr("admin.comms.counts.deliveryRate")}91.7%`);
    expect(text).toContain(`${tr("admin.comms.counts.bounced")}2`);
    expect(text).toContain(`${tr("admin.comms.counts.complained")}1`);
    expect(text).toContain(`${tr("admin.comms.counts.failed")}1`);
    const byTemplate = within(stats.getByRole("region", { name: tr("admin.comms.delivery.byTemplateTitle") }));
    expect(byTemplate.getAllByRole("heading", { level: 4 }).map((h) => h.textContent)).toEqual([
      "auth.magic_link",
      "notifications.broadcast",
    ]);
    // Nothing sent: no rate.
    expect(byTemplate.getAllByText(tr("admin.comms.counts.noRate"))).toHaveLength(1);
  });

  it("filters by period and template", async () => {
    const calls = renderAdmin(PATH, handlers(), SYS);
    const stats = await region("admin.comms.delivery.statsTitle");
    fireEvent.change(stats.getByLabelText(tr("admin.comms.delivery.from")), { target: { value: "2026-11-01" } });
    fireEvent.change(stats.getByLabelText(tr("admin.comms.delivery.to")), { target: { value: "2026-11-30" } });
    await stats.findByRole("option", { name: "auth.magic_link" }, WAIT);
    fireEvent.change(stats.getByLabelText(tr("admin.comms.delivery.template")), { target: { value: "auth.magic_link" } });
    fireEvent.click(stats.getByRole("button", { name: tr("admin.comms.delivery.apply") }));
    const expected = new URLSearchParams({
      template: "auth.magic_link",
      ...periodQuery({ from: "2026-11-01", to: "2026-11-30" }),
    });
    await waitFor(() => {
      const last = calls.filter((c) => c.path === "/api/v1/mail/stats").at(-1);
      expect(Object.fromEntries(new URLSearchParams(last?.query))).toEqual(Object.fromEntries(expected));
    }, WAIT);
  });

  it("refuses a period that ends before it starts", async () => {
    renderAdmin(PATH, handlers(), SYS);
    const stats = await region("admin.comms.delivery.statsTitle");
    fireEvent.change(stats.getByLabelText(tr("admin.comms.delivery.from")), { target: { value: "2026-11-30" } });
    fireEvent.change(stats.getByLabelText(tr("admin.comms.delivery.to")), { target: { value: "2026-11-01" } });
    expect(stats.getByText(tr("admin.comms.delivery.periodInvalid"))).toBeInTheDocument();
    expect(stats.getByRole("button", { name: tr("admin.comms.delivery.apply") })).toBeDisabled();
  });
});

describe("delivery history", () => {
  it("shows masked recipients only and looks an exact address up", async () => {
    const calls = renderAdmin(PATH, handlers(), SYS);
    const history = await region("admin.comms.delivery.historyTitle");
    const card = within(await history.findByRole("listitem", {}, WAIT));
    expect(card.getByText("f***@example.test")).toBeInTheDocument();
    expect(card.getByText(tr("admin.comms.delivery.statuses.bounced"))).toBeInTheDocument();
    expect(card.queryByText(SEND.recipient_hash)).not.toBeInTheDocument();

    const address = history.getByLabelText(tr("admin.comms.delivery.address"));
    fireEvent.change(address, { target: { value: "not-an-address" } });
    expect(history.getByText(tr("admin.comms.delivery.addressInvalid"))).toBeInTheDocument();
    expect(history.getByRole("button", { name: tr("admin.comms.delivery.search") })).toBeDisabled();

    fireEvent.change(address, { target: { value: "fitter@example.test" } });
    fireEvent.change(history.getByLabelText(tr("admin.comms.delivery.status")), { target: { value: "bounced" } });
    fireEvent.click(history.getByRole("button", { name: tr("admin.comms.delivery.search") }));
    await waitFor(() => {
      const last = calls.filter((c) => c.path === "/api/v1/mail/sends").at(-1);
      expect(Object.fromEntries(new URLSearchParams(last?.query))).toEqual({
        status: "bounced",
        address: "fitter@example.test",
        limit: "50",
        offset: "0",
      });
    }, WAIT);
    // The address is never shown back.
    await waitFor(() => { expect(screen.queryAllByText("fitter@example.test")).toHaveLength(0); }, WAIT);
  });

  it("says when nothing matches", async () => {
    renderAdmin(PATH, handlers({ "GET /api/v1/mail/sends": () => json([]) }), SYS);
    expect(await screen.findByText(tr("admin.comms.delivery.empty"), {}, WAIT)).toBeInTheDocument();
  });
});

describe("delivery access", () => {
  it("refuses a PER-09 without calling the mail API", async () => {
    const calls = renderAdmin(PATH, handlers());
    expect(await screen.findByText(tr("admin.comms.forbiddenSystem"), {}, WAIT)).toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/api/v1/mail/"))).toBe(false);
  });

  it("explains a 403 from the server", async () => {
    renderAdmin(PATH, handlers({ "GET /api/v1/mail/stats": () => json({ detail: "Forbidden" }, 403) }), SYS);
    expect(await screen.findByText(tr("admin.comms.forbiddenSystem"), {}, WAIT)).toBeInTheDocument();
  });
});
