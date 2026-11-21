// Admin screens (T-0051, T-0026) with their dialogs open: WCAG 2.1
// AA, contrast and 48 px glove targets in both themes on the field breakpoints (diff §6.3).
import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { i18n } from "../i18n";
import { auditA11y, FIELD_VIEWPORTS, formatFindings, setViewport } from "../test/a11y";
import { FAKE_USER, json } from "../test/fakeApi";
import { applyTheme, THEMES } from "../theme/theme";
import { ADMIN_PROJECT, project, renderAdmin } from "./testing";

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
      await screen.findByText(FAKE_USER.email);

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    });

    it("projects with the new-project dialog", async () => {
      await setViewport(viewport);
      renderAdmin("/backbone/projects", { "GET /api/v1/projects": () => json([project()]) }, { theme });
      await screen.findByRole("link", { name: i18n.t("admin.projects.members") });
      fireEvent.click(screen.getByRole("button", { name: i18n.t("admin.projects.new") }));

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    });

    it("project members with the assign dialog and the SoD warning", async () => {
      await setViewport(viewport);
      const member = {
        user_id: REQUEST.requester.id,
        email: REQUEST.requester.email,
        display_name: REQUEST.requester.display_name,
        grants: [{ persona: "PER-06", site_node_id: null, source: "direct", source_id: null, team_name: null }],
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
      fireEvent.click(await screen.findByRole("button", { name: i18n.t("admin.members.edit") }));
      fireEvent.click(screen.getByRole("checkbox", { name: /PER-09/ }));
      fireEvent.click(screen.getByRole("button", { name: i18n.t("admin.members.save") }));
      await within(screen.getByRole("dialog")).findByRole("alert");

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    });

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
