// Project admin page: legal hold (System admins only) and delete under hold (T-0225, diff §4.7,
// §6.5). Synthetic data only.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { i18n } from "../i18n";
import { json, type Recorded } from "../test/fakeApi";
import { ADMIN_PROJECT, project, renderAdmin } from "./testing";

vi.setConfig({ testTimeout: 20_000 });
const WAIT = { timeout: 5000 };

const P = ADMIN_PROJECT.id;
const PATH = `/admin/projects/${P}/members`;
const HOLD = `/api/v1/admin/system/projects/${P}/legal-hold`;
const LABEL = "SYN-001 — Synthetic Refinery Unit";
const handlers = (onHold: boolean, extra = {}) => ({
  "GET /api/v1/projects": () => json([project({ legal_hold: onHold })]),
  [`GET /api/v1/admin/projects/${P}/members`]: () => json([]),
  ...extra,
});
const panel = () => within(screen.getByRole("region", { name: i18n.t("admin.projectAdmin.title") }));
const loaded = () => screen.findByRole("region", { name: i18n.t("admin.projectAdmin.title") }, WAIT);
const dialog = () => within(screen.getByRole("dialog"));
const holdResponse = (on: boolean) =>
  json({
    project_id: P,
    legal_hold: on,
    legal_hold_reason: on ? "Synthetic claim" : null,
    legal_hold_set_by: null,
    legal_hold_set_at: null,
  });

describe("project admin: legal hold", () => {
  it("puts a project on hold with a mandatory reason (System admin)", async () => {
    let on = false;
    const calls = renderAdmin(
      PATH,
      handlers(false, {
        "GET /api/v1/projects": () => json([project({ legal_hold: on })]),
        [`PUT ${HOLD}`]: () => {
          on = true;
          return holdResponse(true);
        },
      }),
      { systemAdmin: true },
    );
    await loaded();
    expect(panel().getByText(i18n.t("admin.projectAdmin.holdOff"))).toBeInTheDocument();
    fireEvent.click(await panel().findByRole("button", { name: i18n.t("admin.projectAdmin.setHold") }, WAIT));
    expect(screen.getByRole("dialog", { name: i18n.t("admin.projectAdmin.setTitle", { project: LABEL }) })).toBeInTheDocument();
    const confirm = dialog().getByRole("button", { name: i18n.t("admin.projectAdmin.setHold") });
    expect(confirm).toBeDisabled();
    fireEvent.change(dialog().getByLabelText(i18n.t("admin.projectAdmin.reason")), { target: { value: "   " } });
    expect(confirm).toBeDisabled();
    fireEvent.change(dialog().getByLabelText(i18n.t("admin.projectAdmin.reason")), {
      target: { value: " Synthetic claim " },
    });
    fireEvent.click(confirm);
    expect(await screen.findByText(i18n.t("admin.projectAdmin.holdSet"), {}, WAIT)).toBeInTheDocument();
    expect(calls.find((c) => c.path === HOLD)?.body).toEqual({ on: true, reason: "Synthetic claim" });
    expect(await panel().findByText(i18n.t("admin.projectAdmin.holdOn"), {}, WAIT)).toBeInTheDocument();
  });

  it("lifts a hold, again with a reason", async () => {
    const calls = renderAdmin(PATH, handlers(true, { [`PUT ${HOLD}`]: () => holdResponse(false) }), {
      systemAdmin: true,
    });
    await loaded();
    expect(panel().getByText(i18n.t("admin.projectAdmin.holdOn"))).toBeInTheDocument();
    fireEvent.click(await panel().findByRole("button", { name: i18n.t("admin.projectAdmin.clearHold") }, WAIT));
    fireEvent.change(dialog().getByLabelText(i18n.t("admin.projectAdmin.reason")), { target: { value: "Settled" } });
    fireEvent.click(dialog().getByRole("button", { name: i18n.t("admin.projectAdmin.clearHold") }));
    expect(await screen.findByText(i18n.t("admin.projectAdmin.holdCleared"), {}, WAIT)).toBeInTheDocument();
    expect(calls.find((c) => c.path === HOLD)?.body).toEqual({ on: false, reason: "Settled" });
  });

  it("is not offered to a PER-09 who is no System admin", async () => {
    renderAdmin(PATH, handlers(true));
    await loaded();
    // The state is shown (it explains why delete is refused), the toggle is not.
    expect(panel().getByText(i18n.t("admin.projectAdmin.holdOn"))).toBeInTheDocument();
    expect(panel().queryByRole("button", { name: i18n.t("admin.projectAdmin.clearHold") })).not.toBeInTheDocument();
    expect(panel().queryByRole("button", { name: i18n.t("admin.projectAdmin.setHold") })).not.toBeInTheDocument();
  });
});

describe("project admin: delete", () => {
  const del = `DELETE /api/v1/projects/${P}`;
  const deleteNow = async () => {
    await loaded();
    fireEvent.click(await panel().findByRole("button", { name: i18n.t("admin.projectAdmin.delete") }, WAIT));
    expect(screen.getByRole("dialog", { name: i18n.t("admin.projectAdmin.deleteTitle", { project: LABEL }) })).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: i18n.t("admin.projectAdmin.delete") }));
  };

  it("is not offered to a member without PER-09 who is no System admin", async () => {
    renderAdmin(PATH, handlers(false, { [`GET /api/v1/admin/projects/${P}/members`]: () => json({ detail: "Forbidden" }, 403) }), {
      projects: [{ ...ADMIN_PROJECT, personas: ["PER-04"] }],
    });
    await loaded();
    // Wait until the access probe has settled (the hold state renders from the same project list).
    await waitFor(() => { expect(panel().getByText(i18n.t("admin.projectAdmin.holdOff"))).toBeInTheDocument(); }, WAIT);
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(panel().queryByRole("button", { name: i18n.t("admin.projectAdmin.delete") })).not.toBeInTheDocument();
  });

  it("is offered to a System admin who is no member", async () => {
    renderAdmin(PATH, handlers(false), { projects: [], systemAdmin: true });
    expect(await screen.findByRole("button", { name: i18n.t("admin.projectAdmin.delete") }, WAIT)).toBeInTheDocument();
  });

  it("shows the translated LEGAL_HOLD_ACTIVE message when the project is on hold", async () => {
    renderAdmin(
      PATH,
      handlers(true, {
        [del]: () => json({ detail: { code: "LEGAL_HOLD_ACTIVE", message: "Project is on legal hold" } }, 409),
      }),
    );
    await deleteNow();
    expect(
      await screen.findByText(i18n.t("admin.projectAdmin.conflicts.LEGAL_HOLD_ACTIVE"), {}, WAIT),
    ).toBeInTheDocument();
    expect(screen.queryByText("Project is on legal hold")).not.toBeInTheDocument();
  });

  it("explains a project still linked to teams and a 403", async () => {
    let status = 409;
    renderAdmin(
      PATH,
      handlers(false, {
        [del]: () =>
          status === 409
            ? json({ detail: { code: "team_links_present", message: "x" } }, 409)
            : json({ detail: "Forbidden" }, 403),
      }),
    );
    await deleteNow();
    expect(await screen.findByText(i18n.t("admin.projectAdmin.conflicts.team_links_present"), {}, WAIT)).toBeInTheDocument();
    status = 403;
    fireEvent.click(panel().getByRole("button", { name: i18n.t("admin.projectAdmin.delete") }));
    fireEvent.click(dialog().getByRole("button", { name: i18n.t("admin.projectAdmin.delete") }));
    expect(await screen.findByText(i18n.t("admin.projectAdmin.forbidden"), {}, WAIT)).toBeInTheDocument();
  });

  it("deletes after confirmation and returns to the projects", async () => {
    const calls: Recorded[] = renderAdmin(
      PATH,
      handlers(false, { [del]: () => new Response(null, { status: 204 }) }),
    );
    await deleteNow();
    await screen.findByRole("heading", { name: i18n.t("admin.projects.title") }, WAIT);
    await waitFor(() => { expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1); });
  });
});
