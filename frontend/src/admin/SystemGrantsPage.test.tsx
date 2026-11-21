// Admin → System → Admin grants (T-0026, diff §4.1). Synthetic data only.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { i18n } from "../i18n";
import { json, type Recorded } from "../test/fakeApi";
import { renderAdmin } from "./testing";

// Mocked round-trips through the whole shell are slow under load (parallel suites).
vi.setConfig({ testTimeout: 20_000 });
const WAIT = { timeout: 5000 };

const PATH = "/admin/system/grants";
const ROOT = { id: "00000000-0000-4000-8000-000000000001", email: "root@example.test", display_name: "Synthetic Root", is_active: true, is_internal: true };
const PLANNER = { id: "00000000-0000-4000-8000-000000000302", email: "planner@example.test", display_name: "Synthetic Planner", is_active: true, is_internal: true };
const grant = (u: typeof ROOT, g: string) => ({ user_id: u.id, email: u.email, display_name: u.display_name, grant: g });

const handlers = (extra = {}) => ({
  "GET /api/v1/admin/users": () => json([PLANNER, ROOT]),
  "GET /api/v1/admin/grants": () => json([grant(ROOT, "system_admin")]),
  ...extra,
});
const box = (grantKey: "admin.system.systemAdmin" | "admin.system.projectAdmin", name: string) =>
  screen.getByRole("checkbox", { name: i18n.t("admin.system.grantLabel", { grant: i18n.t(grantKey), name }) });
const loaded = () => screen.findByText(PLANNER.email, {}, WAIT);

describe("admin grants (System admins)", () => {
  it("lists users with their admin grants and offers only the two tenant-level grants", async () => {
    renderAdmin(PATH, handlers(), { projects: [], systemAdmin: true });
    await loaded();
    expect(box("admin.system.systemAdmin", "Synthetic Root")).toBeChecked();
    expect(box("admin.system.projectAdmin", "Synthetic Root")).not.toBeChecked();
    expect(box("admin.system.systemAdmin", "Synthetic Planner")).not.toBeChecked();
    // Admin roles never offer hold release or sign-off (diff §4.1).
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("checkbox")).toHaveLength(4);
    expect(screen.getByText(i18n.t("admin.system.grantsNote"))).toBeInTheDocument();
  });

  it("appoints a Project Admin (create_projects)", async () => {
    const calls = renderAdmin(PATH, handlers({ "POST /api/v1/admin/grants": () => json(grant(PLANNER, "create_projects"), 201) }), { projects: [], systemAdmin: true });
    await loaded();
    fireEvent.click(box("admin.system.projectAdmin", "Synthetic Planner"));
    expect(await screen.findByText(i18n.t("admin.system.saved"), {}, WAIT)).toBeInTheDocument();
    expect(calls.find((c: Recorded) => c.method === "POST" && c.path === "/api/v1/admin/grants")?.body).toEqual({
      user_id: PLANNER.id,
      grant: "create_projects",
      confirm_sod: false,
    });
  });

  it("does not call a refused appointment (SoD, 409 on POST) the last System admin", async () => {
    const sod = { detail: { code: "sod_confirmation_required", sod_warning: true } };
    renderAdmin(PATH, handlers({ "POST /api/v1/admin/grants": () => json(sod, 409) }), { projects: [], systemAdmin: true });
    await loaded();
    fireEvent.click(box("admin.system.systemAdmin", "Synthetic Planner"));
    expect(await screen.findByText(i18n.t("admin.actionError"), {}, WAIT)).toBeInTheDocument();
    expect(screen.queryByText(i18n.t("admin.system.lastSystemAdmin"))).not.toBeInTheDocument();
  });

  it("says so when the last System admin would be removed (409)", async () => {
    const del = `DELETE /api/v1/admin/grants/${ROOT.id}/system_admin`;
    const calls = renderAdmin(PATH, handlers({ [del]: () => json({ detail: "last" }, 409) }), { projects: [], systemAdmin: true });
    await loaded();
    fireEvent.click(box("admin.system.systemAdmin", "Synthetic Root"));
    expect(await screen.findByText(i18n.t("admin.system.lastSystemAdmin"), {}, WAIT)).toBeInTheDocument();
    await waitFor(() => {
      expect(calls.filter((c) => c.path === "/api/v1/admin/users").length).toBeGreaterThan(1);
    }, WAIT);
    expect(box("admin.system.systemAdmin", "Synthetic Root")).toBeChecked();
  });

  it("refuses everyone else (403) and hides the tab from them", async () => {
    renderAdmin(PATH, { "GET /api/v1/admin/users": () => json({ detail: "Forbidden" }, 403) });
    expect(await screen.findByText(i18n.t("admin.system.forbidden"), {}, WAIT)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: i18n.t("admin.system.grantsTab") })).not.toBeInTheDocument();
  });
});
