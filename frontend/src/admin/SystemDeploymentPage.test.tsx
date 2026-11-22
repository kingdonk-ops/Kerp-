// Admin → System → Deployment (T-0224, diff §6.5). Synthetic data only.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { i18n } from "../i18n";
import { json } from "../test/fakeApi";
import { formatBytes } from "./system";
import { HEALTH, STATS } from "./systemFixtures";
import { renderAdmin } from "./testing";

vi.setConfig({ testTimeout: 20_000 });
const WAIT = { timeout: 5000 };

const PATH = "/admin/system/deployment";
const handlers = (extra = {}) => ({
  "GET /api/v1/admin/system/stats": () => json(STATS),
  "GET /api/v1/admin/system/health": () => json(HEALTH),
  ...extra,
});
const SYS = { projects: [], systemAdmin: true };
const region = (name: string) => within(screen.getByRole("region", { name }));
const loaded = () => screen.findByRole("region", { name: i18n.t("admin.deployment.healthTitle") }, WAIT);

describe("deployment stats and health", () => {
  it("shows the count cards and the module counts", async () => {
    renderAdmin(PATH, handlers(), SYS);
    await loaded();
    const projects = region(i18n.t("admin.deployment.projects"));
    expect(projects.getByText(i18n.t("admin.deployment.active", { count: 12 }))).toBeInTheDocument();
    expect(projects.getByText(i18n.t("admin.deployment.softDeleted", { count: 2 }))).toBeInTheDocument();
    expect(
      region(i18n.t("admin.deployment.users")).getByText(i18n.t("admin.deployment.deactivated", { count: 3 })),
    ).toBeInTheDocument();
    expect(
      region(i18n.t("admin.deployment.devices")).getByText(i18n.t("admin.deployment.revoked", { count: 4 })),
    ).toBeInTheDocument();
    const modules = region(i18n.t("admin.deployment.modulesTitle"));
    expect(modules.getByText(i18n.t("admin.deployment.modules.asset_management"))).toBeInTheDocument();
    expect(modules.getByText(i18n.t("admin.deployment.moduleCounts.asset_management.asset_nodes"))).toBeInTheDocument();
    // Modules and counts without a label (later tasks) show their keys.
    expect(modules.getByText("synthetic_module")).toBeInTheDocument();
    expect(modules.getByText("widgets")).toBeInTheDocument();
  });

  it("shows object storage per class with its 'as of' time", async () => {
    renderAdmin(PATH, handlers(), SYS);
    await loaded();
    const store = region(i18n.t("admin.deployment.objectStore"));
    expect(store.getByText(i18n.t("admin.deployment.storageClasses.STANDARD"))).toBeInTheDocument();
    expect(
      store.getByText(i18n.t("admin.deployment.objects", { count: 120, size: formatBytes(5 * 1024 * 1024, "en") })),
    ).toBeInTheDocument();
    expect(store.getByText(/^As of /)).toBeInTheDocument();
  });

  it("refreshes the totals and reports a run already in progress", async () => {
    let status = 202;
    const calls = renderAdmin(
      PATH,
      handlers({
        "POST /api/v1/admin/system/stats/object-store/refresh": () =>
          json({ job_id: "00000000-0000-4000-8000-00000000d001", status: "queued" }, status),
      }),
      SYS,
    );
    await loaded();
    const button = screen.getByRole("button", { name: i18n.t("admin.deployment.refresh") });
    fireEvent.click(button);
    expect(await screen.findByText(i18n.t("admin.deployment.refreshQueued"), {}, WAIT)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    status = 409;
    fireEvent.click(button);
    expect(await screen.findByText(i18n.t("admin.deployment.refreshRunning"), {}, WAIT)).toBeInTheDocument();
  });

  it("says when the totals were never counted or a count is running", async () => {
    renderAdmin(
      PATH,
      handlers({
        "GET /api/v1/admin/system/stats": () =>
          json({ ...STATS, object_store: { as_of: null, refreshing: true, storage_classes: [] } }),
      }),
      SYS,
    );
    expect(await screen.findByText(i18n.t("admin.deployment.never"), {}, WAIT)).toBeInTheDocument();
    expect(screen.getByText(i18n.t("admin.deployment.refreshing"))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: i18n.t("admin.deployment.refresh") })).toBeDisabled();
  });

  it("shows a health tile per probe with status and latency, and checks again", async () => {
    const calls = renderAdmin(PATH, handlers(), SYS);
    await loaded();
    const health = region(i18n.t("admin.deployment.healthTitle"));
    expect(
      health.getByText(i18n.t("admin.deployment.overall", { status: i18n.t("admin.system.status.degraded") })),
    ).toBeInTheDocument();
    const db = within(health.getByRole("group", { name: i18n.t("admin.deployment.probes.postgresql") }));
    expect(db.getByText(i18n.t("admin.system.status.ok"))).toBeInTheDocument();
    expect(db.getByText(i18n.t("admin.deployment.latency", { value: "2.4" }))).toBeInTheDocument();
    const scanner = within(health.getByRole("group", { name: i18n.t("admin.deployment.probes.malware_scanner") }));
    expect(scanner.getByText(i18n.t("admin.system.status.down"))).toBeInTheDocument();
    expect(scanner.getByText(i18n.t("admin.deployment.noLatency"))).toBeInTheDocument();

    const before = calls.filter((c) => c.path === "/api/v1/admin/system/health").length;
    fireEvent.click(health.getByRole("button", { name: i18n.t("admin.deployment.checkAgain") }));
    await waitFor(
      () => { expect(calls.filter((c) => c.path === "/api/v1/admin/system/health").length).toBe(before + 1); },
      WAIT,
    );
  });

  it("shows the System tabs in order to System admins", async () => {
    renderAdmin(PATH, handlers(), SYS);
    await loaded();
    const tabs = within(screen.getByRole("navigation", { name: i18n.t("admin.system.tabs") }));
    expect(tabs.getAllByRole("link").map((l) => l.textContent)).toEqual([
      i18n.t("admin.system.deploymentTab"),
      i18n.t("admin.system.queuesTab"),
      i18n.t("admin.system.uploadsTab"),
      i18n.t("admin.system.settingsTab"),
      i18n.t("admin.system.grantsTab"),
      i18n.t("admin.system.devicesTab"),
    ]);
  });

  it("refuses everyone else without calling the API and hides the console tabs", async () => {
    const calls = renderAdmin(PATH, handlers());
    expect(await screen.findByText(i18n.t("admin.system.forbiddenConsole"), {}, WAIT)).toBeInTheDocument();
    for (const tab of ["deploymentTab", "queuesTab", "uploadsTab"] as const) {
      expect(screen.queryByRole("link", { name: i18n.t(`admin.system.${tab}`) })).not.toBeInTheDocument();
    }
    expect(calls.some((c) => c.path.startsWith("/api/v1/admin/system/"))).toBe(false);
  });

  it("explains a 403 from the server", async () => {
    renderAdmin(PATH, handlers({ "GET /api/v1/admin/system/stats": () => json({ detail: "Forbidden" }, 403) }), SYS);
    expect(await screen.findByText(i18n.t("admin.system.forbiddenConsole"), {}, WAIT)).toBeInTheDocument();
  });
});

describe("formatBytes", () => {
  it("picks the largest unit that keeps the value at least 1", () => {
    expect(formatBytes(512, "en")).toBe("512 byte");
    expect(formatBytes(2048, "en")).toBe("2 kB");
    expect(formatBytes(1.5 * 1024 * 1024, "en")).toBe("1.5 MB");
  });
});
