// Admin access-request inbox and device screens (T-0051) with their dialogs open: WCAG 2.1
// AA, contrast and 48 px glove targets in both themes on the field breakpoints (diff §6.3).
import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { i18n } from "../i18n";
import { auditA11y, FIELD_VIEWPORTS, formatFindings, setViewport } from "../test/a11y";
import { FAKE_USER, json } from "../test/fakeApi";
import { applyTheme, THEMES } from "../theme/theme";
import { ADMIN_PROJECT, renderAdmin } from "./testing";

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
