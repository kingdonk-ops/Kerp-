// Backbone → Projects: create, settings, visible in lobby (T-0026, diff §4.1). Synthetic data only.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { i18n } from "../i18n";
import { json } from "../test/fakeApi";
import { ADMIN_PROJECT, project, renderAdmin } from "./testing";

// Mocked round-trips through the whole shell are slow under load (parallel suites).
vi.setConfig({ testTimeout: 20_000 });
const WAIT = { timeout: 5000 };

const PATH = "/backbone/projects";
const OTHER = project({ id: "00000000-0000-4000-8000-000000000102", code: "SYN-002", name: "Synthetic Tank Farm" });
// The open dialog, found without a role query over the whole shell (slow under load).
const dialog = () => {
  const el = document.querySelector<HTMLElement>('[role="dialog"]');
  if (!el) throw new Error("no dialog");
  return el;
};

async function openNew() {
  fireEvent.click(await screen.findByText(i18n.t("admin.projects.new"), {}, WAIT));
  fireEvent.change(screen.getByLabelText(i18n.t("admin.projects.code")), { target: { value: "SYN-009" } });
  fireEvent.change(screen.getByLabelText(i18n.t("admin.projects.name")), { target: { value: "Synthetic Jetty" } });
}

describe("projects", () => {
  it("creates a project with the deployment defaults; the creator lands in it as PER-09", async () => {
    let created = false;
    const calls = renderAdmin(PATH, {
      "GET /api/v1/projects": () => json(created ? [project({ code: "SYN-009", name: "Synthetic Jetty" })] : []),
      "POST /api/v1/projects": () => {
        created = true;
        return json(project({ code: "SYN-009", name: "Synthetic Jetty" }), 201);
      },
    }, { projects: [] });
    await openNew();
    fireEvent.click(within(dialog()).getByRole("button", { name: i18n.t("admin.projects.create") }));

    expect(await screen.findByText(i18n.t("admin.projects.created"), {}, WAIT)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/projects")?.body).toEqual({
      code: "SYN-009",
      name: "Synthetic Jetty",
    });
    expect(await screen.findByText("SYN-009 — Synthetic Jetty", {}, WAIT)).toBeInTheDocument();
    // The project picker refreshes: the new PER-09 membership shows up there.
    expect(calls.filter((c) => c.path === "/api/v1/me/projects").length).toBeGreaterThan(1);
  });

  it("checks the code pattern before sending", async () => {
    renderAdmin(PATH, { "GET /api/v1/projects": () => json([]) }, { projects: [] });
    await openNew();
    fireEvent.change(screen.getByLabelText(i18n.t("admin.projects.code")), { target: { value: "-bad code" } });
    expect(within(dialog()).getByRole("button", { name: i18n.t("admin.projects.create") })).toBeDisabled();
  });

  it.each([
    [403, "admin.projects.forbidden"],
    [409, "admin.projects.duplicate"],
  ] as const)("explains a refused creation (%i)", async (status, key) => {
    renderAdmin(PATH, {
      "GET /api/v1/projects": () => json([]),
      "POST /api/v1/projects": () => json({ detail: "no" }, status),
    }, { projects: [] });
    await openNew();
    fireEvent.click(within(dialog()).getByRole("button", { name: i18n.t("admin.projects.create") }));
    expect(await within(dialog()).findByRole("alert", {}, WAIT)).toHaveTextContent(i18n.t(key));
  });

  it("lets a PER-09 edit settings and toggle visible in lobby only on its own projects", async () => {
    const lobby = `/api/v1/projects/${ADMIN_PROJECT.id}/lobby`;
    const calls = renderAdmin(PATH, {
      "GET /api/v1/projects": () => json([project(), OTHER]),
      [`PUT ${lobby}`]: () => json(project({ visible_in_lobby: true })),
      [`PATCH /api/v1/projects/${ADMIN_PROJECT.id}`]: () => json(project({ name: "Renamed" })),
    }, { projects: [ADMIN_PROJECT, { ...ADMIN_PROJECT, id: OTHER.id, code: OTHER.code, name: OTHER.name, personas: ["PER-14"] }] });

    const main = within(await screen.findByRole("main", {}, WAIT));
    const own = (await main.findByText("SYN-001 — Synthetic Refinery Unit", {}, WAIT)).closest("li");
    const other = main.getByText("SYN-002 — Synthetic Tank Farm").closest("li");
    if (!own || !other) throw new Error("rows");
    expect(within(other).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(within(other).queryByRole("link")).not.toBeInTheDocument();
    expect(within(own).getByRole("link", { name: i18n.t("admin.projects.members") })).toHaveAttribute(
      "href",
      `/admin/projects/${ADMIN_PROJECT.id}/members`,
    );

    fireEvent.click(within(own).getByRole("checkbox", { name: i18n.t("admin.projects.lobby") }));
    await waitFor(() => {
      expect(calls.find((c) => c.path === lobby)?.body).toEqual({ visible_in_lobby: true });
    }, WAIT);

    fireEvent.click(within(own).getByRole("button", { name: i18n.t("admin.projects.settings") }));
    fireEvent.change(screen.getByLabelText(i18n.t("admin.projects.name")), { target: { value: "Renamed" } });
    fireEvent.change(screen.getByLabelText(i18n.t("admin.projects.statusLabel")), { target: { value: "ON_HOLD" } });
    fireEvent.click(within(dialog()).getByText(i18n.t("admin.projects.save")));
    expect(await screen.findByText(i18n.t("admin.projects.savedSettings"), {}, WAIT)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "Renamed", status: "ON_HOLD" });
  });
});
