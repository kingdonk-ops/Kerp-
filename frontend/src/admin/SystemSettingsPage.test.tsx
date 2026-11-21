// Admin → System → Settings (T-0232, diff §4.7, §6.5). Synthetic data only.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { i18n } from "../i18n";
import { json, type Recorded } from "../test/fakeApi";
import { checkSettingValue, type SettingEntry } from "./settings";
import { DEPLOYMENT_CONFIG, REGISTRY, settingEntry } from "./settingsFixtures";
import { renderAdmin } from "./testing";

// Mocked round-trips through the whole shell are slow under load (parallel suites).
vi.setConfig({ testTimeout: 20_000 });
const WAIT = { timeout: 5000 };

const PATH = "/admin/system/settings";
const handlers = (extra = {}) => ({
  "GET /api/v1/settings/registry": () => json(REGISTRY),
  "GET /api/v1/settings/deployment-config": () => json(DEPLOYMENT_CONFIG),
  ...extra,
});
const label = (key: string) =>
  (i18n.t("admin.settings.entries", { returnObjects: true }) as Record<string, string>)[key] ?? key;
const field = (key: string) => screen.getByLabelText(label(key), { selector: "input, select" });
const row = (key: string) => {
  const el = screen.getByRole("group", { name: label(key) });
  return within(el);
};
const loaded = () => screen.findByRole("heading", { name: i18n.t("admin.settings.categories.offline") }, WAIT);
const saveIn = (key: string) => row(key).getByRole("button", { name: i18n.t("admin.settings.save") });

describe("runtime settings (System admins)", () => {
  it("groups entries by category with type-appropriate inputs and their bounds", async () => {
    renderAdmin(PATH, handlers(), { projects: [], systemAdmin: true });
    await loaded();
    expect(screen.getByRole("heading", { name: i18n.t("admin.settings.categories.project_defaults") })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: i18n.t("admin.settings.categories.uploads_media") })).toBeInTheDocument();

    const days = field("offline_window_days");
    expect(days).toHaveAttribute("type", "number");
    expect(days).toHaveValue(7);
    expect(days).toHaveAttribute("min", "1");
    expect(days).toHaveAttribute("max", "14");
    expect(row("offline_window_days").getByText(i18n.t("admin.settings.range", { min: "1", max: "14" }))).toBeInTheDocument();

    const status = field("default_status");
    expect(status.tagName).toBe("SELECT");
    expect(within(status).getAllByRole("option").map((o) => o.textContent)).toEqual(["ACTIVE", "ON_HOLD", "CLOSED"]);

    const zone = field("default_site_timezone");
    expect(zone).toHaveAttribute("type", "text");
    expect(zone).toHaveValue("Australia/Perth");

    expect(field("sample_flag")).toHaveAttribute("type", "checkbox");
    expect(field("sample_list")).toHaveValue("1, 2, 3");
    // Unknown categories and entries (declared by later tasks) still show, by key.
    expect(screen.getByRole("heading", { name: "sample_category" })).toBeInTheDocument();
  });

  it("shows the deployment cap as the effective maximum", async () => {
    renderAdmin(PATH, handlers(), { projects: [], systemAdmin: true });
    await loaded();
    const upload = field("max_upload_size_mb");
    expect(upload).toHaveAttribute("max", "50");
    expect(
      row("max_upload_size_mb").getByText(i18n.t("admin.settings.capped", { cap: "50", source: "KERP_UPLOAD_MAX_BYTES" })),
    ).toBeInTheDocument();
  });

  it("checks bounds client-side and never sends an out-of-bounds value", async () => {
    const calls = renderAdmin(PATH, handlers(), { projects: [], systemAdmin: true });
    await loaded();
    fireEvent.change(field("offline_window_days"), { target: { value: "15" } });
    expect(row("offline_window_days").getByText(i18n.t("admin.settings.errors.max", { max: "14" }))).toBeInTheDocument();
    expect(field("offline_window_days")).toHaveAttribute("aria-invalid", "true");
    expect(saveIn("offline_window_days")).toBeDisabled();

    fireEvent.change(field("max_upload_size_mb"), { target: { value: "60" } });
    expect(row("max_upload_size_mb").getByText(i18n.t("admin.settings.errors.max", { max: "50" }))).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("saves a valid value with PATCH /registry/{key}", async () => {
    const updated = { ...settingEntry("offline_window_days"), value: 10, is_default: false };
    const calls = renderAdmin(
      PATH,
      handlers({ "PATCH /api/v1/settings/registry/offline_window_days": () => json(updated) }),
      { projects: [], systemAdmin: true },
    );
    await loaded();
    fireEvent.change(field("offline_window_days"), { target: { value: "10" } });
    fireEvent.click(saveIn("offline_window_days"));
    expect(await row("offline_window_days").findByText(i18n.t("admin.settings.saved"), {}, WAIT)).toBeInTheDocument();
    const patch = calls.find((c: Recorded) => c.method === "PATCH");
    expect(patch?.path).toBe("/api/v1/settings/registry/offline_window_days");
    expect(patch?.body).toEqual({ value: 10 });
  });

  it("sends booleans, enums and int lists with their JSON types", async () => {
    const calls = renderAdmin(
      PATH,
      handlers({
        "PATCH /api/v1/settings/registry/sample_flag": () => json({ ...settingEntry("sample_flag"), value: false }),
        "PATCH /api/v1/settings/registry/sample_list": () => json({ ...settingEntry("sample_list"), value: [4, 5] }),
      }),
      { projects: [], systemAdmin: true },
    );
    await loaded();
    fireEvent.click(field("sample_flag"));
    fireEvent.click(saveIn("sample_flag"));
    fireEvent.change(field("sample_list"), { target: { value: "4, 5" } });
    fireEvent.click(saveIn("sample_list"));
    await waitFor(() => {
      expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2);
    }, WAIT);
    const bodies = Object.fromEntries(calls.filter((c) => c.method === "PATCH").map((c) => [c.path, c.body]));
    expect(bodies["/api/v1/settings/registry/sample_flag"]).toEqual({ value: false });
    expect(bodies["/api/v1/settings/registry/sample_list"]).toEqual({ value: [4, 5] });
  });

  it("shows the server's 422 inline at the entry", async () => {
    const detail = "offline_grace_hours may be at most 24 x offline_window_days (48 for a 2-day window)";
    renderAdmin(
      PATH,
      handlers({ "PATCH /api/v1/settings/registry/offline_grace_hours": () => json({ detail }, 422) }),
      { projects: [], systemAdmin: true },
    );
    await loaded();
    fireEvent.change(field("offline_grace_hours"), { target: { value: "100" } });
    fireEvent.click(saveIn("offline_grace_hours"));
    expect(
      await row("offline_grace_hours").findByText(i18n.t("admin.settings.errors.server", { detail }), {}, WAIT),
    ).toBeInTheDocument();
    expect(field("offline_grace_hours")).toHaveAttribute("aria-invalid", "true");
  });

  it("shows the read-only deployment configuration without inputs", async () => {
    renderAdmin(PATH, handlers(), { projects: [], systemAdmin: true });
    const section = await screen.findByRole("region", { name: i18n.t("admin.settings.deployment.title") }, WAIT);
    const config = within(section);
    expect(await config.findAllByText("ap-southeast-2", {}, WAIT)).toHaveLength(2);
    expect(config.getByText(i18n.t("admin.settings.deployment.readOnly"))).toBeInTheDocument();
    expect(config.getByText(i18n.t("admin.settings.deployment.hours", { value: 12 }))).toBeInTheDocument();
    expect(config.getByText("noreply@example.test")).toBeInTheDocument();
    expect(config.getByText(i18n.t("admin.settings.deployment.aiNone"))).toBeInTheDocument();
    expect(config.queryAllByRole("textbox")).toHaveLength(0);
    expect(config.queryAllByRole("spinbutton")).toHaveLength(0);
  });

  it("shows the Settings tab to System admins", async () => {
    renderAdmin(PATH, handlers(), { projects: [], systemAdmin: true });
    await loaded();
    expect(screen.getByRole("link", { name: i18n.t("admin.system.settingsTab") })).toHaveAttribute("href", PATH);
  });

  it("refuses everyone else without calling the API and hides the tab", async () => {
    const calls = renderAdmin(PATH, handlers());
    expect(await screen.findByText(i18n.t("admin.settings.forbidden"), {}, WAIT)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: i18n.t("admin.system.settingsTab") })).not.toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/api/v1/settings/"))).toBe(false);
  });

  it("explains a 403 from the server", async () => {
    renderAdmin(
      PATH,
      handlers({ "GET /api/v1/settings/registry": () => json({ detail: "Forbidden" }, 403) }),
      { projects: [], systemAdmin: true },
    );
    expect(await screen.findByText(i18n.t("admin.settings.forbidden"), {}, WAIT)).toBeInTheDocument();
  });
});

describe("checkSettingValue", () => {
  const entry = (extra: Partial<SettingEntry>): SettingEntry => ({ ...settingEntry("offline_window_days"), ...extra });

  it("parses integers within bounds and refuses fractions, blanks and out of range", () => {
    expect(checkSettingValue(entry({}), "7")).toEqual({ ok: true, value: 7 });
    expect(checkSettingValue(entry({}), "7.5")).toEqual({ ok: false, error: { key: "integer" } });
    expect(checkSettingValue(entry({}), " ")).toEqual({ ok: false, error: { key: "required" } });
    expect(checkSettingValue(entry({}), "0")).toEqual({ ok: false, error: { key: "min", min: 1 } });
    expect(checkSettingValue(entry({}), "15")).toEqual({ ok: false, error: { key: "max", max: 14 } });
  });

  it("applies the deployment cap below the maximum", () => {
    expect(checkSettingValue(entry({ cap: 10 }), "11")).toEqual({ ok: false, error: { key: "max", max: 10 } });
    expect(checkSettingValue(entry({ cap: 20 }), "14")).toEqual({ ok: true, value: 14 });
  });

  it("parses floats, strings, enums, booleans and int lists", () => {
    expect(checkSettingValue(entry({ type: "float", minimum: 0, maximum: 1 }), "0.5")).toEqual({ ok: true, value: 0.5 });
    expect(checkSettingValue(entry({ type: "string", max_length: 3, minimum: null, maximum: null }), "abcd")).toEqual({
      ok: false,
      error: { key: "maxLength", max: 3 },
    });
    expect(checkSettingValue(entry({ type: "enum", choices: ["A", "B"] }), "C")).toEqual({ ok: false, error: { key: "choice" } });
    expect(checkSettingValue(entry({ type: "bool" }), true)).toEqual({ ok: true, value: true });
    const list = entry({ type: "int_list", minimum: 1, maximum: 9, max_length: 2 });
    expect(checkSettingValue(list, "1, 2")).toEqual({ ok: true, value: [1, 2] });
    expect(checkSettingValue(list, "")).toEqual({ ok: true, value: [] });
    expect(checkSettingValue(list, "1, x")).toEqual({ ok: false, error: { key: "integer" } });
    expect(checkSettingValue(list, "1, 2, 3")).toEqual({ ok: false, error: { key: "maxItems", max: 2 } });
    expect(checkSettingValue(list, "1, 10")).toEqual({ ok: false, error: { key: "max", max: 9 } });
  });
});
