// Admin → System → Upload quarantine (T-0224, diff §6.5). Synthetic data only.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { i18n } from "../i18n";
import { json, type Recorded } from "../test/fakeApi";
import { countsByProject, PAGE_SIZE } from "./system";
import { COUNTS, INFECTED_FILE, PROJECT_A, PROJECT_B, STUCK_FILE } from "./systemFixtures";
import { renderAdmin } from "./testing";

vi.setConfig({ testTimeout: 20_000 });
const WAIT = { timeout: 5000 };

const PATH = "/admin/system/uploads";
const RECHECK = `/api/v1/admin/system/uploads/${STUCK_FILE.id}/recheck`;
const SWEEP = "/api/v1/admin/system/uploads/sweep";
const page = (items = [STUCK_FILE, INFECTED_FILE], total = items.length, offset = 0) => ({
  items,
  total,
  limit: PAGE_SIZE,
  offset,
});
const handlers = (extra = {}) => ({
  "GET /api/v1/admin/system/uploads": () => json(page()),
  "GET /api/v1/admin/system/uploads/counts": () => json(COUNTS),
  ...extra,
});
const SYS = { projects: [], systemAdmin: true };
const region = (name: string) => within(screen.getByRole("region", { name }));
const loaded = () => screen.findByText(STUCK_FILE.filename, {}, WAIT);
const fileRow = (name: string) =>
  within(region(i18n.t("admin.quarantine.filesTitle")).getByRole("listitem", { name }));
const countsRow = (code: string) =>
  within(region(i18n.t("admin.quarantine.countsTitle")).getByRole("listitem", { name: code }));
const lists = (calls: Recorded[]) => calls.filter((c) => c.path === "/api/v1/admin/system/uploads");

describe("upload quarantine", () => {
  it("lists rows of every project with status, reason, signature and the deleted object", async () => {
    renderAdmin(PATH, handlers(), SYS);
    await loaded();
    const stuck = fileRow(STUCK_FILE.filename);
    expect(stuck.getByText("SYN-A")).toBeInTheDocument();
    expect(stuck.getByText(i18n.t("admin.quarantine.statuses.verifying"))).toBeInTheDocument();
    expect(stuck.queryByText(i18n.t("admin.quarantine.objectDeleted"))).not.toBeInTheDocument();
    const infected = fileRow(INFECTED_FILE.filename);
    expect(infected.getByText("SYN-B")).toBeInTheDocument();
    expect(infected.getByText(i18n.t("admin.quarantine.reasons.infected"))).toBeInTheDocument();
    expect(
      infected.getByText(i18n.t("admin.quarantine.signature", { signature: "Synthetic.Test.Signature" })),
    ).toBeInTheDocument();
    expect(infected.getByText(i18n.t("admin.quarantine.objectDeleted"))).toBeInTheDocument();
    // Only a file still being checked, with its bytes, can be re-checked.
    expect(stuck.getByRole("button", { name: i18n.t("admin.quarantine.recheck") })).toBeInTheDocument();
    expect(infected.queryByRole("button", { name: i18n.t("admin.quarantine.recheck") })).not.toBeInTheDocument();
  });

  it("shows counts per project and status", async () => {
    renderAdmin(PATH, handlers(), SYS);
    await loaded();
    const values = (code: string) => countsRow(code).getAllByRole("definition").map((d) => d.textContent);
    // verifying, rejected, expired, total
    expect(values("SYN-A")).toEqual(["2", "0", "1", "3"]);
    expect(values("SYN-B")).toEqual(["0", "5", "0", "5"]);
  });

  it("filters by status, reason and project on the server", async () => {
    const calls = renderAdmin(PATH, handlers(), SYS);
    await loaded();
    const filters = region(i18n.t("admin.quarantine.filters"));
    const status = filters.getByLabelText(i18n.t("admin.quarantine.status"));
    expect(within(status).getAllByRole("option").map((o) => o.textContent)).toEqual([
      i18n.t("admin.quarantine.all"),
      i18n.t("admin.quarantine.statuses.verifying"),
      i18n.t("admin.quarantine.statuses.rejected"),
      i18n.t("admin.quarantine.statuses.expired"),
    ]);
    fireEvent.change(status, { target: { value: "rejected" } });
    await waitFor(() => { expect(lists(calls).some((c) => c.query.includes("status=rejected"))).toBe(true); }, WAIT);

    const reason = filters.getByLabelText(i18n.t("admin.quarantine.reason"));
    expect(within(reason).getByRole("option", { name: i18n.t("admin.quarantine.reasons.size_mismatch") })).toBeInTheDocument();
    fireEvent.change(reason, { target: { value: "infected" } });
    await waitFor(() => { expect(lists(calls).some((c) => c.query.includes("reason=infected"))).toBe(true); }, WAIT);

    const project = filters.getByLabelText(i18n.t("admin.quarantine.project"));
    expect(within(project).getByRole("option", { name: "SYN-B" })).toHaveValue(PROJECT_B);
    fireEvent.change(project, { target: { value: PROJECT_B } });
    await waitFor(() => {
      const last = lists(calls).at(-1);
      expect(last?.query).toContain(`project=${PROJECT_B}`);
      expect(last?.query).toContain("status=rejected");
      expect(last?.query).toContain("reason=infected");
    }, WAIT);
    // The counts are the whole overview (they also list the projects and reasons to filter by).
    expect(calls.filter((c) => c.path.endsWith("/uploads/counts")).every((c) => c.query === "")).toBe(true);
  });

  it("re-checks a stuck file after confirmation", async () => {
    const calls = renderAdmin(
      PATH,
      handlers({
        [`POST ${RECHECK}`]: () =>
          json({ job_id: "00000000-0000-4000-8000-00000000d002", project_id: PROJECT_A, kind: "uploads.verify", status: "queued" }, 202),
      }),
      SYS,
    );
    await loaded();
    fireEvent.click(fileRow(STUCK_FILE.filename).getByRole("button", { name: i18n.t("admin.quarantine.recheck") }));
    const dialog = within(
      screen.getByRole("dialog", { name: i18n.t("admin.quarantine.recheckTitle", { file: STUCK_FILE.filename }) }),
    );
    expect(calls.some((c) => c.path === RECHECK)).toBe(false);
    fireEvent.click(dialog.getByRole("button", { name: i18n.t("admin.quarantine.recheck") }));
    expect(await screen.findByText(i18n.t("admin.quarantine.rechecked"), {}, WAIT)).toBeInTheDocument();
    expect(calls.filter((c) => c.path === RECHECK)).toHaveLength(1);
  });

  it.each(["NOT_VERIFYING", "CHECK_RUNNING", "OBJECT_GONE"] as const)("explains a re-check refused with %s", async (code) => {
    renderAdmin(PATH, handlers({ [`POST ${RECHECK}`]: () => json({ detail: { code, message: "x" } }, 409) }), SYS);
    await loaded();
    fireEvent.click(fileRow(STUCK_FILE.filename).getByRole("button", { name: i18n.t("admin.quarantine.recheck") }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: i18n.t("admin.quarantine.recheck") }));
    expect(await screen.findByText(i18n.t(`admin.quarantine.conflicts.${code}`), {}, WAIT)).toBeInTheDocument();
  });

  it("sweeps a project now after confirmation", async () => {
    const calls = renderAdmin(
      PATH,
      handlers({
        [`POST ${SWEEP}`]: () =>
          json({ job_id: "00000000-0000-4000-8000-00000000d003", project_id: PROJECT_B, kind: "uploads.sweep_orphans", status: "queued" }, 202),
      }),
      SYS,
    );
    await loaded();
    fireEvent.click(countsRow("SYN-B").getByRole("button", { name: i18n.t("admin.quarantine.sweep") }));
    const dialog = within(screen.getByRole("dialog", { name: i18n.t("admin.quarantine.sweepTitle", { project: "SYN-B" }) }));
    fireEvent.click(dialog.getByRole("button", { name: i18n.t("admin.quarantine.sweep") }));
    expect(await screen.findByText(i18n.t("admin.quarantine.swept"), {}, WAIT)).toBeInTheDocument();
    const sweep = calls.filter((c) => c.path === SWEEP);
    expect(sweep).toHaveLength(1);
    expect(sweep[0]?.query).toBe(`project=${PROJECT_B}`);
  });

  it("pages the rows with the total", async () => {
    const calls = renderAdmin(
      PATH,
      handlers({
        "GET /api/v1/admin/system/uploads": (req: Recorded) =>
          json(req.query.includes(`offset=${String(PAGE_SIZE)}`) ? page([INFECTED_FILE], 51, PAGE_SIZE) : page([STUCK_FILE], 51)),
      }),
      SYS,
    );
    await loaded();
    expect(screen.getByText(i18n.t("admin.system.range", { from: 1, to: 1, total: 51 }))).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: i18n.t("admin.system.next") }));
    expect(await screen.findByText(INFECTED_FILE.filename, {}, WAIT)).toBeInTheDocument();
    expect(lists(calls).at(-1)?.query).toContain(`offset=${String(PAGE_SIZE)}`);
  });

  it("says when nothing matches", async () => {
    renderAdmin(PATH, handlers({ "GET /api/v1/admin/system/uploads": () => json(page([])) }), SYS);
    expect(await screen.findByText(i18n.t("admin.quarantine.empty"), {}, WAIT)).toBeInTheDocument();
  });

  it("refuses everyone else without calling the API", async () => {
    const calls = renderAdmin(PATH, handlers());
    expect(await screen.findByText(i18n.t("admin.system.forbiddenConsole"), {}, WAIT)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: i18n.t("admin.system.uploadsTab") })).not.toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/api/v1/admin/system/"))).toBe(false);
  });

  it("explains a 403 from the server", async () => {
    renderAdmin(PATH, handlers({ "GET /api/v1/admin/system/uploads": () => json({ detail: "Forbidden" }, 403) }), SYS);
    expect(await screen.findByText(i18n.t("admin.system.forbiddenConsole"), {}, WAIT)).toBeInTheDocument();
  });
});

describe("countsByProject", () => {
  it("sums reasons per project and status", () => {
    expect(countsByProject(COUNTS)).toEqual([
      { projectId: PROJECT_A, projectCode: "SYN-A", byStatus: { verifying: 2, rejected: 0, expired: 1 }, total: 3 },
      { projectId: PROJECT_B, projectCode: "SYN-B", byStatus: { verifying: 0, rejected: 5, expired: 0 }, total: 5 },
    ]);
  });
});
