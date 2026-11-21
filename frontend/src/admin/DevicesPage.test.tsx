// Admin → System → Devices and Profile → My devices (T-0051, from T-0048; diff §4.3).
// Synthetic devices only.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { i18n } from "../i18n";
import { FAKE_USER, json } from "../test/fakeApi";
import type { Device } from "./api";
import { ADMIN_PROJECT, renderAdmin } from "./testing";
import { formatTime } from "./ui";

const FITTER = { id: "00000000-0000-4000-8000-000000000301", email: "fitter@example.test", display_name: "Synthetic Fitter" };
const WELDER = { id: "00000000-0000-4000-8000-000000000302", email: "welder@example.test", display_name: "Synthetic Welder" };

function device(n: number, userId: string, extra: Partial<Device> = {}): Device {
  return {
    device_id: `00000000-0000-4000-8000-00000000050${String(n)}`,
    user_id: userId,
    platform: "android",
    os_version: "14",
    app_version: `1.0.${String(n)}`,
    status: "active",
    enrolled_at: "2026-10-01T08:00:00Z",
    last_seen_at: "2026-11-19T07:30:00Z",
    last_sync_at: "2026-11-18T16:45:00Z",
    revoked_at: null,
    revoked_by: null,
    wipe_confirmed_at: null,
    ...extra,
  };
}

const ADMIN = "/admin/system/devices";
const D1 = device(1, FITTER.id);
const D2 = device(2, WELDER.id, { platform: "ios", os_version: "18", last_sync_at: null });
// The table row showing `appVersion` (a text query: role queries over the shell are slow).
const row = async (appVersion: string) => {
  const cell = await screen.findByText(appVersion);
  const tr = cell.closest("tr");
  if (!tr) throw new Error(`no row for ${appVersion}`);
  return tr;
};
const time = (iso: string) => formatTime(iso, "en") ?? "";

describe("Admin → System → Devices", () => {
  it("lists every user's devices for a System admin (user, platform, version, last seen/sync)", async () => {
    renderAdmin(
      ADMIN,
      {
        "GET /api/v1/admin/users": () => json([FITTER, WELDER].map((u) => ({ ...u, is_active: true, is_internal: false }))),
        [`GET /api/v1/users/${FITTER.id}/devices`]: () => json([D1]),
        [`GET /api/v1/users/${WELDER.id}/devices`]: () => json([D2]),
      },
      { projects: [], systemAdmin: true },
    );
    const r1 = await row("1.0.1");
    expect(r1).toHaveTextContent("Synthetic Fitter");
    expect(r1).toHaveTextContent("Android 14");
    expect(r1).toHaveTextContent(time(D1.last_seen_at));
    expect(r1).toHaveTextContent(time("2026-11-18T16:45:00Z"));
    const r2 = await row("1.0.2");
    expect(r2).toHaveTextContent("Synthetic Welder");
    expect(r2).toHaveTextContent("iOS 18");
    expect(r2).toHaveTextContent(i18n.t("devices.never"));
  });

  it("lists the devices of the PER-09's project members", async () => {
    const calls = renderAdmin(ADMIN, {
      [`GET /api/v1/admin/projects/${ADMIN_PROJECT.id}/members`]: () =>
        json([{ user_id: FITTER.id, email: FITTER.email, display_name: FITTER.display_name, grants: [] }]),
      [`GET /api/v1/users/${FITTER.id}/devices`]: () => json([D1]),
    });
    expect(await row("1.0.1")).toHaveTextContent("Synthetic Fitter");
    expect(calls.some((c) => c.path === "/api/v1/admin/users")).toBe(false);
  });

  it("revokes after a confirmation showing the last sync and the wipe; then shows revoked", async () => {
    const calls = renderAdmin(ADMIN, {
      [`GET /api/v1/admin/projects/${ADMIN_PROJECT.id}/members`]: () =>
        json([{ user_id: FITTER.id, email: FITTER.email, display_name: FITTER.display_name, grants: [] }]),
      [`GET /api/v1/users/${FITTER.id}/devices`]: () => json([D1]),
      [`POST /api/v1/devices/${D1.device_id}/revoke`]: () =>
        json({ ...D1, status: "revoked", revoked_at: "2026-11-19T09:00:00Z", role: "per_09", cross_project_warning: false }),
    });
    fireEvent.click(within(await row("1.0.1")).getByRole("button", { name: i18n.t("devices.revoke") }));
    const dialog = screen.getByRole("dialog", { name: i18n.t("devices.revokeTitle") });
    expect(dialog).toHaveTextContent(i18n.t("devices.revokeLastSync", { time: time("2026-11-18T16:45:00Z") }));
    expect(dialog).toHaveTextContent(i18n.t("devices.revokeWipe"));
    fireEvent.click(within(dialog).getByRole("button", { name: i18n.t("devices.confirmRevoke") }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(await row("1.0.1")).toHaveTextContent(i18n.t("devices.revokedWipePending"));
    expect(calls.find((c) => c.path.endsWith("/revoke"))?.body).toEqual({
      confirm_cross_project_wipe: false,
    });
  });

  it("warns that other projects' unsynced work is lost and then sends the confirmation flag", async () => {
    const calls = renderAdmin(ADMIN, {
      [`GET /api/v1/admin/projects/${ADMIN_PROJECT.id}/members`]: () =>
        json([{ user_id: FITTER.id, email: FITTER.email, display_name: FITTER.display_name, grants: [] }]),
      [`GET /api/v1/users/${FITTER.id}/devices`]: () => json([D1]),
      [`POST /api/v1/devices/${D1.device_id}/revoke`]: (req) =>
        (req.body as { confirm_cross_project_wipe: boolean }).confirm_cross_project_wipe
          ? json({ ...D1, status: "revoked", role: "per_09", cross_project_warning: true })
          : json({ detail: { code: "DEVICE_CROSS_PROJECT_WIPE", cross_project_warning: true } }, 409),
    });
    fireEvent.click(within(await row("1.0.1")).getByRole("button", { name: i18n.t("devices.revoke") }));
    fireEvent.click(screen.getByRole("button", { name: i18n.t("devices.confirmRevoke") }));

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("devices.crossProject"));
    fireEvent.click(screen.getByRole("button", { name: i18n.t("devices.confirmCrossProject") }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(await row("1.0.1")).toHaveTextContent(i18n.t("devices.revokedWipePending"));
    expect(
      calls.filter((c) => c.path.endsWith("/revoke")).map((c) => c.body),
    ).toEqual([{ confirm_cross_project_wipe: false }, { confirm_cross_project_wipe: true }]);
  });
});

describe("Profile → My devices", () => {
  it("lists the user's own devices, with the wipe time once the device confirmed it", async () => {
    const wiped = device(3, FAKE_USER.id, {
      status: "revoked",
      revoked_at: "2026-11-10T10:00:00Z",
      wipe_confirmed_at: "2026-11-10T10:05:00Z",
    });
    renderAdmin("/", {
      "GET /api/v1/devices": () => json([device(4, FAKE_USER.id), wiped]),
    }, { projects: [] });
    fireEvent.click(await screen.findByRole("link", { name: i18n.t("profile.myDevices") }));

    expect(await screen.findByRole("heading", { level: 1, name: i18n.t("devices.myTitle") })).toBeInTheDocument();
    const r3 = await row("1.0.3");
    expect(r3).toHaveTextContent(i18n.t("devices.revokedWiped", { time: time("2026-11-10T10:05:00Z") }));
    expect(within(r3).queryByRole("button")).not.toBeInTheDocument();
    expect(within(await row("1.0.4")).getByRole("button", { name: i18n.t("devices.revoke") })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: i18n.t("devices.user") })).not.toBeInTheDocument();
  });
});
