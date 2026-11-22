// Admin screens (T-0051, T-0026, T-0232, T-0224, T-0225) with their dialogs open: WCAG 2.1
// AA, contrast and 48 px glove targets in both themes on the field breakpoints (diff §6.3).
import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { i18n } from "../i18n";
import { auditA11y, FIELD_VIEWPORTS, formatFindings, setViewport } from "../test/a11y";
import { FAKE_USER, json } from "../test/fakeApi";
import { applyTheme, THEMES } from "../theme/theme";
import { DEPLOYMENT_CONFIG, REGISTRY } from "./settingsFixtures";
import {
  COUNTS,
  FAILED_JOB,
  HEALTH,
  INFECTED_FILE,
  PROJECT_ENTRY,
  QUEUES,
  STATS,
  STUCK_FILE,
  TENANT_ENTRY,
} from "./systemFixtures";
import { ADMIN_PROJECT, project, renderAdmin } from "./testing";

// The T-0026 cases wait on mocked round-trips through the whole shell (slow under load).
const WAIT = { timeout: 5000 };
const SLOW = 20_000;

const REQUEST = {
  id: "00000000-0000-4000-8000-000000000201",
  project_id: ADMIN_PROJECT.id,
  project_code: ADMIN_PROJECT.code,
  project_name: ADMIN_PROJECT.name,
  requester: { id: "00000000-0000-4000-8000-000000000301", email: "fitter@example.test", display_name: "Synthetic Fitter" },
  message: "Joining the shutdown crew.",
  created_at: "2026-11-18T08:00:00Z",
};
const DEVICE = {
  device_id: "00000000-0000-4000-8000-000000000501",
  user_id: FAKE_USER.id,
  platform: "android",
  os_version: "14",
  app_version: "1.0.1",
  status: "active",
  enrolled_at: "2026-10-01T08:00:00Z",
  last_seen_at: "2026-11-19T07:30:00Z",
  last_sync_at: "2026-11-18T16:45:00Z",
  revoked_at: null,
  revoked_by: null,
  wipe_confirmed_at: null,
};

describe.each(THEMES)("admin screens in the %s theme", (theme) => {
  afterEach(() => {
    applyTheme("standard");
  });

  describe.each(FIELD_VIEWPORTS)("at $name", (viewport) => {
    it("the access-request inbox with the approve dialog", async () => {
      await setViewport(viewport);
      renderAdmin(
        "/admin/access-requests",
        { "GET /api/v1/access-requests/pending": () => json([REQUEST]) },
        { theme },
      );
      const card = await screen.findByRole("listitem", { name: "Synthetic Fitter" });
      fireEvent.click(within(card).getByRole("button", { name: i18n.t("admin.accessRequests.approve") }));
      fireEvent.click(screen.getByRole("checkbox", { name: /PER-07/ }));

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    });

    it("admin grants", async () => {
      await setViewport(viewport);
      renderAdmin(
        "/admin/system/grants",
        {
          "GET /api/v1/admin/users": () => json([{ ...FAKE_USER, is_active: true, is_internal: true }]),
          "GET /api/v1/admin/grants": () => json([{ ...FAKE_USER, user_id: FAKE_USER.id, grant: "system_admin" }]),
        },
        { theme, systemAdmin: true },
      );
      await screen.findByText(FAKE_USER.email, {}, WAIT);

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    }, SLOW);

    it("runtime settings with an inline error and the deployment configuration", async () => {
      await setViewport(viewport);
      renderAdmin(
        "/admin/system/settings",
        {
          "GET /api/v1/settings/registry": () => json(REGISTRY),
          "GET /api/v1/settings/deployment-config": () => json(DEPLOYMENT_CONFIG),
        },
        { theme, systemAdmin: true, projects: [] },
      );
      await screen.findByRole("region", { name: i18n.t("admin.settings.deployment.title") }, WAIT);
      const days = await screen.findByLabelText(
        i18n.t("admin.settings.entries.offline_window_days"),
        { selector: "input" },
        WAIT,
      );
      fireEvent.change(days, { target: { value: "15" } });
      await screen.findByText(i18n.t("admin.settings.errors.max", { max: "14" }), {}, WAIT);

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    }, SLOW);

    it("deployment stats and health", async () => {
      await setViewport(viewport);
      renderAdmin(
        "/admin/system/deployment",
        {
          "GET /api/v1/admin/system/stats": () => json(STATS),
          "GET /api/v1/admin/system/health": () => json(HEALTH),
        },
        { theme, systemAdmin: true, projects: [] },
      );
      await screen.findByRole("region", { name: i18n.t("admin.deployment.healthTitle") }, WAIT);
      await screen.findByRole("region", { name: i18n.t("admin.deployment.objectStore") }, WAIT);

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    }, SLOW);

    it("queue monitor with the retry dialog", async () => {
      await setViewport(viewport);
      renderAdmin(
        "/admin/system/queues",
        {
          "GET /api/v1/admin/system/queues": () => json(QUEUES),
          "GET /api/v1/admin/system/queues/failed": () => json([FAILED_JOB]),
        },
        { theme, systemAdmin: true, projects: [] },
      );
      const row = await screen.findByRole("listitem", { name: FAILED_JOB.kind }, WAIT);
      fireEvent.click(within(row).getByRole("button", { name: i18n.t("admin.queues.retry") }));
      await screen.findByRole("dialog", {}, WAIT);

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    }, SLOW);

    it("upload quarantine with the re-check dialog", async () => {
      await setViewport(viewport);
      renderAdmin(
        "/admin/system/uploads",
        {
          "GET /api/v1/admin/system/uploads": () =>
            json({ items: [STUCK_FILE, INFECTED_FILE], total: 2, limit: 50, offset: 0 }),
          "GET /api/v1/admin/system/uploads/counts": () => json(COUNTS),
        },
        { theme, systemAdmin: true, projects: [] },
      );
      const row = await screen.findByRole("listitem", { name: STUCK_FILE.filename }, WAIT);
      await screen.findByRole("region", { name: i18n.t("admin.quarantine.countsTitle") }, WAIT);
      fireEvent.click(within(row).getByRole("button", { name: i18n.t("admin.quarantine.recheck") }));
      await screen.findByRole("dialog", {}, WAIT);

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    }, SLOW);

    it("audit log with an open entry, a broken chain and an invalid filter", async () => {
      await setViewport(viewport);
      renderAdmin(
        "/admin/system/audit",
        {
          "GET /api/v1/admin/system/audit": () => json({ items: [PROJECT_ENTRY, TENANT_ENTRY], next_cursor: "c1" }),
          "POST /api/v1/admin/system/audit/verify": () =>
            json({
              chain_key: PROJECT_ENTRY.chain_key,
              project_id: PROJECT_ENTRY.project_id,
              ok: false,
              length: 5,
              head_hash: "e".repeat(64),
              first_broken_seq: 3,
              problem_count: 1,
              problems: ["seq 3: content_hash mismatch"],
            }),
        },
        { theme, systemAdmin: true, projects: [] },
      );
      const row = await screen.findByRole("listitem", { name: `${PROJECT_ENTRY.chain_key} #4` }, WAIT);
      fireEvent.click(within(row).getByText(i18n.t("admin.audit.details")));
      fireEvent.click(within(row).getByRole("button", { name: i18n.t("admin.audit.verify") }));
      await within(row).findByText("seq 3: content_hash mismatch", {}, WAIT);
      fireEvent.change(screen.getByLabelText(i18n.t("admin.audit.flag")), { target: { value: "9bad" } });

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    }, SLOW);

    it("project admin page with the legal-hold dialog", async () => {
      await setViewport(viewport);
      renderAdmin(
        `/admin/projects/${ADMIN_PROJECT.id}/members`,
        {
          "GET /api/v1/projects": () => json([project({ legal_hold: true })]),
          [`GET /api/v1/admin/projects/${ADMIN_PROJECT.id}/members`]: () => json([]),
        },
        { theme, systemAdmin: true },
      );
      fireEvent.click(
        await screen.findByRole("button", { name: i18n.t("admin.projectAdmin.clearHold") }, WAIT),
      );
      await screen.findByRole("dialog", {}, WAIT);
      // axe measures a field's contrast only with a value in it.
      fireEvent.change(screen.getByLabelText(i18n.t("admin.projectAdmin.reason")), {
        target: { value: "Synthetic dispute settled" },
      });

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    }, SLOW);

    it("projects with the new-project dialog", async () => {
      await setViewport(viewport);
      renderAdmin("/backbone/projects", { "GET /api/v1/projects": () => json([project()]) }, { theme });
      await screen.findByText(i18n.t("admin.projects.members"), {}, WAIT);
      fireEvent.click(screen.getByRole("button", { name: i18n.t("admin.projects.new") }));

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    }, SLOW);

    it("project members with the assign dialog and the SoD warning", async () => {
      await setViewport(viewport);
      const member = {
        user_id: REQUEST.requester.id,
        email: REQUEST.requester.email,
        display_name: REQUEST.requester.display_name,
        grants: [
          { persona: "PER-06", site_node_id: null, source: "direct", source_id: null, team_name: null },
          { persona: "PER-07", site_node_id: "00000000-0000-4000-8000-000000000401", source: "direct", source_id: null, team_name: null },
        ],
      };
      renderAdmin(
        `/admin/projects/${ADMIN_PROJECT.id}/members`,
        {
          "GET /api/v1/projects": () => json([project()]),
          [`GET /api/v1/admin/projects/${ADMIN_PROJECT.id}/members`]: () => json([member]),
          [`PUT /api/v1/admin/projects/${ADMIN_PROJECT.id}/members/${member.user_id}`]: () =>
            json({ detail: { code: "sod_confirmation_required", sod_warning: true } }, 409),
        },
        { theme },
      );
      fireEvent.click(await screen.findByText(i18n.t("admin.members.edit"), {}, WAIT));
      fireEvent.click(screen.getByRole("checkbox", { name: /PER-09/ }));
      fireEvent.click(screen.getByRole("button", { name: i18n.t("admin.members.save") }));
      await within(screen.getByRole("dialog")).findByRole("alert", {}, WAIT);

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    }, SLOW);

    it("users with the new-account dialog and the invite SoD warning", async () => {
      await setViewport(viewport);
      renderAdmin(
        "/admin/users",
        {
          "GET /api/v1/admin/users": () =>
            json([
              { ...FAKE_USER, is_active: true, is_internal: true },
              { ...REQUEST.requester, is_active: false, is_internal: false },
            ]),
          "GET /api/v1/projects": () => json([project()]),
          [`GET /api/v1/admin/projects/${ADMIN_PROJECT.id}/invites`]: () => json([]),
          [`POST /api/v1/admin/projects/${ADMIN_PROJECT.id}/invites`]: () =>
            json({ detail: { code: "sod_confirmation_required", sod_warning: true } }, 409),
        },
        { theme, systemAdmin: true },
      );
      await screen.findByText(REQUEST.requester.email, { exact: false }, WAIT);
      fireEvent.click(screen.getByRole("button", { name: i18n.t("admin.users.newAccount") }));
      const target = await screen.findByRole("combobox", { name: i18n.t("admin.users.project") }, WAIT);
      await within(target).findByRole("option", { name: /SYN-001/ }, WAIT);
      fireEvent.change(screen.getByLabelText(i18n.t("admin.users.inviteEmail")), {
        target: { value: "fitter@example.test" },
      });
      fireEvent.click(screen.getByRole("checkbox", { name: /PER-09/ }));
      fireEvent.click(screen.getByRole("checkbox", { name: /PER-02/ }));
      fireEvent.click(screen.getByRole("button", { name: i18n.t("admin.users.send") }));
      await screen.findByRole("button", { name: i18n.t("admin.users.confirmSod") }, WAIT);

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    }, SLOW);

    it("my devices with the revoke dialog", async () => {
      await setViewport(viewport);
      renderAdmin("/profile/devices", { "GET /api/v1/devices": () => json([DEVICE]) }, { theme });
      fireEvent.click(await screen.findByRole("button", { name: i18n.t("devices.revoke") }));
      await screen.findByRole("dialog");

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    });
  });
});
