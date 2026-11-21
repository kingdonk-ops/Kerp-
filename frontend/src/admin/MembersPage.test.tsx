// Project members: personas + site scope, several projects at once, SoD warning (T-0026,
// diff §4.1, edge spec §5). Synthetic data only.
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { i18n } from "../i18n";
import { json, type Recorded } from "../test/fakeApi";
import type { Member } from "./api";
import { ADMIN_PROJECT, project, renderAdmin } from "./testing";

// Mocked round-trips through the whole shell are slow under load (parallel suites).
vi.setConfig({ testTimeout: 20_000 });
const WAIT = { timeout: 5000 };

const P1 = ADMIN_PROJECT.id;
const P2 = "00000000-0000-4000-8000-000000000102";
const SECOND = { ...ADMIN_PROJECT, id: P2, code: "SYN-002", name: "Synthetic Tank Farm" };
const LABEL1 = "SYN-001 — Synthetic Refinery Unit";
const LABEL2 = "SYN-002 — Synthetic Tank Farm";
const PATH = `/admin/projects/${P1}/members`;
const FITTER = { id: "00000000-0000-4000-8000-000000000301", email: "fitter@example.test", display_name: "Synthetic Fitter" };
const SITE = "00000000-0000-4000-8000-000000000401";
const SITE_B = "00000000-0000-4000-8000-000000000402";

type Grant = Member["grants"][number];
const grant = (persona: Grant["persona"], extra: Partial<Grant> = {}): Grant => ({
  persona, site_node_id: null, source: "direct", source_id: null, team_name: null, ...extra,
});
const member = (grants: Member["grants"]): Member => ({ user_id: FITTER.id, email: FITTER.email, display_name: FITTER.display_name, grants });
const put = (p: string) => `PUT /api/v1/admin/projects/${p}/members/${FITTER.id}`;
const putPath = (p: string) => `/api/v1/admin/projects/${p}/members/${FITTER.id}`;
const puts = (calls: Recorded[]) => calls.filter((c) => c.method === "PUT");
const ok = () => json({ ...member([]), sod_warning: false });
/** 409 SoD unless the PUT confirms it. */
const sodUnlessConfirmed = (req: Recorded) =>
  (req.body as { confirm_sod: boolean }).confirm_sod
    ? json({ ...member([]), sod_warning: true })
    : json({ detail: { code: "sod_confirmation_required", sod_warning: true } }, 409);
// The open dialog, found without a role query over the whole shell (slow under load).
const dialog = () => {
  const el = document.querySelector<HTMLElement>('[role="dialog"]');
  if (!el) throw new Error("no dialog");
  return el;
};
const check = (label: RegExp | string) => {
  fireEvent.click(within(dialog()).getByLabelText(label));
};
const save = (key: "admin.members.save" | "admin.members.confirmSod" | "admin.members.remove" | "admin.projects.create" = "admin.members.save") => {
  fireEvent.click(within(dialog()).getByRole("button", { name: i18n.t(key) }));
};
const saved = () => screen.findByText(i18n.t("admin.members.saved"), {}, WAIT);
const sodAlert = (project: string) =>
  within(dialog()).findByText(i18n.t("admin.members.sodWarning", { project }), {}, WAIT);

function base(members: Record<string, Member[]>, extra = {}) {
  return {
    "GET /api/v1/projects": () => json([project(), project({ id: P2, code: SECOND.code, name: SECOND.name })]),
    [`GET /api/v1/admin/projects/${P1}/members`]: () => json(members[P1] ?? []),
    [`GET /api/v1/admin/projects/${P2}/members`]: () => json(members[P2] ?? []),
    "POST /api/v1/admin/users/lookup": () => json(FITTER),
    ...extra,
  };
}

async function find(email = FITTER.email) {
  fireEvent.change(await screen.findByLabelText(i18n.t("admin.members.addEmail"), {}, WAIT), { target: { value: email } });
  // Text queries: role queries over the whole shell are slow under load.
  fireEvent.click(screen.getByText(i18n.t("admin.members.find")));
  if (email === FITTER.email) {
    await screen.findByText(i18n.t("admin.members.addTitle", { name: FITTER.display_name }), {}, WAIT);
  }
}

async function edit() {
  fireEvent.click(await screen.findByText(i18n.t("admin.members.edit"), {}, WAIT));
}

describe("project members", () => {
  it("lists members with personas, site and team-derived grants (read-only)", async () => {
    renderAdmin(PATH, base({ [P1]: [member([grant("PER-07", { site_node_id: SITE }), grant("PER-16", { source: "team", team_name: "Synthetic Crew" })])] }));
    const row = (await screen.findByText(i18n.t("admin.accessRequests.requester", { name: FITTER.display_name, email: FITTER.email }), {}, WAIT)).closest("li");
    if (!row) throw new Error("row");
    expect(row).toHaveTextContent(i18n.t("admin.members.site", { site: SITE }));
    expect(row).toHaveTextContent(i18n.t("admin.members.team", { team: "Synthetic Crew" }));
    expect(await screen.findByText(i18n.t("admin.members.title", { project: LABEL1 }), {}, WAIT)).toBeInTheDocument();
  });

  it("assigns a user found by e-mail to several of the admin's projects in one action", async () => {
    // On P2 the user already holds PER-16 and PER-07 on two sites: all are kept.
    const held = [grant("PER-16"), grant("PER-07", { site_node_id: SITE }), grant("PER-07", { site_node_id: SITE_B })];
    const calls = renderAdmin(PATH, base({ [P2]: [member(held)] }, { [put(P1)]: ok, [put(P2)]: ok }), { projects: [ADMIN_PROJECT, SECOND] });
    await find();
    check(/PER-14/);
    check(LABEL2);
    save();

    expect(await saved()).toBeInTheDocument();
    expect(puts(calls).map((c) => [c.path, c.body])).toEqual([
      [putPath(P1), { grants: [{ persona: "PER-14" }], confirm_sod: false }],
      [putPath(P2), {
        grants: [
          { persona: "PER-16" },
          { persona: "PER-07", site_node_id: SITE },
          { persona: "PER-07", site_node_id: SITE_B },
          { persona: "PER-14" },
        ],
        confirm_sod: false,
      }],
    ]);
  });

  it("adds PER-07 with a site on this project only: other projects are not offered", async () => {
    // On P1 the user already holds PER-07 on another site: adding SITE keeps it.
    const calls = renderAdmin(PATH, base({ [P1]: [member([grant("PER-07", { site_node_id: SITE_B })])] }, { [put(P1)]: ok, [put(P2)]: ok }), { projects: [ADMIN_PROJECT, SECOND] });
    await find();
    check(LABEL2);
    check(/PER-07/);
    expect(dialog()).toHaveTextContent(i18n.t("admin.members.siteThisProject"));
    const other = within(dialog()).getByLabelText(LABEL2);
    expect(other).toBeDisabled();
    expect(other).not.toBeChecked();
    fireEvent.change(within(dialog()).getByLabelText(i18n.t("admin.accessRequests.site")), { target: { value: SITE } });
    save();

    expect(await saved()).toBeInTheDocument();
    expect(puts(calls).map((c) => [c.path, c.body])).toEqual([
      [putPath(P1), {
        grants: [{ persona: "PER-07", site_node_id: SITE_B }, { persona: "PER-07", site_node_id: SITE }],
        confirm_sod: false,
      }],
    ]);
  });

  it("keeps every PER-07 site of a member through an unrelated edit, and removes one on its own", async () => {
    const held = [grant("PER-07", { site_node_id: SITE }), grant("PER-07", { site_node_id: SITE_B }), grant("PER-14")];
    const calls = renderAdmin(PATH, base({ [P1]: [member(held)] }, { [put(P1)]: ok }));
    await edit();
    const siteA = within(dialog()).getByLabelText(i18n.t("admin.members.heldSite", { site: SITE }));
    const siteB = within(dialog()).getByLabelText(i18n.t("admin.members.heldSite", { site: SITE_B }));
    expect(siteA).toBeChecked();
    expect(siteB).toBeChecked();
    check(/PER-16/);
    save();
    expect(await saved()).toBeInTheDocument();
    expect(puts(calls)[0]?.body).toEqual({
      grants: [
        { persona: "PER-14" },
        { persona: "PER-16" },
        { persona: "PER-07", site_node_id: SITE },
        { persona: "PER-07", site_node_id: SITE_B },
      ],
      confirm_sod: false,
    });

    await edit();
    fireEvent.click(within(dialog()).getByLabelText(i18n.t("admin.members.heldSite", { site: SITE_B })));
    save();
    await waitFor(() => {
      expect(puts(calls)).toHaveLength(2);
    }, WAIT);
    expect(puts(calls)[1]?.body).toEqual({
      grants: [{ persona: "PER-14" }, { persona: "PER-07", site_node_id: SITE }],
      confirm_sod: false,
    });
  });

  it("says when no active account has the e-mail", async () => {
    renderAdmin(PATH, base({}, { "POST /api/v1/admin/users/lookup": () => json({ detail: "Not Found" }, 404) }));
    await find("nobody@example.test");
    expect(await screen.findByText(i18n.t("admin.members.notFound"), {}, WAIT)).toBeInTheDocument();
  });

  it("shows the SoD warning for PER-09 + a hold persona and saves only after confirmation", async () => {
    const calls = renderAdmin(PATH, base({ [P1]: [member([grant("PER-14")])] }, { [put(P1)]: sodUnlessConfirmed }));
    await edit();
    expect(within(dialog()).getByLabelText(/PER-14/)).toBeChecked();
    check(/PER-09/);
    check(/PER-06/);
    save();

    expect(await sodAlert(LABEL1)).toBeInTheDocument();
    expect(puts(calls)).toHaveLength(1);
    save("admin.members.confirmSod");
    expect(await saved()).toBeInTheDocument();
    expect(puts(calls).map((c) => c.body)).toEqual([
      { grants: [{ persona: "PER-06" }, { persona: "PER-09" }, { persona: "PER-14" }], confirm_sod: false },
      { grants: [{ persona: "PER-06" }, { persona: "PER-09" }, { persona: "PER-14" }], confirm_sod: true },
    ]);
  });

  it("confirms SoD project by project: each confirmation follows a warning naming its project", async () => {
    const calls = renderAdmin(PATH, base({}, { [put(P1)]: sodUnlessConfirmed, [put(P2)]: sodUnlessConfirmed }), { projects: [ADMIN_PROJECT, SECOND] });
    await find();
    check(/PER-09/);
    check(/PER-06/);
    check(LABEL2);
    save();

    expect(await sodAlert(LABEL1)).toBeInTheDocument();
    save("admin.members.confirmSod");
    expect(await sodAlert(LABEL2)).toBeInTheDocument();
    expect(within(dialog()).queryByText(i18n.t("admin.members.sodWarning", { project: LABEL1 }))).not.toBeInTheDocument();
    save("admin.members.confirmSod");
    expect(await saved()).toBeInTheDocument();
    expect(puts(calls).map((c) => [c.path, (c.body as { confirm_sod: boolean }).confirm_sod])).toEqual([
      [putPath(P1), false],
      [putPath(P1), true],
      [putPath(P2), false],
      [putPath(P2), true],
    ]);
  });

  it("names the project where a multi-project add failed", async () => {
    renderAdmin(PATH, base({}, { [put(P1)]: ok, [put(P2)]: () => json({ detail: "boom" }, 500) }), { projects: [ADMIN_PROJECT, SECOND] });
    await find();
    check(/PER-14/);
    check(LABEL2);
    save();
    expect(await within(dialog()).findByText(i18n.t("admin.members.failedAt", { project: LABEL2 }), {}, WAIT)).toBeInTheDocument();
  });

  it("removes a member after confirmation; team-only access is explained (409)", async () => {
    const del = `DELETE /api/v1/admin/projects/${P1}/members/${FITTER.id}`;
    const calls = renderAdmin(PATH, base({ [P1]: [member([grant("PER-14")])] }, {
      [del]: () => json({ detail: { code: "managed_through_team" } }, 409),
    }));
    fireEvent.click(await screen.findByText(i18n.t("admin.members.remove"), {}, WAIT));
    expect(dialog()).toHaveTextContent(i18n.t("admin.members.removeBody"));
    save("admin.members.remove");
    expect(await screen.findByText(i18n.t("admin.members.teamOnly"), {}, WAIT)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(true);
  });
});

describe("journey: System admin appoints a Project Admin → they create a project → assign a user", () => {
  it("runs through the three screens", async () => {
    const planner = { id: "00000000-0000-4000-8000-000000000302", email: "planner@example.test", display_name: "Synthetic Planner", is_active: true, is_internal: true };
    const sys = renderAdmin("/admin/system/grants", {
      "GET /api/v1/admin/users": () => json([planner]),
      "POST /api/v1/admin/grants": () => json({ user_id: planner.id, email: planner.email, display_name: planner.display_name, grant: "create_projects" }, 201),
    }, { projects: [], systemAdmin: true });
    await screen.findByText(planner.email, {}, WAIT);
    fireEvent.click(screen.getByLabelText(
      i18n.t("admin.system.grantLabel", { grant: i18n.t("admin.system.projectAdmin"), name: planner.display_name }),
    ));
    expect(await screen.findByText(i18n.t("admin.system.saved"), {}, WAIT)).toBeInTheDocument();
    expect(sys.find((c) => c.method === "POST")?.body).toEqual({ user_id: planner.id, grant: "create_projects" });
    cleanup();

    // The new Project Admin (no project yet) creates one and becomes its PER-09.
    let mine: typeof ADMIN_PROJECT[] = [];
    const created = renderAdmin("/backbone/projects", {
      "GET /api/v1/projects": () => json(mine.length ? [project()] : []),
      "GET /api/v1/me/projects": () => json(mine),
      "POST /api/v1/projects": () => {
        mine = [ADMIN_PROJECT];
        return json(project(), 201);
      },
      [`GET /api/v1/admin/projects/${P1}/members`]: () => json([]),
      "POST /api/v1/admin/users/lookup": () => json(FITTER),
      [put(P1)]: () => json({ ...member([grant("PER-14")]), sod_warning: false }),
    }, { projects: [] });
    fireEvent.click(await screen.findByText(i18n.t("admin.projects.new"), {}, WAIT));
    fireEvent.change(screen.getByLabelText(i18n.t("admin.projects.code")), { target: { value: ADMIN_PROJECT.code } });
    fireEvent.change(screen.getByLabelText(i18n.t("admin.projects.name")), { target: { value: ADMIN_PROJECT.name } });
    save("admin.projects.create");
    fireEvent.click(await screen.findByText(i18n.t("admin.projects.members"), {}, WAIT));
    expect(created.some((c) => c.method === "POST" && c.path === "/api/v1/projects")).toBe(true);

    // … and assigns a user on the new project's members screen.
    await find();
    check(/PER-14/);
    save();
    expect(await saved()).toBeInTheDocument();
    expect(created.find((c) => c.method === "PUT")?.path).toBe(putPath(P1));
  });
});
